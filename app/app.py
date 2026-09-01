"""
cintel UI - lokale Browser-Oberflaeche fuer die Competitive Intel Master DB.

Start:
    py app\\app.py            (Standard-Port 8742)
    dann http://127.0.0.1:8742 im Browser oeffnen.

Funktionen:
  - Dashboard: Kennzahlen der aktuellsten DB-Version, offene Suchauftraege,
    letzte Ingest-Reports.
  - Firmenliste mit Filter (Tier, Textsuche) und Detailansicht je Firma
    (Company-Zeile + Produktzeilen).
  - Bearbeiten: Aenderungen landen in einem Aenderungskorb
    (data/ui_changes.json) und werden gesammelt als NEUE DB-Version
    uebernommen - die Eingangsdatei bleibt unveraendert (fill-only-Prinzip
    gilt fuer den Agenten; die UI ist der Weg fuer KURATIERTE Handpflege
    und darf deshalb auch ueberschreiben).
  - Suchauftraege: legt einen Auftrag als JSON in data/jobs/ ab und zeigt
    den Claude-Befehl, der den Recherche-Agenten damit startet.
"""

from __future__ import annotations

import datetime as dt
import html
import json
import re
import sys
from pathlib import Path
from urllib.parse import quote, unquote

import uvicorn
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from cintel.masterdb import SHEET_MASTER, MasterDB  # noqa: E402
from cintel.schema import COLUMNS, Record  # noqa: E402

DATA_DIR = REPO_ROOT / "data"
OUT_DIR = DATA_DIR / "outputs"
JOBS_DIR = DATA_DIR / "jobs"
CHANGES_FILE = DATA_DIR / "ui_changes.json"
VERSION_RE = re.compile(r"_v(\d+)\.(\d+)\.xlsx$", re.IGNORECASE)

FIELD_LABELS = dict(COLUMNS)
EDITABLE = [name for name, _ in COLUMNS if name not in ("company_id", "row_kind")]

app = FastAPI(title="cintel UI")


# --------------------------------------------------------------------------
# DB-Auffindung und Aenderungskorb
# --------------------------------------------------------------------------

def latest_db_path() -> Path | None:
    candidates: list[tuple[tuple[int, int], Path]] = []
    for folder in (OUT_DIR, DATA_DIR, REPO_ROOT.parent.parent):
        if not folder.exists():
            continue
        for path in folder.glob("Competitive_Intel_Master_DB_v*.xlsx"):
            match = VERSION_RE.search(path.name)
            if match:
                candidates.append(
                    ((int(match.group(1)), int(match.group(2))), path))
    if not candidates:
        return None
    return max(candidates, key=lambda item: item[0])[1]


def load_changes() -> dict:
    if CHANGES_FILE.exists():
        return json.loads(CHANGES_FILE.read_text(encoding="utf-8"))
    return {"db": "", "edits": {}}


def save_changes(changes: dict) -> None:
    CHANGES_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHANGES_FILE.write_text(
        json.dumps(changes, ensure_ascii=False, indent=2), encoding="utf-8")


# --------------------------------------------------------------------------
# HTML-Bausteine
# --------------------------------------------------------------------------

