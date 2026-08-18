"""Validierung und Bestandsreparatur."""

from __future__ import annotations

from cintel.masterdb import MasterDB
from cintel.repair import fix_mojibake, repair_records, run_repair
from cintel.schema import ROW_KIND_COMPANY, Record
from cintel.validate import validate_records


def _codes(report) -> set[str]:
    return set(report.by_code())


def test_validate_finds_the_v22_defect_classes(mini_master, taxonomy):
    db = MasterDB(mini_master)
    report = validate_records(db.records, taxonomy)
    codes = _codes(report)
    assert "url_not_a_url" in codes, "Seitentitel im URL-Feld"
    assert "url_polluted" in codes, "URL mit Zusatztext"
    assert "tier_non_canonical" in codes, "Bindestrich statt Halbgeviertstrich"
    assert "encoding_broken" in codes, "Mojibake"


def test_validate_flags_float_company_id(taxonomy):
    """
    Die echte v2.2 liefert Company_ID als 1.0 / 42.0. Ueber eine
    xlsx-Fixture ist das nicht nachstellbar, weil openpyxl den Float 1.0
    als "1" serialisiert und beim Lesen ein int zurueckgibt - deshalb hier
    direkt auf dem Record.
    """
    records = [Record(values={
        "company_id": 42.0, "company": "Foo", "row_kind": ROW_KIND_COMPANY,
    })]
    assert "company_id_not_int" in _codes(validate_records(records, taxonomy))

    records[0].values["company_id"] = 42
    assert "company_id_not_int" not in _codes(validate_records(records, taxonomy))


def test_validate_clean_records_report_nothing(taxonomy):
    clean = [Record(values={
        "company_id": 1, "company": "Foo AG", "row_kind": ROW_KIND_COMPANY,
        "url": "https://foo.example", "competitor_tier": "Tier 1 – Direkt",
        "beachhead": "Cross-cutting", "sub_category": "LCA Software & Platforms",
        "key_categories": "5. Sustainability & Compliance (LCA/DPP)",
    })]
    report = validate_records(clean, taxonomy, strict=True)
    assert not report.issues, report.render()


def test_strict_mode_escalates_vocabulary_drift(taxonomy):
    drifted = [Record(values={
        "company": "Foo", "row_kind": ROW_KIND_COMPANY,
        "key_categories": "Frei erfundene Kategorie",
    })]
    assert validate_records(drifted, taxonomy, strict=False).errors == []
    assert validate_records(drifted, taxonomy, strict=True).errors != []


def test_fix_mojibake_roundtrip():
    assert fix_mojibake("nicht Ã¶ffentlich verfÃ¼gbar") == "nicht öffentlich verfügbar"
    assert fix_mojibake("schon korrekt") == "schon korrekt"
    assert fix_mojibake("") == ""


def test_repair_normalises_the_known_defects(mini_master, taxonomy):
    db = MasterDB(mini_master)
    updates, report = repair_records(db.records, taxonomy)
    rules = set(report.stats)
    assert "tier_canonicalised" in rules
    assert "url_cleared" in rules, "Seitentitel muss aus dem URL-Feld raus"
    assert "url_cleaned" in rules, "Zusatztext muss abgeschnitten werden"
    assert "encoding_fixed" in rules

    for record in updates.values():
        tier = record.values.get("competitor_tier")
        if tier:
            assert "–" in tier and "-" not in tier.replace("–", "")


def test_repair_converts_float_company_id(taxonomy):
    """1.0 == 1 gilt in Python - deshalb muss auf den TYP geprueft werden."""
    records = [Record(values={
        "company_id": 42.0, "company": "Foo", "row_kind": ROW_KIND_COMPANY,
    })]
    updates, report = repair_records(records, taxonomy)
    assert "company_id_to_int" in report.stats
    assert updates[0].values["company_id"] == 42
    assert isinstance(updates[0].values["company_id"], int)


def test_repair_preserves_cleared_url_in_remarks(mini_master, taxonomy):
    """Der Fremdinhalt darf nicht verloren gehen, nur umziehen."""
    db = MasterDB(mini_master)
    updates, _ = repair_records(db.records, taxonomy)
    moved = [
        r for r in updates.values()
        if "aus URL-Feld uebernommen" in str(r.values.get("remarks") or "")
    ]
    assert moved, "geleerte URL muss in den Bemerkungen auftauchen"
    assert "Was ist PLM?" in str(moved[0].values["remarks"])


def test_repair_output_passes_validation(mini_master, taxonomy, tmp_path):
    """Nach der Reparatur duerfen die reparierbaren Fehlerklassen weg sein."""
    target, _ = run_repair(mini_master, taxonomy, tmp_path / "out", version="2.3")
    assert target is not None
    codes = _codes(validate_records(MasterDB(target).records, taxonomy))
    for gone in ("tier_non_canonical", "company_id_not_int",
                 "url_not_a_url", "url_polluted", "encoding_broken"):
        assert gone not in codes, f"{gone} haette repariert sein muessen"


def test_repair_dry_run_writes_nothing(mini_master, taxonomy, tmp_path):
    out = tmp_path / "out"
    target, report = run_repair(mini_master, taxonomy, out, dry_run=True)
    assert target is None
    assert report.changes
    assert not out.exists() or not list(out.glob("*.xlsx"))
