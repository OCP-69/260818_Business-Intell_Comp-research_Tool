"""
Bestandsreparatur - getrennt vom Anreicherungslauf.

Behebt genau die Mangelklassen, die `cintel validate` im Bestand findet:

  encoding_broken     Mojibake aus einer UTF-8/cp1252-Fehlinterpretation
  tier_non_canonical  "Tier 2 - Nachbar" -> "Tier 2 – Nachbar"
  company_id_not_int  42.0 -> 42
  url_not_a_url       Seitentitel/Ort/Jahr im URL-Feld -> geleert und protokolliert
  url_polluted        "https://x.io/ | Berlin" -> "https://x.io"
  row_kind_invalid    "Platform" -> "Product"
  key/sub_category    Drift-Varianten -> kanonischer Wert

Der Lauf schreibt IMMER in eine neue Datei und liefert einen
Vorher/Nachher-Bericht. Die Eingangsdatei bleibt unangetastet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .dedupe import first_url
from .masterdb import MasterDB
from .schema import (
    ROW_KIND_COMPANY,
    ROW_KIND_PRODUCT,
    Record,
    Taxonomy,
    join_multi,
    split_multi,
)

log = logging.getLogger(__name__)

# Haeufige Mojibake-Sequenzen aus "UTF-8 als cp1252 gelesen".
MOJIBAKE_MAP = {
    "Ã¤": "ä", "Ã¶": "ö", "Ã¼": "ü", "Ã„": "Ä", "Ã–": "Ö", "Ãœ": "Ü",
    "ÃŸ": "ß", "Ã©": "é", "Ã¨": "è", "Ã¡": "á", "Ã ": "à", "Ã­": "í",
    "Ã³": "ó", "Ãº": "ú", "Ã±": "ñ", "Ã§": "ç",
    "â€“": "–", "â€”": "—", "â€™": "'", "â€œ": '"', "â€\x9d": '"',
    "â€¢": "•", "Â ": " ", "Â": "",
}


@dataclass
class RepairChange:
    row: int
    company: str
    field_name: str
    before: Any
    after: Any
    rule: str


@dataclass
class RepairReport:
    changes: list[RepairChange] = field(default_factory=list)
    stats: dict[str, int] = field(default_factory=dict)

    def add(self, row: int, company: str, field_name: str,
            before: Any, after: Any, rule: str) -> None:
        self.changes.append(RepairChange(row, company, field_name, before, after, rule))
        self.stats[rule] = self.stats.get(rule, 0) + 1

    def render(self, limit: int = 30) -> str:
        lines = ["=" * 72, "REPARATURBERICHT", "=" * 72]
        if not self.changes:
            lines.append("  Nichts zu reparieren.")
            lines.append("=" * 72)
            return "\n".join(lines)
        for rule, count in sorted(self.stats.items(), key=lambda kv: -kv[1]):
            lines.append(f"  {count:5d}  {rule}")
        lines.append("-" * 72)
        for change in self.changes[:limit]:
            before = str(change.before)[:38]
            after = str(change.after)[:38]
            lines.append(
                f"  Z{change.row:<5d} {change.company[:18]:18s} "
                f"{change.field_name:16s} {before!r} -> {after!r}"
            )
        if len(self.changes) > limit:
            lines.append(f"  ... und {len(self.changes) - limit} weitere")
        lines.append("=" * 72)
        lines.append(f"  {len(self.changes)} Aenderungen in "
                     f"{len({c.row for c in self.changes})} Zeilen")
        return "\n".join(lines)

    def to_csv_rows(self) -> list[dict[str, Any]]:
        return [
            {
                "row": c.row, "company": c.company, "field": c.field_name,
                "before": c.before, "after": c.after, "rule": c.rule,
            }
            for c in self.changes
        ]


def fix_mojibake(text: str) -> str:
    """
    Repariert falsch dekodierte Umlaute.

    Bevorzugt der saubere Weg ueber latin-1/utf-8-Roundtrip; schlaegt der
    fehl, greift die Ersetzungstabelle. Das U+FFFD-Ersatzzeichen ist
    verlustbehaftet - dort ist die Information weg und wird nur bei
    bekannten Mustern (Tier-Spalte) rekonstruiert.
    """
    if not text or not isinstance(text, str):
        return text
    if any(seq in text for seq in ("Ã", "â€", "Â")):
        try:
            repaired = text.encode("cp1252", errors="strict").decode("utf-8", errors="strict")
            if "�" not in repaired:
                return repaired
        except (UnicodeEncodeError, UnicodeDecodeError):
            pass
        for broken, fixed in MOJIBAKE_MAP.items():
            text = text.replace(broken, fixed)
    return text


def repair_records(
    records: list[Record], taxonomy: Taxonomy
) -> tuple[dict[int, Record], RepairReport]:
    """
    Erzeugt die Menge zu aktualisierender Zeilen.

    Returns:
        ({0-basierter Index: bereinigter Record}, Bericht)
    """
    report = RepairReport()
    updates: dict[int, Record] = {}

    for index, record in enumerate(records):
        excel_row = index + 2
        company = str(record.values.get("company") or "").strip()
        changed = False
        new_values = dict(record.values)

        # 1. Kodierung ueber alle Textfelder
        for name, value in list(new_values.items()):
            if isinstance(value, str):
                fixed = fix_mojibake(value)
                if fixed != value:
                    report.add(excel_row, company, name, value, fixed, "encoding_fixed")
                    new_values[name] = fixed
                    changed = True

        # 2. Company_ID als Ganzzahl
        # Auf den TYP pruefen, nicht auf Gleichheit: in Python gilt 1.0 == 1,
        # deshalb wuerde ein Wertvergleich Excel-Floats nie erkennen. Die
        # Master-DB liefert Company_ID als 1.0, 42.0 usw.
        company_id = new_values.get("company_id")
        if isinstance(company_id, float):
            as_int = int(company_id)
            report.add(excel_row, company, "company_id", company_id, as_int,
                       "company_id_to_int")
            new_values["company_id"] = as_int
            changed = True

        # 3. Tier kanonisieren (auch aus dem U+FFFD-Fall rekonstruierbar)
        tier = new_values.get("competitor_tier")
        if tier not in (None, ""):
            canonical = taxonomy.canon_tier(tier)
            if canonical and str(tier).strip() != canonical:
                report.add(excel_row, company, "competitor_tier", tier, canonical,
                           "tier_canonicalised")
                new_values["competitor_tier"] = canonical
                changed = True

        # 4. Zeilenart
        kind = str(new_values.get("row_kind") or "").strip()
        if kind and kind not in (ROW_KIND_COMPANY, ROW_KIND_PRODUCT):
            target = (
                ROW_KIND_COMPANY
                if "compan" in kind.lower()
                else ROW_KIND_PRODUCT
            )
            report.add(excel_row, company, "row_kind", kind, target, "row_kind_fixed")
            new_values["row_kind"] = target
            changed = True

        # 5. URL-Feld saeubern
        raw_url = str(new_values.get("url") or "").strip()
        if raw_url:
            extracted = first_url(raw_url)
            if not extracted:
                # Kein Verlust: der Fremdinhalt wandert in die Bemerkungen.
                report.add(excel_row, company, "url", raw_url, "", "url_cleared")
                note = str(new_values.get("remarks") or "").strip()
                marker = f"[aus URL-Feld uebernommen] {raw_url}"
                new_values["remarks"] = f"{note}\n{marker}".strip() if note else marker
                new_values["url"] = None
                changed = True
            elif extracted != raw_url.rstrip("/"):
                report.add(excel_row, company, "url", raw_url, extracted, "url_cleaned")
                new_values["url"] = extracted
                changed = True

        # 6. Vokabular-Drift
        for name, canon in (
            ("key_categories", taxonomy.canon_key_category),
            ("sub_category", taxonomy.canon_sub_category),
        ):
            raw = new_values.get(name)
            parts = split_multi(raw)
            if not parts:
                continue
            mapped = [c for c in (canon(p) for p in parts) if c]
            joined = join_multi(name, mapped) or None
            if joined != (str(raw).strip() if raw else None) and mapped != parts:
                report.add(excel_row, company, name, raw, joined, "vocabulary_mapped")
                new_values[name] = joined
                changed = True

        if changed:
            updates[index] = Record(values=new_values)

    log.info("Reparatur: %d Zeilen betroffen, %d Aenderungen",
             len(updates), len(report.changes))
    return updates, report


def run_repair(
    master_path: str | Path,
    taxonomy: Taxonomy,
    out_dir: str | Path,
    *,
    version: str | None = None,
    dry_run: bool = False,
) -> tuple[Path | None, RepairReport]:
    """Fuehrt die Reparatur aus und schreibt eine neue Version."""
    db = MasterDB(master_path)
    updates, report = repair_records(db.records, taxonomy)
    if dry_run or not updates:
        return None, report
    target = db.write_new_version([], out_dir, version=version, updated=updates)
    return target, report
