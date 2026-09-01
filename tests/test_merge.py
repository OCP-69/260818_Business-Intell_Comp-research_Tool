"""Merge: fill-only-Politik und Produktzeilen-Anhang."""

from __future__ import annotations

from cintel.masterdb import MasterDB
from cintel.merge import MergePlan, Merger
from cintel.schema import ROW_KIND_COMPANY, ROW_KIND_PRODUCT, Record


def _record(kind: str, confidence: float = 0.9, **values) -> Record:
    record = Record(values={"row_kind": kind, **values})
    record.confidence["*"] = confidence
    return record


def test_enrich_fills_only_empty_fields(mini_master):
    db = MasterDB(mini_master)
    block = next(b for b in db.blocks() if b.company == "Makersite")
    merger = Merger(db)
    plan = MergePlan()

    merger.enrich_existing(block, [_record(
        ROW_KIND_COMPANY, company="Makersite",
        location="SOLL NICHT UEBERSCHREIBEN",  # bereits "Stuttgart"
        stage="Series B",                       # bisher leer -> fuellen
    )], plan)

    assert any(f == ("Makersite", "", "stage") for f in plan.filled_fields)
    assert any("location" in why for _, why in plan.skipped)
    updated = list(plan.updates.values())[0]
    assert updated.values["location"] == "Stuttgart"
    assert updated.values["stage"] == "Series B"


def test_enrich_skips_low_confidence(mini_master):
    db = MasterDB(mini_master)
    block = next(b for b in db.blocks() if b.company == "Makersite")
    merger = Merger(db, min_confidence=0.5)
    plan = MergePlan()

    merger.enrich_existing(block, [_record(
        ROW_KIND_COMPANY, confidence=0.2, company="Makersite", stage="Seed",
    )], plan)

    assert not plan.updates
    assert any("Confidence" in why for _, why in plan.skipped)


def test_enrich_appends_only_new_products(mini_master):
    db = MasterDB(mini_master)
    block = next(b for b in db.blocks() if b.company == "Makersite")
    merger = Merger(db)
    plan = MergePlan()

    merger.enrich_existing(block, [
        _record(ROW_KIND_COMPANY, company="Makersite"),
        _record(ROW_KIND_PRODUCT, company="Makersite",
                product_name="Makersite Platform"),   # existiert schon
        _record(ROW_KIND_PRODUCT, company="Makersite",
                product_name="Makersite API"),        # neu
    ], plan)

    names = [r.values["product_name"] for r in plan.new_rows]
    assert names == ["Makersite API"]
    assert plan.new_rows[0].values["company_id"] == block.company_id


def test_new_company_gets_next_free_id(mini_master):
    db = MasterDB(mini_master)
    merger = Merger(db)
    rows = merger.add_new_company([
        _record(ROW_KIND_COMPANY, company="Tset"),
        _record(ROW_KIND_PRODUCT, company="Tset", product_name="Tset Platform"),
    ])
    assert len({r.values["company_id"] for r in rows}) == 1
    assert rows[0].values["company_id"] >= 4  # 1-3 sind vergeben
    assert all(r.values.get("last_update") for r in rows)
