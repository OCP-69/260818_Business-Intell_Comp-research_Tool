"""
Strukturierte Extraktion aus gecrawltem Seitentext.

Erzeugt genau das Zeilenmodell der Master-DB:
  1 Company-Zeile  ("Company information")
  N Produktzeilen  ("Product")

Die Enum-Felder werden ueber das JSON-Schema hart auf das kontrollierte
Vokabular begrenzt und danach nochmals gegen die Taxonomie normalisiert.
Ohne diesen doppelten Boden driftet die DB wieder auseinander - v2.2 hat
34 "Key Categories" statt der vorgesehenen 8.
"""

from __future__ import annotations

import logging
from typing import Any

from .crawl import CrawlResult
from .dedupe import first_url
from .llm import LLMError, LLMResult, UsageLedger, call_claude, call_codex
from .masterdb import today_stamp
from .schema import (
    ROW_KIND_COMPANY,
    ROW_KIND_PRODUCT,
    Record,
    Taxonomy,
    join_multi,
)

log = logging.getLogger(__name__)

MAX_INPUT_CHARS = 55_000


def build_schema(taxonomy: Taxonomy, max_products: int = 25) -> dict[str, Any]:
    """JSON-Schema fuer die Extraktion, mit Enums aus der Taxonomie."""
    confidence = {
        "type": "number", "minimum": 0.0, "maximum": 1.0,
        "description": "0.0 = geraten, 1.0 = woertlich auf der Quellseite belegt",
    }
    text = {"type": "string"}

    product_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["product_name", "source_url", "confidence"],
        "properties": {
            "product_name": {"type": "string", "description": "Offizieller Produktname"},
            "remarks": text,
            "sustainability": text,
            "lca": text,
            "compliance": text,
            "technology": text,
            "usp": text,
            "pricing_model": text,
            "sub_category": {"type": "string", "enum": taxonomy.sub_categories},
            "r_strategies": {
                "type": "array",
                "items": {"type": "string", "enum": taxonomy.r_strategies},
                "description": "Nur setzen, wenn die Quelle es belegt.",
            },
            "r_rationale": text,
            "source_url": {"type": "string", "description": "Belegende URL"},
            "confidence": confidence,
        },
    }

    company_schema = {
        "type": "object",
        "additionalProperties": False,
        "required": ["company", "url", "confidence"],
        "properties": {
            "company": {"type": "string"},
            "url": {"type": "string"},
            "key_categories": {
                "type": "array",
                "items": {"type": "string", "enum": taxonomy.key_categories},
            },
            "sub_category": {"type": "string", "enum": taxonomy.sub_categories},
            "sustainability": text,
            "lca": text,
            "compliance": text,
            "technology": text,
            "key_customers": text,
            "strengths": text,
            "weaknesses": text,
            "usp": text,
            "target_market": text,
            "pricing_model": text,
            "business_model": text,
            "founding_year": {"type": "string"},
            "stage": {"type": "string"},
            "founding_type": {"type": "string"},
            "location": {"type": "string"},
            "employees": {"type": "string"},
            "revenue": {"type": "string"},
            "lfl_dimension": {
                "type": "array",
                "items": {"type": "string", "enum": taxonomy.lfl_dimension},
            },
            "competitor_tier": {"type": "string", "enum": taxonomy.competitor_tier},
            "beachhead": {
                "type": "array",
                "items": {"type": "string", "enum": taxonomy.beachhead_relevanz},
            },
            "confidence": confidence,
        },
    }

    return {
        "type": "object",
        "additionalProperties": False,
        "required": ["company_info", "products"],
        "properties": {
            "company_info": company_schema,
            "products": {
                "type": "array", "maxItems": max_products, "items": product_schema,
            },
            "notes": {
                "type": "string",
                "description": "Was konnte nicht belegt werden und warum.",
            },
        },
    }


