"""Pywebview entry point — creates the window and starts the event loop.

Run via `densa-deck app`, which calls `run()`. pywebview is an optional
dependency (declared in pyproject.toml's [desktop] extra); if it's missing
we print a clear install message instead of crashing.

The frontend ships as static files inside this package (`static/index.html`
+ siblings). At runtime we resolve the path relative to this module so the
PyInstaller bundle still finds the assets when frozen.
"""

from __future__ import annotations

import sys
from pathlib import Path

from densa_deck.app.api import AppApi

STATIC_DIR = Path(__file__).parent / "static"


def run(debug: bool = False):
    """Create the window and start the pywebview main loop.

    `debug=True` enables the browser devtools overlay — useful when iterating
    on the frontend but noisy for end users, so it's off by default.
    """
    try:
        import webview  # pywebview
    except ImportError:
        _print_install_hint()
        sys.exit(1)

    api = AppApi()
    entry = STATIC_DIR / "index.html"
    if not entry.exists():
        print(f"ERROR: Frontend assets not found at {entry}", file=sys.stderr)
        sys.exit(1)

    # One app at a time, per data directory.
    #
    # Two copies share cards.db, collection.db and versions.db, and SQLite in
    # WAL mode lets both write — so the second window shows a collection the
    # first has already changed, edits land in whichever process the click
    # reached, and the two disagree about what you own with nothing on screen
    # to explain it. Closing a window and opening another is exactly how
    # someone gets there.
    #
    # Taken AFTER the asset check so a broken install still fails with the
    # message about the actual problem.
    from densa_deck.app.single_instance import AlreadyRunning, InstanceLock

    lock = InstanceLock(Path(api._get_db().db_path).parent)
    try:
        lock.acquire()
    except AlreadyRunning as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        _report_already_running(exc)
        api.close()
        sys.exit(1)

    # Pull the version at import time so the window title is informative —
    # the installer shows "Densa Deck" but the running window adds
    # version so users who installed via different channels know what's up.
    try:
        from densa_deck import __version__ as version
        title = f"Densa Deck — v{version}"
    except ImportError:
        title = "Densa Deck"

    window = webview.create_window(
        title=title,
        url=str(entry),
        js_api=api,
        width=1200, height=800,
        min_size=(800, 600),
        # Keep the window chrome simple so cross-platform parity is easier —
        # no custom titlebar, no frameless mode. Ship something that Works
        # first, polish chrome second.
    )

    def _on_closing():
        api.close()
        # Released here as well as in the `finally` below: a window closed
        # normally should free the lock at the moment it closes, not whenever
        # the interpreter gets round to unwinding.
        lock.release()

    window.events.closing += _on_closing

    # Bring phone scanning back up if it was on when the app last closed.
    # Without this, the phone is paired but nothing is listening, so scanning
    # from a shop still needs a trip to the desktop — which is the situation
    # persistent pairing exists to remove. Failure here is never fatal: the
    # desktop app works perfectly well with no phone attached.
    try:
        api.start_phone_bridge_if_enabled()
    except Exception as exc:                                  # pragma: no cover
        print(f"Phone scanning could not start: {exc}", file=sys.stderr)

    # Window + taskbar icon.
    #
    # pywebview's `start(icon=...)` is documented "Supported only on GTK/QT",
    # so on Windows it does nothing at all. The winforms backend instead does:
    #
    #     icon_handle = windll.shell32.ExtractIconW(handle, sys.executable, 0)
    #
    # i.e. it takes the icon from whatever executable is hosting it. Frozen,
    # that's densa-deck.exe and we get the right icon for free. Run from
    # source it's python.exe — which is why a dev run wears the Python icon
    # no matter what we pass to start().
    #
    # So pass `icon` for GTK/QT where it works, and on Windows overwrite the
    # form's icon ourselves once the window exists.
    _set_windows_app_id()
    icon = _icon_path()

    def _after_start():
        _apply_windows_icon(icon)

    try:
        try:
            if icon:
                webview.start(_after_start, debug=debug, icon=str(icon))
            else:
                webview.start(_after_start, debug=debug)
        except TypeError:
            # Older pywebview builds don't accept `icon`. Losing it is not
            # worth failing to launch over.
            webview.start(_after_start, debug=debug)
    finally:
        # However the loop ended — closed, crashed, or killed from the task
        # manager while the interpreter still got to unwind — the next launch
        # must not find a lock nobody holds.
        lock.release()


