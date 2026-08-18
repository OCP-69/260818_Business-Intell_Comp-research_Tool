"""
Stufe 5 - Zusammenfuehren und versioniert speichern.

Zwei Faelle:
  neue Firma       -> naechste freie Company_ID, Company-Zeile + Produktzeilen anhaengen
  bestehende Firma -> nur LEERE Felder auffuellen, vorhandene Werte bleiben stehen

Der zweite Fall ist entscheidend fuer den Gaps-Lauf: kuratierte Bewertungen
(Tier, Beachhead, Weaknesses) sind Handarbeit und duerfen nicht von einer
Maschine ueberschrieben werden.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .masterdb import CompanyBlock, MasterDB, today_stamp
from .schema import ROW_KIND_PRODUCT, Record

log = logging.getLogger(__name__)


@dataclass
class MergePlan:
    """Was der Merge tun wird - erst pruefbar, dann ausfuehrbar."""

    new_rows: list[Record] = field(default_factory=list)
    updates: dict[int, Record] = field(default_factory=dict)
    filled_fields: list[tuple[str, str, str]] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)

    def summary(self) -> str:
        return (
            f"{len(self.new_rows)} neue Zeilen, "
            f"{len(self.updates)} bestehende Zeilen ergaenzt, "
            f"{len(self.filled_fields)} Felder gefuellt, "
            f"{len(self.skipped)} uebersprungen"
        )


class Merger:
    def __init__(self, db: MasterDB, *, min_confidence: float = 0.35) -> None:
        self.db = db
        self.min_confidence = min_confidence
        self._next_id = db.next_company_id()
        self._blocks_by_name = {
            b.company.strip().lower(): b for b in db.blocks() if b.company
        }
        self._row_index = {id(r): i for i, r in enumerate(db.records)}

    # -- neue Firmen -------------------------------------------------------

    def add_new_company(self, records: list[Record]) -> list[Record]:
        """Vergibt eine Company_ID und bereitet die Zeilen zum Anhaengen vor."""
        if not records:
            return []
        company_id = self._next_id
        self._next_id += 1
        prepared: list[Record] = []
        for record in records:
            values = dict(record.values)
            values["company_id"] = company_id
            values.setdefault("last_update", today_stamp())
            prepared.append(Record(values=values, sources=record.sources,
                                   confidence=record.confidence))
        return prepared

    # -- bestehende Firmen -------------------------------------------------

    def enrich_existing(
        self, block: CompanyBlock, extracted: list[Record], plan: MergePlan
    ) -> None:
        """
        Fuellt Luecken einer bekannten Firma.

        - Company-Zeile: nur leere Felder uebernehmen.
        - Produkte: nur Produkte anhaengen, deren Name noch nicht existiert.
        """
        if not extracted:
            return
        company_id = block.company_id
        new_company_row = extracted[0]

        # 1. Company-Zeile ergaenzen
        target = block.company_row
        if target is not None:
            index = self._row_index.get(id(target))
            if index is not None:
                merged = dict(target.values)
                touched = False
                for name, value in new_company_row.values.items():
                    if name in ("company_id", "row_kind", "company", "product_name"):
                        continue
                    if value in (None, ""):
                        continue
                    if str(merged.get(name) or "").strip():
                        plan.skipped.append((block.company, f"{name} (bereits gefuellt)"))
                        continue
                    confidence = new_company_row.confidence.get("*", 0.0)
                    if confidence < self.min_confidence:
                        plan.skipped.append(
                            (block.company, f"{name} (Confidence {confidence:.2f})")
                        )
                        continue
                    merged[name] = value
                    plan.filled_fields.append((block.company, "", name))
                    touched = True
                if touched:
                    merged["last_update"] = today_stamp()
                    plan.updates[index] = Record(values=merged)

        # 2. Neue Produktzeilen anhaengen
        existing = {
            str(r.values.get("product_name") or "").strip().lower()
            for r in block.product_rows
        }
        for record in extracted[1:]:
            name = str(record.values.get("product_name") or "").strip()
            if not name:
                continue
            if name.lower() in existing:
                plan.skipped.append((block.company, f"Produkt '{name}' existiert"))
                continue
            confidence = record.confidence.get("*", 0.0)
            if confidence < self.min_confidence:
                plan.skipped.append(
                    (block.company, f"Produkt '{name}' (Confidence {confidence:.2f})")
                )
                continue
            values = dict(record.values)
            values["company_id"] = company_id
            values["company"] = block.company
            values["row_kind"] = ROW_KIND_PRODUCT
            values["last_update"] = today_stamp()
            plan.new_rows.append(Record(values=values, sources=record.sources,
                                        confidence=record.confidence))
            plan.filled_fields.append((block.company, name, "product_name"))
            existing.add(name.lower())

    # -- Ausfuehrung -------------------------------------------------------

    def apply(
        self, plan: MergePlan, out_dir: str | Path, *, version: str | None = None
    ) -> Path:
        return self.db.write_new_version(
            plan.new_rows, out_dir, version=version, updated=plan.updates
        )


def write_run_artifacts(
    run_dir: str | Path,
    *,
    plan: MergePlan,
    rejected: list[dict[str, Any]],
    sources: list[dict[str, Any]],
    report_text: str,
) -> None:
    """Schreibt new_rows.csv, rejected.csv, sources.csv und report.md."""
    run_dir = Path(run_dir)
    run_dir.mkdir(parents=True, exist_ok=True)

    if plan.new_rows:
        fieldnames = sorted({k for r in plan.new_rows for k in r.values})
        with (run_dir / "new_rows.csv").open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            for record in plan.new_rows:
                writer.writerow({k: record.values.get(k) for k in fieldnames})

    if rejected:
        fieldnames = sorted({k for r in rejected for k in r})
        with (run_dir / "rejected.csv").open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(rejected)

    if sources:
        fieldnames = sorted({k for s in sources for k in s})
        with (run_dir / "sources.csv").open("w", newline="", encoding="utf-8-sig") as fh:
            writer = csv.DictWriter(fh, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(sources)

    (run_dir / "report.md").write_text(report_text, encoding="utf-8")
    (run_dir / "plan.json").write_text(
        json.dumps(
            {
                "new_rows": len(plan.new_rows),
                "updated_rows": len(plan.updates),
                "filled_fields": plan.filled_fields[:500],
                "skipped": plan.skipped[:500],
            },
            ensure_ascii=False, indent=2,
        ),
        encoding="utf-8",
    )
    log.info("Lauf-Artefakte geschrieben nach %s", run_dir)
