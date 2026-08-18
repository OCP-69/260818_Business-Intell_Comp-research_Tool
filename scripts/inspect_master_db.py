"""
Diagnose einer Master-DB-Datei - Struktur, Fuellgrade, Vokabular.

    py scripts/inspect_master_db.py "<pfad zur xlsx>"

Nuetzlich, um eine neue Version zu pruefen, bevor sie weiterverwendet wird,
und um zu sehen, welche Spalten der naechste Anreicherungslauf angehen sollte.
"""

from __future__ import annotations

import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from cintel.masterdb import MasterDB
from cintel.schema import COLUMNS, Taxonomy, split_multi

REPO_ROOT = Path(__file__).resolve().parents[1]


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2

    db = MasterDB(argv[1])
    taxonomy = Taxonomy.load(REPO_ROOT / "config" / "taxonomy.yaml")
    blocks = db.blocks()
    total = len(db.records)

    print("=" * 74)
    print(f"  {Path(argv[1]).name}")
    print("=" * 74)
    print(f"  Datenzeilen          {total}")
    print(f"  Firmen (Bloecke)     {len(blocks)}")
    print(f"  mit Company-Zeile    {sum(1 for b in blocks if b.company_row)}")
    print(f"  mit Produktzeilen    {sum(1 for b in blocks if b.has_products)}")
    print(f"  naechste Company_ID  {db.next_company_id()}")

    print("-" * 74)
    print("  FUELLGRADE (aufsteigend - oben stehen die Anreicherungskandidaten)")
    rates: list[tuple[int, str]] = []
    for fieldname, header in COLUMNS:
        filled = sum(1 for r in db.records if str(r.values.get(fieldname) or "").strip())
        rates.append((round(filled * 100 / total) if total else 0, header))
    for percent, header in sorted(rates):
        bar = "#" * (percent // 4)
        print(f"   {percent:3d}%  {bar:<25s} {header[:40]}")

    print("-" * 74)
    print("  VOKABULAR-ABWEICHUNGEN")
    for fieldname, canon, label in (
        ("key_categories", taxonomy.canon_key_category, "Key Category"),
        ("sub_category", taxonomy.canon_sub_category, "Sub Category"),
        ("competitor_tier", taxonomy.canon_tier, "Competitor_Tier"),
    ):
        unknown: Counter[str] = Counter()
        for record in db.records:
            raw = record.values.get(fieldname)
            values = split_multi(raw) if fieldname != "competitor_tier" else (
                [raw] if raw else []
            )
            for value in values:
                if canon(value) is None and str(value).strip():
                    unknown[str(value).strip()] += 1
        if unknown:
            print(f"   {label}: {len(unknown)} unbekannte Werte")
            for value, count in unknown.most_common(8):
                print(f"      {count:4d}  {value[:58]}")
        else:
            print(f"   {label}: alles im Vokabular")

    print("-" * 74)
    print("  GROESSTE FIRMEN NACH PRODUKTZAHL")
    for block in sorted(blocks, key=lambda b: -len(b.product_rows))[:10]:
        print(f"   {len(block.product_rows):3d} Produkte  "
              f"id={block.company_id!s:<7s} {block.company[:40]}")
    print("=" * 74)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
