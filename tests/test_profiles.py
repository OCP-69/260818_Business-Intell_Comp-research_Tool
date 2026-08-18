"""Rechercheprofile: laden, pruefen, ueber die Grundeinstellungen legen."""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from cintel.profiles import (
    Profile,
    ProfileError,
    apply_profile,
    load_profiles,
    resolve,
    validate_profile,
)

REPO_ROOT = Path(__file__).resolve().parents[1]

BASE_CONFIG = {
    "mode": "gaps",
    "gaps": {
        "target_columns": ["Product_name"],
        "tier_priority": ["Tier 1 – Direkt"],
        "require_url": True,
    },
    "new": {
        "key_categories": ["1. Design & Authoring (CAD/BIM)"],
        "regions": ["Global"],
    },
    "limits": {"max_companies_per_run": 25, "max_pages_per_company": 12},
    "llm": {"model": "sonnet", "timeout_seconds": 600},
}


def write_profiles(tmp_path: Path, body: dict) -> Path:
    path = tmp_path / "profiles.yaml"
    path.write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
    return path


# ------------------------------------------------------- mitgelieferte Datei

def test_shipped_profiles_are_all_valid(taxonomy):
    """
    Die ausgelieferte config/profiles.yaml muss fehlerfrei sein - sonst
    scheitert ein zeitgesteuerter Lauf erst nachts um halb sieben.
    """
    profiles = load_profiles(REPO_ROOT / "config" / "profiles.yaml")
    assert profiles, "es muessen Profile ausgeliefert werden"
    for name, profile in profiles.items():
        assert not validate_profile(profile, taxonomy), f"Profil '{name}' fehlerhaft"


def test_shipped_profiles_cover_both_modes(taxonomy):
    profiles = load_profiles(REPO_ROOT / "config" / "profiles.yaml")
    modes = {p.mode for p in profiles.values()}
    assert modes == {"gaps", "new"}


# ------------------------------------------------------------------- laden

def test_missing_file_yields_no_profiles(tmp_path):
    assert load_profiles(tmp_path / "gibtsnicht.yaml") == {}


def test_load_reads_all_fields(tmp_path):
    path = write_profiles(tmp_path, {"profiles": {
        "test": {
            "description": "Beschreibung",
            "mode": "new",
            "limit": 7,
            "version": "3.1",
            "cross_check": "codex",
            "key_categories": ["5. Sustainability & Compliance (LCA/DPP)"],
        }
    }})
    profile = load_profiles(path)["test"]
    assert profile.mode == "new"
    assert profile.limit == 7
    assert profile.version == "3.1"
    assert profile.cross_check == "codex"
    assert profile.settings["key_categories"] == [
        "5. Sustainability & Compliance (LCA/DPP)"
    ]
    assert "description" not in profile.settings, "Metadaten gehoeren nicht in settings"


def test_unknown_mode_is_rejected_at_load(tmp_path):
    path = write_profiles(tmp_path, {"profiles": {"x": {"mode": "voodoo"}}})
    with pytest.raises(ProfileError, match="mode="):
        load_profiles(path)


def test_malformed_profiles_section_is_rejected(tmp_path):
    path = tmp_path / "profiles.yaml"
    path.write_text("profiles:\n  - nur\n  - eine\n  - liste\n", encoding="utf-8")
    with pytest.raises(ProfileError, match="Zuordnung"):
        load_profiles(path)


# ------------------------------------------------------------------ pruefen

def test_validate_flags_vocabulary_drift(taxonomy):
    profile = Profile(name="x", mode="new",
                      settings={"key_categories": ["Frei erfunden"]})
    problems = validate_profile(profile, taxonomy)
    assert any("Frei erfunden" in p for p in problems)


def test_validate_flags_new_without_focus(taxonomy):
    profile = Profile(name="x", mode="new", settings={})
    assert any("Suchfokus" in p for p in validate_profile(profile, taxonomy))


def test_validate_flags_gaps_without_columns(taxonomy):
    profile = Profile(name="x", mode="gaps", settings={})
    assert any("target_columns" in p for p in validate_profile(profile, taxonomy))


def test_validate_flags_nonsense_limit(taxonomy):
    profile = Profile(name="x", mode="gaps", limit=0,
                      settings={"target_columns": ["Product_name"]})
    assert any("limit" in p for p in validate_profile(profile, taxonomy))


# ------------------------------------------------------------------ anwenden

def test_apply_overrides_only_its_own_section():
    profile = Profile(name="x", mode="new", limit=9, settings={
        "key_categories": ["6. Industrial AI & Future Engineering"],
        "regions": ["DACH"],
    })
    merged = apply_profile(BASE_CONFIG, profile)
    assert merged["mode"] == "new"
    assert merged["new"]["key_categories"] == ["6. Industrial AI & Future Engineering"]
    assert merged["new"]["regions"] == ["DACH"]
    assert merged["limits"]["max_companies_per_run"] == 9
    # Der gaps-Abschnitt bleibt unangetastet.
    assert merged["gaps"]["target_columns"] == ["Product_name"]


def test_apply_does_not_mutate_the_original():
    before = yaml.safe_dump(BASE_CONFIG, allow_unicode=True)
    apply_profile(BASE_CONFIG, Profile(name="x", mode="new", limit=3,
                                       settings={"regions": ["Asia"]}))
    assert yaml.safe_dump(BASE_CONFIG, allow_unicode=True) == before


