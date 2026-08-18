"""
Abgleich neuer Kandidaten gegen den Bestand.

Diese Stufe hat in den Vorlaeufer-Anlaeufen gefehlt - deshalb enthaelt v2.2
Dubletten (Revit zweimal, 3D Spark zweimal). Gematcht wird zweistufig:

  1. registrierbare Domain (stark)  - gleiche Domain = gleiche Firma
  2. normalisierter Firmenname      - Rechtsformen und Sonderzeichen entfernt

Zusaetzlich ein unscharfer Namensvergleich ueber difflib, um Schreibvarianten
("Dassault Systemes" vs. "Dassault Systemes SE") zu fangen.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from difflib import SequenceMatcher
from urllib.parse import urlparse

log = logging.getLogger(__name__)

# Mehrteilige Public Suffixes, bei denen ein Label mehr zur Domain gehoert.
MULTI_PART_TLDS = {
    "co.uk", "org.uk", "ac.uk", "gov.uk", "co.jp", "com.au", "co.nz",
    "com.br", "co.za", "com.cn", "co.in", "com.tr", "com.mx",
}

# Nur echte Rechtsformen. Woerter wie "Systems", "Solutions" oder
# "Technologies" werden BEWUSST nicht entfernt - sie sind haeufig Teil der
# Marke, und ihr Entfernen wuerde "X Systems" und "X Solutions" faelschlich
# verschmelzen.
LEGAL_SUFFIXES = (
    "gmbh", "mbh", "ag", "se", "kg", "ohg", "ug", "inc", "incorporated",
    "ltd", "limited", "llc", "lp", "llp", "plc", "bv", "nv", "sa", "sas",
    "srl", "spa", "ab", "as", "oy", "aps", "corp", "corporation", "co",
    "company", "the",
)

FUZZY_THRESHOLD = 0.90

# Das URL-Feld in v2.2 enthaelt teils mehrere Werte und Fliesstext, z.B.
#   "https://makersite.io/ | Berlin, Germany | Stuttgart, Germany"
# oder sogar einen Seitentitel statt einer URL.
URL_TOKEN_RE = re.compile(r"https?://[^\s|,;]+", re.IGNORECASE)


def first_url(value: str) -> str:
    """Zieht die erste echte http(s)-URL aus einem verschmutzten Feld."""
    if not value:
        return ""
    match = URL_TOKEN_RE.search(str(value))
    return match.group(0).rstrip("/.,;|") if match else ""


def domain_matches_name(domain: str, company: str) -> bool:
    """
    Gehoert die Domain plausibel zur Firma?

    Notwendig, weil Produktzeilen fremde Links enthalten: Autodesk hat eine
    Zeile mit einer makersite.io-Customer-Story. Ohne diese Pruefung wuerde
    die Domain makersite.io auf Autodesk indiziert - und jeder echte
    Makersite-Kandidat faelschlich Autodesk zugeordnet.
    """
    if not domain or not company:
        return False
    domain_core = re.sub(r"[^a-z0-9]+", "", domain.rsplit(".", 1)[0])
    name_core = re.sub(r"[^a-z0-9]+", "", normalize_company(company))
    if not domain_core or not name_core:
        return False
    if len(name_core) >= 4 and name_core in domain_core:
        return True
    if len(domain_core) >= 4 and domain_core in name_core:
        return True
    return SequenceMatcher(None, domain_core, name_core).ratio() >= 0.80


@dataclass
class MatchResult:
    is_duplicate: bool
    matched_company: str = ""
    reason: str = ""
    score: float = 0.0


def registrable_domain(url: str) -> str:
    """
    https://www.makersite.io/pricing -> makersite.io
    https://foo.co.uk/x              -> foo.co.uk

    Bewusst eine Heuristik ohne Public-Suffix-List-Abhaengigkeit; deckt die
    in der DB vorkommenden Faelle ab.
    """
    if not url:
        return ""
    text = first_url(url)
    if not text:
        # Kein http-Token: koennte eine nackte Domain sein ("autodesk.com"),
        # aber auch ein Seitentitel. Nur uebernehmen, wenn es wie eine Domain
        # aussieht und keine Leerzeichen enthaelt.
        bare = str(url).strip()
        if " " in bare or "." not in bare:
            return ""
        text = "https://" + bare
    host = urlparse(text).netloc.lower().split(":")[0]
    host = host.removeprefix("www.")
    if not host or "." not in host:
        return ""
    parts = host.split(".")
    if len(parts) >= 3 and ".".join(parts[-2:]) in MULTI_PART_TLDS:
        return ".".join(parts[-3:])
    return ".".join(parts[-2:])


def normalize_company(name: str) -> str:
    """'Makersite GmbH' -> 'makersite';  'Dassault Systemes SE' -> 'dassault systemes'"""
    text = str(name or "").lower()
    text = re.sub(r"[–—_/,]", " ", text)
    text = re.sub(r"[^a-z0-9\s&+.-]", " ", text)
    tokens = [t for t in re.split(r"[\s.]+", text) if t]
    while tokens and tokens[-1].strip("&+-") in LEGAL_SUFFIXES:
        tokens.pop()
    tokens = [t for t in tokens if t not in ("the",)]
    return " ".join(tokens).strip()


class Deduper:
    """Index ueber den Bestand, gegen den Kandidaten geprueft werden."""

    def __init__(self, known: list[tuple[str, str]]) -> None:
        """
        Args:
            known: Liste aus (Firmenname, URL) des Bestands. URL darf leer sein.
        """
        self.by_domain: dict[str, str] = {}
        self.by_name: dict[str, str] = {}
        self.names: list[tuple[str, str]] = []  # (normalisiert, original)

        for company, url in known:
            company = str(company or "").strip()
            if not company:
                continue
            domain = registrable_domain(url)
            # Nur indizieren, wenn die Domain plausibel zur Firma gehoert -
            # sonst vergiften Partner- und Customer-Story-Links den Index.
            if domain and domain_matches_name(domain, company):
                self.by_domain.setdefault(domain, company)
            norm = normalize_company(company)
            if norm:
                self.by_name.setdefault(norm, company)
                self.names.append((norm, company))

        log.info("Deduper: %d Domains, %d Namen im Bestand",
                 len(self.by_domain), len(self.by_name))

    def check(self, company: str, url: str = "") -> MatchResult:
        """Prueft einen Kandidaten gegen den Bestand."""
        domain = registrable_domain(url)
        if domain and domain in self.by_domain:
            return MatchResult(
                True, self.by_domain[domain],
                f"gleiche Domain ({domain})", 1.0,
            )

        norm = normalize_company(company)
        if not norm:
            return MatchResult(False, reason="leerer Firmenname")

        if norm in self.by_name:
            return MatchResult(
                True, self.by_name[norm], "identischer Name (normalisiert)", 1.0
            )

        best_score, best_name = 0.0, ""
        for known_norm, original in self.names:
            # Guenstiger Vorfilter: gleicher Anfangsbuchstabe.
            if known_norm[:1] != norm[:1]:
                continue
            score = SequenceMatcher(None, norm, known_norm).ratio()
            if score > best_score:
                best_score, best_name = score, original

        if best_score >= FUZZY_THRESHOLD:
            return MatchResult(
                True, best_name,
                f"aehnlicher Name (Score {best_score:.2f})", best_score,
            )

        return MatchResult(False, best_name, f"kein Treffer (bester Score {best_score:.2f})",
                           best_score)

    def partition(
        self, candidates: list[dict]
    ) -> tuple[list[dict], list[dict]]:
        """
        Teilt Kandidaten in (neu, bereits bekannt).

        Erwartet je Kandidat mindestens die Schluessel "name" und "homepage".
        Bekannte Kandidaten bekommen "duplicate_of" und "duplicate_reason".
        """
        fresh: list[dict] = []
        known: list[dict] = []
        # Auch innerhalb des Kandidatensatzes deduplizieren.
        seen_domains: set[str] = set()
        seen_names: set[str] = set()

        for candidate in candidates:
            name = str(candidate.get("name") or "").strip()
            url = str(candidate.get("homepage") or "").strip()

            domain = registrable_domain(url)
            norm = normalize_company(name)
            if (domain and domain in seen_domains) or (norm and norm in seen_names):
                candidate = dict(candidate)
                candidate["duplicate_of"] = "(innerhalb dieses Laufs)"
                candidate["duplicate_reason"] = "doppelt im Kandidatensatz"
                known.append(candidate)
                continue

            result = self.check(name, url)
            if result.is_duplicate:
                candidate = dict(candidate)
                candidate["duplicate_of"] = result.matched_company
                candidate["duplicate_reason"] = result.reason
                known.append(candidate)
            else:
                fresh.append(candidate)
                if domain:
                    seen_domains.add(domain)
                if norm:
                    seen_names.add(norm)

        log.info("Dedupe: %d neu, %d bereits bekannt", len(fresh), len(known))
        return fresh, known