EXTRACTION_INSTRUCTIONS = """\
Du erstellst Wettbewerbsdaten fuer eine Master-Datenbank der diskreten Fertigung
(CAD/CAE/PLM/MES/LCA/Industrial AI).

REGELN
1. Nutze AUSSCHLIESSLICH den unten gelieferten Seitentext. Erfinde nichts.
2. Ist ein Wert nicht belegt, lass das Feld LEER - schreibe nicht "N/A",
   "unbekannt" oder eine Schaetzung.
3. `products` enthaelt die eigenstaendig benannten Produkte/Module der Firma.
   Marketing-Schlagworte, Blog-Rubriken oder Branchenseiten sind KEINE Produkte.
   Hat die Firma nur ein Produkt gleichen Namens, gib genau dieses eine an.
4. `source_url` je Produkt muss eine der gelieferten SOURCE-URLs sein.
5. `confidence`: 1.0 nur bei woertlichem Beleg; 0.5 bei Ableitung; <0.3 wenn unsicher.
6. Enum-Felder duerfen nur die vorgegebenen Werte annehmen.

EINORDNUNG (fuer competitor_tier aus Sicht von LoopForgeLab)
- "Tier 1 – Direkt":     Entscheidungsunterstuetzung in der Angebots-/fruehen
                          Entwicklungsphase: Kosten, Machbarkeit, Nachhaltigkeit.
- "Tier 2 – Nachbar":    Angrenzend (reines PLM, reines LCA, reines CAD).
- "Tier 3 – Beobachten": Nur thematisch verwandt.

FIRMA: {company}
STARTSEITE: {url}

SEITENTEXT:
{pages}
"""


