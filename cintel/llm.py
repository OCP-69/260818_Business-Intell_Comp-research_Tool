"""
LLM-Adapter - laeuft ueber die lokal installierten CLIs, NICHT ueber
kostenpflichtige API-Keys.

Primaer:  `claude -p` (Claude Code Max-Abo, OAuth/Keychain)
Optional: `codex exec` (ChatGPT Pro) als unabhaengige Zweitmeinung

Verifizierte Eigenschaften der Claude-CLI (v2.1.219):
  --output-format json   liefert ein Result-Objekt mit Feld `structured_output`
  --json-schema <schema> erzwingt Schema-Konformitaet der Antwort
  --tools "WebSearch,WebFetch"  legt fest, WELCHE Werkzeuge existieren
  --allowedTools "..."          gibt sie zusaetzlich FREI - zwingend noetig
  --permission-mode dontAsk     fragt nicht nach und verweigert im Zweifel
  --system-prompt        ERSETZT den Default-Prompt (spart ~11k Cache-Token/Call)

Drei Fallen, alle im Betrieb aufgetreten:

1. --bare darf NICHT gesetzt werden. Es deaktiviert OAuth und Keychain und
   erzwingt einen ANTHROPIC_API_KEY - genau das, was wir vermeiden.

2. --tools allein genuegt nicht. "dontAsk" heisst nicht "erlaube
   stillschweigend", sondern "frage nicht und VERWEIGERE". Ohne
   --allowedTools meldet die CLI intern "Permission to use WebSearch has
   been denied because Claude Code is running in don't ask mode" - und das
   Modell liefert daraufhin eine leere, aber schema-gueltige Antwort. Der
   Fehler sieht dann aus wie "nichts gefunden" statt wie ein Fehler.

3. CLIs immer ueber shutil.which aufloesen. Unter Windows legt npm Wrapper
   als .CMD an, die CreateProcess nicht direkt starten kann - der blosse
   Name scheitert mit "[WinError 2]", obwohl das Programm installiert ist.

Hinweis zur Messung: `usage.server_tool_use.web_search_requests` bleibt auch
bei erfolgreicher Suche 0, weil Claude Code die Websuche clientseitig
ausfuehrt. Der Zaehler taugt nicht als Beleg dafuer, ob gesucht wurde.
"""

from __future__ import annotations

import json
import logging
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

RESEARCH_SYSTEM_PROMPT = (
    "You are a precise B2B competitive-intelligence extractor for industrial "
    "software markets. You never invent companies, products, URLs or figures. "
    "If a fact is not supported by the provided material or by a source you "
    "retrieved, you leave the field empty and lower the confidence. "
    "You answer only with the requested structured data."
)


class LLMError(RuntimeError):
    """Der LLM-Aufruf ist fehlgeschlagen oder lieferte kein gueltiges Ergebnis."""


@dataclass
class LLMUsage:
    """Verbrauchsdaten eines Aufrufs - fuer das Run-Ledger."""

    backend: str = "claude"
    model: str = ""
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_creation_tokens: int = 0
    web_search_requests: int = 0
    cost_usd: float = 0.0
    duration_ms: int = 0
    num_turns: int = 0


@dataclass
class LLMResult:
    data: dict[str, Any]
    usage: LLMUsage = field(default_factory=LLMUsage)
    raw_text: str = ""


# --------------------------------------------------------------------------
# Claude Code CLI
# --------------------------------------------------------------------------

def _resolve(program: str) -> str | None:
    """
    Vollstaendigen Pfad einer CLI ermitteln.

    Der blosse Name genuegt unter Windows NICHT: npm legt Wrapper als
    .CMD an, und CreateProcess kann .CMD nicht direkt starten - nur .EXE.
    `codex` scheiterte deshalb mit "[WinError 2] Das System kann die
    angegebene Datei nicht finden", obwohl es installiert war.
    shutil.which loest ueber PATHEXT korrekt auf codex.CMD auf.
    """
    return shutil.which(program)


def claude_available() -> bool:
    return _resolve("claude") is not None