STYLE = """
<style>
  body { font-family: system-ui, sans-serif; margin: 0; background:#f5f6f8; color:#1c2733; }
  header { background:#12314f; color:#fff; padding:.7rem 1.2rem; display:flex; gap:1.4rem; align-items:baseline;}
  header a { color:#cfe3f7; text-decoration:none; margin-right:.9rem; }
  header a:hover { color:#fff; }
  main { max-width: 1100px; margin: 1.2rem auto; padding: 0 1rem; }
  .card { background:#fff; border:1px solid #dde3ea; border-radius:8px; padding:1rem 1.2rem; margin-bottom:1rem; }
  table { border-collapse: collapse; width:100%; font-size:.9rem; }
  th, td { text-align:left; padding:.35rem .5rem; border-bottom:1px solid #e8ecf1; vertical-align:top;}
  tr:hover td { background:#f0f5fb; }
  .tier1 { color:#a30f2d; font-weight:600; } .tier2 { color:#9a6a00; } .tier3 { color:#5c6b7a; }
  input[type=text], select, textarea { width:100%; box-sizing:border-box; padding:.3rem .4rem;
      border:1px solid #c6cfd9; border-radius:5px; font:inherit; font-size:.85rem;}
  textarea { min-height: 3.2rem; }
  button { background:#12314f; color:#fff; border:0; border-radius:6px; padding:.45rem .9rem; cursor:pointer;}
  button.secondary { background:#5c6b7a; }
  .pill { display:inline-block; background:#e8f0fa; border-radius:99px; padding:.1rem .6rem; font-size:.8rem; margin:.1rem;}
  .warn { background:#fdf3d7; border:1px solid #eedca6; padding:.5rem .8rem; border-radius:6px;}
  code { background:#eef1f5; padding:.15rem .35rem; border-radius:4px; font-size:.85rem; }
  .grid { display:grid; grid-template-columns: 200px 1fr; gap:.4rem .8rem; align-items:start;}
  .muted { color:#5c6b7a; font-size:.85rem; }
</style>
"""


def page(title: str, body: str, staged: int = 0) -> HTMLResponse:
    korb = f' &middot; <a href="/aenderungen">Änderungskorb ({staged})</a>' if staged else \
        ' &middot; <a href="/aenderungen">Änderungskorb</a>'
    return HTMLResponse(
        "<!doctype html><html><head><meta charset='utf-8'>"
        f"<title>{html.escape(title)}</title>{STYLE}</head><body>"
        "<header><strong>cintel</strong>"
        "<nav><a href='/'>Dashboard</a><a href='/firmen'>Firmen</a>"
        f"<a href='/auftraege'>Suchaufträge</a>{korb}</nav></header>"
        f"<main>{body}</main></body></html>"
    )


def esc(value: object) -> str:
    return html.escape(str(value if value is not None else ""))


# --------------------------------------------------------------------------
# Routen
# --------------------------------------------------------------------------

@app.get("/", response_class=HTMLResponse)
def dashboard() -> HTMLResponse:
    db_path = latest_db_path()
    changes = load_changes()
    staged = sum(len(v) for v in changes["edits"].values())
    if db_path is None:
        return page("cintel", "<div class='card warn'>Keine Master-DB gefunden. "
                              "Erwarte Competitive_Intel_Master_DB_v*.xlsx in data/outputs.</div>")
    db = MasterDB(db_path)
    blocks = db.blocks()
    tiers: dict[str, int] = {}
    for record in db.records:
        tier = str(record.values.get("competitor_tier") or "").strip()
        if tier:
            tiers[tier] = tiers.get(tier, 0) + 1
    tier_html = "".join(f"<span class='pill'>{esc(t)}: {n}</span>"
                        for t, n in sorted(tiers.items()))

    jobs = sorted(JOBS_DIR.glob("auftrag_*.json")) if JOBS_DIR.exists() else []
    jobs_html = "".join(
        f"<li><code>{esc(j.name)}</code></li>" for j in jobs[-5:]) or "<li>keine</li>"

    runs = sorted(OUT_DIR.glob("run_*/report.md"), reverse=True)[:5]
    runs_html = "".join(
        f"<li><a href='/report/{quote(r.parent.name)}'>{esc(r.parent.name)}</a></li>"
        for r in runs) or "<li>keine</li>"

    body = f"""
    <div class='card'><h2>Aktuelle Master-DB</h2>
      <p><code>{esc(db_path.name)}</code> &middot; {len(db.records)} Zeilen &middot;
         {len(blocks)} Firmen &middot;
         {sum(len(b.product_rows) for b in blocks)} Produktzeilen</p>
      <p>{tier_html}</p></div>
    <div class='card'><h2>Offene Suchaufträge</h2><ul>{jobs_html}</ul>
      <p><a href='/auftraege'>Neuen Suchauftrag anlegen &rarr;</a></p></div>
    <div class='card'><h2>Letzte Ingest-Läufe</h2><ul>{runs_html}</ul></div>
    """
    return page("cintel Dashboard", body, staged)


