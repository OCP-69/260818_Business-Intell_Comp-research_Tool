# Competitive Intel Research Tool (`cintel`)

Automatisiertes Retrieval für die **Competitive Intel Master DB** von LoopForgeLab.
Das Werkzeug findet Unternehmen in den Zielsektoren, crawlt ihre Websites,
extrahiert Unternehmens- und Produktinformationen und schreibt sie im
Zeilenmodell der Masterdatei in eine neue, höher versionierte Datei.

**Ohne kostenpflichtige API-Keys.** Die LLM-Aufrufe laufen über die lokal
installierte Claude-Code-CLI und damit über das Max-Abo.

---

## Was das Werkzeug erzeugt

Pro Unternehmen entsteht genau das Zeilenmodell, das die Masterdatei bereits
verwendet — eine Unternehmenszeile, darunter je Produkt eine weitere Zeile:

| Company_ID | Company & Product | Company | Product_name | … |
|---|---|---|---|---|
| 42 | `Company information` | Autodesk | *(leer)* | Gründungsjahr, Stage, Ort, Umsatz … |
| 42 | `Product` | Autodesk | Revit | Sub Category, LCA, USP, Pricing … |
| 42 | `Product` | Autodesk | Fusion 360 | … |

Alle 31 Spalten und ihre Reihenfolge sind ein harter Vertrag
(`cintel/schema.py`). Enum-Felder werden gegen `config/taxonomy.yaml`
erzwungen — 8 Key Categories, 30 Sub Categories, 3 Tiers, 7 Beachhead-Werte.

---

## Die Pipeline

```
1 DISCOVER  Kandidaten finden
            gaps: Bestandsfirmen mit Lücken (keine Websuche, günstig)
            new:  Websuche je Key Category × Sub Category × Region

2 DEDUPE    Abgleich gegen den Bestand über registrierbare Domain und
            normalisierten Firmennamen, plus unscharfer Namensvergleich

3 CRAWL     Homepage → sitemap.xml + Navigation → Produkt-/Solution-Seiten
            robots.txt-konform, rate-limited, Plattencache
            >> gleichzeitig das Verifikations-Gate (siehe unten)

4 EXTRACT   Zwei strukturierte Pässe gegen das echte Schema:
            CompanyRecord + ProductRecord[], je Feld mit Confidence
            und Quell-URL

5 MERGE     Neue Firma  -> nächste freie Company_ID, Zeilen anhängen
            Bekannte    -> nur LEERE Felder füllen, nie überschreiben
            -> Competitive_Intel_Master_DB_v<n+1>.xlsx
```

Jede Stufe ist einzeln aufrufbar und resumierbar. Der Crawl-Cache ist
inhaltsadressiert, dadurch kostet ein erneuter Extraktionslauf keinen
weiteren Netzverkehr.

### Das Verifikations-Gate

Kein Unternehmen kommt in die Datenbank, ohne dass seine Homepage
erreichbar ist **und** der Inhalt den Firmennamen bestätigt. Das ist keine
theoretische Vorsichtsmaßnahme: im Aufbau lieferte die Websuche eine Firma
mit falscher Top-Level-Domain (`carbontrail.com` statt `.net`). Ohne Gate
wäre daraus eine tote Zeile geworden. Abgelehnte Kandidaten landen mit Grund
in `rejected.csv`.

---

## Voraussetzungen

| | |
|---|---|
| Python | 3.11+ (getestet mit 3.14 auf Windows) |
| `claude`-CLI | angemeldet über das Max-Abo — einmalig `claude` starten, `/login` |
| `codex`-CLI | optional, nur für `--cross-check codex` |

```bash
pip install -r requirements.txt
```

Selbstdiagnose:

```bash
py -m cintel doctor --master "H:\...\Competitive_Intel_Master_DB_v2.2.xlsx"
```

> **Wichtig:** Das CLI-Flag `--bare` darf nicht verwendet werden. Es
> deaktiviert OAuth und Keychain und erzwingt einen `ANTHROPIC_API_KEY` —
> genau das, was hier vermieden werden soll.

---

## Verwendung

