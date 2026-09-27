"""Double-clicking densa-deck.exe opens the app.

Reported by a tester: unzip the download, double-click densa-deck.exe, "a
console screen flashes then nothing". The bundled exe is the CLI; with no
command it printed its help into a console that closed immediately. Only
the installer's shortcuts, which pass `app`, ever opened a window.
"""

from __future__ import annotations

import sys

import pytest

from densa_deck import cli


def _run_main(monkeypatch, argv):
    monkeypatch.setattr(sys, "argv", ["densa-deck", *argv])
    cli.main()


def test_a_double_click_opens_the_app(monkeypatch):
    opened = []
    monkeypatch.setattr(cli, "_launched_by_double_click", lambda: True)
    monkeypatch.setattr(cli, "_hide_own_console", lambda: None)
    monkeypatch.setattr(cli, "cmd_app", lambda args: opened.append(args))

    _run_main(monkeypatch, [])

    assert len(opened) == 1
    assert opened[0].debug is False and opened[0].activation_url is None


def test_typed_with_no_command_still_prints_help(monkeypatch, capsys):
    monkeypatch.setattr(cli, "_launched_by_double_click", lambda: False)
    monkeypatch.setattr(cli, "cmd_app", lambda args: pytest.fail("opened the app from a shell"))

    _run_main(monkeypatch, [])

    assert "usage: densa-deck" in capsys.readouterr().out


def test_a_real_command_is_never_hijacked(monkeypatch):
    """Double-click detection only matters when there is no command at all."""
    monkeypatch.setattr(cli, "_launched_by_double_click", lambda: True)
    called = []
    monkeypatch.setattr(cli, "cmd_info", lambda args: called.append("info"), raising=False)
    monkeypatch.setattr(cli, "cmd_app", lambda args: pytest.fail("hijacked a real command"))
    try:
        _run_main(monkeypatch, ["info"])
    except SystemExit:
        pass


def test_python_dash_m_is_always_a_terminal(monkeypatch):
    # Not frozen: `python -m densa_deck` must keep printing help.
    monkeypatch.delattr(sys, "frozen", raising=False)
    assert cli._launched_by_double_click() is False


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console semantics")
def test_under_a_test_runner_it_is_not_a_double_click(monkeypatch):
    """Frozen or not, a process started from a shell shares its console."""
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    assert cli._launched_by_double_click() is False
