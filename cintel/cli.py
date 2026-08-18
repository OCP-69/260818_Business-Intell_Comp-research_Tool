"""
Kommandozeile.

    py -m cintel doctor
    py -m cintel validate --master <pfad.xlsx>
    py -m cintel repair   --master <pfad.xlsx>
    py -m cintel run      --master <pfad.xlsx> --targets config/targets.yaml
"""

from __future__ import annotations

import argparse
import datetime as dt
import logging
import shutil
import sys
from pathlib import Path
from typing import Any

import yaml

from .crawl import Crawler
from .dedupe import Deduper, first_url
from .discover import discover_new, select_gap_companies
from .extract import Extractor
from .llm import LLMError, UsageLedger, claude_available, codex_available
from .masterdb import MasterDB
from .merge import MergePlan, Merger, write_run_artifacts
from .repair import run_repair
from .schema import Taxonomy
from .validate import validate_records

log = logging.getLogger("cintel")

DEFAULT_TAXONOMY = "config/taxonomy.yaml"
DEFAULT_TARGETS = "config/targets.yaml"
DEFAULT_OUT = "data/outputs"
DEFAULT_CACHE = "data/cache"


# --------------------------------------------------------------------------

def cmd_doctor(args: argparse.Namespace) -> int:
    """Prueft die Betriebsvoraussetzungen."""
    print("=" * 62)
    print("CINTEL DOCTOR")
    print("=" * 62)
    ok = True

    if claude_available():
        print(f"  [ok]   claude-CLI gefunden: {shutil.which('claude')}")
    else:
        print("  [FEHL] claude-CLI nicht gefunden - ohne sie laeuft keine Extraktion.")
        ok = False

    if codex_available():
        print("  [ok]   codex-CLI gefunden (Cross-Check moeglich)")
    else:
        print("  [--]   codex-CLI nicht gefunden (Cross-Check deaktiviert)")

    for label, path in (("Taxonomie", args.taxonomy), ("Ziele", args.targets)):
        exists = Path(path).exists()
        print(f"  {'[ok]  ' if exists else '[FEHL]'} {label}: {path}")
        ok = ok and exists

    if args.master:
        try:
            db = MasterDB(args.master)
            print(f"  [ok]   Master-DB: {len(db)} Zeilen, "
                  f"{len(db.blocks())} Firmen, naechste ID {db.next_company_id()}")
        except Exception as exc:
            print(f"  [FEHL] Master-DB: {exc}")
            ok = False
    else:
        print("  [--]   Master-DB: nicht angegeben (--master)")

    print("=" * 62)
    print("  Bereit." if ok else "  Es fehlen Voraussetzungen (siehe oben).")
    return 0 if ok else 1


def cmd_validate(args: argparse.Namespace) -> int:
    db = MasterDB(args.master)
    taxonomy = Taxonomy.load(args.taxonomy)
    report = validate_records(db.records, taxonomy, strict=args.strict)
    print(report.render(limit=args.limit))
    if args.out:
        Path(args.out).write_text(report.render(limit=10_000), encoding="utf-8")
        print(f"\nBericht geschrieben: {args.out}")
    return 1 if (args.fail_on_error and report.errors) else 0