@app.get("/firmen", response_class=HTMLResponse)
def firmen(q: str = "", tier: str = "") -> HTMLResponse:
    db_path = latest_db_path()
    if db_path is None:
        return RedirectResponse("/")
    db = MasterDB(db_path)
    staged = sum(len(v) for v in load_changes()["edits"].values())
    rows = []
    for block in sorted(db.blocks(), key=lambda b: b.company.lower()):
        source = block.company_row or (block.product_rows[0]
                                       if block.product_rows else None)
        if source is None:
            continue
        block_tier = str(source.values.get("competitor_tier") or "")
        if q and q.lower() not in block.company.lower():
            continue
        if tier and not block_tier.startswith(tier):
            continue
        tier_class = ("tier1" if "Tier 1" in block_tier else
                      "tier2" if "Tier 2" in block_tier else "tier3")
        rows.append(
            f"<tr><td><a href='/firma/{quote(block.company)}'>{esc(block.company)}</a></td>"
            f"<td class='{tier_class}'>{esc(block_tier)}</td>"
            f"<td>{esc(source.values.get('beachhead'))}</td>"
            f"<td>{len(block.product_rows)}</td>"
            f"<td class='muted'>{esc(str(source.values.get('last_update') or '')[:10])}</td></tr>")
    body = f"""
    <div class='card'>
      <form method='get'>
        <input type='text' name='q' placeholder='Firma suchen…' value='{esc(q)}'
               style='width:40%'>
        <select name='tier' style='width:20%'>
          <option value=''>alle Tiers</option>
          <option value='Tier 1' {"selected" if tier == "Tier 1" else ""}>Tier 1 – Direkt</option>
          <option value='Tier 2' {"selected" if tier == "Tier 2" else ""}>Tier 2 – Nachbar</option>
          <option value='Tier 3' {"selected" if tier == "Tier 3" else ""}>Tier 3 – Beobachten</option>
        </select>
        <button>Filtern</button>
      </form></div>
    <div class='card'><p class='muted'>{len(rows)} Firmen</p>
    <table><tr><th>Firma</th><th>Tier</th><th>Beachhead</th><th>Produkte</th><th>Update</th></tr>
    {''.join(rows)}</table></div>"""
    return page("Firmen", body, staged)


@app.get("/firma/{name}", response_class=HTMLResponse)
def firma(name: str) -> HTMLResponse:
    name = unquote(name)
    db_path = latest_db_path()
    db = MasterDB(db_path)
    changes = load_changes()
    staged = sum(len(v) for v in changes["edits"].values())
    block = next((b for b in db.blocks() if b.company == name), None)
    if block is None:
        return page("Nicht gefunden", f"<div class='card warn'>Firma {esc(name)} "
                                      "nicht gefunden.</div>", staged)

    def form_for(record: Record, index: int, heading: str) -> str:
        fields = []
        pending = changes["edits"].get(str(index), {})
        for field in EDITABLE:
            value = pending.get(field, record.values.get(field))
            label = FIELD_LABELS[field]
            marker = " *" if field in pending else ""
            fields.append(
                f"<div class='muted'>{esc(label)}{marker}</div>"
                f"<div><textarea name='{field}'>{esc(value)}</textarea></div>")
        return (f"<div class='card'><h3>{esc(heading)}</h3>"
                f"<form method='post' action='/firma/{quote(name)}/zeile/{index}'>"
                f"<div class='grid'>{''.join(fields)}</div>"
                "<p><button>In den Änderungskorb</button></p></form></div>")

    index_of = {id(r): i for i, r in enumerate(db.records)}
    parts = [f"<h2>{esc(name)} <span class='muted'>(Company_ID "
             f"{esc(block.company_id)})</span></h2>"]
    if block.company_row is not None:
        parts.append(form_for(block.company_row, index_of[id(block.company_row)],
                              "Firmenzeile (Company information)"))
    for product in block.product_rows:
        title = f"Produkt: {product.values.get('product_name') or '(ohne Namen)'}"
        parts.append(form_for(product, index_of[id(product)], title))
    return page(name, "".join(parts), staged)


