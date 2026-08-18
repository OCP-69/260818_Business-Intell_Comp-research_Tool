"""
Stufe 1 - Kandidatenfindung.

Zwei Modi:

  gaps: keine Websuche noetig. Es werden Firmen aus dem Bestand ausgewaehlt,
        deren Zielspalten leer sind. Das ist der guenstige und sichere
        Einstieg, weil die Wahrheit bekannt ist.

  new:  Websuche je (Key Category x Sub Category x Region). Die Ergebnisse
        sind ausdruecklich UNGEPRUEFT - erst der Crawl verifiziert sie. Im
        Test lieferte die Suche eine Firma mit falscher Top-Level-Domain
        (carbontrail.com statt .net) und haette ohne Gate eine tote Zeile
        erzeugt.
"""

from __future__ import annotations

import logging
from typing import Any

from .dedupe import first_url
from .llm import LLMError, UsageLedger, call_claude
from .masterdb import CompanyBlock, MasterDB
from .schema import Taxonomy

log = logging.getLogger(__name__)

DISCOVERY_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["companies"],
    "properties": {
        "companies": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["name", "homepage", "rationale"],
                "properties": {
                    "name": {"type": "string"},
                    "homepage": {
                        "type": "string",
                        "description": "Offizielle Startseite, vollstaendige URL",
                    },
                    "rationale": {"type": "string"},
                    "location": {"type": "string"},
                    "stage": {"type": "string"},
                },
            },
        }
    },
}

# Eigener System-Prompt fuer die Suche.
#
# Der Extraktions-Prompt ("erfinde nichts, lass Felder lieber leer") ist hier
# genau falsch: kombiniert mit einer langen Ausschlussliste liefert das Modell
# dann gar keine Treffer mehr - beobachtet im ersten Echtlauf, 0 Kandidaten
# aus drei Suchzellen.
#
# Die Suche DARF grosszuegig sein, weil jeder Kandidat anschliessend das
# Crawl-Gate passieren muss: Homepage abrufbar und Firmenname auf der Seite.
# Erfundenes faellt dort auf und landet in rejected.csv. Vorsicht an dieser
# Stelle bringt nichts ausser leeren Ergebnissen.
DISCOVERY_SYSTEM_PROMPT = (
    "You are a B2B market researcher for industrial software. You actively "
    "search the web and report every plausible company you find in the "
    "requested segment. Report a company whenever your search results show "
    "it exists; take its homepage URL from those results rather than "
    "constructing one. Downstream automation verifies every URL, so being "
    "comprehensive is more useful than being cautious - but never invent a "
    "company you did not find. Answer only with the requested structured data."
)

DISCOVERY_PROMPT = """\
Finde reale Unternehmen im folgenden Marktsegment. Nutze Websuche.

SEGMENT
  Key Category : {key_category}
  Sub Category : {sub_category}
  Region       : {region}
  Reifegrad    : {stages}

EINSCHLUSS
{inclusion}

AUSSCHLUSS
{exclusion}

BEREITS BEKANNT (nicht erneut nennen)
{known}

REGELN
- Suche aktiv im Web. Nenne jedes Unternehmen, das deine Suchergebnisse als
  real existierend ausweisen.
- `homepage` ist die offizielle Startseite, uebernommen aus den
  Suchergebnissen - keine LinkedIn-, Crunchbase- oder Verzeichnisseite.
  Setze keine Domain aus dem Firmennamen zusammen.
- Die Liste unter BEREITS BEKANNT dient nur der Dublettenvermeidung. Sie ist
  kein Hinweis darauf, dass der Markt erschoepft waere - suche unabhaengig
  davon.
- Lieber ein Kandidat zu viel als einer zu wenig: jede Adresse wird
  anschliessend automatisch geprueft, nicht erreichbare Eintraege fallen
  heraus. Gib nur dann nichts zurueck, wenn die Suche wirklich nichts
  hergibt.
- Hoechstens {max_results} Unternehmen.
"""


