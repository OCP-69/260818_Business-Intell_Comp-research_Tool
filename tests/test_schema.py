"""Schema-Vertrag und Taxonomie-Normalisierung."""

from __future__ import annotations

import pytest

from cintel.schema import (
    COLUMNS,
    N_COLUMNS,
    SchemaError,
    join_multi,
    normalize_header,
    resolve_columns,
    split_multi,
)
from tests.conftest import REAL_HEADERS


def test_column_contract_is_31_and_unique():
    assert N_COLUMNS == 31
    fields = [f for f, _ in COLUMNS]
    assert len(set(fields)) == 31


def test_resolve_columns_handles_multiline_headers():
    """Die echten Header enthalten Zeilenumbrueche und Trailing-Spaces."""
    mapping = resolve_columns(REAL_HEADERS)
    assert len(mapping) == N_COLUMNS
    assert len(set(mapping.values())) == N_COLUMNS, "keine Spalte doppelt belegt"
    assert mapping["company"] == 2
    assert mapping["r_strategies"] == 9
    assert mapping["r_rationale"] == 10
    assert mapping["beachhead"] == 30


def test_resolve_columns_rejects_incomplete_header():
    with pytest.raises(SchemaError):
        resolve_columns(["Company", "URL"])


def test_normalize_header_collapses_whitespace():
    assert (normalize_header("R-Strategy: Rationale \nto categorization")
            == "r-strategy: rationale to categorization")
    assert normalize_header(None) == ""


@pytest.mark.parametrize("raw,expected", [
    ("Tier 1 - Direkt", "Tier 1 – Direkt"),
    ("Tier 2 – Nachbar", "Tier 2 – Nachbar"),
    ("Tier 3 � Beobachten", "Tier 3 – Beobachten"),
    ("tier 1 irgendwas", "Tier 1 – Direkt"),
    ("Unsinn", None),
    ("", None),
])
def test_canon_tier(taxonomy, raw, expected):
    assert taxonomy.canon_tier(raw) == expected


def test_canon_tier_always_uses_en_dash(taxonomy):
    for value in taxonomy.competitor_tier:
        assert "–" in value
        assert "�" not in value


@pytest.mark.parametrize("raw,expected", [
    ("Supply Chain & Quality", "7. Supply Chain & Operations"),
    ("5. Supply Chain & Procurement", "7. Supply Chain & Operations"),
    ("6. Industrial AI & Future Engineering", "6. Industrial AI & Future Engineering"),
    ("UNMAPPED", None),
    ("Frei erfundene Kategorie", None),
])
def test_canon_key_category(taxonomy, raw, expected):
    assert taxonomy.canon_key_category(raw) == expected


def test_canon_sub_category_alias(taxonomy):
    assert taxonomy.canon_sub_category("8. Other / Cross-cutting") == "Other / Cross-cutting"
    assert taxonomy.canon_sub_category("AI & ML Tools") == "Engineering Copilots & AI Assistants"


def test_legend_subs_are_within_vocabulary(taxonomy):
    """Jede Sub-Kategorie der Legende muss im Vokabular stehen."""
    for key_category, subs in taxonomy.legend.items():
        assert key_category in taxonomy.key_categories
        for sub in subs:
            assert sub in taxonomy.sub_categories, f"{sub} fehlt in sub_categories"


def test_join_and_split_multi():
    assert join_multi("key_categories", ["A", "B", "A"]) == "A | B"
    assert join_multi("sub_category", ["X", "Y"]) == "X; Y"
    assert split_multi("A | B; C") == ["A", "B", "C"]
    assert split_multi(None) == []


def test_every_sub_category_belongs_to_a_key_category(taxonomy):
    """
    Jede Sub-Kategorie muss in der Legende mindestens einer Hauptkategorie
    zugeordnet sein.

    Verwaiste Werte sind gefaehrlich: die Discovery filtert Sub-Kategorien
    gegen die Legende und verwirft nicht zuordenbare stillschweigend - der
    Lauf sucht dann ueber alles statt gezielt. Drei Werte waren anfangs
    verwaist (Cost Engineering & Value Analysis, Sustainability Reporting &
    ESG, Testing & Validation).
    """
    assigned = set()
    for subs in taxonomy.legend.values():
        assigned.update(subs)
    orphans = [s for s in taxonomy.sub_categories if s not in assigned]
    assert not orphans, f"keiner Hauptkategorie zugeordnet: {orphans}"


def test_legend_has_no_entries_outside_the_vocabulary(taxonomy):
    """Umgekehrt: die Legende darf nichts nennen, was es nicht gibt."""
    known = set(taxonomy.sub_categories)
    for key_category, subs in taxonomy.legend.items():
        unknown = [s for s in subs if s not in known]
        assert not unknown, f"{key_category} nennt unbekannte Werte: {unknown}"