def cmd_repair(args: argparse.Namespace) -> int:
    taxonomy = Taxonomy.load(args.taxonomy)
    target, report = run_repair(
        args.master, taxonomy, args.out_dir,
        version=args.version, dry_run=args.dry_run,
    )
    print(report.render(limit=args.limit))
    if args.dry_run:
        print("\n(Probelauf - es wurde nichts geschrieben.)")
    elif target:
        print(f"\nRepariert geschrieben: {target}")
        run_dir = Path(args.out_dir) / f"repair_{_stamp()}"
        run_dir.mkdir(parents=True, exist_ok=True)
        import csv
        rows = report.to_csv_rows()
        if rows:
            with (run_dir / "changes.csv").open("w", newline="", encoding="utf-8-sig") as fh:
                writer = csv.DictWriter(fh, fieldnames=list(rows[0]))
                writer.writeheader()
                writer.writerows(rows)
        (run_dir / "report.txt").write_text(report.render(limit=100_000), encoding="utf-8")
        print(f"Protokoll: {run_dir}")
    else:
        print("\nNichts zu reparieren.")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """Der vollstaendige Durchlauf."""
    config = yaml.safe_load(Path(args.targets).read_text(encoding="utf-8"))
    taxonomy = Taxonomy.load(args.taxonomy)
    limits = config.get("limits", {}) or {}
    llm_config = config.get("llm", {}) or {}
    mode = args.mode or config.get("mode", "gaps")

    max_companies = args.limit or int(limits.get("max_companies_per_run", 25))
    ledger = UsageLedger()
    run_dir = Path(args.out_dir) / f"run_{_stamp()}"

    db = MasterDB(args.master)
    print(f"Master-DB: {len(db)} Zeilen, {len(db.blocks())} Firmen "
          f"({Path(args.master).name})")

    crawler = Crawler(
        cache_dir=args.cache_dir,
        delay=float(limits.get("crawl_delay_seconds", 1.5)),
        timeout=int(limits.get("request_timeout_seconds", 20)),
        max_pages=int(limits.get("max_pages_per_company", 12)),
        offline=args.offline,
    )
    extractor = Extractor(
        taxonomy,
        model=llm_config.get("model", "sonnet"),
        max_products=int(limits.get("max_products_per_company", 25)),
        timeout=int(llm_config.get("timeout_seconds", 600)),
        max_retries=int(llm_config.get("max_retries", 2)),
        ledger=ledger,
        cross_check=args.cross_check == "codex",
    )
    merger = Merger(db)
    plan = MergePlan()
    rejected: list[dict[str, Any]] = []
    sources: list[dict[str, Any]] = []

    # -- Stufe 1+2: Arbeitsliste -----------------------------------------
    worklist: list[tuple[str, str, Any]] = []  # (Firma, URL, Block oder None)

    if mode == "gaps":
        gaps_config = config.get("gaps", {}) or {}
        blocks = select_gap_companies(
            db,
            gaps_config.get("target_columns", []),
            limit=max_companies,
            require_url=bool(gaps_config.get("require_url", True)),
            tier_priority=gaps_config.get("tier_priority", []),
        )
        for block in blocks:
            url = first_url(block.urls[0]) if block.urls else ""
            if url:
                worklist.append((block.company, url, block))
            else:
                rejected.append({"company": block.company, "reason": "keine gueltige URL"})
        print(f"Modus 'gaps': {len(worklist)} Firmen zur Anreicherung ausgewaehlt.")

    elif mode == "new":
        known = [(r.values.get("company"), r.values.get("url")) for r in db.records]
        deduper = Deduper(known)
        candidates = discover_new(
            taxonomy, config.get("new", {}) or {}, db.company_names(),
            model=llm_config.get("discovery_model", "sonnet"),
            limit=max_companies * 2, ledger=ledger,
        )
        fresh, duplicates = deduper.partition(candidates)
        for duplicate in duplicates:
            rejected.append({
                "company": duplicate.get("name"), "url": duplicate.get("homepage"),
                "reason": f"Dublette: {duplicate.get('duplicate_reason')} "
                          f"-> {duplicate.get('duplicate_of')}",
            })
        for candidate in fresh[:max_companies]:
            worklist.append((candidate["name"], candidate["homepage"], None))
        print(f"Modus 'new': {len(candidates)} Kandidaten, {len(fresh)} neu, "
              f"{len(worklist)} werden bearbeitet.")
    else:
        print(f"Unbekannter Modus: {mode!r} (erlaubt: gaps, new)", file=sys.stderr)
        return 2

    # -- Stufe 3+4: Crawl und Extraktion ---------------------------------
    for position, (company, url, block) in enumerate(worklist, start=1):
        print(f"[{position}/{len(worklist)}] {company} - {url}")
        crawl = crawler.crawl_company(company, url)

        for page in crawl.pages:
            sources.append({
                "company": company, "url": page.url, "status": page.status,
                "title": page.title[:120], "error": page.error[:120],
            })

        if not crawl.verified:
            print(f"    abgelehnt: {crawl.reject_reason[:100]}")
            rejected.append({"company": company, "url": url,
                             "reason": crawl.reject_reason[:300]})
            continue

        if args.crawl_only:
            print(f"    {len(crawl.ok_pages)} Seiten im Cache (crawl-only)")
            continue

        try:
            records, meta = extractor.extract(crawl)
        except LLMError as exc:
            print(f"    Extraktion fehlgeschlagen: {exc}")
            rejected.append({"company": company, "url": url,
                             "reason": f"Extraktion: {exc}"[:300]})
            continue

        products = len(records) - 1
        print(f"    {len(crawl.ok_pages)} Seiten -> 1 Firmenzeile + {products} Produkte")

        cross = (meta or {}).get("cross_check") or {}
        for name, values in (cross.get("disagreements") or {}).items():
            print(f"    ! Cross-Check weicht ab bei '{name}': "
                  f"claude={values['claude']!r} codex={values['codex']!r}")

        if block is not None:
            merger.enrich_existing(block, records, plan)
        else:
            plan.new_rows.extend(merger.add_new_company(records))

    # -- Stufe 5: Merge --------------------------------------------------
    print("\n" + plan.summary())
    print(ledger.summary())

    new_validation = validate_records(plan.new_rows, taxonomy, strict=True)
    if new_validation.errors:
        print(f"\nWARNUNG: {len(new_validation.errors)} Fehler in den neuen Zeilen:")
        for issue in new_validation.errors[:10]:
            print("   " + str(issue))

    report_text = _build_report(
        args, mode, plan, rejected, ledger, new_validation.render(limit=25)
    )

    if args.dry_run or (not plan.new_rows and not plan.updates):
        write_run_artifacts(run_dir, plan=plan, rejected=rejected,
                            sources=sources, report_text=report_text)
        print(f"\n(Probelauf - keine xlsx geschrieben.) Artefakte: {run_dir}")
        return 0

    target = merger.apply(plan, args.out_dir, version=args.version)
    write_run_artifacts(run_dir, plan=plan, rejected=rejected,
                        sources=sources, report_text=report_text)
    print(f"\nNeue Version : {target}")
    print(f"Lauf-Ordner  : {run_dir}")
    return 0


