"""
Rechercheprofile - vorkonfigurierte Laeufe, die per Name gestartet werden.

Ein Profil buendelt Suchfokus, Branche, Funktionsbereiche, Region und
Reifegrad unter einem Namen. Damit laesst sich ein Lauf ohne jede
Parameterkenntnis starten:

    py -m cintel run --profile lca-startups-dach

Und genau deshalb ist er auch zeitgesteuert ausfuehrbar: die
Windows-Aufgabenplanung ruft immer denselben kurzen Befehl auf.

Ein Profil ueberschreibt gezielt Teile von config/targets.yaml. Alles,
was ein Profil nicht setzt, bleibt auf dem Wert aus targets.yaml.
"""

from __future__ import annotations

import copy
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .schema import Taxonomy

log = logging.getLogger(__name__)

# Schluessel, die ein Profil im Abschnitt "new" ueberschreiben darf.
NEW_KEYS = (
    "key_categories", "sub_categories", "regions", "stages",
    "inclusion_criteria", "exclusion_criteria", "max_per_cell",
)
# Schluessel, die ein Profil im Abschnitt "gaps" ueberschreiben darf.
GAPS_KEYS = ("target_columns", "only_incomplete", "tier_priority", "require_url")


class ProfileError(RuntimeError):
    """Das Profil existiert nicht oder ist fehlerhaft."""


@dataclass
class Profile:
    name: str
    description: str = ""
    mode: str = "gaps"
    limit: int | None = None
    version: str | None = None
    cross_check: str | None = None
    settings: dict[str, Any] = field(default_factory=dict)

    @property
    def summary(self) -> str:
        parts = [f"Modus {self.mode}"]
        if self.limit:
            parts.append(f"max. {self.limit} Firmen")
        regions = self.settings.get("regions")
        if regions:
            parts.append("Region " + ", ".join(regions))
        return " | ".join(parts)


def load_profiles(path: str | Path) -> dict[str, Profile]:
    """Liest config/profiles.yaml. Fehlt die Datei, gibt es keine Profile."""
    path = Path(path)
    if not path.exists():
        return {}

    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    entries = raw.get("profiles") or {}
    if not isinstance(entries, dict):
        raise ProfileError(
            f"{path}: Der Abschnitt 'profiles' muss eine Zuordnung "
            f"Name -> Einstellungen sein."
        )

    out: dict[str, Profile] = {}
    for name, body in entries.items():
        if not isinstance(body, dict):
            raise ProfileError(f"{path}: Profil '{name}' ist keine Zuordnung.")
        mode = str(body.get("mode", "gaps")).strip()
        if mode not in ("gaps", "new"):
            raise ProfileError(
                f"{path}: Profil '{name}' hat mode='{mode}'. "
                f"Erlaubt sind nur 'gaps' und 'new'."
            )
        settings = {k: v for k, v in body.items()
                    if k not in ("description", "mode", "limit", "version", "cross_check")}
        out[str(name)] = Profile(
            name=str(name),
            description=str(body.get("description", "")).strip(),
            mode=mode,
            limit=int(body["limit"]) if body.get("limit") is not None else None,
            version=str(body["version"]) if body.get("version") else None,
            cross_check=str(body["cross_check"]) if body.get("cross_check") else None,
            settings=settings,
        )
    return out


