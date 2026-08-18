"""
Qualitaetspruefung fuer Bestand und Neuzugaenge.

Die Pruefungen bilden die Probleme ab, die in v2.2 tatsaechlich vorkommen:
kaputte Kodierung, uneinheitliche Tier-Schreibweise, Seitentitel im URL-Feld,
Company_ID als Fliesskommazahl, doppelte Produktzeilen, Vokabular-Drift.
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from .dedupe import first_url
from .schema import (
    ROW_KIND_COMPANY,
    ROW_KIND_PRODUCT,
    Record,
    Taxonomy,
    split_multi,
)

SEVERITY_ORDER = {"error": 0, "warning": 1, "info": 2}

# U+FFFD ist das Ersatzzeichen; die uebrigen Muster sind typische
# UTF-8-als-cp1252-Fehlinterpretationen.
MOJIBAKE_RE = re.compile(r"[�]|Ã[-¿]|â€[]")


@dataclass
class Issue:
    severity: str
    code: str
    message: str
    row: int | None = None
    company: str = ""

    def __str__(self) -> str:
        where = f" [Zeile {self.row}]" if self.row is not None else ""
        who = f" ({self.company})" if self.company else ""
        return f"{self.severity.upper():7s} {self.code:22s}{where}{who} {self.message}"


@dataclass
class Report:
    issues: list[Issue] = field(default_factory=list)
    stats: dict[str, Any] = field(default_factory=dict)

    def add(self, severity: str, code: str, message: str,
            row: int | None = None, company: str = "") -> None:
        self.issues.append(Issue(severity, code, message, row, company))

    @property
    def errors(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "error"]

    @property
    def warnings(self) -> list[Issue]:
        return [i for i in self.issues if i.severity == "warning"]

    def by_code(self) -> dict[str, int]:
        return dict(Counter(i.code for i in self.issues))

    def render(self, limit: int = 40) -> str:
        lines = ["=" * 72, "VALIDIERUNGSBERICHT", "=" * 72]
        for key, value in self.stats.items():
            lines.append(f"  {key:32s} {value}")
        lines.append("-" * 72)
        counts = self.by_code()
        if not counts:
            lines.append("  Keine Befunde.")
        else:
            for code, count in sorted(counts.items(), key=lambda kv: -kv[1]):
                lines.append(f"  {count:5d}  {code}")
            lines.append("-" * 72)
            ordered = sorted(self.issues, key=lambda i: SEVERITY_ORDER[i.severity])
            for issue in ordered[:limit]:
                lines.append("  " + str(issue))
            if len(self.issues) > limit:
                lines.append(f"  ... und {len(self.issues) - limit} weitere")
        lines.append("=" * 72)
        lines.append(
            f"  {len(self.errors)} Fehler, {len(self.warnings)} Warnungen, "
            f"{len(self.issues)} Befunde gesamt"
        )
        return "\n".join(lines)


def validate_records(
    records: Iterable[Record],
    taxonomy: Taxonomy,
    *,
    strict: bool = False,
) -> Report:
    """
    Prueft eine Zeilenmenge gegen den Schema-Vertrag.

    Args:
        strict: wenn True, gelten Vokabular-Abweichungen als Fehler statt
                als Warnung (fuer neue Zeilen sinnvoll, fuer den Bestand nicht).
    """
    report = Report()
    records = list(records)
    severity_vocab = "error" if strict else "warning"

    seen_rows: dict[tuple[str, str, str], int] = {}
    company_rows: dict[str, int] = defaultdict(int)
    id_to_company: dict[str, set[str]] = defaultdict(set)

    for index, record in enumerate(records, start=2):  # +2 = Excel-Zeilennummer
        values = record.values
        company = str(values.get("company") or "").strip()
        product = str(values.get("product_name") or "").strip()
        kind = str(values.get("row_kind") or "").strip()

        if not company:
            report.add("error", "company_missing", "Company ist leer", index)
            continue

        # -- Zeilenmodell --------------------------------------------------
        if kind not in (ROW_KIND_COMPANY, ROW_KIND_PRODUCT):
            report.add("error", "row_kind_invalid",
                       f"'Company & Product' = {kind!r}", index, company)
        if kind == ROW_KIND_COMPANY:
            company_rows[company] += 1
            if product:
                report.add("warning", "company_row_has_product",
                           f"Company-Zeile traegt Product_name {product!r}",
                           index, company)
        elif kind == ROW_KIND_PRODUCT and not product:
            report.add("warning", "product_row_no_name",
                       "Produktzeile ohne Product_name", index, company)

        # -- Company_ID ----------------------------------------------------
        company_id = values.get("company_id")
        if company_id not in (None, ""):
            # Jeder Float ist zu beanstanden, auch 42.0: die kanonische Form
            # ist die Ganzzahl. Ein Wertvergleich (42.0 != 42) wuerde nichts
            # finden, weil Python beide als gleich behandelt.
            if isinstance(company_id, float):
                report.add("warning", "company_id_not_int",
                           f"Company_ID ist keine Ganzzahl: {company_id!r}",
                           index, company)
            id_to_company[str(int(float(company_id)))
                          if _is_number(company_id) else str(company_id)].add(company)

        # -- URL -----------------------------------------------------------
        raw_url = str(values.get("url") or "").strip()
        if raw_url:
            extracted = first_url(raw_url)
            if not extracted:
                report.add("error", "url_not_a_url",
                           f"URL-Feld enthaelt keine URL: {raw_url[:60]!r}",
                           index, company)
            elif extracted != raw_url.rstrip("/"):
                report.add("warning", "url_polluted",
                           f"URL-Feld enthaelt Zusatztext: {raw_url[:60]!r}",
                           index, company)

        # -- Kodierung -----------------------------------------------------
        for fieldname, value in values.items():
            if isinstance(value, str) and MOJIBAKE_RE.search(value):
                report.add("error", "encoding_broken",
                           f"Kaputte Kodierung in '{fieldname}': {value[:45]!r}",
                           index, company)
                break

        # -- Vokabular -----------------------------------------------------
        tier = values.get("competitor_tier")
        if tier not in (None, ""):
            canonical = taxonomy.canon_tier(tier)
            if canonical is None:
                report.add("error", "tier_invalid",
                           f"Competitor_Tier unbekannt: {tier!r}", index, company)
            elif str(tier).strip() != canonical:
                report.add("warning", "tier_non_canonical",
                           f"Tier-Schreibweise abweichend: {tier!r}", index, company)

        for value in split_multi(values.get("key_categories")):
            if taxonomy.canon_key_category(value) is None:
                report.add(severity_vocab, "key_category_unknown",
                           f"Key Category ausserhalb der Taxonomie: {value!r}",
                           index, company)
        for value in split_multi(values.get("sub_category")):
            if taxonomy.canon_sub_category(value) is None:
                report.add(severity_vocab, "sub_category_unknown",
                           f"Sub Category ausserhalb der Taxonomie: {value!r}",
                           index, company)
        for value in split_multi(values.get("beachhead")):
            for part in re.split(r"\s*,\s*", value):
                if part and taxonomy.canon_beachhead(part) is None:
                    report.add(severity_vocab, "beachhead_unknown",
                               f"Beachhead unbekannt: {part!r}", index, company)

        # -- Dubletten -----------------------------------------------------
        key = (company.lower(), product.lower(), first_url(raw_url).lower())
        if key in seen_rows and (product or kind == ROW_KIND_COMPANY):
            report.add("warning", "duplicate_row",
                       f"identisch zu Zeile {seen_rows[key]}", index, company)
        else:
            seen_rows[key] = index

    # -- blockweite Pruefungen --------------------------------------------
    for company, count in company_rows.items():
        if count > 1:
            report.add("warning", "multiple_company_rows",
                       f"{count} Company-Zeilen fuer dieselbe Firma", None, company)

    for company_id, names in id_to_company.items():
        if len(names) > 1:
            report.add("error", "company_id_collision",
                       f"Company_ID {company_id} nutzt {len(names)} Namen: "
                       f"{sorted(names)[:4]}")

    firms = {str(r.values.get("company") or "").strip() for r in records}
    report.stats = {
        "Zeilen geprueft": len(records),
        "Firmen": len(firms - {""}),
        "Company-Zeilen": sum(company_rows.values()),
        "Produktzeilen": sum(
            1 for r in records if r.values.get("row_kind") == ROW_KIND_PRODUCT
        ),
    }
    return report


def _is_number(value: Any) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False