def test_apply_can_override_limits_and_model():
    profile = Profile(name="x", mode="gaps", settings={
        "target_columns": ["USP"],
        "max_pages_per_company": 20,
        "model": "opus",
    })
    merged = apply_profile(BASE_CONFIG, profile)
    assert merged["limits"]["max_pages_per_company"] == 20
    assert merged["llm"]["model"] == "opus"
    assert merged["llm"]["timeout_seconds"] == 600, "nicht gesetzte Werte bleiben"
    assert merged["gaps"]["target_columns"] == ["USP"]


def test_profile_without_limit_keeps_base_value():
    profile = Profile(name="x", mode="gaps", limit=None,
                      settings={"target_columns": ["USP"]})
    merged = apply_profile(BASE_CONFIG, profile)
    assert merged["limits"]["max_companies_per_run"] == 25


# ------------------------------------------------------------------ resolve

def test_resolve_unknown_name_lists_alternatives(taxonomy):
    profiles = {"vorhanden": Profile(name="vorhanden", mode="gaps",
                                     settings={"target_columns": ["USP"]})}
    with pytest.raises(ProfileError) as exc:
        resolve(BASE_CONFIG, profiles, "tippfehler", taxonomy)
    assert "vorhanden" in str(exc.value), "die Meldung muss weiterhelfen"


def test_resolve_rejects_faulty_profile(taxonomy):
    profiles = {"kaputt": Profile(name="kaputt", mode="new",
                                  settings={"key_categories": ["Quatsch"]})}
    with pytest.raises(ProfileError, match="Quatsch"):
        resolve(BASE_CONFIG, profiles, "kaputt", taxonomy)


def test_resolve_returns_merged_config_and_profile(taxonomy):
    profiles = load_profiles(REPO_ROOT / "config" / "profiles.yaml")
    merged, profile = resolve(BASE_CONFIG, profiles, "lca-startups-dach", taxonomy)
    assert profile.name == "lca-startups-dach"
    assert merged["mode"] == "new"
    assert "DACH" in merged["new"]["regions"]
    assert "LCA Software & Platforms" in merged["new"]["sub_categories"]


def test_summary_is_human_readable():
    profile = Profile(name="x", mode="new", limit=12,
                      settings={"regions": ["DACH", "Europe"]})
    text = profile.summary
    assert "new" in text and "12" in text and "DACH" in text


# ------------------------------------------------- Legende: Sub passt zu Key

def test_validate_flags_sub_category_not_under_chosen_key(taxonomy):
    """
    "Quality & Inspection" gehoert zu Kategorie 3, nicht zu 4. Ohne diese
    Pruefung wuerde die Discovery den Wert stillschweigend verwerfen und
    ueber ALLE Sub-Kategorien suchen - der Lauf traefe etwas anderes als
    beabsichtigt, ohne das zu melden.
    """
    profile = Profile(name="x", mode="new", settings={
        "key_categories": ["4. Lifecycle & Data Management (PLM/PDM)"],
        "sub_categories": ["Quality & Inspection"],
    })
    problems = validate_profile(profile, taxonomy)
    assert any("passt zu keiner" in p for p in problems)
    assert any("Manufacturing" in p for p in problems), "Hinweis, wohin sie gehoert"


def test_validate_accepts_matching_sub_category(taxonomy):
    profile = Profile(name="x", mode="new", settings={
        "key_categories": ["4. Lifecycle & Data Management (PLM/PDM)"],
        "sub_categories": ["PLM & PDM Platforms", "MBSE & Systems Engineering"],
    })
    assert validate_profile(profile, taxonomy) == []


def test_validate_accepts_sub_under_any_of_several_keys(taxonomy):
    """Es genuegt, wenn die Sub-Kategorie zu EINER der Hauptkategorien passt."""
    profile = Profile(name="x", mode="new", settings={
        "key_categories": [
            "4. Lifecycle & Data Management (PLM/PDM)",
            "3. Manufacturing & Production (CAM/MES)",
        ],
        "sub_categories": ["Quality & Inspection"],
    })
    assert validate_profile(profile, taxonomy) == []


def test_handbook_example_profile_is_valid(taxonomy):
    """
    Das Beispielprofil aus Kapitel 10.3 des Handbuchs. Es steht dort zum
    Abtippen - also muss es fehlerfrei sein. Ein frueherer Entwurf nutzte
    "Quality & Inspection" unter Kategorie 4 und war damit falsch.
    """
    example = yaml.safe_load("""
      medtech-europa:
        description: Sucht Anbieter mit Schwerpunkt Medizintechnik in Europa.
        mode: new
        limit: 12
        key_categories:
          - "4. Lifecycle & Data Management (PLM/PDM)"
        sub_categories:
          - "PLM & PDM Platforms"
          - "MBSE & Systems Engineering"
        regions:
          - "Europe"
        stages:
          - "Series A"
          - "Series B"
        inclusion_criteria: Software mit nachweisbarem Bezug zur Medizintechnik.
        exclusion_criteria: Reine Beratung ohne eigenes Produkt.
    """)
    body = example["medtech-europa"]
    profile = Profile(
        name="medtech-europa", mode=body["mode"], limit=body["limit"],
        settings={k: v for k, v in body.items()
                  if k not in ("description", "mode", "limit")},
    )
    assert validate_profile(profile, taxonomy) == []
