"""One loaded model for every Densanon app, instead of one copy per app.

Each app that runs a local model loads its own GGUF and keeps it in memory for
as long as the app is open. Two apps holding models means two models in RAM,
even when you only use one of them at a time.

The model service fixes that in the same way the hub fixes ports:

- **One app holds the ``/models`` route.** Every app that can run models
  starts a small service and competes for the route. One wins; the others
  stand by and take over within a lease or two if it quits. The route is
  loopback-only and never reachable from the network.
- **It loads on demand and unloads when idle.** Nothing is loaded until a
  request names a model; one model is resident at a time, and it is dropped
  after a few idle minutes. Using one app and then another a minute later
  costs one model's memory, not two.
- **Only catalogued models load.** Apps add their models to
  ``~/.densanon/models.json`` by id. A request names an id, never a path, so
  nothing reaching the route can make the service open an arbitrary file.
- **Apps barely change.** ``SharedLlama`` stands in for ``llama_cpp.Llama``
  for the calls the products make (``create_completion``,
  ``create_chat_completion``, ``tokenize``, ``detokenize``, ``n_ctx``). If no
  service is reachable it uses the app's own service directly, or loads the
  model itself, so an app works exactly as before when it is alone.

The service also speaks llama-server's ``/completion``, ``/health`` and
``/v1/models``, so DensaBooks' existing local-model provider works when it is
pointed at ``http://127.0.0.1:8770/models``.
"""

from __future__ import annotations

import base64
import gc
import http.client
import json
import logging
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from .config import hub_home, hub_port
from .member import CONFLICT, HubMember

log = logging.getLogger("densanon_hub")

CATALOG = "models.json"
PREFIX = "/models"
IDLE_UNLOAD = 300.0

# Generation settings passed through to llama.cpp. Anything else in a request
# is ignored, so a caller cannot reach load-time options or the file system.
_GEN_KEYS = {
    "max_tokens", "temperature", "top_p", "top_k", "min_p", "typical_p", "stop",
    "seed", "repeat_penalty", "presence_penalty", "frequency_penalty", "echo",
    "logprobs", "response_format",
}


# -- the catalog ---------------------------------------------------------------

def _catalog_path(home: Path | None) -> Path:
    return (home or hub_home()) / CATALOG