def _apply_windows_icon(icon_path, *, timeout: float = 8.0) -> bool:
    """Force our icon onto the window via Win32, after the form exists.

    winforms sets `Form.Icon` from `sys.executable` while constructing the
    window, so anything we do beforehand is overwritten. WM_SETICON on the
    live HWND is the only thing that sticks, and it drives both the titlebar
    and the taskbar button.

    Polls for the window because `start()`'s callback can fire fractionally
    before the HWND is realised. Returns whether it succeeded — cosmetic, so
    every failure path is swallowed rather than raised.
    """
    if sys.platform != "win32" or not icon_path:
        return False
    try:
        import ctypes
        import time
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        WM_SETICON, ICON_SMALL, ICON_BIG = 0x0080, 0, 1
        IMAGE_ICON, LR_LOADFROMFILE, LR_DEFAULTSIZE = 1, 0x00000010, 0x00000040

        user32.FindWindowW.restype = wintypes.HWND
        deadline = time.time() + timeout
        hwnd = 0
        while time.time() < deadline:
            # The title carries the version, so match on the class pywebview
            # uses rather than an exact caption.
            hwnd = user32.FindWindowW("WindowsForms10.Window.8.app.0.141b42a_r6_ad1", None)
            if not hwnd:
                hwnd = _find_window_by_prefix("Densa Deck")
            if hwnd:
                break
            time.sleep(0.15)
        if not hwnd:
            return False

        applied = False
        for size_flag, wparam in ((16, ICON_SMALL), (32, ICON_BIG)):
            handle = user32.LoadImageW(None, str(icon_path), IMAGE_ICON,
                                       size_flag, size_flag,
                                       LR_LOADFROMFILE | LR_DEFAULTSIZE)
            if handle:
                user32.SendMessageW(hwnd, WM_SETICON, wparam, handle)
                applied = True
        return applied
    except Exception:
        return False


def _find_window_by_prefix(prefix: str) -> int:
    """First top-level window whose title starts with `prefix`, or 0."""
    try:
        import ctypes
        from ctypes import wintypes

        user32 = ctypes.windll.user32
        found = []

        @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        def _enum(hwnd, _lparam):
            length = user32.GetWindowTextLengthW(hwnd)
            if length:
                buf = ctypes.create_unicode_buffer(length + 1)
                user32.GetWindowTextW(hwnd, buf, length + 1)
                if buf.value.startswith(prefix):
                    found.append(hwnd)
                    return False
            return True

        user32.EnumWindows(_enum, 0)
        return found[0] if found else 0
    except Exception:
        return 0


def _icon_path():
    """Locate densa-deck.ico in a source checkout or a frozen bundle."""
    here = Path(__file__).resolve()
    candidates = [
        # Frozen: PyInstaller unpacks datas next to the executable.
        Path(getattr(sys, "_MEIPASS", "")) / "assets" / "densa-deck.ico",
        # Source checkout: src/densa_deck/app/main.py -> repo root/assets
        here.parents[3] / "assets" / "densa-deck.ico",
        here.parent / "static" / "densa-deck.ico",
    ]
    for candidate in candidates:
        try:
            if candidate and candidate.is_file():
                return candidate
        except (OSError, ValueError):
            continue
    return None


def _set_windows_app_id() -> None:
    """Give Windows an explicit AppUserModelID.

    Without one, a python.exe-hosted window inherits the interpreter's
    identity: the taskbar shows the Python icon and groups Densa Deck with
    every other Python process. Harmless no-op off Windows.
    """
    if sys.platform != "win32":
        return
    try:
        import ctypes
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
            "Densanon.DensaDeck")
    except Exception:
        pass  # cosmetic only — never block launch


def _report_already_running(exc) -> None:
    """Say so in a window, not only on a console nobody is watching.

    Launched from a shortcut there is no terminal, so a printed message is a
    silent failure — which is what "it just does not open" looks like from the
    outside. Best-effort: if a dialog cannot be raised, the console line above
    is still there.
    """
    message = (
        f"{exc}\n\n"
        "Two copies would share the same card and collection databases, "
        "which is how the two windows end up disagreeing about what you own."
    )
    # Only when there is nobody watching a console. A message box is modal —
    # it blocks until someone clicks it — which is right for a double-clicked
    # shortcut and wrong for a scripted or startup launch, where it would hang
    # forever with no one to dismiss it. A real terminal already got the line
    # printed above.
    #
    # `isatty` alone cannot tell those apart: it is False for a shortcut with
    # no console AND for `densa-deck app > log.txt`, which is a script, has a
    # console, and is the case that hangs. So on Windows the question is asked
    # of the process rather than of the stream — GetConsoleWindow returns null
    # only when there genuinely is no console attached, which is exactly the
    # double-clicked case and nothing else.
    has_console = False
    try:
        has_console = bool(sys.stderr and sys.stderr.isatty())
    except Exception:
        has_console = False
    if not has_console and sys.platform == "win32":
        try:
            import ctypes

            has_console = bool(ctypes.windll.kernel32.GetConsoleWindow())
        except Exception:
            has_console = False
    if has_console:
        return

    if sys.platform == "win32":
        try:
            import ctypes

            ctypes.windll.user32.MessageBoxW(
                None, message, "Densa Deck is already running", 0x40)
            return
        except Exception:
            pass
    try:
        import webview

        webview.create_window("Densa Deck is already running", html=(
            f"<body style='font:14px system-ui;padding:24px'>{message}</body>"))
        webview.start()
    except Exception:
        pass


def _print_install_hint():
    print("pywebview is not installed. The desktop app requires it.", file=sys.stderr)
    print("", file=sys.stderr)
    print("Install with:", file=sys.stderr)
    print("    pip install 'densa-deck[desktop]'", file=sys.stderr)
    print("", file=sys.stderr)
    print("or directly:", file=sys.stderr)
    print("    pip install pywebview", file=sys.stderr)


if __name__ == "__main__":
    run(debug="--debug" in sys.argv)