class Extractor:
    """Fuehrt die Extraktion je Firma durch."""

    def __init__(
        self,
        taxonomy: Taxonomy,
        *,
        model: str = "sonnet",
        max_products: int = 25,
        timeout: int = 600,
        max_retries: int = 2,
        ledger: UsageLedger | None = None,
        cross_check: bool = False,
    ) -> None:
        self.taxonomy = taxonomy
        self.model = model
        self.max_products = max_products
        self.timeout = timeout
        self.max_retries = max_retries
        self.ledger = ledger or UsageLedger()
        self.cross_check = cross_check
        self.schema = build_schema(taxonomy, max_products)

    def extract(self, crawl: CrawlResult) -> tuple[list[Record], dict[str, Any]]:
        """
        Wandelt ein Crawl-Ergebnis in Master-DB-Zeilen.

        Returns:
            (Zeilen, Metadaten). Zeilen[0] ist die Company-Zeile.

        Raises:
            LLMError: wenn die Extraktion endgueltig scheitert.
        """
        if not crawl.verified:
            raise LLMError(f"nicht verifiziert: {crawl.reject_reason}")

        pages = crawl.combined_text(MAX_INPUT_CHARS)
        if not pages.strip():
            raise LLMError("kein verwertbarer Seitentext")

        prompt = EXTRACTION_INSTRUCTIONS.format(
            company=crawl.company, url=crawl.start_url, pages=pages
        )

        # Keine Tools: der Seitentext liegt bereits vor. Das ist deutlich
        # schneller, guenstiger und verhindert, dass das Modell Fakten aus
        # dem Netz dazuerfindet, die nicht durch den Crawl belegt sind.
        result: LLMResult = call_claude(
            prompt, self.schema, model=self.model, tools="",
            timeout=self.timeout, max_retries=self.max_retries,
        )
        self.ledger.add(f"extract:{crawl.company}", result.usage)

        data = result.data
        meta: dict[str, Any] = {
            "notes": data.get("notes", ""),
            "pages_used": len(crawl.ok_pages),
            "cross_check": None,
        }

        if self.cross_check:
            meta["cross_check"] = self._run_cross_check(crawl, data)

        records = self._to_records(crawl, data)
        return records, meta

    # -- intern ------------------------------------------------------------

    def _to_records(self, crawl: CrawlResult, data: dict[str, Any]) -> list[Record]:
        info = data.get("company_info", {}) or {}
        stamp = today_stamp()
        company = str(info.get("company") or crawl.company).strip()

        key_categories = [
            c for c in (
                self.taxonomy.canon_key_category(v)
                for v in info.get("key_categories", []) or []
            ) if c
        ]
        beachhead = [
            b for b in (
                self.taxonomy.canon_beachhead(v)
                for v in info.get("beachhead", []) or []
            ) if b
        ]
        tier = self.taxonomy.canon_tier(info.get("competitor_tier"))

        company_values: dict[str, Any] = {
            "last_update": stamp,
            "company": company,
            "product_name": None,
            "row_kind": ROW_KIND_COMPANY,
            "key_categories": join_multi("key_categories", key_categories) or None,
            "sub_category": self.taxonomy.canon_sub_category(info.get("sub_category")),
            "sustainability": _clean(info.get("sustainability")),
            "lca": _clean(info.get("lca")),
            "compliance": _clean(info.get("compliance")),
            "technology": _clean(info.get("technology")),
            "key_customers": _clean(info.get("key_customers")),
            "strengths": _clean(info.get("strengths")),
            "weaknesses": _clean(info.get("weaknesses")),
            "usp": _clean(info.get("usp")),
            "target_market": _clean(info.get("target_market")),
            "pricing_model": _clean(info.get("pricing_model")),
            "business_model": _clean(info.get("business_model")),
            "url": first_url(info.get("url") or "") or crawl.start_url,
            "founding_year": _clean(info.get("founding_year")),
            "stage": _clean(info.get("stage")),
            "founding_type": _clean(info.get("founding_type")),
            "location": _clean(info.get("location")),
            "employees": _clean(info.get("employees")),
            "revenue": _clean(info.get("revenue")),
            "lfl_dimension": join_multi(
                "lfl_dimension", info.get("lfl_dimension", []) or []
            ) or None,
            "competitor_tier": tier,
            "beachhead": join_multi("beachhead", beachhead) or None,
        }
        confidence = float(info.get("confidence", 0.0) or 0.0)
        records = [Record(
            values=company_values,
            sources={"*": crawl.start_url},
            confidence={"*": confidence},
        )]

        for product in data.get("products", []) or []:
            name = _clean(product.get("product_name"))
            if not name:
                continue
            r_strategies = [
                s for s in (product.get("r_strategies") or [])
                if s in self.taxonomy.r_strategies
            ]
            product_values: dict[str, Any] = {
                "last_update": stamp,
                "company": company,
                "product_name": name,
                "row_kind": ROW_KIND_PRODUCT,
                "sub_category": (
                    self.taxonomy.canon_sub_category(product.get("sub_category"))
                    or company_values["sub_category"]
                ),
                "remarks": _clean(product.get("remarks")),
                "sustainability": _clean(product.get("sustainability")),
                "r_strategies": join_multi("r_strategies", r_strategies) or None,
                "r_rationale": _clean(product.get("r_rationale")),
                "lca": _clean(product.get("lca")),
                "compliance": _clean(product.get("compliance")),
                "technology": _clean(product.get("technology")),
                "usp": _clean(product.get("usp")),
                "pricing_model": _clean(product.get("pricing_model")),
                "url": first_url(product.get("source_url") or "") or crawl.start_url,
                "competitor_tier": tier,
                "beachhead": company_values["beachhead"],
            }
            records.append(Record(
                values=product_values,
                sources={"*": product_values["url"]},
                confidence={"*": float(product.get("confidence", 0.0) or 0.0)},
            ))

        return records

    def _run_cross_check(
        self, crawl: CrawlResult, primary: dict[str, Any]
    ) -> dict[str, Any]:
        """
        Zweitmeinung ueber codex fuer die Hartdaten. Abweichungen werden
        gemeldet, nicht stillschweigend uebernommen.
        """
        fields = ["founding_year", "stage", "location", "employees", "revenue"]
        schema = {
            "type": "object", "additionalProperties": False,
            "required": fields,
            "properties": {f: {"type": "string"} for f in fields},
        }
        prompt = (
            f"Ermittle fuer die Firma '{crawl.company}' ({crawl.start_url}) "
            f"ausschliesslich aus dem folgenden Seitentext diese Angaben: "
            f"{', '.join(fields)}. Nicht belegte Felder bleiben leer.\n\n"
            f"{crawl.combined_text(30_000)}"
        )
        try:
            result = call_codex(prompt, schema, timeout=self.timeout)
        except LLMError as exc:
            log.warning("Cross-Check fuer %s fehlgeschlagen: %s", crawl.company, exc)
            return {"available": False, "error": str(exc)}

        self.ledger.add(f"crosscheck:{crawl.company}", result.usage)
        info = primary.get("company_info", {}) or {}
        disagreements = {}
        for name in fields:
            mine = str(info.get(name) or "").strip().lower()
            theirs = str(result.data.get(name) or "").strip().lower()
            if mine and theirs and mine not in theirs and theirs not in mine:
                disagreements[name] = {"claude": mine, "codex": theirs}
        return {"available": True, "disagreements": disagreements}


def _clean(value: Any) -> str | None:
    """Leerwerte und Platzhalter auf None normalisieren."""
    if value in (None, ""):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.lower() in {
        "n/a", "na", "unknown", "unbekannt", "keine angabe", "not available",
        "nicht verfuegbar", "nicht verfügbar", "-", "--", "tbd", "null", "none",
    }:
        return None
    return text
