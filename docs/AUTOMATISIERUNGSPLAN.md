# Plan: automatisierte Prüf- und Korrekturschleife

**Status:** Entwurf zur Entscheidung — nichts davon ist gebaut.
**Stand:** 19. August 2026

Ziel: Fehlerfindung, Widerspruchsaufdeckung, Korrektur und Nachprüfung laufen
selbsttätig zwischen **Claude Code** (baut und repariert) und **OpenAI Codex**
(prüft unabhängig), bis ein definiertes Qualitätskriterium erfüllt ist. Der
Nutzer wird erst danach informiert — oder wenn die Schleife nachweislich
feststeckt.

---

## 1. Was die Datenlage sagt

Grundlage ist keine Theorie, sondern die 14 Fehler, die in diesem Projekt
tatsächlich aufgetreten sind. Entscheidend ist **wodurch** sie aufgefallen
sind:

| # | Fehler | Entdeckt durch |
|---|---|---|
| 1 | `missing_fields` ebenenblind → 375/375 Firmen „lückenhaft" | Lauf gegen echte Daten, Ergebnis unplausibel |
| 2 | robots.txt sperrte offene Seiten (403 auf robots.txt selbst) | Echter Crawl + Nachsehen im robots.txt |
| 3 | Partner-Link ordnete `makersite.io` Autodesk zu | Dedupe-Lauf gegen echten Bestand |
| 4 | `sheet.cell(value=None)` ist No-op → Felder nicht leerbar | Test: „repariertes Ergebnis muss Prüfung bestehen" |
| 5 | `1.0 == 1` → Float-Erkennung lief nie an | Testerwartung wich vom Verhalten ab |
| 6 | `max_row` zählt formatierte Leerzeilen | Zeilenzahl der Ausgabe nachgerechnet |
| 7 | Windows-Argumentgrenze 32767 Zeichen | Echter Lauf |
| 8 | `_bump_filename` hängte an statt zu ersetzen | Echter Lauf, Dateiname angesehen |
| 9 | Klammerzusatz im Firmennamen → falsch abgelehnt | Echter Lauf |
| 10 | Befehl lief über den PDF-Seitenrand | PDF gerendert und **angesehen** |
| 11 | 3 Sub-Kategorien keiner Hauptkategorie zugeordnet | Neue Selbstprüfung gegen eigene Config |
| 12 | **WebSearch war gesperrt — `new`-Modus lieferte immer 0** | Echter Lauf, Ergebnis unplausibel, dann isoliert |
| 13 | `codex.CMD` unter Windows nicht startbar | Echter Lauf |
| 14 | codex scheitert sporadisch beim Start | Messreihe über 8 Läufe |

**Die zentrale Erkenntnis steckt in #12.** Dieser Fehler erzeugte

- keine Ausnahme,
- keinen Fehler-Exitcode,
- eine **schema-gültige** Antwort.

Er sah aus wie „nichts gefunden". Kein Lint, kein Typcheck und kein Unit-Test
hätte ihn je gefunden. Aufgefallen ist er nur, weil ein Mensch das Ergebnis
für **unplausibel** hielt: drei Suchzellen, null Treffer.

Daraus folgt das Leitprinzip des ganzen Plans:

> **Prüfe auf positive Belege für geleistete Arbeit, nicht auf Abwesenheit von
> Fehlern.**

Genau 6 der 14 Fehler (1, 2, 6, 8, 12, 14) sind nur so auffindbar. Eine
Automatik ohne Plausibilitätsprüfung würde sie alle durchlassen.

---

## 1a. Prüfung der eigenen Annahmen (19.08.2026)

Die tragende Annahme des Plans lautete: *Eine fremde Instanz findet Fehler,
die der Autor übersieht.* Behauptet — aber nie belegt. Deshalb ein Versuch mit
bekannter Wahrheit.