@app.post("/firma/{name}/zeile/{index}")
async def edit_row(name: str, index: int, request: Request) -> RedirectResponse:
    form = await request.form()
    db_path = latest_db_path()
    db = MasterDB(db_path)
    if index < 0 or index >= len(db.records):
        return RedirectResponse(f"/firma/{quote(unquote(name))}", status_code=303)
    record = db.records[index]
    changes = load_changes()
    if changes.get("db") and changes["db"] != db_path.name:
        changes = {"db": db_path.name, "edits": {}}
    changes["db"] = db_path.name
    edits = changes["edits"].setdefault(str(index), {})
    for field in EDITABLE:
        new_value = str(form.get(field, "")).strip()
        old_value = str(record.values.get(field) or "").strip()
        if new_value != old_value:
            edits[field] = new_value
    if not edits:
        changes["edits"].pop(str(index), None)
    save_changes(changes)
    return RedirectResponse(f"/firma/{quote(unquote(name))}", status_code=303)


@app.get("/aenderungen", response_class=HTMLResponse)
def aenderungen() -> HTMLResponse:
    changes = load_changes()
    staged = sum(len(v) for v in changes["edits"].values())
    if not changes["edits"]:
        return page("Änderungskorb", "<div class='card'>Der Änderungskorb ist leer.</div>")
    rows = []
    db = MasterDB(latest_db_path())
    for index, fields in changes["edits"].items():
        record = db.records[int(index)]
        who = f"{record.values.get('company')} / {record.values.get('product_name') or 'Firmenzeile'}"
        for field, value in fields.items():
            rows.append(f"<tr><td>{esc(who)}</td><td>{esc(FIELD_LABELS[field])}</td>"
                        f"<td>{esc(record.values.get(field))}</td><td>{esc(value)}</td></tr>")
    body = f"""
    <div class='card'><h2>Änderungskorb ({staged} Feldänderungen, Basis
      <code>{esc(changes['db'])}</code>)</h2>
      <table><tr><th>Zeile</th><th>Feld</th><th>bisher</th><th>neu</th></tr>
      {''.join(rows)}</table>
      <p style='margin-top:1rem'>
        <form method='post' action='/aenderungen/uebernehmen' style='display:inline'>
          <button>Als neue DB-Version übernehmen</button></form>
        <form method='post' action='/aenderungen/verwerfen' style='display:inline;margin-left:.6rem'>
          <button class='secondary'>Alles verwerfen</button></form></p></div>"""
    return page("Änderungskorb", body, staged)


@app.post("/aenderungen/uebernehmen")
def uebernehmen() -> HTMLResponse:
    changes = load_changes()
    db_path = latest_db_path()
    if not changes["edits"]:
        return RedirectResponse("/aenderungen", status_code=303)
    if changes.get("db") and changes["db"] != db_path.name:
        return page("Konflikt", f"<div class='card warn'>Der Korb basiert auf "
                                f"<code>{esc(changes['db'])}</code>, aktuell ist aber "
                                f"<code>{esc(db_path.name)}</code>. Bitte Korb verwerfen "
                                "und Änderungen neu erfassen.</div>")
    db = MasterDB(db_path)
    updated: dict[int, Record] = {}
    stamp = dt.date.today().strftime("%Y-%m-%d")
    for index, fields in changes["edits"].items():
        record = db.records[int(index)]
        merged = dict(record.values)
        merged.update(fields)
        merged["last_update"] = stamp
        updated[int(index)] = Record(values=merged)
    target = db.write_new_version([], OUT_DIR, updated=updated)
    save_changes({"db": "", "edits": {}})
    return page("Übernommen", f"<div class='card'>Neue Version geschrieben: "
                              f"<code>{esc(target.name)}</code> "
                              f"({len(updated)} Zeilen aktualisiert). "
                              "<a href='/'>Zurück zum Dashboard</a></div>")


