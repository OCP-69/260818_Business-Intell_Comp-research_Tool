"""
cintel v3 - deterministischer Kern fuer die Competitive Intel Master DB.

Die Recherche (Discovery, Quellenlektuere, Extraktion, Bewertung) macht ein
Claude-Code-Agent (siehe prompts/wettbewerbsanalyse-agent_v1.1.md). Dieses
Paket ist die einzige Schreibschnittstelle zur Master-DB:

    py -m cintel ingest records.json --db <master.xlsx>   Recherche einspielen
    py -m cintel validate --db <master.xlsx>              Bestand pruefen
    py -m cintel stats --db <master.xlsx>                 Fuellgrade/Kennzahlen
    py -m cintel doctor                                   Umgebung pruefen

`ingest` erwartet das in docs/RECORDS_FORMAT.md beschriebene JSON: je Firma
ein Objekt mit `company_row` (eine Zeile "Company information") und
`products` (je Produkt eine Zeile "Product"). Es gilt fill-only: bestehende,
kuratierte Werte werden NIE ueberschrieben.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import logging
import sys
from pathlib import Path
from typing import Any

from .dedupe import Deduper, first_url
from .masterdb import MasterDB, today_stamp
from .merge import MergePlan, Merger, write_run_artifacts
from .schema import (
    FIELD_NAMES,
    ROW_KIND_COMPANY,
    ROW_KIND_PRODUCT,
    Record,
    Taxonomy,
    join_multi,
    split_multi,
)
from .validate import validate_records

log = logging.getLogger("cintel")

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_TAXONOMY = REPO_ROOT / "config" / "taxonomy.yaml"
DEFAULT_OUT_DIR = REPO_ROOT / "data" / "outputs"

# Felder, deren Werte gegen die Taxonomie kanonisiert werden.
VOCAB_FIELDS = ("key_categories", "sub_category", "competitor_tier", "beachhead")


# --------------------------------------------------------------------------
# records.json -> Records
# --------------------------------------------------------------------------

class IngestError(RuntimeError):
    """records.json verletzt den Vertrag."""


def _clean_value(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, str):
        text = value.strip()
        # Die N/A-Politik von v3: Unbekanntes bleibt LEER. Der alte
        # Platzhalter hat in v2.x die Fuellgrade um 30-70 Punkte geschoent.
        if not text or text.lower().startswith("n/a"):
            return None
        return text
    return value


def _canonize(field: str, value: Any, taxonomy: Taxonomy, notes: list[str],
              company: str) -> Any:
    """Kanonisiert Vokabular-Felder; nicht aufloesbare Werte werden verworfen."""
    if value in (None, ""):
        return None
    if field == "competitor_tier":
        canonical = taxonomy.canon_tier(value)
        if canonical is None:
            notes.append(f"{company}: Tier {value!r} unbekannt - verworfen")
        return canonical
    if field == "beachhead":
        parts = [p for v in split_multi(value) for p in str(v).split(",")]
        kept = [taxonomy.canon_beachhead(p.strip()) for p in parts if p.strip()]
        kept = [k for k in kept if k]
        if not kept:
            notes.append(f"{company}: Beachhead {value!r} unbekannt - verworfen")
            return None
        return join_multi("beachhead", kept)
    canon = (taxonomy.canon_key_category if field == "key_categories"
             else taxonomy.canon_sub_category)
    kept = []
    for part in split_multi(value):
        resolved = canon(part)
        if resolved:
            kept.append(resolved)
        else:
            notes.append(f"{company}: {field} {part!r} unbekannt - verworfen")
    return join_multi(field, kept) if kept else None


def _build_record(payload: dict, *, company: str, kind: str,
                  confidence: float, source: str, taxonomy: Taxonomy,
                  notes: list[str]) -> Record:
    values: dict[str, Any] = {}
    for name, raw in payload.items():
        if name not in FIELD_NAMES:
            notes.append(f"{company}: unbekanntes Feld {name!r} ignoriert")
            continue
        value = _clean_value(raw)
        if name in VOCAB_FIELDS:
            value = _canonize(name, value, taxonomy, notes, company)
        if value is not None:
            values[name] = value
    values["company"] = company
    values["row_kind"] = kind
    values.setdefault("last_update", today_stamp())
    if kind == ROW_KIND_COMPANY:
        values.pop("product_name", None)
    record = Record(values=values)
    record.confidence["*"] = confidence
    if source:
        record.sources["*"] = source
    return record


def load_records_json(path: Path, taxonomy: Taxonomy) -> tuple[list[dict], list[str]]:
    """
    Liest records.json und liefert je Firma:
        {"company": str, "records": [Record, ...], "sources": [...],
         "confidence": float}
    plus eine Liste von Kanonisierungs-Notizen fuer den Report.
    """
    try:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
    except json.JSONDecodeError as exc:
        raise IngestError(f"records.json ist kein gueltiges JSON: {exc}") from exc

    companies = data.get("companies")
    if not isinstance(companies, list) or not companies:
        raise IngestError("records.json: 'companies' fehlt oder ist leer.")

    notes: list[str] = []
    out: list[dict] = []
    for entry in companies:
        company = str(entry.get("company") or "").strip()
        if not company:
            raise IngestError("Firma ohne 'company'-Namen in records.json.")
        confidence = float(entry.get("confidence", 0.0))
        sources = [s for s in (entry.get("sources") or []) if s]
        primary_source = sources[0] if sources else ""

        records = [_build_record(
            entry.get("company_row") or {}, company=company,
            kind=ROW_KIND_COMPANY, confidence=confidence,
            source=primary_source, taxonomy=taxonomy, notes=notes,
        )]
        for product in entry.get("products") or []:
            name = str(product.get("product_name") or "").strip()
            if not name:
                notes.append(f"{company}: Produkt ohne product_name uebersprungen")
                continue
            records.append(_build_record(
                product, company=company, kind=ROW_KIND_PRODUCT,
                confidence=float(product.get("confidence", confidence)),
                source=primary_source, taxonomy=taxonomy, notes=notes,
            ))
        out.append({"company": company, "records": records,
                    "sources": sources, "confidence": confidence})
    return out, notes


# --------------------------------------------------------------------------
# Subcommands
# --------------------------------------------------------------------------

def cmd_ingest(args: argparse.Namespace) -> int:
    taxonomy = Taxonomy.load(args.taxonomy)
    db = MasterDB(args.db)
    companies, notes = load_records_json(Path(args.records), taxonomy)

    # Neue Zeilen strikt validieren, BEVOR irgendetwas geschrieben wird.
    all_new = [r for c in companies for r in c["records"]]
    report = validate_records(all_new, taxonomy, strict=True)
    if report.errors:
        print(report.render())
        print("\nABBRUCH: records.json enthaelt Fehler - nichts geschrieben.")
        return 2

    deduper = Deduper([(name, url) for name, url in db.iter_urls()]
                      + [(n, "") for n in db.company_names()])
    blocks_by_name = {b.company.strip().lower(): b for b in db.blocks() if b.company}

    merger = Merger(db, min_confidence=args.min_confidence)
    plan = MergePlan()
    sources_rows: list[dict[str, Any]] = []
    new_companies: list[str] = []
    enriched: list[str] = []

    for entry in companies:
        company = entry["company"]
        url = ""
        for record in entry["records"]:
            url = first_url(str(record.values.get("url") or "")) or url
        match = deduper.check(company, url)
        for source in entry["sources"]:
            sources_rows.append({"company": company, "source": source,
                                 "confidence": entry["confidence"],
                                 "date": today_stamp()})
        if match.is_duplicate:
            block = blocks_by_name.get(match.matched_company.strip().lower())
            if block is None:
                plan.skipped.append((company, "Dublette ohne aufloesbaren Block"))
                continue
            merger.enrich_existing(block, entry["records"], plan)
            enriched.append(f"{company} -> {match.matched_company} ({match.reason})")
        else:
            if entry["confidence"] < args.min_confidence:
                plan.skipped.append(
                    (company, f"Confidence {entry['confidence']:.2f} unter Schwelle"))
                continue
            plan.new_rows.extend(merger.add_new_company(entry["records"]))
            new_companies.append(company)

    print(f"Plan: {plan.summary()}")
    if notes:
        print(f"  {len(notes)} Kanonisierungs-Hinweise (Details im Report)")
    if not plan.new_rows and not plan.updates:
        print("Nichts zu tun - keine neuen Zeilen, keine Ergaenzungen.")
        return 0
    if args.dry_run:
        for line in enriched:
            print("  ergaenzt:", line)
        for name in new_companies:
            print("  neu:     ", name)
        print("Dry-Run: nichts geschrieben.")
        return 0

    before = len(db.records)
    target = merger.apply(plan, args.out_dir, version=args.version)

    # Plausibilitaets-Gate: Zeilenbilanz der geschriebenen Datei pruefen.
    written = MasterDB(target)
    expected = before + len(plan.new_rows)
    if len(written.records) != expected:
        print(f"GATE VERLETZT: {len(written.records)} Zeilen statt {expected} "
              f"in {target.name} - Datei pruefen!")
        return 3

    run_dir = Path(args.out_dir) / f"run_{dt.datetime.now():%Y%m%d_%H%M%S}"
    report_text = "\n".join(
        ["# Ingest-Report", "",
         f"- Eingespielt: `{args.records}`",
         f"- Ziel: `{target.name}` ({expected} Zeilen)",
         f"- Neue Firmen: {', '.join(new_companies) or 'keine'}",
         f"- Ergaenzte Firmen: {len(enriched)}",
         *[f"  - {line}" for line in enriched],
         f"- Uebersprungen: {len(plan.skipped)}",
         *[f"  - {who}: {why}" for who, why in plan.skipped[:50]],
         f"- Kanonisierungs-Hinweise: {len(notes)}",
         *[f"  - {note}" for note in notes[:50]],
         ])
    write_run_artifacts(run_dir, plan=plan, rejected=[],
                        sources=sources_rows, report_text=report_text)
    print(f"Geschrieben: {target}")
    print(f"Artefakte:   {run_dir}")
    return 0


def cmd_validate(args: argparse.Namespace) -> int:
    taxonomy = Taxonomy.load(args.taxonomy)
    db = MasterDB(args.db)
    report = validate_records(db.records, taxonomy, strict=args.strict)
    print(report.render())
    return 1 if report.errors else 0


def cmd_stats(args: argparse.Namespace) -> int:
    db = MasterDB(args.db)
    blocks = db.blocks()
    tiers: dict[str, int] = {}
    for record in db.records:
        tier = str(record.values.get("competitor_tier") or "").strip()
        if tier:
            tiers[tier] = tiers.get(tier, 0) + 1
    print(f"Datei:        {db.path.name}")
    print(f"Zeilen:       {len(db.records)}")
    print(f"Firmen:       {len(blocks)}")
    print(f"Produktzeilen:{sum(len(b.product_rows) for b in blocks):5d}")
    for tier in sorted(tiers):
        print(f"  {tier:24s} {tiers[tier]}")
    print("Fuellgrad je Feld (echt, 'N/A...' zaehlt als leer):")
    total = len(db.records)
    for name in FIELD_NAMES:
        filled = sum(
            1 for r in db.records
            if _clean_value(r.values.get(name)) is not None
        )
        print(f"  {name:18s} {100 * filled / total:5.1f} %")
    return 0


def cmd_doctor(_args: argparse.Namespace) -> int:
    ok = True
    for label, path in [("Taxonomie", DEFAULT_TAXONOMY),
                        ("Output-Verzeichnis", DEFAULT_OUT_DIR)]:
        exists = path.exists()
        print(f"  {'ok ' if exists else 'FEHLT'} {label}: {path}")
        ok = ok and (exists or label == "Output-Verzeichnis")
    try:
        import fastapi  # noqa: F401
        import uvicorn  # noqa: F401
        print("  ok  UI-Abhaengigkeiten (fastapi, uvicorn)")
    except ImportError:
        print("  --  UI-Abhaengigkeiten fehlen (pip install -r requirements.txt)")
    return 0 if ok else 1


# --------------------------------------------------------------------------
# Argumente
# --------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cintel",
        description="Deterministischer Kern der Competitive Intel Master DB.",
    )
    parser.add_argument("--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="records.json in die Master-DB einspielen")
    p_ingest.add_argument("records", help="Pfad zur records.json des Agenten")
    p_ingest.add_argument("--db", required=True, help="aktuelle Master-DB (xlsx)")
    p_ingest.add_argument("--out-dir", default=str(DEFAULT_OUT_DIR))
    p_ingest.add_argument("--taxonomy", default=str(DEFAULT_TAXONOMY))
    p_ingest.add_argument("--min-confidence", type=float, default=0.35)
    p_ingest.add_argument("--version", default=None, help="Zielversion, z.B. 2.5")
    p_ingest.add_argument("--dry-run", action="store_true")
    p_ingest.set_defaults(func=cmd_ingest)

    p_validate = sub.add_parser("validate", help="Bestand gegen den Vertrag pruefen")
    p_validate.add_argument("--db", required=True)
    p_validate.add_argument("--taxonomy", default=str(DEFAULT_TAXONOMY))
    p_validate.add_argument("--strict", action="store_true")
    p_validate.set_defaults(func=cmd_validate)

    p_stats = sub.add_parser("stats", help="Kennzahlen und echte Fuellgrade")
    p_stats.add_argument("--db", required=True)
    p_stats.set_defaults(func=cmd_stats)

    p_doctor = sub.add_parser("doctor", help="Umgebung pruefen")
    p_doctor.set_defaults(func=cmd_doctor)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )
    try:
        return int(args.func(args))
    except (IngestError, FileNotFoundError) as exc:
        print(f"FEHLER: {exc}")
        return 2


if __name__ == "__main__":
    sys.exit(main())