**Versuchsaufbau.** Codex bekam den Code von `call_claude` und `discover_new`
im Stand **vor** dem Fix, dazu den Zielvertrag und die adversarische
Prüfanweisung aus Abschnitt 5.3. Der bekannte Fehler: fehlendes
`--allowedTools`, wodurch WebSearch stillschweigend verweigert wurde.

| Variante | Information | Ergebnis |
|---|---|---|
| **A** | nur Code + Vertrag | Ursache **nicht** gefunden. Aber die Fehlerklasse erkannt: „leere, schema-gültige Antwort gilt als Erfolg" (blockierend) und „`--tools` wird übergeben, aber nie geprüft, ob wirklich gesucht wurde" (wichtig) |
| **B** | Code + Vertrag + **Symptom** („0 Kandidaten, kein Fehler, Exitcode 0") | Ursache **exakt getroffen** in 59 Sekunden: „`dontAsk` … WebSearch ist im Headless-Lauf nicht automatisch erlaubt … wird abgelehnt" — blockierend |

**Bewertung der Annahme: teilweise bestätigt, mit einer wichtigen Korrektur.**

- Bestätigt: Die fremde Instanz erkennt ohne jeden Hinweis die *Fehlerklasse*
  — genau jene, die kein Test findet. Sie kam damit unabhängig auf dasselbe
  Leitprinzip, das aus der Fehlerhistorie folgt.
- Korrektur: Ohne Symptom fand sie die **konkrete Ursache nicht**. Mit Symptom
  in unter einer Minute.

**Folge für das Design:** Die Gegenprüfung gehört **nicht** primär hinter grüne
Gates (so stand es in Abschnitt 4), sondern **an die Seite eines roten Gates**
— als Diagnostiker. Der ursprüngliche Entwurf hätte den teuersten Fehler des
Projekts nicht gefangen, weil dort alle Gates grün waren.

Beide Rollen bleiben sinnvoll, aber mit unterschiedlichem Gewicht:

| Rolle | Wann | Erwartbarer Nutzen |
|---|---|---|
| **Diagnostiker** | Gate ist rot | hoch — belegt |
| **Vorprüfer** | alle Gates grün | mittel — findet Klassen, nicht Fälle |

**Einschränkung des Versuchs:** Codex sah nur einen gekürzten Auszug, nicht das
ganze Modul. Zwei Befunde („`known_names` ungenutzt", „`returncode` ungeprüft")
treffen auf den Auszug zu, nicht auf den echten Code. Ein Prüfer braucht also
die **vollständige** Datei, sonst entstehen Scheinbefunde.

### Status der übrigen Annahmen

| Annahme | Status |
|---|---|
| Plausibilitätsorakel fangen stille Fehler | **bestätigt** — aus der Historie und unabhängig von Codex benannt |
| Baumeister darf Gates nicht anfassen | logisch zwingend, kein Gegenargument |
| Prüfer darf die Begründung des Baumeisters nicht sehen | **ungeprüft** |
| Codex-Zuverlässigkeit 7/8 | gemessen bei n=8; heute 2 von 2 im ersten Versuch — die Stichprobe ist klein |
| Kosten 4- bis 6-fach | **Schätzung, ungeprüft** |
| Iterationsgrenze 4 | **willkürlich gesetzt** |

---

## 2. Rollenverteilung der beiden Instanzen

| | Claude Code | OpenAI Codex |
|---|---|---|
| Rolle | Baumeister und Reparateur | Unabhängiger Prüfer |
| Rechte | Schreiben im Arbeitszweig, Tests ausführen | **Nur lesen** (`--sandbox read-only`) |
| Sieht | Auftrag, Code, Gate-Ergebnisse | Diff, Gate-Ergebnisse, Zielvertrag — **nicht** die Begründung des Baumeisters |
| Auftrag | „Erfülle den Vertrag" | „Finde, was daran falsch ist" |

**Warum zwei Instanzen und nicht eine mit zwei Hüten:** Wer seinen eigenen Code
prüft, teilt dessen blinde Flecken. Bei Fehler #12 habe ich den Fehler
geschrieben *und* die Tests dazu — meine Tests haben mein Missverständnis
mitkodiert und bestätigt. Ein Prüfer aus einer anderen Modellfamilie hat andere
Vorannahmen und fragt eher „warum ist `dontAsk` hier der richtige Modus?".

Die Begründung des Baumeisters wird dem Prüfer bewusst **vorenthalten** — sonst
übernimmt er dessen Denkrahmen und die Unabhängigkeit ist dahin.

---

## 3. Die Prüfstufen

Sieben Stufen, aufsteigend nach Kosten. Abbruch bei der ersten harten
Verletzung — teure Stufen laufen nur, wenn die billigen grün sind.

| Stufe | Was | Kosten | Fängt |
|---|---|---|---|
| **G1** Statik | `ruff`, Syntax, Importzyklen | Sekunden | Formfehler |
| **G2** Selbstkonsistenz | Taxonomie ohne Waisen, Profile gegen Legende, Schema gegen echte Kopfzeile | Sekunden | #11 |
| **G3** Tests | `pytest`, keine Regression | Sekunden | #4, #5 |
| **G4** Echtdaten-Plausibilität | Kennzahlen gegen die reale Master-DB | ~1 Min | #1, #3, #6 |
| **G5** Live-Rauchtest | Kleiner echter Lauf mit **Wirksamkeitsnachweis** | 3–5 Min | #2, #7, #8, #9, **#12**, #13 |
| **G6** Artefaktprüfung | PDF rendern, Seiten auf Überlauf prüfen | ~1 Min | #10 |
| **G7** Gegenprüfung | Codex, adversarisch | 2–5 Min | Denkfehler, falsche Annahmen |

### G4 und G5 sind das Herzstück

Beispiele für **Plausibilitäts-Orakel** — Aussagen, die wahr sein *müssen*:

```
G4  merge:      ausgabe_zeilen == eingabe_zeilen + neue_zeilen      (exakt)
G4  gaps:       0 < ausgewaehlte_firmen < alle_firmen               (weder alle noch keine)
G4  repair:     fehler_nachher == 0  UND  geaenderte_zeilen > 0
G4  taxonomie:  jede Sub-Kategorie hat >= 1 Hauptkategorie

G5  discovery:  mindestens eine Suchzelle liefert > 0 Kandidaten
G5  websuche:   das Modell bestaetigt aktiv, gesucht zu haben       <- faengt #12
G5  crawl:      verifizierte_quote >= 0.5 auf bekannter Stichprobe
G5  extract:    median(produkte_je_firma) >= 1
G5  werkzeuge:  jede externe CLI liefert einen positiven Wirksamkeitsnachweis
```

Die letzte Zeile ist die Lehre aus #12 in Reinform. Ein Vorflug prüft
**vor** jedem unbeaufsichtigten Lauf, dass jedes Werkzeug wirklich arbeitet —
nicht nur, dass es keinen Fehler wirft.

---

## 4. Die Schleife

```
  ZIELVERTRAG (maschinenprüfbar, einmal formuliert)
        |
        v
  +-> BAUEN (Claude Code)
  |     |
  |     v
  |   G1 -> G2 -> G3 -> G4 -> G5 -> G6      harte Gates
  |     |
  |     +-- Verletzung? --> DIAGNOSE --> URSACHE --> Fix -+
  |                                                        |
  |     alle gruen                                         |
  |     v                                                  |
  |   G7 GEGENPRÜFUNG (Codex, read-only)                   |
  |     |                                                  |
  |     +-- Befund? --> VERIFIZIEREN (Claude) --+          |
  |     |                                        |          |
  |     |                      bestaetigt -------+----------+
  |     |                      widerlegt --> protokollieren
  |     v
  |   KONVERGENZ-ENTSCHEID
  |     |
  +-----+ nicht konvergiert und Abbruchbedingung nicht erreicht
        |
        v
  BERICHT AN DEN NUTZER  (einmal, am Ende)
```

### Abbruchbedingungen — nicht verhandelbar

Ohne sie dreht sich die Schleife im Kreis und verbrennt Kontingent.

| Bedingung | Schwelle | Reaktion |
|---|---|---|
| Iterationen | 4 | Halt, Bericht mit Stand |
| Gleiche Fehlersignatur zweimal | 2 | Halt — offensichtlich nicht verstanden |
| Oszillation (Fix A bricht B, Fix B bricht A) | erkannt | Halt, beide Diffs vorlegen |
| Verbrauch | Budget aus dem Auftrag | Halt |
| Wanduhr | 60 Min voreingestellt | Halt |
| Prüfer meldet dreimal denselben Befund | 3 | Halt, an den Nutzer |

**Härteste Regel:** Der Baumeister darf **die Gates nicht anfassen**.
Änderungen an `tests/`, an den Orakeln oder an der Gate-Konfiguration sind ihm
gesperrt und erfordern Nutzerfreigabe. Sonst ist der bequemste Weg zum grünen
Gate, das Gate zu lockern — und die Automatik optimiert sich selbst blind.

---

## 5. Die Prompts

Fünf Bausteine. Die Gates selbst sind **kein** Prompt, sondern ein
deterministisches Skript — Prüfungen dürfen nicht von Modelllaune abhängen.

### 5.1 Zielvertrag (einmal, vom Nutzer oder mit ihm)

```
ZIEL
  <Ein Satz: was am Ende gilt, das jetzt nicht gilt.>

FERTIG, WENN
  - <maschinenprüfbare Bedingung 1>
  - <maschinenprüfbare Bedingung 2>
  Jede Bedingung muss ein Skript mit Ja/Nein beantworten können.
  "Der Code ist sauber" ist keine Bedingung. "ruff meldet 0 Befunde" ist eine.

NICHT ANFASSEN
  - <Dateien/Verhalten, die unverändert bleiben müssen>
  - Immer gesperrt: tests/, die Gate-Definitionen, config/taxonomy.yaml

PLAUSIBILITÄT
  - <Aussagen, die nach der Änderung wahr sein müssen>
  - Mindestens eine Aussage der Form "X hat nachweislich stattgefunden"

BUDGET
  Iterationen: 4 | Zeit: 60 Min | Verbrauch: <Obergrenze>

AUTONOMIESTUFE
  A0 | A1 | A2 | A3   (siehe Abschnitt 6)
```

### 5.2 Baumeister (Claude Code)

```
Du erfüllst den folgenden Zielvertrag in diesem Repository.

<ZIELVERTRAG>

VORGEHEN
1. Bevor du etwas änderst: nenne die Ursache, nicht das Symptom.
   Formuliere sie als prüfbare Behauptung.
2. Ändere so wenig wie möglich. Jede Änderung muss auf eine
   Vertragsbedingung zurückführbar sein.
3. Nach jeder Änderung: python scripts/gates.py --stage all
4. Bei rotem Gate: erst die Ursache benennen, dann korrigieren.
   Passe NIEMALS ein Gate, einen Test oder ein Orakel an, damit es grün
   wird. Diese Dateien sind für dich gesperrt.
5. Hältst du eine Vertragsbedingung selbst für falsch: halte an und sage
   es. Ein falscher Vertrag wird nicht stillschweigend umgangen.

AUSGABE
  ursache          Was war die Ursache? (nicht: was hast du geändert)
  aenderungen[]    Datei, Zeilen, Begründung je Änderung
  gates            Ergebnis je Stufe
  restrisiko       Was könnte trotz grüner Gates falsch sein?
```

Der letzte Punkt ist wichtig: Er zwingt den Baumeister, seine eigene
Unsicherheit zu benennen — und liefert dem Prüfer die Ansatzpunkte.

### 5.3 Prüfer (Codex, read-only, adversarisch)

```
Du prüfst eine fremde Änderung. Deine Aufgabe ist NICHT zu bestätigen,
sondern zu widerlegen. Du hast nur Leserechte.

ZIELVERTRAG
<...>

DIFF
<...>

GATE-ERGEBNISSE
<...>   (alle grün — das ist die Behauptung, die du angreifen sollst)

PRÜFE IN DIESER REIHENFOLGE
1. Erfüllt die Änderung den Vertrag WIRKLICH, oder nur die Messung davon?
   Suche gezielt nach Ergebnissen, die gültig aussehen, aber leer oder
   bedeutungslos sind.
2. Welche stillschweigende Annahme steckt in der Änderung? Unter welchen
   Umständen ist sie falsch?
3. Welcher Fall aus der Praxis ist nicht abgedeckt? Nenne einen konkreten
   Eingabewert.
4. Wurde etwas gelockert, verworfen oder übersprungen, um ein Gate grün
   zu bekommen?
5. Was ist die schlechteste realistische Folge, wenn du falsch liegst?

REGELN
- Jeder Befund braucht einen konkreten Auslöser: Datei, Zeile, Eingabe.
- "Könnte problematisch sein" ist kein Befund.
- Findest du nichts, sage das klar. Erfinde keine Befunde.

AUSGABE (JSON)
  befunde[]  { schwere: blockierend|wichtig|hinweis,
               datei, zeile, ausloeser, folge, begruendung }
  urteil     bestanden | beanstandet
```

### 5.4 Diagnose (bei rotem Gate)

```
Dieses Gate ist rot:
<GATE, Ausgabe, Erwartung>

Beantworte NUR diese Fragen. Ändere noch nichts.
1. Was genau wurde erwartet, was ist eingetreten?
2. Ist die Erwartung richtig — oder ist das Gate falsch? Begründe.
3. Wenn die Erwartung richtig ist: was ist die Ursache?
   Unterscheide Ursache von Symptom.
4. Womit lässt sich die Ursache beweisen, bevor du sie behebst?
5. Was ist die kleinstmögliche Korrektur?

Verdacht auf ein falsches Gate wird dem Nutzer vorgelegt, nicht selbst
behoben.
```

### 5.5 Schiedsrichter (Konvergenzentscheid)

```
Vertrag: <...>
Gates: <...>
Befunde des Prüfers: <...>
Iteration: <n> von <max>

Entscheide GENAU EINES:
  KONVERGIERT   alle Gates grün, kein blockierender Befund offen
  WEITER        klar benannte Restarbeit, Budget reicht -> nächste Iteration
  FESTGEFAHREN  keine Aussicht auf Fortschritt -> an den Nutzer
  VERTRAGSFEHLER  das Ziel selbst ist falsch/unerfüllbar -> an den Nutzer

Begründe in höchstens drei Sätzen. Im Zweifel: FESTGEFAHREN.
Ein Bericht "fertig", der es nicht ist, ist teurer als eine Rückfrage.
```

---

## 6. Autonomiestufen und Berechtigungen

Die Stufe legt der Nutzer im Auftrag fest.

| Stufe | Der Ablauf | Claude-Modus | Codex-Modus |
|---|---|---|---|
| **A0** Nur Plan | Analysiert, ändert nichts | `--permission-mode plan` | `--sandbox read-only` |
| **A1** Vorschlag | Ändert im Zweig, hält beim **ersten** roten Gate an | `acceptEdits` + Allowlist | `read-only` |
| **A2** Autonom | Schleift bis grün oder Abbruchbedingung, **ein** Bericht | `acceptEdits` + Allowlist | `read-only` |
| **A3** Autonom bis PR | wie A2, öffnet zusätzlich den Pull Request | `acceptEdits` + Allowlist | `read-only` |

**A2 ist die Stufe, die die Frage beantwortet:** Der Nutzer erfährt erst etwas,
wenn geprüft und korrigiert wurde.

### Werkzeug-Allowlist des Baumeisters

```
erlaubt:   Read, Grep, Glob, Edit, Write
           Bash(python -m pytest*), Bash(python -m ruff*),
           Bash(python scripts/gates.py*), Bash(git add*), Bash(git commit*)
gesperrt:  Bash(git push*), Bash(gh *), Bash(rm *)
           Schreibzugriff auf tests/, scripts/gates.py, config/taxonomy.yaml
           jeder Pfad ausserhalb des Projektordners  -> insbesondere H:\
```

`--permission-mode bypassPermissions` und `codex --dangerously-bypass-...`
kommen in **keiner** Stufe vor.

### Niemals automatisch, unabhängig von der Stufe

- Pull Request **mergen**
- Schreiben nach `H:\` (Team-Laufwerk)
- Löschen von Daten oder Verläufen
- Alles nach außen: Mail, Veröffentlichung, fremde Systeme

Begründung: Diese Handlungen sind nicht oder nur teuer umkehrbar. Der Gewinn
durch Automatisierung ist gering, der Schaden im Fehlerfall hoch.

### Eine Lehre aus #12 gilt auch für die Schleife selbst

Der Vorflug muss prüfen, ob die **konfigurierten Rechte tatsächlich wirken** —
nicht, ob sie gesetzt sind. Ein Modus, der stillschweigend verweigert, sieht
sonst genauso aus wie einer, der erlaubt. Das war der teuerste Fehler dieses
Projekts, und eine unbeaufsichtigte Schleife würde ihn stundenlang wiederholen.

---

## 7. Vorteile

**Die Fehlerklasse, die heute durchrutscht, wird systematisch gefangen.**
Sechs der vierzehn Fehler waren nur an unplausiblen Ergebnissen erkennbar.
Diese Prüfung heute macht ein Mensch, wenn er hinschaut — die Orakel machen
sie bei jedem Lauf.

**Unabhängige blinde Flecken.** Bei #12 hat mein eigener Test meinen eigenen
Denkfehler bestätigt. Ein Prüfer mit anderer Vorannahme ist die einzige
verlässliche Gegenkraft.

**Ein Bericht statt zwanzig Unterbrechungen.** Der Nutzer entscheidet über
Ergebnisse, nicht über Zwischenstände.

**Die Prüfungen werden reproduzierbar.** Was heute Erfahrung ist, wird als
Orakel abgelegt und gilt für jede künftige Änderung.

**Nachvollziehbarkeit.** Jede Iteration hinterlässt Vertrag, Diff,
Gate-Ergebnisse und Prüferbefunde. Ein missglückter Lauf ist auswertbar.

## 8. Nachteile — ungeschönt

**Das Orakel-Problem bleibt.** Automatisch gefunden wird nur, woran jemand
gedacht hat. Fehler #10 (Befehl über den Seitenrand) fiel auf, weil ich das PDF
*angesehen* habe. Kein Orakel hätte das vorhergesagt — erst danach ließ es sich
als Regel formulieren. **Die Automatik ersetzt das Hinsehen nicht, sie
konserviert es.**

**Codex ist unzuverlässig.** Gemessen: 7 von 8 Läufen erfolgreich, und das nur
mit Wiederholung; Ausfälle kosteten bis zu 211 Sekunden. Die Gegenprüfung muss
deshalb **nicht-blockierend** sein — fällt sie aus, läuft die Schleife weiter
und vermerkt es. Sonst hängt die Automatik an der schwächsten Komponente.

**Gefahr des Zurechtbiegens.** Der bequemste Weg zum grünen Gate ist ein
lockereres Gate. Die Sperre auf `tests/` und die Gate-Definitionen ist deshalb
tragend — ohne sie ist die ganze Konstruktion wertlos. Diese Sperre muss
technisch durchgesetzt werden, nicht per Anweisung im Prompt.

**Kosten- und Zeitvervielfachung.** Jede Iteration führt alle Gates aus. Eine
Schleife mit vier Iterationen kostet grob das Vier- bis Sechsfache eines
einzelnen Laufs. Bei Max-Kontingent heißt das Rate-Limits, nicht Rechnungen —
aber es ist endlich.

**Unbeaufsichtigte Läufe sind schwerer zu verstehen.** Wenn nach 40 Minuten
„festgefahren" gemeldet wird, muss das Protokoll gut genug sein, um den Weg
nachzuvollziehen. Das ist Zusatzaufwand, der sich erst ab mehreren Läufen
lohnt.

**Verlust an Gelegenheitsfunden.** Beim schrittweisen Arbeiten fällt Nebenbei
auf, was nicht im Auftrag stand — etwa dass das URL-Feld in v2.2 Seitentitel
enthält. Eine zielgerichtete Automatik läuft daran vorbei.

**Angemessenheit.** Für eine einzelne kleine Änderung ist der Aufbau
überdimensioniert. Er lohnt sich bei wiederkehrenden Aufgaben mit klarem
Zielbild — Spalte ergänzen, Profil hinzufügen, Extraktionsqualität heben.

---

## 9. Umsetzung, falls gewünscht

| Schritt | Inhalt | Aufwand |
|---|---|---|
| 1 | `scripts/gates.py` — G1 bis G4 aus Vorhandenem zusammenführen | klein |
| 2 | G5 Live-Rauchtest mit Wirksamkeitsnachweis je Werkzeug | mittel |
| 3 | G6 Artefaktprüfung (PDF) — Bauzeit-Sperre besteht schon | klein |
| 4 | `scripts/loop.py` — Schleife, Abbruchbedingungen, Protokoll | mittel |
| 5 | Prompt-Vorlagen als Dateien unter `prompts/` | klein |
| 6 | Schreibsperre auf `tests/` technisch durchsetzen | klein |
| 7 | Erprobung an einer echten Aufgabe, z. B. „Spalte Funding_Total" | mittel |

Sinnvolle Reihenfolge: **1 → 2 → 6 → 4**. Damit ist der Kern lauffähig; G6, die
Prompt-Dateien und die Erprobung folgen.

Ein Zwischenschritt lohnt sich unabhängig von der Entscheidung über die
Schleife: **Schritt 1 und 2 allein** liefern schon den größten Teil des Nutzens,
weil sie die Plausibilitätsprüfung reproduzierbar machen. Sie sind auch ohne
jede Automatik wertvoll.

---

## 9a. Gemini als Prüfer statt Codex?

### Befund zur Verfügbarkeit (19.08.2026, geprüft)

| Prüfung | Ergebnis |
|---|---|
| `gemini` auf dem PATH | **nein** |
| `@google/gemini-cli` global über npm | **nein** — installiert sind nur codex, npm, openclaw, pnpm |
| `GEMINI_API_KEY` / `GOOGLE_API_KEY` u. a. | **nicht gesetzt** |
| `gcloud` / Vertex-Zugang | **nicht vorhanden** |
| `~/.gemini` | vorhanden — stammt aber von **Antigravity IDE**, nicht von der CLI |
| Antigravity headless nutzbar | **nein** — nur `antigravity-ide.cmd`, ein GUI-Starter |

**Gemini ist derzeit nicht programmatisch erreichbar.** Ein Vergleich zweier
Prüfer ist heute nicht durchführbar. Nötig wären zwei Schritte, davon einer
nur durch den Nutzer selbst:

1. `npm install -g @google/gemini-cli` — Systemänderung, braucht Freigabe
2. Anmeldung mit dem Google-Konto — **kann nur der Nutzer**

Zu klären ist dabei eine Annahme, die ich **nicht** bestätigen kann: ob das
bezahlte Gemini-Abo dieselbe Headless-Nutzung freischaltet, wie ChatGPT Pro es
für Codex tut. Bei Codex ist das belegt (`auth_mode: chatgpt`, kein API-Key).
Für Gemini ist es zu prüfen — die CLI kennt sowohl Konto-Anmeldung als auch
einen separaten API-Schlüssel, und ob das Abo dabei über das kostenlose
Kontingent hinaushilft, zeigt erst der Versuch.

### Das Google-Drive-Argument trägt nicht

Teilergebnisse in Google Drive abzulegen ist **unabhängig von der Modellwahl**
möglich: `H:\` ist bereits als Laufwerk eingebunden und verhält sich wie ein
normaler Pfad. Die Schleife kann dorthin schreiben, gleich wer prüft. Ein
Vorteil für Gemini entsteht daraus nicht.

Für Zwischenstände wäre ohnehin ein **lokaler** Ordner die bessere Wahl:
Google Drive gleicht laufend ab, und halbfertige Artefakte einer laufenden
Schleife im Team-Laufwerk erzeugen Verwirrung. Empfehlung: `data/loop/`
lokal, und nur das Endergebnis nach `H:\` — genau wie beim Recherchelauf.

### Was für einen Wechsel spräche

- Andere Modellfamilie, andere blinde Flecken — das ist der eigentliche Zweck
  der zweiten Instanz
- Sehr großes Kontextfenster: Master-DB und Modul könnten gemeinsam
  hineinpassen, ohne Auszüge — das würde die Scheinbefunde aus dem Versuch
  oben vermeiden

### Was dagegen spricht

- Codex ist **installiert, angemeldet und heute empirisch bewährt** — es hat
  den realen Fehler in 59 Sekunden gefunden
- Strukturierte Ausgabe über `--output-schema` und der Read-only-Sandkasten
  sind erprobte Eigenschaften
- Ein dritter Anbieter bedeutet eine dritte Anmeldung, ein drittes
  Ausfallverhalten und eine dritte Fehlerquelle in der Automatik

### Empfehlung

**Nicht austauschen — allenfalls ergänzen, und erst nach Messung.**

Codex hat den Nachweis erbracht, Gemini steht dieser Nachweis noch aus. Ein
Wechsel auf Verdacht tauscht Belegtes gegen Unbelegtes.

Der Versuchsaufbau aus Abschnitt 1a ist wiederverwendbar und liefert die
Entscheidung auf Messbasis. Als Prüfmaß schlage ich vor:

| Maß | Erhebung |
|---|---|
| Trefferquote | Findet der Prüfer die bekannte Ursache? (Variante A und B getrennt) |
| Scheinbefunde | Wie viele Befunde treffen auf den echten Code nicht zu? |
| Zuverlässigkeit | Erfolgsquote über 10 Läufe |
| Antwortzeit | Median |
| Schema-Treue | Hält er die vorgegebene JSON-Struktur ein? |

Sinnvoll wäre die Messung an **drei** bekannten Fehlern statt an einem — etwa
zusätzlich #4 (`sheet.cell(value=None)`) und #11 (verwaiste Sub-Kategorien).
Ein einzelner Fall trägt keine Anbieterentscheidung.

---

## 10. Offene Punkte für die Entscheidung

1. **Autonomiestufe als Voreinstellung** — A1 (hält beim ersten roten Gate an)
   oder A2 (läuft bis grün durch)?
2. **Iterationsobergrenze** — 4 ist ein Vorschlag, kein Messwert.
3. **Live-Gate im Routinelauf?** G5 kostet echtes Kontingent. Bei jeder
   Änderung, oder nur wenn `cintel/llm.py`, `crawl.py` oder `discover.py`
   berührt wurden?
4. **Gegenprüfung verpflichtend oder optional?** Angesichts der gemessenen
   Unzuverlässigkeit von Codex empfehle ich: optional und nicht-blockierend.
5. **Umfang** — nur für dieses Repository, oder als wiederverwendbares Muster
   für andere Projekte?