### Lücken im Bestand schließen (empfohlener erster Lauf)

```bash
py -m cintel run --master "H:\...\Competitive_Intel_Master_DB_v2.2.xlsx" --limit 5 --version 2.3
```

Arbeitet gegen bekannte Firmen, deren Wahrheit du kennst — damit ist die
Extraktionsqualität sofort beurteilbar. Welche Spalten angegangen werden,
steht in `config/targets.yaml` unter `gaps.target_columns`.

### Neue Unternehmen entdecken

```bash
py -m cintel run --master "<pfad.xlsx>" --mode new --limit 10
```

Zielsektoren, Regionen und Reifegrade stehen in `config/targets.yaml`
unter `new`.

### Weitere Befehle

```bash
py -m cintel validate --master "<pfad.xlsx>"              # Datenqualität prüfen
py -m cintel repair   --master "<pfad.xlsx>" --dry-run    # Bestand bereinigen
py scripts/inspect_master_db.py "<pfad.xlsx>"             # Struktur & Füllgrade
```

Nützliche Schalter für `run`:

| Schalter | Wirkung |
|---|---|
| `--dry-run` | alles rechnen, keine xlsx schreiben |
| `--crawl-only` | nur crawlen und cachen, keine LLM-Aufrufe |
| `--offline` | ausschließlich den Cache nutzen |
| `--cross-check codex` | Hartdaten unabhängig zweitprüfen |
| `--limit N` | Obergrenze der Firmen pro Lauf |

### Ausgabe

```
data/outputs/
├─ Competitive_Intel_Master_DB_v2.3.xlsx   # neue Version, Original unberührt
└─ run_<zeitstempel>/
   ├─ report.md        # was gefüllt, was abgelehnt wurde
   ├─ new_rows.csv     # die angehängten Zeilen
   ├─ rejected.csv     # abgelehnte Kandidaten mit Grund
   ├─ sources.csv      # jede abgerufene Seite mit Status
   └─ plan.json
```

Die Eingangsdatei wird **nie** verändert. `data/` ist von Git ausgenommen —
Wettbewerbsdaten gehören nicht ins Repository.

---

## Bestandsdaten bereinigen

`cintel repair` behebt die Mangelklassen, die in v2.2 tatsächlich vorkommen —
getrennt vom Anreicherungslauf, mit Vorher/Nachher-Protokoll:

| Befund | Behandlung |
|---|---|
| kaputte Kodierung (`nicht Ã¶ffentlich`) | cp1252/UTF-8-Roundtrip |
| `Tier 2 - Nachbar` / `Tier 3 � Beobachten` | auf Halbgeviertstrich normalisiert |
| `Company_ID` als `42.0` | Ganzzahl |
| Seitentitel, Ort oder Jahr im URL-Feld | geleert, Inhalt wandert in die Bemerkungen |
| `https://x.io/ \| Berlin, Germany` | auf die URL gekürzt |
| `Company & Product = Platform` | auf `Product` gesetzt |
| Vokabular-Drift | auf den kanonischen Wert gemappt |

Ergebnis auf der echten v2.2: **1013 Änderungen, danach 0 Fehler**
(vorher 15 Fehler). Die verbleibenden 76 Warnungen sind Ermessensfragen —
doppelte Zeilen, Produkte ohne Namen, mehrfache Unternehmenszeilen — und
werden bewusst nicht automatisch geändert.

---

## Kuratierte Werte sind geschützt

Beim Anreichern bekannter Firmen werden **ausschließlich leere Felder**
gefüllt. `Competitor_Tier`, `Beachhead_Relevanz`, `Weaknesses` und die
übrigen Bewertungen sind Handarbeit und bleiben stehen, auch wenn die
Extraktion etwas anderes vorschlägt. Übersprungene Felder erscheinen mit
Begründung in `report.md`.

Zusätzlich greift eine Confidence-Schwelle: Werte unter 0.35 werden nicht
übernommen.

---

## Projektstruktur

