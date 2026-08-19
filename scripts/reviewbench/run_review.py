"""
Reviewbench - misst, ob eine fremde Instanz bekannte Fehler findet.

    py scripts/reviewbench/run_review.py codex
    py scripts/reviewbench/run_review.py gemini
    py scripts/reviewbench/run_review.py codex --fall 01_websearch

Jeder Fall wird in zwei Varianten geprueft:
    A  nur Code + Zielvertrag          (findet er den Fehler von sich aus?)
    B  zusaetzlich das Symptom         (diagnostiziert er richtig?)

Ergebnisse landen als JSON in data/reviewbench/, damit sich Laeufe
vergleichen lassen.

Das Ergebnis ist eine MESSUNG, kein Urteil: ein Treffer wird ueber
Stichwortgruppen erkannt und muss von Hand nachgelesen werden. Die
Stichwortpruefung schuetzt nur davor, dass ein zufaellig passendes Wort
als Treffer durchgeht.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from scripts.reviewbench.cases import ALLE_FAELLE, Fall  # noqa: E402

AUSGABE = Path("data/reviewbench")

BEFUND_SCHEMA = {
    "type": "object", "additionalProperties": False,
    "required": ["befunde", "urteil"],
    "properties": {
        "urteil": {"type": "string", "enum": ["bestanden", "beanstandet"]},
        "befunde": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["schwere", "ausloeser", "folge", "begruendung"],
            "properties": {
                "schwere": {"type": "string",
                            "enum": ["blockierend", "wichtig", "hinweis"]},
                "ausloeser": {"type": "string"},
                "folge": {"type": "string"},
                "begruendung": {"type": "string"}}}}},
}

PROMPT = """Du pruefst fremden Code. Deine Aufgabe ist NICHT zu bestaetigen,
sondern zu widerlegen.

ZIELVERTRAG
{vertrag}

CODE (vollstaendig, nicht gekuerzt)
```python
{code}
```
{symptom_block}
PRUEFE IN DIESER REIHENFOLGE
1. Erfuellt der Code den Vertrag WIRKLICH, oder nur die Messung davon?
   Suche gezielt nach Ergebnissen, die gueltig aussehen, aber leer oder
   bedeutungslos sind.
2. Welche stillschweigende Annahme steckt darin? Wann ist sie falsch?
3. Welcher Fall aus der Praxis ist nicht abgedeckt? Nenne einen konkreten
   Eingabewert.
4. Wurde etwas uebersehen, das dazu fuehrt, dass ein Fehler NICHT als
   Fehler auffaellt?

REGELN
- Jeder Befund braucht einen konkreten Ausloeser: Funktion, Zeile, Eingabe.
- "Koennte problematisch sein" ist kein Befund.
- Findest du nichts, sage das klar. Erfinde nichts.