def call_claude(
    prompt: str,
    schema: dict[str, Any],
    *,
    model: str = "sonnet",
    tools: str = "WebSearch,WebFetch",
    system_prompt: str = RESEARCH_SYSTEM_PROMPT,
    timeout: int = 600,
    max_retries: int = 2,
    cwd: str | Path | None = None,
) -> LLMResult:
    """
    Ruft `claude -p` mit erzwungenem JSON-Schema auf.

    Args:
        prompt:  Die Nutzeranweisung.
        schema:  JSON-Schema, das die Antwort erfuellen muss.
        tools:   Komma-Liste erlaubter Built-in-Tools. "" = keine Tools
                 (rein wissensbasiert, deutlich schneller und guenstiger).

    Returns:
        LLMResult mit `data` = validiertes structured_output.

    Raises:
        LLMError: nach `max_retries` erfolglosen Versuchen.
    """
    if not claude_available():
        raise LLMError(
            "Die 'claude'-CLI wurde nicht gefunden. Sie ist die Voraussetzung "
            "fuer den Betrieb ohne API-Key. Installation: "
            "https://docs.claude.com/en/docs/claude-code"
        )

    schema_json = json.dumps(schema, ensure_ascii=False)
    last_error: Exception | None = None

    for attempt in range(1, max_retries + 2):
        # Der Prompt geht ueber stdin, NICHT als Argument: Windows begrenzt
        # die Kommandozeile auf 32767 Zeichen (CreateProcess). Ein
        # Extraktions-Prompt mit Seitentext liegt weit darueber und schlug
        # mit "[WinError 206] Der Dateiname oder die Erweiterung ist zu lang"
        # fehl.
        cmd = [
            _resolve("claude") or "claude", "-p",
            "--output-format", "json",
            "--json-schema", schema_json,
            "--system-prompt", system_prompt,
            "--model", model,
            "--permission-mode", "dontAsk",
            "--no-session-persistence",
        ]
        # --tools "" schaltet alle Tools ab; das Flag muss trotzdem gesetzt sein.
        cmd += ["--tools", tools]

        # Werden Tools gebraucht, muessen sie ZUSAETZLICH freigegeben werden.
        #
        # --permission-mode dontAsk bedeutet nicht "erlaube stillschweigend",
        # sondern "frage nicht und VERWEIGERE". Ohne --allowedTools meldet die
        # CLI intern:
        #   "Permission to use WebSearch has been denied because Claude Code
        #    is running in don't ask mode"
        # Das Modell liefert dann eine leere, aber schema-gueltige Antwort -
        # der Fehler faellt also nicht als Fehler auf, sondern als "nichts
        # gefunden". Genau daran scheiterte der gesamte new-Modus.
        if tools:
            cmd += ["--allowedTools", tools]

        try:
            proc = subprocess.run(
                cmd,
                input=prompt,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
                cwd=str(cwd) if cwd else None,
            )
        except subprocess.TimeoutExpired:
            last_error = LLMError(f"claude-CLI Timeout nach {timeout}s")
            log.warning("Versuch %d: Timeout", attempt)
            time.sleep(2 * attempt)
            continue

        if proc.returncode != 0 and not proc.stdout.strip():
            last_error = LLMError(
                f"claude-CLI Exit {proc.returncode}: {proc.stderr.strip()[:400]}"
            )
            log.warning("Versuch %d: Exit %d", attempt, proc.returncode)
            time.sleep(2 * attempt)
            continue

        try:
            envelope = json.loads(proc.stdout)
        except json.JSONDecodeError:
            last_error = LLMError(
                f"claude-CLI lieferte kein JSON: {proc.stdout[:400]!r}"
            )
            time.sleep(2 * attempt)
            continue

        if envelope.get("is_error"):
            message = str(envelope.get("result", ""))[:400]
            # "Not logged in" ist nicht durch Wiederholen loesbar.
            if "not logged in" in message.lower() or "/login" in message:
                raise LLMError(
                    "Die claude-CLI ist nicht angemeldet. Bitte einmalig "
                    "`claude` starten und `/login` ausfuehren. "
                    "(Hinweis: --bare deaktiviert OAuth - nicht verwenden.)"
                )
            last_error = LLMError(f"claude-CLI Fehler: {message}")
            time.sleep(2 * attempt)
            continue

        data = envelope.get("structured_output")
        if data is None:
            # Fallback: manche Antworten liefern das JSON nur im Text.
            raw = str(envelope.get("result", "")).strip()
            data = _salvage_json(raw)
        if data is None:
            last_error = LLMError("Antwort enthielt kein structured_output.")
            time.sleep(2 * attempt)
            continue

        return LLMResult(
            data=data,
            usage=_usage_from_envelope(envelope, model),
            raw_text=str(envelope.get("result", "")),
        )

    raise LLMError(f"claude-CLI nach {max_retries + 1} Versuchen erfolglos: {last_error}")


def _usage_from_envelope(envelope: dict[str, Any], model: str) -> LLMUsage:
    usage = envelope.get("usage", {}) or {}
    server = usage.get("server_tool_use", {}) or {}
    return LLMUsage(
        backend="claude",
        model=model,
        input_tokens=int(usage.get("input_tokens", 0) or 0),
        output_tokens=int(usage.get("output_tokens", 0) or 0),
        cache_read_tokens=int(usage.get("cache_read_input_tokens", 0) or 0),
        cache_creation_tokens=int(usage.get("cache_creation_input_tokens", 0) or 0),
        web_search_requests=int(server.get("web_search_requests", 0) or 0),
        cost_usd=float(envelope.get("total_cost_usd", 0.0) or 0.0),
        duration_ms=int(envelope.get("duration_ms", 0) or 0),
        num_turns=int(envelope.get("num_turns", 0) or 0),
    )


