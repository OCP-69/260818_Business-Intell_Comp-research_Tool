"""Dedupe, URL-Saeuberung und Domain-Zuordnung."""

from __future__ import annotations

import pytest

from cintel.dedupe import (
    Deduper,
    domain_matches_name,
    first_url,
    normalize_company,
    registrable_domain,
)


@pytest.mark.parametrize("raw,expected", [
    ("https://makersite.io/ | Berlin, Germany | Stuttgart", "https://makersite.io"),
    ("https://www.carbonbright.co/\nhttps://www.carbonbright.co/platform",
     "https://www.carbonbright.co"),
    ("Autodesk AutoCAD 2026 | Preise ansehen", ""),
    ("2003", ""),
    ("Netherland, North Holland", ""),
    ("", ""),
])
def test_first_url_survives_polluted_fields(raw, expected):
    assert first_url(raw) == expected


@pytest.mark.parametrize("raw,expected", [
    ("https://www.makersite.io/pricing", "makersite.io"),
    ("http://foo.co.uk/x", "foo.co.uk"),
    ("autodesk.com", "autodesk.com"),
    ("Autodesk AutoCAD 2026 | Preise ansehen", ""),
    ("", ""),
])
def test_registrable_domain(raw, expected):
    assert registrable_domain(raw) == expected


def test_normalize_company_keeps_brand_words():
    """'Solutions'/'Systems' sind Markenbestandteile, keine Rechtsformen."""
    assert normalize_company("Makersite GmbH") == "makersite"
    assert normalize_company("o9 Solutions, Inc.") == "o9 solutions"
    assert normalize_company("Dassault Systemes SE") == "dassault systemes"


def test_domain_must_match_company_before_indexing():
    """
    Autodesk hat in v2.2 eine Produktzeile mit einem makersite.io-Link
    (Customer Story). Ohne diese Pruefung wuerde die Domain auf Autodesk
    indiziert und jeder echte Makersite-Kandidat falsch zugeordnet.
    """
    assert domain_matches_name("makersite.io", "Makersite GmbH") is True
    assert domain_matches_name("makersite.io", "Autodesk") is False
    assert domain_matches_name("autodesk.com", "Autodesk") is True


def test_deduper_ignores_third_party_links():
    known = [
        ("Autodesk", "https://makersite.io/customer-story/partners-with-autodesk/"),
        ("Autodesk", "https://www.autodesk.com"),
        ("Makersite", "https://makersite.io/"),
    ]
    deduper = Deduper(known)
    result = deduper.check("Makersite", "https://makersite.io")
    assert result.is_duplicate
    assert result.matched_company == "Makersite", "darf nicht Autodesk sein"


def test_deduper_detects_new_company():
    deduper = Deduper([("Makersite", "https://makersite.io")])
    result = deduper.check("Voellig Neue Firma", "https://neu-xyz-2026.example")
    assert not result.is_duplicate


def test_deduper_fuzzy_name_match():
    deduper = Deduper([("Dassault Systemes", "")])
    assert deduper.check("Dassault Systemes SE", "").is_duplicate


def test_partition_deduplicates_within_batch():
    deduper = Deduper([])
    fresh, known = deduper.partition([
        {"name": "Foo", "homepage": "https://foo.example"},
        {"name": "Foo GmbH", "homepage": "https://foo.example"},
    ])
    assert len(fresh) == 1
    assert len(known) == 1
    assert known[0]["duplicate_reason"] == "doppelt im Kandidatensatz"