# --------------------------------------------------------------------------

def _stamp() -> str:
    return dt.datetime.now().strftime("%Y%m%d_%H%M%S")


def _build_report(
    args: argparse.Namespace, mode: str, plan: MergePlan,
    rejected: list[dict[str, Any]], ledger: UsageLedger, validation: str,
) -> str:
    lines = [
        f"# cintel-Lauf {_stamp()}",
        "",
        f"- Modus: `{mode}`",
        f"- Master-DB: `{args.master}`",
        f"- {plan.summary()}",
        f"- {ledger.summary()}",
        "",
        "## Abgelehnt",
        "",
    ]
    if rejected:
        lines += ["| Firma | Grund |", "|---|---|"]
        for entry in rejected[:100]:
            reason = str(entry.get("reason", "")).replace("|", "/")[:140]
            lines.append(f"| {entry.get('company', '')} | {reason} |")
    else:
        lines.append("Keine Ablehnungen.")

    lines += ["", "## Gefuellte Felder", ""]
    if plan.filled_fields:
        lines += ["| Firma | Produkt | Feld |", "|---|---|---|"]
        for company, product, name in plan.filled_fields[:200]:
            lines.append(f"| {company} | {product} | {name} |")
    else:
        lines.append("Keine.")

    lines += ["", "## Validierung der neuen Zeilen", "", "```", validation, "```"]
    return "\n".join(lines)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="cintel",
        description="Competitive Intelligence Retrieval fuer die LFL Master DB",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Debug-Ausgaben")
    parser.add_argument("--taxonomy", default=DEFAULT_TAXONOMY)
    sub = parser.add_subparsers(dest="command", required=True)

    doctor = sub.add_parser("doctor", help="Voraussetzungen pruefen")
    doctor.add_argument("--master", default=None)
    doctor.add_argument("--targets", default=DEFAULT_TARGETS)
    doctor.set_defaults(func=cmd_doctor)

    validate = sub.add_parser("validate", help="Datenqualitaet pruefen")
    validate.add_argument("--master", required=True)
    validate.add_argument("--strict", action="store_true",
                          help="Vokabular-Abweichungen als Fehler werten")
    validate.add_argument("--limit", type=int, default=40)
    validate.add_argument("--out", default=None, help="Bericht in Datei schreiben")
    validate.add_argument("--fail-on-error", action="store_true",
                          help="Exit-Code 1 bei Fehlern (fuer CI)")
    validate.set_defaults(func=cmd_validate)

    repair = sub.add_parser("repair", help="Bestandsdaten bereinigen")
    repair.add_argument("--master", required=True)
    repair.add_argument("--out-dir", default=DEFAULT_OUT)
    repair.add_argument("--version", default=None, help="Zielversion, z.B. 2.3")
    repair.add_argument("--limit", type=int, default=30)
    repair.add_argument("--dry-run", action="store_true")
    repair.set_defaults(func=cmd_repair)

    run = sub.add_parser("run", help="Vollstaendiger Recherchelauf")
    run.add_argument("--master", required=True)
    run.add_argument("--targets", default=DEFAULT_TARGETS)
    run.add_argument("--out-dir", default=DEFAULT_OUT)
    run.add_argument("--cache-dir", default=DEFAULT_CACHE)
    run.add_argument("--mode", choices=["gaps", "new"], default=None)
    run.add_argument("--limit", type=int, default=None,
                     help="Maximale Firmenzahl (ueberschreibt targets.yaml)")
    run.add_argument("--version", default=None, help="Zielversion, z.B. 2.3")
    run.add_argument("--dry-run", action="store_true",
                     help="Alles rechnen, aber keine xlsx schreiben")
    run.add_argument("--offline", action="store_true",
                     help="Nur den Cache nutzen, keine Netzanfragen")
    run.add_argument("--crawl-only", action="store_true",
                     help="Nur crawlen und cachen, keine LLM-Aufrufe")
    run.add_argument("--cross-check", choices=["none", "codex"], default="none")
    run.set_defaults(func=cmd_run)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)-7s %(name)s: %(message)s",
    )
    try:
        return int(args.func(args))
    except KeyboardInterrupt:
        print("\nAbgebrochen.", file=sys.stderr)
        return 130
    except (FileNotFoundError, LLMError) as exc:
        print(f"Fehler: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