def read_catalog(home: Path | None = None) -> dict:
    try:
        data = json.loads(_catalog_path(home).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def register_model(model_id: str, path: str | os.PathLike, *, n_ctx: int = 4096,
                   n_gpu_layers: int = 0, home: Path | None = None) -> None:
    """Add a model to the shared catalog, or widen its context if it is there.

    Two apps can use one model with different context sizes; the service
    loads it once with the larger, which serves both.
    """
    cat = read_catalog(home)
    before = cat.get(model_id) or {}
    entry = dict(before)
    entry.update({
        "path": str(Path(path).resolve()),
        "n_ctx": max(int(n_ctx), int(entry.get("n_ctx") or 0)),
        "n_gpu_layers": int(n_gpu_layers) if "n_gpu_layers" not in before else before["n_gpu_layers"],
    })
    if before == entry:
        return
    cat[model_id] = entry
    target = _catalog_path(home)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{CATALOG}.{os.getpid()}.tmp")
    tmp.write_text(json.dumps(cat, indent=2), encoding="utf-8")
    os.replace(tmp, target)


def _default_loader(entry: dict):
    from llama_cpp import Llama
    return Llama(model_path=entry["path"], n_ctx=entry["n_ctx"],
                 n_gpu_layers=entry.get("n_gpu_layers", 0), verbose=False)


def llama_available() -> bool:
    try:
        import llama_cpp  # noqa: F401
        return True
    except Exception:
        return False


class ModelError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status


# -- the service -----------------------------------------------------------------

class ModelService:
    """Loads catalogued models on demand, one at a time, and unloads them when idle."""

    def __init__(self, *, home: Path | None = None, loader=_default_loader,
                 idle_unload: float = IDLE_UNLOAD):
        self.home = home
        self._loader = loader
        self.idle_unload = idle_unload
        self._lock = threading.RLock()
        self._loaded_id: str | None = None
        self._model = None
        self._last_used = 0.0
        self._server: ThreadingHTTPServer | None = None
        self._stop = threading.Event()

    # The HTTP side is a thin wrapper over call(), which an app can also use
    # directly when the hub is unreachable.

    def start(self) -> int:
        handler = type("ModelHandler", (_ModelHandler,), {"service": self})
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
        self._server.daemon_threads = True
        threading.Thread(target=self._server.serve_forever, name="densanon-models", daemon=True).start()
        threading.Thread(target=self._reaper, name="densanon-models-idle", daemon=True).start()
        return self._server.server_address[1]

    def stop(self) -> None:
        self._stop.set()
        if self._server:
            self._server.shutdown()
            self._server.server_close()
        self.unload()

    @property
    def loaded(self) -> str | None:
        return self._loaded_id

    def unload(self) -> None:
        with self._lock:
            if self._model is not None:
                log.info("unloading model %s", self._loaded_id)
            self._model = None
            self._loaded_id = None
        gc.collect()

    def _reaper(self) -> None:
        while not self._stop.wait(5.0):
            # Only unload when nothing is running: the lock is held for the
            # whole of a generation, so a busy model is never pulled out.
            if self._model is not None and time.monotonic() - self._last_used > self.idle_unload:
                if self._lock.acquire(blocking=False):
                    try:
                        if time.monotonic() - self._last_used > self.idle_unload:
                            self.unload()
                    finally:
                        self._lock.release()

    def _model_for(self, model_id: str | None):
        cat = read_catalog(self.home)
        if not model_id:
            model_id = self._loaded_id or next(iter(cat), None)
        if not model_id or model_id not in cat:
            raise ModelError(404, f"unknown model {model_id!r}; catalogued: {sorted(cat)}")
        if self._loaded_id != model_id:
            self.unload()  # one resident model: that is the saving
            entry = cat[model_id]
            if not Path(entry["path"]).is_file():
                raise ModelError(404, f"model file for {model_id!r} is missing")
            log.info("loading model %s", model_id)
            self._model = self._loader(entry)
            self._loaded_id = model_id
        return model_id, self._model, cat[model_id]

    def call(self, op: str, body: dict) -> dict:
        """Run one request. Raises ModelError for anything the caller got wrong."""
        if op == "models":
            cat = read_catalog(self.home)
            return {"object": "list", "data": [
                {"id": mid, "object": "model", "owned_by": "local",
                 "n_ctx": e.get("n_ctx"), "loaded": mid == self._loaded_id}
                for mid, e in cat.items()]}
        if op == "health":
            return {"status": "ok", "loaded": self._loaded_id}
        if body.get("stream"):
            raise ModelError(400, "streaming is not supported by the shared model service yet")
        with self._lock:
            model_id, model, entry = self._model_for(body.get("model"))
            self._last_used = time.monotonic()
            try:
                return self._run(op, body, model_id, model, entry)
            finally:
                self._last_used = time.monotonic()

    def _run(self, op: str, body: dict, model_id: str, model, entry: dict) -> dict:
        gen = {k: v for k, v in body.items() if k in _GEN_KEYS}
        grammar = body.get("grammar")
        if grammar:
            from llama_cpp import LlamaGrammar
            gen["grammar"] = LlamaGrammar.from_string(grammar, verbose=False)
        if op == "completions":
            return model.create_completion(prompt=str(body.get("prompt", "")), **gen)
        if op == "chat":
            messages = body.get("messages")
            if not isinstance(messages, list):
                raise ModelError(400, "messages must be a list")
            return model.create_chat_completion(messages=messages, **gen)
        if op == "completion":
            # llama-server's native shape, which DensaBooks speaks.
            if "n_predict" in body:
                gen["max_tokens"] = int(body["n_predict"])
            out = model.create_completion(prompt=str(body.get("prompt", "")), **gen)
            usage = out.get("usage", {})
            return {"content": out["choices"][0]["text"], "model": model_id, "stop": True,
                    "tokens_evaluated": usage.get("prompt_tokens", 0),
                    "tokens_predicted": usage.get("completion_tokens", 0)}
        if op == "tokenize":
            text = base64.b64decode(body["content_b64"]) if "content_b64" in body \
                else str(body.get("content", "")).encode("utf-8")
            return {"tokens": model.tokenize(text, add_bos=bool(body.get("add_bos", True)),
                                             special=bool(body.get("special", False))),
                    "n_ctx": entry["n_ctx"]}
        if op == "detokenize":
            raw = model.detokenize([int(t) for t in body.get("tokens", [])])
            return {"content_b64": base64.b64encode(raw).decode("ascii"),
                    "content": raw.decode("utf-8", "ignore")}
        raise ModelError(404, f"unknown operation {op!r}")


_ROUTES = {
    ("GET", "/health"): "health",
    ("GET", "/v1/models"): "models",
    ("POST", "/v1/completions"): "completions",
    ("POST", "/v1/chat/completions"): "chat",
    ("POST", "/completion"): "completion",
    ("POST", "/tokenize"): "tokenize",
    ("POST", "/detokenize"): "detokenize",
}


class _ModelHandler(BaseHTTPRequestHandler):
    service: ModelService
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):
        log.debug("models %s", fmt % args)

    def _reply(self, status: int, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _dispatch(self) -> None:
        op = _ROUTES.get((self.command, self.path.split("?", 1)[0]))
        if op is None:
            return self._reply(404, {"error": "not_found"})
        body: dict = {}
        if self.command == "POST":
            length = int(self.headers.get("Content-Length") or 0)
            try:
                body = json.loads(self.rfile.read(length) or b"{}")
            except ValueError:
                return self._reply(400, {"error": "bad_json"})
            if not isinstance(body, dict):
                return self._reply(400, {"error": "bad_json"})
        try:
            self._reply(200, self.service.call(op, body))
        except ModelError as exc:
            self._reply(exc.status, {"error": str(exc)})
        except Exception as exc:  # a model crash must reach the caller as an error
            log.exception("model request failed")
            self._reply(500, {"error": f"{type(exc).__name__}: {exc}"})

    do_GET = do_POST = _dispatch


# -- the provider: compete for /models, stand by when someone else has it ------------

class ModelProvider:
    """This app's model service, registered with the hub as ``/models`` if it can get it."""

    def __init__(self, app: str, *, home: Path | None = None, loader=_default_loader,
                 idle_unload: float = IDLE_UNLOAD, hub_port_: int | None = None):
        self.service = ModelService(home=home, loader=loader, idle_unload=idle_unload)
        self._app = app
        self._home = home
        self._hub_port = hub_port_
        self.member: HubMember | None = None

    def start(self) -> str:
        port = self.service.start()
        # Loopback only: a model is a resource of this PC, never of the network.
        self.member = HubMember(f"models:{self._app}", PREFIX, port, exposure=[],
                                home=self._home, hub_port_=self._hub_port)
        return self.member.start()

    @property
    def serving(self) -> bool:
        return self.member is not None and self.member.role not in (CONFLICT,)

    def stop(self) -> None:
        if self.member:
            self.member.stop()
        self.service.stop()


_provider: ModelProvider | None = None
_provider_lock = threading.Lock()


def ensure_provider(app: str, *, home: Path | None = None, **kw) -> ModelProvider | None:
    """Start this process's provider once, if it can run models at all."""
    global _provider
    kw = {k: v for k, v in kw.items() if v is not None}
    with _provider_lock:
        if _provider is None and (kw.get("loader") or llama_available()):
            _provider = ModelProvider(app, home=home, **kw)
            _provider.start()
        return _provider


def stop_provider() -> None:
    global _provider
    with _provider_lock:
        if _provider is not None:
            _provider.stop()
            _provider = None


# -- the client ----------------------------------------------------------------------

class SharedLlama:
    """Stands in for ``llama_cpp.Llama`` for the calls Densanon apps make.

    Requests go through the hub to whichever app holds ``/models``. If none
    does (no hub, or the port is held by another program), this app's own
    provider serves them in-process, and failing that the model is loaded
    here, exactly as the app did before.
    """

    def __init__(self, model_id: str, model_path: str | os.PathLike, *, n_ctx: int = 4096,
                 n_gpu_layers: int = 0, app: str = "app", provide: bool = True,
                 home: Path | None = None, hub_port_: int | None = None, timeout: float = 900.0,
                 loader=None):
        self.model_id = model_id
        self._n_ctx = n_ctx
        self._home = home
        self._hub_port = hub_port_ or hub_port()
        self._timeout = timeout
        self._local = None
        self._loader = loader or _default_loader
        self._entry = {"path": str(Path(model_path).resolve()), "n_ctx": n_ctx,
                       "n_gpu_layers": n_gpu_layers}
        register_model(model_id, model_path, n_ctx=n_ctx, n_gpu_layers=n_gpu_layers, home=home)
        self._provider = ensure_provider(app, home=home, loader=loader) if provide else None

    # -- llama_cpp.Llama surface --------------------------------------------

    def create_completion(self, prompt: str = "", **kw) -> dict:
        return self._call("completions", "/v1/completions", {"prompt": prompt, **kw})

    def create_chat_completion(self, messages=None, **kw) -> dict:
        return self._call("chat", "/v1/chat/completions", {"messages": messages or [], **kw})

    def tokenize(self, text: bytes, add_bos: bool = True, special: bool = False) -> list[int]:
        out = self._call("tokenize", "/tokenize", {
            "content_b64": base64.b64encode(text).decode("ascii"),
            "add_bos": add_bos, "special": special})
        return out["tokens"]

    def detokenize(self, tokens) -> bytes:
        out = self._call("detokenize", "/detokenize", {"tokens": list(tokens)})
        return base64.b64decode(out["content_b64"])

    def n_ctx(self) -> int:
        return int(read_catalog(self._home).get(self.model_id, {}).get("n_ctx") or self._n_ctx)

    # -- routing --------------------------------------------------------------

    def _call(self, op: str, path: str, body: dict) -> dict:
        body = {**body, "model": self.model_id}
        if "grammar" in body and not isinstance(body["grammar"], (str, type(None))):
            raise TypeError("pass the grammar as GBNF text, not a LlamaGrammar object")
        reply = self._via_hub(path, body)
        if reply is not None:
            return reply
        if self._provider is not None:
            return self._provider.service.call(op, body)
        return self._call_local(op, body)

    def _via_hub(self, path: str, body: dict) -> dict | None:
        """The reply, or None when no model service is reachable through the hub."""
        data = json.dumps(body).encode()
        conn = http.client.HTTPConnection("127.0.0.1", self._hub_port, timeout=self._timeout)
        try:
            conn.request("POST", PREFIX + path, body=data,
                         headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            raw = resp.read()
        except OSError:
            return None
        finally:
            conn.close()
        if resp.status == 502 or (resp.status == 404 and b'"not_found"' in raw):
            return None  # no provider holds /models right now, or it just left
        try:
            out = json.loads(raw)
        except ValueError:
            return None
        if resp.status != 200:
            raise RuntimeError(f"model service: {out.get('error', resp.status)}")
        return out

    def _call_local(self, op: str, body: dict) -> dict:
        if self._local is None:
            self._local = ModelService(home=self._home, loader=self._loader, idle_unload=10**9)
        return self._local.call(op, body)
