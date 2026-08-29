"""Every button in the desktop app reaches something that exists.

The audit found three endpoints with no caller and one caller naming an
endpoint that was never there:

* `rulings_download_progress` was polled as `rulings_progress`, because the
  helper derived the method name from an element-id prefix and those are two
  namespaces that need not agree. The bar read "Progress poll failed" for the
  whole download, which ran fine underneath.
* `delete_playgroup`, `rulings_check_update` and `get_price_history` had no
  caller anywhere — API surface built for screens nobody wrote.

Both shapes are cheap to check mechanically and expensive to notice by hand,
so they are checked here on every run rather than by reading.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from densa_deck.app.api import AppApi

STATIC = Path(__file__).resolve().parent.parent / "src" / "densa_deck" / "app" / "static"


def _js() -> dict[str, str]:
    return {f.name: f.read_text(encoding="utf-8") for f in STATIC.rglob("*.js")}


def _html() -> str:
    return "".join(f.read_text(encoding="utf-8") for f in STATIC.rglob("*.html"))


def _split_args(text: str) -> list[str]:
    """Top-level comma split that survives nesting, strings and comments."""
    out, depth, cur, quote, esc, comment = [], 0, "", None, False, False
    for ch in text:
        if comment:
            if ch == "\n":
                comment = False
            continue
        if not quote and ch == "/" and cur.endswith("/"):
            cur, comment = cur[:-1], True
            continue
        if quote:
            cur += ch
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == quote:
                quote = None
            continue
        if ch in "\"'`":
            quote, cur = ch, cur + ch
            continue
        if ch in "([{":
            depth += 1
        elif ch in ")]}":
            depth -= 1
        if ch == "," and depth == 0:
            out.append(cur)
            cur = ""
        else:
            cur += ch
    if cur.strip():
        out.append(cur)
    return out


class TestEveryCallReachesAMethod:
    def test_every_callApi_names_a_real_endpoint(self):
        methods = {m for m in dir(AppApi) if not m.startswith("__")}
        missing = []
        for name, text in _js().items():
            for m in re.finditer(r'callApi\(\s*["\'](\w+)["\']', text):
                if m.group(1) not in methods:
                    line = text[:m.start()].count("\n") + 1
                    missing.append(f"{name}:{line} {m.group(1)}")
        assert not missing, missing

    def test_every_call_passes_a_workable_number_of_arguments(self):
        """A pywebview bridge takes positional arguments with no type check
        at all: too few or too many raises at runtime, and only when somebody
        clicks that button.

        Signatures come from the SOURCE rather than from introspection. Every
        endpoint is wrapped by `@_safe`, so `inspect.signature` reports the
        wrapper's own parameters and reads every call as wrong.
        """
        import ast

        source = (Path(__file__).resolve().parent.parent / "src" / "densa_deck"
                  / "app" / "api.py").read_text(encoding="utf-8")
        sigs: dict[str, tuple[int, int, bool]] = {}
        for node in ast.walk(ast.parse(source)):
            if not (isinstance(node, ast.ClassDef) and node.name == "AppApi"):
                continue
            for fn in node.body:
                if isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    args = [a.arg for a in fn.args.args if a.arg != "self"]
                    sigs[fn.name] = (len(args) - len(fn.args.defaults),
                                     len(args), bool(fn.args.vararg))

        problems = []
        for name, text in _js().items():
            for m in re.finditer(
                    r'callApi\(\s*["\'](\w+)["\']((?:[^()]|\([^()]*\))*)\)', text):
                if m.group(1) not in sigs:
                    continue
                required, total, vararg = sigs[m.group(1)]
                given = len([a for a in _split_args(m.group(2)) if a.strip()])
                if given < required or (given > total and not vararg):
                    line = text[:m.start()].count("\n") + 1
                    problems.append(
                        f"{name}:{line} {m.group(1)}({given}) needs "
                        f"{required}..{total}")
        assert not problems, problems


class TestEveryProgressPollHasSomethingToPoll:
    """The one that actually broke. `pollProgress(op)` guesses the endpoint
    from `op`, and the rulings download draws into `rulings-progress-*` while
    being polled by `rulings_download_progress`."""

    def test_each_poll_names_a_method_that_exists(self):
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        missing = []
        for m in re.finditer(
                r'pollProgress\(\s*["\'](\w+)["\']((?:[^()]|\([^()]*\))*)\)', app):
            op, rest = m.group(1), m.group(2)
            explicit = re.findall(r'["\'](\w+_progress)["\']', rest)
            method = explicit[-1] if explicit else f"{op}_progress"
            if not hasattr(AppApi, method):
                missing.append(f"pollProgress({op!r}) -> {method}")
        assert not missing, missing

    def test_each_poll_has_the_elements_it_draws_into(self):
        app = (STATIC / "app.js").read_text(encoding="utf-8")
        html = _html()
        missing = []
        for m in re.finditer(r'pollProgress\(\s*["\'](\w+)["\']', app):
            op = m.group(1).replace("_", "-")
            for part in ("fill", "msg"):
                if f'id="{op}-progress-{part}"' not in html:
                    missing.append(f"{op}-progress-{part}")
        assert not missing, missing


class TestTheOrphanedEndpointsNowHaveCallers:
    """Each of these was API surface with no screen. They are named here so
    that unwiring one is a failing test rather than a quiet regression."""

    @pytest.mark.parametrize("endpoint", [
        "delete_playgroup",
        "create_playgroup",
        "set_default_playgroup",
        "add_pod_member",
        "remove_pod_member",
        "list_playgroups",
        "rulings_check_update",
        "get_price_history",
        "rulings_download_progress",
    ])
    def test_something_calls_it(self, endpoint):
        called = any(f'"{endpoint}"' in text or f"'{endpoint}'" in text
                     for text in _js().values())
        assert called, f"{endpoint} is reachable from no screen"

    def test_and_the_method_is_actually_there(self):
        for endpoint in ("delete_playgroup", "rulings_check_update",
                         "get_price_history"):
            assert hasattr(AppApi, endpoint), endpoint
