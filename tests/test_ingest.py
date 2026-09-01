"""Ende-zu-Ende: records.json -> ingest -> neue DB-Version."""

from __future__ import annotations

import json

from cintel.cli import main
from cintel.masterdb import MasterDB

RECORDS = {
    "run": {"mode": "test"},
    "companies": [
        {   # neue Firma -> Company-Zeile + Produktzeile anhaengen
            "company": "Tset",
            "confidence": 0.8,
            "sources": ["https://www.tset.com"],
            "company_row": {
                "url": "https://www.tset.com",
                "location": "Vienna, Austria",
                "stage": "Series A",
                "competitor_tier": "Tier 1 - Direkt",   # Bindestrich -> kanonisiert
                "beachhead": "Machinery",
                "key_categories": "7. Supply Chain & Operations",
            },
            "products": [{
                "product_name": "Tset Platform",
                "sub_category": "Cost Engineering & Value Analysis",
                "remarks": "Should-cost and TCO platform",
            }],
        },
        {   # bekannte Firma -> nur leere Felder fuellen
            "company": "Makersite",
            "confidence": 0.9,
            "sources": ["https://makersite.io"],
            "company_row": {
                "url": "https://makersite.io",
                "stage": "Series B",                    # leer -> wird gefuellt
                "location": "DARF NICHT LANDEN",        # gefuellt -> geschuetzt
            },
            "products": [],
        },
        {   # unter der Confidence-Schwelle -> wird uebersprungen
            "company": "Wackelkandidat GmbH",
            "confidence": 0.1,
            "sources": [],
            "company_row": {"url": "https://wackel.example"},
            "products": [],
        },
    ],
}


def test_ingest_end_to_end(mini_master, tmp_path):
    records_path = tmp_path / "records.json"
    records_path.write_text(json.dumps(RECORDS, ensure_ascii=False),
                            encoding="utf-8")
    out_dir = tmp_path / "outputs"

    rc = main(["ingest", str(records_path), "--db", str(mini_master),
               "--out-dir", str(out_dir), "--version", "9.9"])
    assert rc == 0

    target = out_dir / "Competitive_Intel_Master_DB_v9.9.xlsx"
    assert target.exists()
    db = MasterDB(target)

    blocks = {b.company: b for b in db.blocks()}
    assert "Tset" in blocks
    tset = blocks["Tset"]
    assert tset.company_row is not None
    assert tset.company_row.values["competitor_tier"] == "Tier 1 – Direkt"
    assert [r.values["product_name"] for r in tset.product_rows] == ["Tset Platform"]

    makersite = blocks["Makersite"]
    assert makersite.company_row.values["stage"] == "Series B"
    assert makersite.company_row.values["location"] == "Stuttgart"

    assert "Wackelkandidat GmbH" not in blocks

    run_dirs = list(out_dir.glob("run_*"))
    assert run_dirs, "Run-Artefakte fehlen"
    assert (run_dirs[0] / "report.md").exists()
    assert (run_dirs[0] / "sources.csv").exists()


def test_ingest_dry_run_writes_nothing(mini_master, tmp_path):
    records_path = tmp_path / "records.json"
    records_path.write_text(json.dumps(RECORDS, ensure_ascii=False),
                            encoding="utf-8")
    out_dir = tmp_path / "outputs"
    rc = main(["ingest", str(records_path), "--db", str(mini_master),
               "--out-dir", str(out_dir), "--dry-run"])
    assert rc == 0
    assert not list(out_dir.glob("*.xlsx")) if out_dir.exists() else True


def test_ingest_rejects_broken_json(mini_master, tmp_path):
    bad = tmp_path / "records.json"
    bad.write_text("{nicht json", encoding="utf-8")
    rc = main(["ingest", str(bad), "--db", str(mini_master),
               "--out-dir", str(tmp_path / "out")])
    assert rc == 2