@app.post("/aenderungen/verwerfen")
def verwerfen() -> RedirectResponse:
    save_changes({"db": "", "edits": {}})
    return RedirectResponse("/", status_code=303)


@app.get("/auftraege", response_class=HTMLResponse)
def auftraege() -> HTMLResponse:
    staged = sum(len(v) for v in load_changes()["edits"].values())
    jobs = sorted(JOBS_DIR.glob("auftrag_*.json"), reverse=True) if JOBS_DIR.exists() else []
    items = []
    for job in jobs[:10]:
        data = json.loads(job.read_text(encoding="utf-8"))
        items.append(f"<li><code>{esc(job.name)}</code> — {esc(data.get('modus'))}: "
                     f"{esc(data.get('ziel'))}</li>")
    body = f"""
    <div class='card'><h2>Neuer Suchauftrag</h2>
      <form method='post' action='/auftraege'>
        <div class='grid'>
          <div class='muted'>Modus</div>
          <div><select name='modus'>
            <option value='refresh-tier1'>refresh-tier1 – Tier-1-Bestand aktualisieren</option>
            <option value='discover-new'>discover-new – neue Wettbewerber finden</option>
            <option value='monitor-delta'>monitor-delta – Veränderungen/Signale melden</option>
            <option value='deep-dive'>deep-dive – eine Firma tief analysieren</option>
          </select></div>
          <div class='muted'>Ziel (Firmen, Segment oder Suchraum)</div>
          <div><input type='text' name='ziel'
               placeholder='z.B. aPriori, Tset, up2parts — oder: Costing-Startups Polen'></div>
          <div class='muted'>Entscheidungsbezug (Pflicht)</div>
          <div><input type='text' name='entscheidung'
               placeholder='Welche Entscheidung soll diese Recherche stützen?'></div>
          <div class='muted'>Max. Firmen</div>
          <div><input type='text' name='limit' value='10'></div>
        </div>
        <p><button>Auftrag anlegen</button></p>
      </form></div>
    <div class='card'><h2>Angelegte Aufträge</h2><ul>{''.join(items) or '<li>keine</li>'}</ul>
      <p class='muted'>Ausführen: In Claude Code im Repo-Ordner eingeben:<br>
      <code>Führe prompts/wettbewerbsanalyse-agent_v1.1.md mit dem Auftrag
      data/jobs/&lt;datei&gt;.json aus.</code><br>
      Der Agent legt records.json an und spielt sie über
      <code>py -m cintel ingest</code> ein; das Ergebnis erscheint hier unter
      „Letzte Ingest-Läufe“.</p></div>"""
    return page("Suchaufträge", body, staged)


@app.post("/auftraege")
def auftrag_anlegen(modus: str = Form(...), ziel: str = Form(""),
                    entscheidung: str = Form(""), limit: str = Form("10")) -> RedirectResponse:
    JOBS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now().strftime("%Y%m%d_%H%M%S")
    payload = {"modus": modus, "ziel": ziel, "entscheidung": entscheidung,
               "limit": limit, "angelegt": stamp, "status": "offen"}
    (JOBS_DIR / f"auftrag_{stamp}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return RedirectResponse("/auftraege", status_code=303)


@app.get("/report/{run_name}", response_class=HTMLResponse)
def report(run_name: str) -> HTMLResponse:
    staged = sum(len(v) for v in load_changes()["edits"].values())
    path = OUT_DIR / unquote(run_name) / "report.md"
    if not path.exists() or ".." in run_name:
        return page("Report", "<div class='card warn'>Report nicht gefunden.</div>", staged)
    text = esc(path.read_text(encoding="utf-8"))
    return page(run_name, f"<div class='card'><pre style='white-space:pre-wrap'>{text}</pre></div>",
                staged)


if __name__ == "__main__":
    assert SHEET_MASTER  # Import-Selbsttest
    uvicorn.run(app, host="127.0.0.1", port=8742)