def validate_profile(profile: Profile, taxonomy: Taxonomy) -> list[str]:
    """
    Prueft ein Profil gegen das kontrollierte Vokabular.

    Returns:
        Liste von Beanstandungen. Leer = in Ordnung.
    """
    problems: list[str] = []

    for value in profile.settings.get("key_categories", []) or []:
        if taxonomy.canon_key_category(value) is None:
            problems.append(
                f"Key Category '{value}' steht nicht in config/taxonomy.yaml"
            )
    for value in profile.settings.get("sub_categories", []) or []:
        if taxonomy.canon_sub_category(value) is None:
            problems.append(
                f"Sub Category '{value}' steht nicht in config/taxonomy.yaml"
            )

    # Passt jede Sub-Kategorie zu mindestens einer der gewaehlten
    # Hauptkategorien? Wenn nicht, verwirft die Discovery sie stillschweigend
    # und sucht stattdessen ueber ALLE Sub-Kategorien - der Lauf traefe dann
    # etwas voellig anderes als beabsichtigt, ohne das zu melden.
    key_categories = [
        c for c in (taxonomy.canon_key_category(v)
                    for v in profile.settings.get("key_categories", []) or [])
        if c
    ]
    if key_categories:
        allowed: set[str] = set()
        for key_category in key_categories:
            allowed.update(taxonomy.valid_subs_for(key_category))
        for value in profile.settings.get("sub_categories", []) or []:
            canonical = taxonomy.canon_sub_category(value)
            if canonical and canonical not in allowed:
                fits = [k for k in taxonomy.legend
                        if canonical in taxonomy.valid_subs_for(k)]
                hint = f" - sie gehoert zu: {', '.join(fits)}" if fits else ""
                problems.append(
                    f"Sub Category '{canonical}' passt zu keiner der gewaehlten "
                    f"Key Categories{hint}"
                )
    for value in profile.settings.get("tier_priority", []) or []:
        if taxonomy.canon_tier(value) is None:
            problems.append(f"Competitor_Tier '{value}' ist unbekannt")

    if profile.mode == "new" and not profile.settings.get("key_categories"):
        problems.append(
            "Modus 'new' ohne key_categories - der Lauf haette keinen Suchfokus"
        )
    if profile.mode == "gaps" and not profile.settings.get("target_columns"):
        problems.append(
            "Modus 'gaps' ohne target_columns - es waere nicht definiert, "
            "welche Luecken gefuellt werden sollen"
        )
    if profile.limit is not None and profile.limit < 1:
        problems.append(f"limit={profile.limit} ist kleiner als 1")

    return problems


def apply_profile(config: dict[str, Any], profile: Profile) -> dict[str, Any]:
    """
    Legt ein Profil ueber die Grundeinstellungen aus targets.yaml.

    Das Ergebnis ist eine neue Zuordnung; `config` bleibt unveraendert.
    """
    merged = copy.deepcopy(config)
    merged["mode"] = profile.mode

    section = "new" if profile.mode == "new" else "gaps"
    allowed = NEW_KEYS if profile.mode == "new" else GAPS_KEYS
    target = merged.setdefault(section, {}) or {}

    for key in allowed:
        if key in profile.settings:
            target[key] = profile.settings[key]
    merged[section] = target

    # Limits und Modellwahl duerfen ebenfalls je Profil abweichen.
    for key in ("max_pages_per_company", "max_products_per_company",
                "crawl_delay_seconds", "request_timeout_seconds"):
        if key in profile.settings:
            merged.setdefault("limits", {})[key] = profile.settings[key]
    for key in ("model", "discovery_model", "timeout_seconds", "max_retries"):
        if key in profile.settings:
            merged.setdefault("llm", {})[key] = profile.settings[key]

    if profile.limit is not None:
        merged.setdefault("limits", {})["max_companies_per_run"] = profile.limit

    log.info("Profil '%s' angewendet: %s", profile.name, profile.summary)
    return merged


def resolve(
    config: dict[str, Any],
    profiles: dict[str, Profile],
    name: str,
    taxonomy: Taxonomy,
) -> tuple[dict[str, Any], Profile]:
    """
    Sucht ein Profil, prueft es und legt es ueber die Grundeinstellungen.

    Raises:
        ProfileError: unbekannter Name oder fehlerhaftes Profil.
    """
    if name not in profiles:
        available = ", ".join(sorted(profiles)) or "(keine)"
        raise ProfileError(
            f"Profil '{name}' gibt es nicht. Vorhanden: {available}. "
            f"Die Liste zeigt auch: py -m cintel profiles"
        )
    profile = profiles[name]
    problems = validate_profile(profile, taxonomy)
    if problems:
        joined = "\n  - ".join(problems)
        raise ProfileError(f"Profil '{name}' ist fehlerhaft:\n  - {joined}")
    return apply_profile(config, profile), profile