```
cintel/
├─ schema.py     31-Spalten-Vertrag, Taxonomie, tolerante Header-Auflösung
├─ llm.py        claude-CLI- und codex-CLI-Adapter (kein API-Key)
├─ masterdb.py   Laden, Zeilenmodell, versioniertes Schreiben
├─ discover.py   Stufe 1
├─ dedupe.py     Stufe 2
├─ crawl.py      Stufe 3 inkl. Verifikations-Gate und Cache
├─ extract.py    Stufe 4
├─ merge.py      Stufe 5
├─ validate.py   Qualitätsprüfung
├─ repair.py     Bestandsbereinigung
└─ cli.py        Kommandozeile
config/
├─ taxonomy.yaml  kontrolliertes Vokabular (aus v2.2 generiert)
└─ targets.yaml   Zielsektoren und Limits
```

---

## Git-Workflow

Das Repository ist privat. Änderungen laufen über Branch und Pull Request,
`main` bleibt auslieferbar.

```bash
git checkout -b feat/meine-aenderung
```

Änderungen machen, dann prüfen — **bevor** committet wird:

```bash
py -m pytest tests/ -q
```

```bash
git add -A
git commit -m "feat: kurze Beschreibung im Imperativ"
```

```bash
git push -u origin feat/meine-aenderung
```

Pull Request eröffnen:

```bash
gh pr create --fill
```

Der CI-Lauf prüft Lint, Taxonomie-Konsistenz und die Testsuite auf
Python 3.11/3.12/3.13. Status ansehen:

```bash
gh pr checks
```

```bash
gh pr view --web
```

Nach erfolgreichem Review mergen und den Branch aufräumen:

```bash
gh pr merge --squash --delete-branch
```

```bash
git checkout main && git pull
```

### Commit-Konventionen

`feat:` neue Funktion · `fix:` Fehlerbehebung · `docs:` Dokumentation ·
`test:` Tests · `chore:` Aufräumarbeiten · `data:` Taxonomie-/Config-Änderung

### Was nicht ins Repository gehört

`.gitignore` schließt `data/`, alle `*.xlsx` und `.env` aus. Die Master-DB
und jeder Laufergebnis-Ordner bleiben lokal. Falls doch einmal eine xlsx
versehentlich eingecheckt wird, vor dem Push entfernen:

```bash
git rm --cached "pfad/zur/datei.xlsx"
```

---

## Tests

```bash
py -m pytest tests/ -q
```

82 Tests, ohne Netz und ohne LLM-Aufrufe. Die Fixture `mini_master` baut eine
Master-DB nach, die die realen Mängel enthält — mehrzeilige Header,
Float-IDs, Seitentitel im URL-Feld, Mojibake, formatierte Leerzeilen am Ende.

Die Suite deckt unter anderem Regressionen ab, die im Aufbau echte Fehler
waren:

- `sheet.cell(..., value=None)` ist in openpyxl ein No-op — Felder ließen
  sich nicht leeren
- `1.0 == 1` gilt in Python — die Float-Erkennung der `Company_ID` lief nie an
- `openpyxl.max_row` zählt formatierte Leerzeilen mit — neue Zeilen landeten
  hinter einer Lücke von 65 Zeilen
- `RobotFileParser.read()` wird von Cloudflare mit 403 abgewiesen und sperrt
  daraufhin *alles* — offene Seiten galten fälschlich als verboten
- Windows begrenzt die Kommandozeile auf 32767 Zeichen — der Extraktions-Prompt
  muss über stdin gehen, nicht als Argument
- Domains aus Partner-Links dürfen nicht auf die falsche Firma indiziert werden
  (Autodesk hatte eine `makersite.io`-Customer-Story)

---

## Herkunft

Die Vorarbeiten stecken in `OCP-69/260226_Starter` (Paket `competitive_intel`,
3-Agenten-Pipeline über die Anthropic-API) und `OCP-69/Competitive-Analysis_Update`
(handgeschriebene Profile). Übernommen wurde die Stufengliederung
Discovery → Profiling → Analysis. Neu sind: das echte 31-Spalten-Schema mit
Company/Product-Zeilenmodell, der Dedupe-Schritt, echtes Website-Crawling
mit Verifikations-Gate, der Merge in die versionierte Masterdatei — und der
Betrieb ohne API-Key.
