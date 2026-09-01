# cintel — Competitive Intel Master DB (v3)

Werkzeugkasten für die Wettbewerbsanalyse von LoopForgeLab (LFL).
Seit v3 gilt eine klare Arbeitsteilung:

| Schicht | Was | Wo |
|---|---|---|
| **Recherche** | Claude-Code-Agent: Discovery, Quellenlektüre, Extraktion, Bewertung, Report | [prompts/wettbewerbsanalyse-agent_v1.1.md](prompts/wettbewerbsanalyse-agent_v1.1.md) |
| **Deterministischer Kern** | Schema-Vertrag, Taxonomie, Dedupe, fill-only-Merge, Validierung, versioniertes Schreiben | `cintel/` (dieses Paket) |
| **Bedienung** | Lokale Browser-UI: Dashboard, Firmenpflege, Suchaufträge | `app/app.py` |

Die Pipeline-Module der v2 (eigener Crawler, Discovery-Raster, Headless-CLI-
Anbindung, Codex-Cross-Check, Reparatur, Scheduling) sind entfernt — ihre
Aufgaben übernimmt der Agent bzw. sind erledigt. Die Historie liegt in
[docs/archive/](docs/archive/) und in der Git-History.

## Datenmodell

Eine Excel-Datei (`Competitive_Intel_Master_DB_vX.Y.xlsx`, Sheet
`Competitors_All-Master`, 31 Spalten) mit Blockmodell je Firma:

```
Company_ID 42 | "Company information" | Firmenfelder (Stage, Location, …)
Company_ID 42 | "Product"             | Produkt 1 (Sub Category, Remarks, …)
Company_ID 42 | "Product"             | Produkt 2
```

Firmenweite Felder stehen NUR auf der Firmenzeile. Jeder Schreibvorgang
erzeugt eine **neue Version** — die Eingangsdatei wird nie verändert, und
bestehende (kuratierte) Werte werden vom Agenten nie überschrieben
(fill-only). Die Daten selbst liegen **nicht** im Repo (public!), sondern
lokal unter `data/` (gitignored).

## Schnellstart

```
pip install -r requirements.txt
py -m pytest tests/ -q                # Selbsttest
py -m cintel stats --db data\outputs\Competitive_Intel_Master_DB_v2.4.xlsx
py app\app.py                         # UI auf http://127.0.0.1:8742
```

## Ablauf einer Recherche

1. **Suchauftrag anlegen** — in der UI (`/auftraege`) oder direkt als JSON in
   `data/jobs/`. Pflichtfeld: der Entscheidungsbezug.
2. **Agent ausführen** — in Claude Code im Repo-Ordner:
   *"Führe prompts/wettbewerbsanalyse-agent_v1.1.md mit dem Auftrag
   data/jobs/&lt;datei&gt;.json aus."* Der Agent recherchiert (Claude-nativ;
   Apify nur für LinkedIn-Felder und JS-Seiten) und erzeugt eine
   `records.json` nach [docs/RECORDS_FORMAT.md](docs/RECORDS_FORMAT.md).
3. **Ingest (deterministisch)** — der Agent ruft
   `py -m cintel ingest records.json --db <xlsx>` auf: strikte Validierung,
   Kanonisierung, Dedupe, fill-only-Merge, neue Version + Run-Artefakte
   (`report.md`, `new_rows.csv`, `sources.csv`).
4. **Ergebnis prüfen** — Report im Dashboard unter "Letzte Ingest-Läufe";
   Handpflege über die Firmen-Detailseiten (Änderungskorb → neue Version).

## CLI

```
py -m cintel ingest <records.json> --db <xlsx> [--dry-run] [--version 2.5]
py -m cintel validate --db <xlsx> [--strict]
py -m cintel stats --db <xlsx>
py -m cintel doctor
```

## Entwicklung

- Tests: `py -m pytest tests/ -q` (offline, ohne LLM)
- Lint: `ruff check cintel tests scripts app`
- CI: GitHub Actions (Lint, Taxonomie-Konsistenz, Tests auf 3.11–3.13)

## Sicherheit

- `data/` ist gitignored — Wettbewerbsdaten und Auftragsdateien bleiben lokal.
- API-Tokens (Apify) liegen außerhalb des Repos; `.env.example` dokumentiert
  die Konvention. Niemals Tokens committen — das Repo ist öffentlich.