def _salvage_json(text: str) -> dict[str, Any] | None:
    """Zieht ein JSON-Objekt aus einem Text mit moeglichem Markdown-Rahmen."""
    if not text:
        return None
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = stripped.split("```")[1]
        stripped = stripped.removeprefix("json")
    start, end = stripped.find("{"), stripped.rfind("}")
    if start == -1 or end <= start:
        return None
    try:
        parsed = json.loads(stripped[start:end + 1])
        return parsed if isinstance(parsed, dict) else None
    except json.JSONDecodeError:
        return None


# --------------------------------------------------------------------------
# Codex CLI (optionale Zweitmeinung)
# --------------------------------------------------------------------------

def codex_available() -> bool:
    return _resolve("codex") is not None


def call_codex(
    prompt: str,
    schema: dict[str, Any],
    *,
    model: str | None = None,
    timeout: int = 600,
) -> LLMResult:
    """
    Unabhaengige Zweitextraktion ueber `codex exec`.

    Verwendet --output-schema (JSON-Schema-Datei) und -o (letzte Nachricht in
    eine Datei). Sandbox bleibt read-only, das Repo wird nicht angefasst.

    Raises:
        LLMError: wenn codex fehlt, scheitert oder kein JSON liefert.
    """
    if not codex_available():
        raise LLMError("Die 'codex'-CLI wurde nicht gefunden (ChatGPT Pro).")

    with tempfile.TemporaryDirectory(prefix="cintel_codex_") as tmp:
        schema_path = Path(tmp) / "schema.json"
        out_path = Path(tmp) / "out.json"
        schema_path.write_text(json.dumps(schema, ensure_ascii=False), encoding="utf-8")

        # "-" laesst codex die Anweisung von stdin lesen - gleiche
        # Kommandozeilen-Begrenzung wie bei claude.
        cmd = [
            _resolve("codex") or "codex", "exec", "-",
            "--output-schema", str(schema_path),
            "-o", str(out_path),
            "--sandbox", "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--color", "never",
        ]
        if model:
            cmd += ["--model", model]

        try:
            proc = subprocess.run(
                cmd, input=prompt, capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=timeout, cwd=tmp,
            )
        except subprocess.TimeoutExpired as exc:
            raise LLMError(f"codex-CLI Timeout nach {timeout}s") from exc
        except OSError as exc:
            # z.B. WinError 2, wenn der Wrapper nicht startbar ist. Der
            # Cross-Check ist eine Zusatzpruefung - er darf den Lauf nicht
            # abbrechen.
            raise LLMError(f"codex-CLI nicht ausfuehrbar: {exc}") from exc

        if not out_path.exists():
            combined = f"{proc.stderr} {proc.stdout}".lower()
            # Fehlende Anmeldung ist der haeufigste Fall und durch
            # Wiederholen nicht zu beheben - dann lieber klar ansagen, was
            # zu tun ist, statt den rohen API-Fehler auszugeben.
            if any(marker in combined for marker in
                   ("invalid_refresh_token", "unauthorized", "not logged in",
                    "please login", "401")):
                raise LLMError(
                    "codex ist nicht angemeldet (ChatGPT Pro). Einmalig "
                    "`codex login` ausfuehren. Der Cross-Check ist optional - "
                    "der Lauf funktioniert auch ohne ihn."
                )
            detail = " ".join(proc.stderr.split())[:200]
            raise LLMError(
                f"codex lieferte keine Ausgabedatei (Exit {proc.returncode}): "
                f"{detail}"
            )
        raw = out_path.read_text(encoding="utf-8", errors="replace")

    data = _salvage_json(raw)
    if data is None:
        raise LLMError(f"codex-Ausgabe war kein JSON: {raw[:300]!r}")

    return LLMResult(data=data, usage=LLMUsage(backend="codex", model=model or "default"),
                     raw_text=raw)


# --------------------------------------------------------------------------
# Ledger
# --------------------------------------------------------------------------

class UsageLedger:
    """Sammelt den Verbrauch eines Laufs fuer den Abschlussreport."""

    def __init__(self) -> None:
        self.entries: list[tuple[str, LLMUsage]] = []

    def add(self, label: str, usage: LLMUsage) -> None:
        self.entries.append((label, usage))

    @property
    def total_cost_usd(self) -> float:
        return sum(u.cost_usd for _, u in self.entries)

    @property
    def total_output_tokens(self) -> int:
        return sum(u.output_tokens for _, u in self.entries)

    def summary(self) -> str:
        if not self.entries:
            return "Keine LLM-Aufrufe."
        by_backend: dict[str, int] = {}
        for _, usage in self.entries:
            by_backend[usage.backend] = by_backend.get(usage.backend, 0) + 1
        backends = ", ".join(f"{k}: {v}" for k, v in sorted(by_backend.items()))
        return (
            f"{len(self.entries)} LLM-Aufrufe ({backends}), "
            f"{self.total_output_tokens} Output-Token, "
            f"rechnerisch ${self.total_cost_usd:.2f} "
            f"(im Max-Abo durch das Kontingent gedeckt)"
        )
