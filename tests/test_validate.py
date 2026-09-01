"""Validierung: die Fehlerklassen der echten v2.2 muessen erkannt werden."""

from __future__ import annotations

from cintel.masterdb import MasterDB
from cintel.schema import ROW_KIND_COMPANY, Record
from cintel.validate import validate_records


def test_mini_master_findings(mini_master, taxonomy):
    db = MasterDB(mini_master)
    report = validate_records(db.records, taxonomy)
    codes = report.by_code()

    # Hinweis: openpyxl liest 1.0 beim Rueckweg als int - der Float-Check
    # greift nur bei echten Bruchwerten und wird hier nicht mitgetestet.
    assert "tier_non_canonical" in codes      # Bindestrich statt Halbgeviert
    assert "url_not_a_url" in codes           # Seitentitel im URL-Feld
    assert "url_polluted" in codes            # "https://... | Berlin, Germany"
    assert "encoding_broken" in codes         # "Ã¶ffentlich"
    assert report.stats["Firmen"] == 3


def test_strict_makes_vocab_errors(taxonomy):
    record = Record(values={
        "company": "Testfirma", "row_kind": ROW_KIND_COMPANY,
        "key_categories": "Voellig unbekannte Kategorie",
    })
    lenient = validate_records([record], taxonomy)
    strict = validate_records([record], taxonomy, strict=True)
    assert not lenient.errors
    assert any(i.code == "key_category_unknown" for i in strict.errors)


def test_clean_record_passes_strict(taxonomy):
    record = Record(values={
        "company": "Testfirma", "row_kind": ROW_KIND_COMPANY,
        "key_categories": "5. Sustainability & Compliance (LCA/DPP)",
        "competitor_tier": "Tier 1 – Direkt",
        "beachhead": "Cross-cutting",
        "url": "https://example.com",
    })
    report = validate_records([record], taxonomy, strict=True)
    assert not report.errors