ANTWORTE AUSSCHLIESSLICH mit einem JSON-Objekt dieser Form, ohne
Markdown-Rahmen und ohne Vor- oder Nachtext:
{schema}
"""

SYMPTOM_BLOCK = """
ZUSAETZLICH BEOBACHTET
{symptom}
"""


# --------------------------------------------------------------- Backends

def rufe_codex(prompt: str, timeout: int = 300) -> tuple[dict | None, str]:
    """Codex erzwingt das Schema ueber --output-schema."""
    with tempfile.TemporaryDirectory(prefix="bench_codex_") as tmp:
        schema_pfad = Path(tmp) / "schema.json"
        aus_pfad = Path(tmp) / "out.json"
        schema_pfad.write_text(json.dumps(BEFUND_SCHEMA, ensure_ascii=False),
                               encoding="utf-8")
        cmd = [
            shutil.which("codex") or "codex", "exec", "-",
            "--output-schema", str(schema_pfad),
            "-o", str(aus_pfad),
            "--sandbox", "read-only",
            "--skip-git-repo-check", "--ephemeral", "--color", "never",
        ]
        try:
            proc = subprocess.run(cmd, input=prompt, capture_output=True,
                                  text=True, encoding="utf-8",
                                  errors="replace", timeout=timeout, cwd=tmp)
        except subprocess.TimeoutExpired:
            return None, f"Timeout nach {timeout}s"
        if not aus_pfad.exists():
            return None, " ".join(proc.stderr.split())[:200]
        return _bergen(aus_pfad.read_text(encoding="utf-8", errors="replace"))


def rufe_gemini(prompt: str, timeout: int = 300) -> tuple[dict | None, str]:
    """
    Gemini kennt KEIN --output-schema.

    Es gibt nur --output-format json als Huellformat; die Struktur der
    Antwort muss ueber den Prompt erbeten und anschliessend selbst
    geprueft werden. Genau diesen Unterschied soll der Bench messen.
    """
    cmd = [
        shutil.which("gemini") or "gemini",
        "-p", prompt,
        "-o", "json",
        "--approval-mode", "plan",   # nur lesen
        "--skip-trust",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              timeout=timeout)
    except subprocess.TimeoutExpired:
        return None, f"Timeout nach {timeout}s"

    try:
        huelle = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return _bergen(proc.stdout)

    if isinstance(huelle, dict) and huelle.get("error"):
        return None, str(huelle["error"].get("message", ""))[:200]

    inhalt = huelle.get("response") if isinstance(huelle, dict) else None
    return _bergen(inhalt if isinstance(inhalt, str) else json.dumps(huelle))


def _bergen(text: str) -> tuple[dict | None, str]:
    """Zieht ein JSON-Objekt aus einer moeglicherweise umrahmten Antwort."""
    if not text:
        return None, "leere Antwort"
    s = text.strip()
    if "```" in s:
        teile = s.split("```")
        for teil in teile:
            teil = teil.removeprefix("json").strip()
            if teil.startswith("{"):
                s = teil
                break
    a, b = s.find("{"), s.rfind("}")
    if a == -1 or b <= a:
        return None, f"kein JSON gefunden: {s[:120]!r}"
    try:
        daten = json.loads(s[a:b + 1])
    except json.JSONDecodeError as exc:
        return None, f"JSON ungueltig: {exc}"
    return (daten, "") if isinstance(daten, dict) else (None, "kein Objekt")


BACKENDS = {"codex": rufe_codex, "gemini": rufe_gemini}


# --------------------------------------------------------------- Bewertung

def ist_treffer(fall: Fall, befunde: list[dict]) -> tuple[bool, str]:
    """
    Nennt ein Befund die bekannte Ursache?

    Es muss aus JEDER Stichwortgruppe mindestens ein Wort vorkommen -
    sonst wuerde ein einzelnes Allerweltswort schon als Treffer zaehlen.
    """
    for befund in befunde:
        text = " ".join([
            str(befund.get("ausloeser", "")),
            str(befund.get("folge", "")),
            str(befund.get("begruendung", "")),
        ]).lower()
        if all(any(w in text for w in gruppe) for gruppe in fall.treffer_gruppen):
            return True, str(befund.get("schwere", "?"))
    return False, ""


def schema_treu(daten: dict) -> bool:
    """Haelt die Antwort die vorgegebene Struktur ein?"""
    if not isinstance(daten, dict):
        return False
    if daten.get("urteil") not in ("bestanden", "beanstandet"):
        return False
    befunde = daten.get("befunde")
    if not isinstance(befunde, list):
        return False
    pflicht = {"schwere", "ausloeser", "folge", "begruendung"}
    return all(isinstance(b, dict) and pflicht <= set(b) for b in befunde)


# ------------------------------------------------------------------- Lauf

def pruefe(backend: str, fall: Fall, mit_symptom: bool,
           timeout: int) -> dict:
    prompt = PROMPT.format(
        vertrag=fall.vertrag.strip(),
        code=fall.code.strip(),
        schema=json.dumps(BEFUND_SCHEMA, ensure_ascii=False, indent=1),
        symptom_block=(SYMPTOM_BLOCK.format(symptom=fall.symptom.strip())
                       if mit_symptom else ""),
    )
    t0 = time.time()
    daten, fehler = BACKENDS[backend](prompt, timeout=timeout)
    dauer = time.time() - t0

    if daten is None:
        return {"fall": fall.kennung, "variante": "B" if mit_symptom else "A",
                "backend": backend, "erfolg": False, "fehler": fehler,
                "dauer_s": round(dauer, 1)}

    befunde = daten.get("befunde", []) if isinstance(daten, dict) else []
    treffer, schwere = ist_treffer(fall, befunde)
    return {
        "fall": fall.kennung, "variante": "B" if mit_symptom else "A",
        "backend": backend, "erfolg": True, "dauer_s": round(dauer, 1),
        "schema_treu": schema_treu(daten),
        "anzahl_befunde": len(befunde),
        "treffer": treffer, "treffer_schwere": schwere,
        "urteil": daten.get("urteil"),
        "befunde": befunde,
    }


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Reviewbench")
    p.add_argument("backend", choices=sorted(BACKENDS))
    p.add_argument("--fall", default=None, help="nur diesen Fall")
    p.add_argument("--timeout", type=int, default=300)
    p.add_argument("--variante", choices=["A", "B", "AB"], default="AB")
    args = p.parse_args(argv)

    if shutil.which(args.backend) is None:
        print(f"'{args.backend}' ist nicht installiert.", file=sys.stderr)
        return 2

    faelle = [f for f in ALLE_FAELLE
              if args.fall is None or f.kennung == args.fall]
    if not faelle:
        print(f"Kein Fall mit der Kennung {args.fall!r}.", file=sys.stderr)
        return 2

    varianten = [False, True] if args.variante == "AB" else \
                [args.variante == "B"]

    print("=" * 74)
    print(f"  REVIEWBENCH - {args.backend}")
    print("=" * 74)

    ergebnisse = []
    for fall in faelle:
        print(f"\n{fall.kennung}  {fall.titel}")
        print(f"   Klasse: {fall.klasse}")
        for mit_symptom in varianten:
            kennung = "B (mit Symptom)" if mit_symptom else "A (ohne Symptom)"
            e = pruefe(args.backend, fall, mit_symptom, args.timeout)
            ergebnisse.append(e)
            if not e["erfolg"]:
                print(f"   {kennung:18s} FEHLGESCHLAGEN ({e['dauer_s']}s) "
                      f"{e['fehler'][:70]}")
                continue
            marke = "TREFFER" if e["treffer"] else "verfehlt"
            print(f"   {kennung:18s} {marke:9s} "
                  f"{e['anzahl_befunde']} Befunde, "
                  f"{e['dauer_s']}s, Schema {'ok' if e['schema_treu'] else 'ABWEICHUNG'}")
            if e["treffer"]:
                print(f"      -> als '{e['treffer_schwere']}' erkannt")

    gelungen = [e for e in ergebnisse if e["erfolg"]]
    treffer_a = [e for e in gelungen if e["variante"] == "A" and e["treffer"]]
    treffer_b = [e for e in gelungen if e["variante"] == "B" and e["treffer"]]
    anz_a = len([e for e in gelungen if e["variante"] == "A"])
    anz_b = len([e for e in gelungen if e["variante"] == "B"])

    print("\n" + "=" * 74)
    print(f"  Laeufe erfolgreich : {len(gelungen)}/{len(ergebnisse)}")
    print(f"  Treffer Variante A : {len(treffer_a)}/{anz_a}  (ohne Symptom)")
    print(f"  Treffer Variante B : {len(treffer_b)}/{anz_b}  (mit Symptom)")
    if gelungen:
        schema_ok = sum(1 for e in gelungen if e["schema_treu"])
        median = sorted(e["dauer_s"] for e in gelungen)[len(gelungen) // 2]
        print(f"  Schema eingehalten : {schema_ok}/{len(gelungen)}")
        print(f"  Antwortzeit Median : {median}s")
    print("=" * 74)

    AUSGABE.mkdir(parents=True, exist_ok=True)
    ziel = AUSGABE / f"{args.backend}_{time.strftime('%Y%m%d_%H%M%S')}.json"
    ziel.write_text(json.dumps(ergebnisse, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    print(f"\nEinzelbefunde: {ziel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
