"""Crawl-Gate, Merge-Logik und Extraktions-Schema - ohne Netz und ohne LLM."""

from __future__ import annotations

import json

import pytest

from cintel.crawl import (
    Crawler,
    Page,
    _is_about,
    _is_relevant,
    _name_core,
    _name_matches,
    _name_variants,
)
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


def test_name_variants_splits_parenthetical():
    """
    Regression: "CAESES (Friendship Systems)" wurde faelschlich abgelehnt.

    `_name_core` entfernt Allerweltswoerter wie "Systems" - im Crawl-Gate
    ist dieser groszuegige Abgleich gewollt, weil zusaetzlich mindestens
    eine Variante wirklich auf der Seite stehen muss.
    """
    variants = _name_variants("CAESES (Friendship Systems)")
    assert "caeses" in variants
    assert "friendship" in variants
    assert variants[0] == "caesesfriendship", "Vollname bleibt erste Variante"


def test_name_matches_accepts_partial_company_name():
    page = Page(url="https://www.caeses.com", status=200,
                title="CAESES | CAD for Applications", text="CAESES software")
    assert _name_matches("CAESES (Friendship Systems)", page)


def test_name_matches_still_rejects_unrelated_site():
    """Die Zerlegung darf das Gate nicht aufweichen."""
    page = Page(url="https://www.caeses.com", status=200,
                title="CAESES | CAD", text="CAESES software")
    assert not _name_matches("Siemens Digital Industries", page)


# ------------------------------------------------------- Ueber-uns-Seiten

@pytest.mark.parametrize("url,expected", [
    ("https://x.de/about-us", True),
    ("https://x.de/ueber-uns", True),
    ("https://x.de/en/company", True),
    ("https://x.de/team", True),
    ("https://x.de/products/foo", False),
    ("https://x.de/pricing", False),
])
def test_is_about_detects_company_pages(url, expected):
    assert _is_about(url) is expected


@pytest.mark.parametrize("path,expected", [
    ("/products", True),
    ("/about-us", True),
    ("/impressum", True),
    ("/irgendwas-anderes", False),
])
def test_is_relevant_covers_products_and_company(path, expected):
    assert _is_relevant(path) is expected


def test_about_pages_are_fetched_before_product_pages(tmp_path):
    """
    Regression: die Company-Level-Felder (Gruendungsjahr, Standort,
    Mitarbeiterzahl) stehen auf der Ueber-uns-Seite. Ohne Vorrang fiel
    sie bei knappem Seitenbudget als erstes weg - im ersten Echtlauf des
    new-Modus blieben zwei von drei Firmen ohne diese Angaben.
    """
    crawler = Crawler(cache_dir=tmp_path / "cache", offline=True, max_pages=3)
    home = Page(
        url="https://x.example", status=200, title="X GmbH",
        text="X GmbH Startseite",
        links=[
            "https://x.example/products/a",
            "https://x.example/products/b",
            "https://x.example/products/c",
            "https://x.example/about-us",
        ],
    )
    crawler._write_cache(home)
    for url in ["https://x.example/products/a", "https://x.example/products/b",
                "https://x.example/products/c", "https://x.example/about-us"]:
        crawler._write_cache(Page(url=url, status=200, title="t", text="Inhalt"))

    result = crawler.crawl_company("X", "https://x.example")
    assert result.verified
    fetched = [p.url for p in result.pages]
    assert "https://x.example/about-us" in fetched, "Ueber-uns muss dabei sein"
