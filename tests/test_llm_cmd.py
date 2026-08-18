"""
Aufbau der CLI-Aufrufe.

Ohne Netz und ohne echten LLM-Aufruf: subprocess.run wird ersetzt und der
zusammengebaute Befehl geprueft. Hier stecken zwei Fehler, die im Betrieb
teuer waren und nicht als Fehler auffielen.
"""

from __future__ import annotations

import json

import pytest

from cintel import llm


class FakeProc:
    def __init__(self, stdout: str, returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


@pytest.fixture
def captured(monkeypatch):
    """Faengt den Aufruf ab und liefert (cmd, kwargs) des letzten Laufs."""
    calls: list[tuple[list[str], dict]] = []

    def fake_run(cmd, **kwargs):
        calls.append((cmd, kwargs))
        envelope = {
            "is_error": False,
            "structured_output": {"ok": True},
            "result": '{"ok": true}',
            "usage": {"input_tokens": 1, "output_tokens": 2,
                      "server_tool_use": {"web_search_requests": 0}},
            "total_cost_usd": 0.0, "duration_ms": 5, "num_turns": 1,
        }
        return FakeProc(json.dumps(envelope))

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    monkeypatch.setattr(llm.shutil, "which", lambda name: f"/usr/bin/{name}")
    return calls


SCHEMA = {"type": "object", "properties": {"ok": {"type": "boolean"}}}


def test_tools_are_also_allowlisted(captured):
    """
    Regression: --permission-mode dontAsk heisst "frage nicht und
    VERWEIGERE". Ohne --allowedTools meldete die CLI intern
    "Permission to use WebSearch has been denied" und lieferte eine leere,
    aber schema-gueltige Antwort. Der Fehler sah damit aus wie
    "nichts gefunden" - der gesamte new-Modus lief monatelang ins Leere.
    """
    llm.call_claude("hallo", SCHEMA, tools="WebSearch,WebFetch")
    cmd, _ = captured[-1]
    assert "--tools" in cmd
    assert "--allowedTools" in cmd
    assert cmd[cmd.index("--tools") + 1] == "WebSearch,WebFetch"
    assert cmd[cmd.index("--allowedTools") + 1] == "WebSearch,WebFetch"


def test_no_allowlist_when_no_tools_requested(captured):
    """Reine Extraktion braucht keine Tools - dann auch keine Freigabe."""
    llm.call_claude("hallo", SCHEMA, tools="")
    cmd, _ = captured[-1]
    assert "--allowedTools" not in cmd
    assert cmd[cmd.index("--tools") + 1] == ""


def test_bare_flag_is_never_used(captured):
    """
    --bare deaktiviert OAuth und Keychain und erzwingt einen bezahlten
    ANTHROPIC_API_KEY - genau das, was hier vermieden werden soll.
    """
    llm.call_claude("hallo", SCHEMA, tools="WebSearch")
    cmd, _ = captured[-1]
    assert "--bare" not in cmd


def test_prompt_goes_through_stdin_not_argv(captured):
    """
    Windows begrenzt die Kommandozeile auf 32767 Zeichen. Ein
    Extraktions-Prompt mit Seitentext liegt darueber und scheiterte mit
    "[WinError 206] Der Dateiname oder die Erweiterung ist zu lang".
    """
    long_prompt = "x" * 50_000
    llm.call_claude(long_prompt, SCHEMA, tools="")
    cmd, kwargs = captured[-1]
    assert kwargs.get("input") == long_prompt
    assert long_prompt not in cmd
    assert all(len(part) < 10_000 for part in cmd)


def test_system_prompt_is_passed_through(captured):
    llm.call_claude("hallo", SCHEMA, tools="", system_prompt="SEI KURZ")
    cmd, _ = captured[-1]
    assert cmd[cmd.index("--system-prompt") + 1] == "SEI KURZ"


def test_not_logged_in_fails_fast(monkeypatch):
    """Eine fehlende Anmeldung ist durch Wiederholen nicht zu beheben."""
    envelope = {"is_error": True, "result": "Not logged in · Please run /login"}
    attempts = []

    def fake_run(cmd, **kwargs):
        attempts.append(cmd)
        return FakeProc(json.dumps(envelope))

    monkeypatch.setattr(llm.subprocess, "run", fake_run)
    monkeypatch.setattr(llm.shutil, "which", lambda name: "/usr/bin/claude")

    with pytest.raises(llm.LLMError, match="nicht angemeldet"):
        llm.call_claude("hallo", SCHEMA, tools="", max_retries=3)
    assert len(attempts) == 1, "kein Wiederholen bei fehlender Anmeldung"


def test_missing_cli_gives_actionable_message(monkeypatch):
    monkeypatch.setattr(llm.shutil, "which", lambda name: None)
    with pytest.raises(llm.LLMError, match="claude"):
        llm.call_claude("hallo", SCHEMA, tools="")
