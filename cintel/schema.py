"""
Schema-Vertrag der Competitive Intel Master DB.

Die Master-DB (v2.2) hat 31 Spalten in fester Reihenfolge und ein
zweistufiges Zeilenmodell je Firma:

    Company_ID 42 | Company & Product = "Company information" | Product_name = leer
    Company_ID 42 | Company & Product = "Product"             | Product_name = "Revit"
    Company_ID 42 | Company & Product = "Product"             | Product_name = "Fusion 360"

Dieses Modul ist die einzige Stelle, die diesen Vertrag kennt. Header werden
NICHT hart verglichen - die echten Header enthalten Zeilenumbrueche und
Trailing-Spaces (z.B. "R-Strategy: Rationale \\nto categorization"). Statt
dessen wird ueber `normalize_header()` tolerant aufgeloest.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

# --------------------------------------------------------------------------
# Kanonische Spalten. Der Key ist der stabile interne Name, der Wert der
# menschenlesbare Header wie er in der xlsx steht (ohne Zeilenumbrueche).
# --------------------------------------------------------------------------

COLUMNS: list[tuple[str, str]] = [
    ("company_id",        "Company_ID"),
    ("last_update",       "Last_Update"),
    ("company",           "Company"),
    ("product_name",      "Product_name"),
    ("row_kind",          "Company & Product"),
    ("key_categories",    "All_Key_Categories of this company"),
    ("sub_category",      "Sub Category B"),
    ("remarks",           "Remarks Product/solution"),
    ("sustainability",    "Sustainability Features"),
    ("r_strategies",      "R-Strategies"),
    ("r_rationale",       "R-Strategy: Rationale to categorization"),
    ("lca",               "LCA Capabilities"),
    ("compliance",        "Regulatory Compliance"),
    ("technology",        "Technology/Innovation"),
    ("key_customers",     "Key customers"),
    ("strengths",         "Strengths"),
    ("weaknesses",        "Weaknesses"),
    ("usp",               "USP"),
    ("target_market",     "Target Market"),
    ("pricing_model",     "Pricing Model"),
    ("business_model",    "Business Model"),
    ("url",               "URL"),
    ("founding_year",     "Founding Year"),
    ("stage",             "Stage/ Status"),
    ("founding_type",     "Founding/ founding type"),
    ("location",          "Location"),
    ("employees",         "# Employees"),
    ("revenue",           "Revenue (Year)"),
    ("lfl_dimension",     "LFL_Dimension"),
    ("competitor_tier",   "Competitor_Tier"),
    ("beachhead",         "Beachhead_Relevanz"),
]

FIELD_NAMES: list[str] = [k for k, _ in COLUMNS]
HEADER_NAMES: list[str] = [v for _, v in COLUMNS]
N_COLUMNS = len(COLUMNS)

ROW_KIND_COMPANY = "Company information"
ROW_KIND_PRODUCT = "Product"

# Felder, die NUR auf der Company-Zeile stehen.
COMPANY_LEVEL_FIELDS = {
    "founding_year", "stage", "founding_type", "location",
    "employees", "revenue", "key_categories",
}

# Felder, die je Produkt unterschiedlich sein duerfen.
PRODUCT_LEVEL_FIELDS = {
    "product_name", "remarks", "sustainability", "r_strategies",
    "r_rationale", "lca", "compliance", "technology", "usp",
    "pricing_model", "url", "sub_category",
}

# Mehrwertige Felder und ihr kanonisches Trennzeichen.
MULTI_VALUE_SEPARATORS = {
    "key_categories": " | ",
    "sub_category": "; ",
    "lfl_dimension": ", ",
    "beachhead": ", ",
    "r_strategies": ", ",
}


class SchemaError(RuntimeError):
    """Die Master-DB entspricht nicht dem erwarteten 31-Spalten-Vertrag."""


# --------------------------------------------------------------------------
# Header-Aufloesung
# --------------------------------------------------------------------------

def normalize_header(value: Any) -> str:
    """
    Bringt einen Header auf eine vergleichbare Form: Unicode-normalisiert,
    Whitespace/Zeilenumbrueche kollabiert, lowercase.

    "R-Strategy: Rationale \\nto categorization"
        -> "r-strategy: rationale to categorization"
    """
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace(" ", " ")
    return re.sub(r"\s+", " ", text).strip().lower()


def resolve_columns(actual_headers: list[Any]) -> dict[str, int]:
    """
    Bildet interne Feldnamen auf die 0-basierte Spaltenposition der
    tatsaechlichen Datei ab.

    Toleriert Zeilenumbrueche und Trailing-Spaces. Die R-Strategies-Spalte
    hat in v2.2 einen mehrzeiligen Header, der die komplette Legende
    enthaelt - deshalb zusaetzlich eine Praefix-Pruefung.

    Raises:
        SchemaError: wenn eine Pflichtspalte fehlt.
    """
    norm_actual = [normalize_header(h) for h in actual_headers]
    mapping: dict[str, int] = {}
    used: set[int] = set()
    missing: list[str] = []

    # Runde 1: exakte Treffer nach Normalisierung.
    for fieldname, header in COLUMNS:
        want = normalize_header(header)
        if want in norm_actual:
            pos = norm_actual.index(want)
            if pos not in used:
                mapping[fieldname] = pos
                used.add(pos)

    # Runde 2: Praefix-Treffer fuer die noch offenen Felder.
    for fieldname, header in COLUMNS:
        if fieldname in mapping:
            continue
        want = normalize_header(header)
        stem = want.split(":")[0].split("(")[0].strip()
        pos = None
        for i, got in enumerate(norm_actual):
            if i in used or not got:
                continue
            got_stem = got.split(":")[0].split("(")[0].strip()
            if got.startswith(stem) or got_stem.startswith(stem) or stem.startswith(got_stem):
                pos = i
                break
        if pos is None:
            missing.append(header)
        else:
            mapping[fieldname] = pos
            used.add(pos)

    if missing:
        raise SchemaError(
            f"{len(missing)} Pflichtspalte(n) nicht in der Master-DB gefunden: "
            f"{missing}. Gefundene Header: {[str(h)[:40] for h in actual_headers]}"
        )
    return mapping


# --------------------------------------------------------------------------
# Taxonomie
# --------------------------------------------------------------------------

@dataclass
class Taxonomy:
    """Kontrolliertes Vokabular aus config/taxonomy.yaml."""

    key_categories: list[str] = field(default_factory=list)
    key_category_aliases: dict[str, str | None] = field(default_factory=dict)
    sub_categories: list[str] = field(default_factory=list)
    sub_category_aliases: dict[str, str | None] = field(default_factory=dict)
    legend: dict[str, list[str]] = field(default_factory=dict)
    competitor_tier: list[str] = field(default_factory=list)
    beachhead_relevanz: list[str] = field(default_factory=list)
    lfl_dimension: list[str] = field(default_factory=list)
    r_strategies: list[str] = field(default_factory=list)

    @classmethod
    def load(cls, path: str | Path) -> Taxonomy:
        data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
        return cls(
            key_categories=data.get("key_categories", []),
            key_category_aliases=data.get("key_category_aliases", {}) or {},
            sub_categories=data.get("sub_categories", []),
            sub_category_aliases=data.get("sub_category_aliases", {}) or {},
            legend=data.get("legend", {}) or {},
            competitor_tier=data.get("competitor_tier", []),
            beachhead_relevanz=data.get("beachhead_relevanz", []),
            lfl_dimension=data.get("lfl_dimension", []),
            r_strategies=data.get("r_strategies", []),
        )

    def canon_key_category(self, value: Any) -> str | None:
        """Drift-Variante -> kanonische Key Category. None = verwerfen."""
        return _canon(value, self.key_categories, self.key_category_aliases)

    def canon_sub_category(self, value: Any) -> str | None:
        return _canon(value, self.sub_categories, self.sub_category_aliases)

    def canon_tier(self, value: Any) -> str | None:
        """
        Normalisiert Tier-Werte. v2.2 enthaelt "Tier 2 - Nachbar"
        (Bindestrich) und mojibake-kaputte Varianten - beide werden auf die
        Halbgeviertstrich-Schreibweise gezogen.
        """
        if value in (None, ""):
            return None
        text = re.sub(r"\s+", " ", str(value)).strip()
        match = re.match(r"^tier\s*([123])\b", text, flags=re.IGNORECASE)
        if not match:
            return None
        for canonical in self.competitor_tier:
            if canonical.lower().startswith(f"tier {match.group(1)}"):
                return canonical
        return None

    def canon_beachhead(self, value: Any) -> str | None:
        return _canon(value, self.beachhead_relevanz, {})

    def valid_subs_for(self, key_category: str) -> list[str]:
        """Zulaessige Sub-Kategorien laut Legend-Sheet."""
        return self.legend.get(key_category, self.sub_categories)


def _canon(value: Any, allowed: list[str], aliases: dict[str, str | None]) -> str | None:
    if value in (None, ""):
        return None
    text = re.sub(r"\s+", " ", str(value)).strip()
    if not text:
        return None
    for candidate in allowed:
        if candidate.lower() == text.lower():
            return candidate
    for alias, target in aliases.items():
        if alias.lower() == text.lower():
            return target
    return None


# --------------------------------------------------------------------------
# Datensatz-Container
# --------------------------------------------------------------------------

@dataclass
class Record:
    """
    Eine Zeile der Master-DB. `values` ist auf interne Feldnamen indiziert,
    `sources` haelt je Feld die Quell-URL, `confidence` den Wert 0.0-1.0.
    """

    values: dict[str, Any] = field(default_factory=dict)
    sources: dict[str, str] = field(default_factory=dict)
    confidence: dict[str, float] = field(default_factory=dict)

    @property
    def is_company_row(self) -> bool:
        return self.values.get("row_kind") == ROW_KIND_COMPANY

    def to_row(self, column_map: dict[str, int]) -> list[Any]:
        """Serialisiert in die Spaltenreihenfolge der Zieldatei."""
        row: list[Any] = [None] * (max(column_map.values()) + 1)
        for fieldname, pos in column_map.items():
            row[pos] = self.values.get(fieldname)
        return row


def join_multi(field_name: str, values: list[str]) -> str:
    """Mehrwertige Felder mit dem kanonischen Trennzeichen zusammensetzen."""
    sep = MULTI_VALUE_SEPARATORS.get(field_name, "; ")
    seen: list[str] = []
    for value in values:
        text = str(value).strip()
        if text and text not in seen:
            seen.append(text)
    return sep.join(seen)


def split_multi(value: Any) -> list[str]:
    """Zerlegt ein mehrwertiges Feld an | oder ;."""
    if value in (None, ""):
        return []
    out: list[str] = []
    for part in re.split(r"\s*[|;]\s*", str(value)):
        part = part.strip()
        if part:
            out.append(part)
    return out
