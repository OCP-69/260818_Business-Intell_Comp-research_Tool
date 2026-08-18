"""Crawl-Gate, Merge-Logik und Extraktions-Schema - ohne Netz und ohne LLM."""

from __future__ import annotations

import json

import pytest

from cintel.crawl import Crawler, Page, _name_core, _name_matches
from cintel.extract import _clean, build_schema
from cintel.masterdb import MasterDB
from cintel.merge import MergePlan, Merger
from cintel.schema import ROW_KIND_COMPANY, ROW_KIND_PRODUCT, Record

# ----------------------------------------------------------------- Crawl-Gate

@pytest.mark.parametrize("raw,expected", [
    ("Makersite GmbH", "makersite"),
    ("3D Spark", "3dspark"),
    ("Dassault Systemes SE", "dassaultsystemes"),
])
def test_name_core(raw, expected):
    assert _name_core(raw) == expected


def test_name_matches_accepts_legal_suffix_difference():
    page = Page(url="https://makersite.io", status=200,
                title="Makersite | AI-Powered", text="Makersite GmbH Stuttgart")
    assert _name_matches("Makersite GmbH", page)


def test_name_matches_rejects_wrong_company():
    page = Page(url="https://makersite.io", status=200,
                title="Makersite | AI-Powered", text="Makersite GmbH")
    assert not _name_matches("Voellig Erfundene Firma XY", page)


def test_offline_crawler_rejects_uncached_url(tmp_path):
    """Ohne Cache und ohne Netz muss die Firma abgelehnt werden, nicht raten."""
    crawler = Crawler(cache_dir=tmp_path / "cache", offline=True)
    result = crawler.crawl_company("Egal", "https://nicht-im-cache.example")
    assert not result.verified
    assert "offline" in result.reject_reason or "nicht erreichbar" in result.reject_reason


def test_crawler_rejects_invalid_start_url(tmp_path):
    crawler = Crawler(cache_dir=tmp_path / "cache", offline=True)
    result = crawler.crawl_company("Foo", "Autodesk AutoCAD 2026 | Preise")
    assert not result.verified
    assert "Start-URL" in result.reject_reason


def test_cache_roundtrip_persists_links(tmp_path):
    crawler = Crawler(cache_dir=tmp_path / "cache", offline=True)
    page = Page(url="https://x.example", status=200, title="T", text="Body",
                links=["https://x.example/products"])
    crawler._write_cache(page)
    restored = crawler.fetch("https://x.example")
    assert restored.from_cache
    assert restored.links == ["https://x.example/products"]
    assert restored.ok


# -------------------------------------------------------- Extraktions-Schema

def test_build_schema_constrains_enums_to_taxonomy(taxonomy):
    schema = build_schema(taxonomy, max_products=5)
    company = schema["properties"]["company_info"]["properties"]
    assert company["competitor_tier"]["enum"] == taxonomy.competitor_tier
    assert company["key_categories"]["items"]["enum"] == taxonomy.key_categories
    products = schema["properties"]["products"]
    assert products["maxItems"] == 5
    assert products["items"]["properties"]["sub_category"]["enum"] == taxonomy.sub_categories
    # Muss als JSON serialisierbar sein - wird als CLI-Argument uebergeben.
    assert json.loads(json.dumps(schema))


def test_schema_forbids_extra_properties(taxonomy):
    schema = build_schema(taxonomy)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["company_info"]["additionalProperties"] is False


@pytest.mark.parametrize("raw", [
    "N/A", "n/a", "unbekannt", "Nicht verfügbar", "-", "TBD", "none", "", None,
])
def test_clean_drops_placeholder_values(raw):
    assert _clean(raw) is None


def test_clean_keeps_real_content():
    assert _clean("  Echter Inhalt  ") == "Echter Inhalt"


# ------------------------------------------------------------------- Merge

def test_enrich_existing_never_overwrites_curated_values(mini_master):
    """
    Tier, Beachhead und Bewertungen sind Handarbeit. Der Merge darf nur
    LEERE Felder fuellen.
    """
    db = MasterDB(mini_master)
    merger = Merger(db)
    block = next(b for b in db.blocks() if b.company == "Makersite")
    plan = MergePlan()

    extracted = [Record(
        values={
            "company": "Makersite", "row_kind": ROW_KIND_COMPANY,
            "competitor_tier": "Tier 3 – Beobachten",   # widerspricht dem Bestand
            "founding_year": "1999",                     # widerspricht dem Bestand
            "target_market": "Neuer Wert fuer leeres Feld",
        },
        confidence={"*": 0.9},
    )]
    merger.enrich_existing(block, extracted, plan)

    assert plan.updates, "leeres Feld haette gefuellt werden muessen"
    merged = next(iter(plan.updates.values())).values
    assert merged["competitor_tier"] == "Tier 1 - Direkt", "kuratierter Wert bleibt"
    assert merged["founding_year"] == "2018", "kuratierter Wert bleibt"
    assert merged["target_market"] == "Neuer Wert fuer leeres Feld"


def test_enrich_existing_skips_known_products(mini_master):
    db = MasterDB(mini_master)
    merger = Merger(db)
    block = next(b for b in db.blocks() if b.company == "Makersite")
    plan = MergePlan()

    extracted = [
        Record(values={"company": "Makersite", "row_kind": ROW_KIND_COMPANY},
               confidence={"*": 0.9}),
        Record(values={"company": "Makersite", "product_name": "Makersite Platform",
                       "row_kind": ROW_KIND_PRODUCT}, confidence={"*": 0.9}),
        Record(values={"company": "Makersite", "product_name": "Makersite Insights",
                       "row_kind": ROW_KIND_PRODUCT}, confidence={"*": 0.9}),
    ]
    merger.enrich_existing(block, extracted, plan)

    names = [r.values["product_name"] for r in plan.new_rows]
    assert names == ["Makersite Insights"], "bekanntes Produkt nicht doppelt anhaengen"
    assert any("existiert" in reason for _, reason in plan.skipped)


def test_low_confidence_rows_are_skipped(mini_master):
    db = MasterDB(mini_master)
    merger = Merger(db, min_confidence=0.5)
    block = next(b for b in db.blocks() if b.company == "Makersite")
    plan = MergePlan()
    merger.enrich_existing(block, [
        Record(values={"company": "Makersite", "row_kind": ROW_KIND_COMPANY},
               confidence={"*": 0.9}),
        Record(values={"company": "Makersite", "product_name": "Geraten",
                       "row_kind": ROW_KIND_PRODUCT}, confidence={"*": 0.1}),
    ], plan)
    assert not plan.new_rows
    assert any("Confidence" in reason for _, reason in plan.skipped)


def test_add_new_company_assigns_sequential_ids(mini_master):
    db = MasterDB(mini_master)
    merger = Merger(db)
    first = merger.add_new_company([
        Record(values={"company": "A", "row_kind": ROW_KIND_COMPANY}),
        Record(values={"company": "A", "product_name": "A1",
                       "row_kind": ROW_KIND_PRODUCT}),
    ])
    second = merger.add_new_company([
        Record(values={"company": "B", "row_kind": ROW_KIND_COMPANY}),
    ])
    assert {r.values["company_id"] for r in first} == {4}
    assert {r.values["company_id"] for r in second} == {5}
    assert all(r.values["last_update"] for r in first)