def select_gap_companies(
    db: MasterDB,
    target_fields: list[str],
    *,
    limit: int = 25,
    require_url: bool = True,
    tier_priority: list[str] | None = None,
) -> list[CompanyBlock]:
    """
    Waehlt Bestandsfirmen mit Luecken in den Zielspalten.

    Sortiert nach Tier-Prioritaet, danach nach Anzahl fehlender Felder.
    """
    tier_priority = tier_priority or []
    candidates: list[tuple[int, int, CompanyBlock]] = []

    for block in db.blocks():
        missing = block.missing_fields(target_fields)
        if not missing:
            continue
        if require_url and not block.urls:
            continue

        source = block.company_row or (block.product_rows[0] if block.product_rows else None)
        tier = str(source.values.get("competitor_tier") or "") if source else ""
        try:
            rank = next(
                i for i, t in enumerate(tier_priority)
                if tier.lower().startswith(t.lower()[:6])
            )
        except StopIteration:
            rank = len(tier_priority)

        candidates.append((rank, -len(missing), block))

    candidates.sort(key=lambda item: (item[0], item[1], item[2].company.lower()))
    selected = [block for _, _, block in candidates[:limit]]
    log.info("Gap-Auswahl: %d von %d Firmen mit Luecken in %s",
             len(selected), len(candidates), target_fields)
    return selected


def discover_new(
    taxonomy: Taxonomy,
    config: dict[str, Any],
    known_names: set[str],
    *,
    model: str = "sonnet",
    limit: int = 25,
    ledger: UsageLedger | None = None,
) -> list[dict[str, Any]]:
    """
    Websuche je Segmentkombination. Ergebnisse sind ungeprueft.

    Returns:
        Kandidaten mit name / homepage / rationale / segment-Metadaten.
    """
    ledger = ledger or UsageLedger()
    key_categories = config.get("key_categories") or taxonomy.key_categories
    sub_categories = config.get("sub_categories") or []
    regions = config.get("regions") or ["Global"]
    stages = config.get("stages") or []
    max_per_cell = int(config.get("max_per_cell", 10))

    # Bekannte Namen gekuerzt mitgeben - der ganze Bestand waere zu lang.
    known_sample = sorted(known_names)
    known_text = ", ".join(known_sample[:400]) or "(keine)"

    out: list[dict[str, Any]] = []
    for key_category in key_categories:
        allowed_subs = taxonomy.valid_subs_for(key_category)
        cells = [s for s in sub_categories if s in allowed_subs] or [""]
        for sub_category in cells:
            for region in regions:
                if len(out) >= limit:
                    log.info("Discovery-Limit %d erreicht.", limit)
                    return out
                prompt = DISCOVERY_PROMPT.format(
                    key_category=key_category,
                    sub_category=sub_category or "(alle)",
                    region=region,
                    stages=", ".join(stages) or "(beliebig)",
                    inclusion=config.get("inclusion_criteria", "(keine)"),
                    exclusion=config.get("exclusion_criteria", "(keine)"),
                    known=known_text,
                    max_results=max_per_cell,
                )
                try:
                    result = call_claude(
                        prompt, DISCOVERY_SCHEMA, model=model,
                        tools="WebSearch,WebFetch",
                        system_prompt=DISCOVERY_SYSTEM_PROMPT,
                    )
                except LLMError as exc:
                    log.warning("Discovery %s/%s/%s fehlgeschlagen: %s",
                                key_category, sub_category, region, exc)
                    continue

                ledger.add(f"discover:{key_category}/{sub_category}/{region}",
                           result.usage)

                for entry in result.data.get("companies", []) or []:
                    homepage = first_url(entry.get("homepage", ""))
                    if not homepage:
                        log.debug("Kandidat ohne brauchbare URL verworfen: %s",
                                  entry.get("name"))
                        continue
                    out.append({
                        "name": str(entry.get("name", "")).strip(),
                        "homepage": homepage,
                        "rationale": entry.get("rationale", ""),
                        "location": entry.get("location", ""),
                        "stage": entry.get("stage", ""),
                        "segment_key_category": key_category,
                        "segment_sub_category": sub_category,
                        "segment_region": region,
                    })
                log.info("Discovery %s / %s / %s: %d Kandidaten",
                         key_category, sub_category or "-", region, len(out))
    return out
