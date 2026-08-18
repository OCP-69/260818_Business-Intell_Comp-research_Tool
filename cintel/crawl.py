"""
Website-Crawler mit Plattencache.

Der Crawler ist gleichzeitig das Verifikations-Gate der Pipeline: eine Firma
kommt nur in die DB, wenn ihre Homepage wirklich erreichbar ist und der Inhalt
den Firmennamen bestaetigt. Das faengt halluzinierte Firmen ab - im Test hatte
die Discovery eine Domain erfunden, die gar nicht existiert.

Der Cache ist inhaltsadressiert (sha256 der URL). Dadurch kann die Extraktion
beliebig oft neu laufen, ohne die Zielseiten erneut zu belasten oder erneut
LLM-Token zu verbrauchen.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import time
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

log = logging.getLogger(__name__)

USER_AGENT = (
    "cintel/0.1 (LoopForgeLab competitive research; "
    "+https://github.com/OCP-69/260818_Business-Intell_Comp-research_Tool)"
)

# Pfadfragmente, die auf Produkt-/Loesungsseiten hindeuten.
PRODUCT_PATH_HINTS = (
    "product", "produkt", "solution", "loesung", "lösung", "platform",
    "plattform", "software", "features", "funktionen", "modules", "module",
    "services", "leistungen", "technology", "technologie", "pricing", "preise",
)

# Diese Pfade sind fuer die Extraktion wertlos.
SKIP_PATH_HINTS = (
    "/blog", "/news", "/career", "/karriere", "/jobs", "/legal", "/impressum",
    "/privacy", "/datenschutz", "/terms", "/agb", "/login", "/signin",
    "/cookie", "/press", "/presse", "/event", "/webinar", "/support",
    "/download", "/cdn-cgi", "/wp-content", "/wp-admin",
)

BINARY_SUFFIXES = (
    ".pdf", ".zip", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp",
    ".mp4", ".mp3", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
)


@dataclass
class Page:
    url: str
    status: int
    title: str = ""
    text: str = ""
    links: list[str] = field(default_factory=list)
    from_cache: bool = False
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.status == 200 and bool(self.text.strip())


@dataclass
class CrawlResult:
    company: str
    start_url: str
    pages: list[Page] = field(default_factory=list)
    verified: bool = False
    reject_reason: str = ""

    @property
    def ok_pages(self) -> list[Page]:
        return [p for p in self.pages if p.ok]

    def combined_text(self, max_chars: int = 60_000) -> str:
        """Alle Seiten zu einem Extraktions-Input zusammenfassen."""
        chunks: list[str] = []
        budget = max_chars
        for page in self.ok_pages:
            block = f"\n\n=== SOURCE: {page.url}\n=== TITLE: {page.title}\n{page.text}"
            chunks.append(block[:budget])
            budget -= len(block)
            if budget <= 0:
                break
        return "".join(chunks)


class Crawler:
    """HTTP-Crawler mit Cache, robots.txt-Pruefung und Rate-Limit."""

    def __init__(
        self,
        cache_dir: str | Path = "data/cache",
        *,
        delay: float = 1.5,
        timeout: int = 20,
        max_pages: int = 12,
        respect_robots: bool = True,
        offline: bool = False,
    ) -> None:
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.delay = delay
        self.timeout = timeout
        self.max_pages = max_pages
        self.respect_robots = respect_robots
        self.offline = offline
        self._robots: dict[str, urllib.robotparser.RobotFileParser | None] = {}
        self._last_request = 0.0
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": USER_AGENT})

    # -- Cache -------------------------------------------------------------

    def _cache_path(self, url: str) -> Path:
        digest = hashlib.sha256(url.encode("utf-8")).hexdigest()
        sub = self.cache_dir / digest[:2]
        sub.mkdir(parents=True, exist_ok=True)
        return sub / f"{digest}.json"

    def _read_cache(self, url: str) -> Page | None:
        path = self._cache_path(url)
        if not path.exists():
            return None
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return None
        return Page(
            url=data["url"], status=data["status"], title=data.get("title", ""),
            text=data.get("text", ""), links=data.get("links", []) or [],
            from_cache=True, error=data.get("error", ""),
        )

    def _write_cache(self, page: Page) -> None:
        payload = {
            "url": page.url, "status": page.status, "title": page.title,
            "text": page.text, "links": page.links, "error": page.error,
            "fetched_at": time.time(),
        }
        self._cache_path(page.url).write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )

    # -- robots.txt --------------------------------------------------------

    def _allowed(self, url: str) -> bool:
        """
        robots.txt-Pruefung.

        WICHTIG: robots.txt wird ueber die eigene Session geholt, nicht ueber
        RobotFileParser.read(). Dessen urllib-Default-User-Agent wird von
        WAFs wie Cloudflare mit 403 abgewiesen - und ein 403 laesst
        RobotFileParser `disallow_all` setzen. Ergebnis waere, dass voellig
        offene Seiten ("Disallow:" = alles erlaubt) faelschlich als gesperrt
        gelten. Genau das ist bei makersite.io und ecochain.com passiert.
        """
        if not self.respect_robots:
            return True
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"

        if origin not in self._robots:
            parser: urllib.robotparser.RobotFileParser | None = None
            try:
                response = self.session.get(
                    urljoin(origin, "/robots.txt"), timeout=self.timeout
                )
                if response.status_code == 200:
                    parser = urllib.robotparser.RobotFileParser()
                    parser.parse(response.text.splitlines())
                elif response.status_code in (401, 403):
                    # Explizit abgeschirmt -> als Verbot werten.
                    parser = urllib.robotparser.RobotFileParser()
                    parser.disallow_all = True
                # 404 / 5xx -> keine robots.txt -> parser bleibt None (erlaubt)
            except requests.RequestException:
                parser = None
            self._robots[origin] = parser

        parser = self._robots[origin]
        if parser is None:
            return True
        try:
            return parser.can_fetch(USER_AGENT, url)
        except Exception:
            return True

    # -- Fetch -------------------------------------------------------------

    def fetch(self, url: str) -> Page:
        cached = self._read_cache(url)
        if cached is not None:
            return cached
        if self.offline:
            return Page(url=url, status=0, error="offline: nicht im Cache")
        if not self._allowed(url):
            page = Page(url=url, status=0, error="durch robots.txt untersagt")
            self._write_cache(page)
            return page

        elapsed = time.monotonic() - self._last_request
        if elapsed < self.delay:
            time.sleep(self.delay - elapsed)
        self._last_request = time.monotonic()

        try:
            response = self.session.get(url, timeout=self.timeout, allow_redirects=True)
            content_type = response.headers.get("Content-Type", "").lower()
            is_xml = "xml" in content_type or url.lower().endswith(".xml")
            if is_xml:
                # Sitemaps unveraendert ablegen - <loc>-Eintraege werden
                # spaeter per Regex gelesen, BeautifulSoup wuerde sie zerlegen.
                page = Page(url=url, status=response.status_code,
                            title="(sitemap)", text=response.text[:400_000])
            elif "html" not in content_type:
                page = Page(url=url, status=response.status_code,
                            error=f"kein HTML ({content_type[:40]})")
            else:
                title, text = _extract_text(response.text)
                # Links direkt hier extrahieren - danach ist das rohe HTML weg.
                links = _links_from_html(response.text, response.url or url)
                page = Page(url=url, status=response.status_code, title=title,
                            text=text, links=links)
        except requests.RequestException as exc:
            page = Page(url=url, status=0, error=f"{type(exc).__name__}: {exc}"[:200])

        self._write_cache(page)
        return page

    # -- Seitenauswahl -----------------------------------------------------

    def crawl_company(self, company: str, start_url: str) -> CrawlResult:
        """
        Crawlt eine Firma ab ihrer Startseite und verifiziert sie.

        Verifikation: Homepage liefert HTTP 200 mit Text UND der Firmenname
        taucht in Titel oder Text auf. Sonst -> verified=False mit Grund.
        """
        result = CrawlResult(company=company, start_url=start_url)

        if not start_url or not start_url.lower().startswith("http"):
            result.reject_reason = f"keine gueltige Start-URL: {start_url!r}"
            return result

        home = self.fetch(start_url)
        result.pages.append(home)

        if not home.ok:
            result.reject_reason = (
                f"Homepage nicht erreichbar (status={home.status} {home.error})"
            )
            return result

        if not _name_matches(company, home):
            result.reject_reason = (
                f"Firmenname '{company}' nicht auf der Seite gefunden "
                f"(Titel: {home.title[:60]!r})"
            )
            return result

        result.verified = True

        candidates = _sitemap_candidates(self, start_url)
        for link in _nav_candidates(home, start_url):
            if link not in candidates:
                candidates.append(link)

        for link in candidates:
            if len(result.pages) >= self.max_pages:
                break
            if link == start_url:
                continue
            result.pages.append(self.fetch(link))

        log.info("Crawl %s: %d Seiten (%d ok), verifiziert=%s",
                 company, len(result.pages), len(result.ok_pages), result.verified)
        return result


# --------------------------------------------------------------------------
# Hilfsfunktionen
# --------------------------------------------------------------------------

def _extract_text(html: str) -> tuple[str, str]:
    """HTML -> (Titel, sichtbarer Text)."""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style", "noscript", "svg", "iframe"]):
        tag.decompose()
    title = soup.title.get_text(strip=True) if soup.title else ""
    text = re.sub(r"\n{3,}", "\n\n", soup.get_text("\n", strip=True))
    return title, text


def _links_from_html(html: str, base: str) -> list[str]:
    soup = BeautifulSoup(html, "html.parser")
    out: list[str] = []
    for anchor in soup.find_all("a", href=True):
        href = anchor["href"].strip()
        if href.startswith(("#", "mailto:", "tel:", "javascript:")):
            continue
        out.append(urljoin(base, href).split("#")[0].rstrip("/"))
    return out


def _sitemap_candidates(crawler: Crawler, start_url: str) -> list[str]:
    """Produktseiten aus sitemap.xml ziehen, falls vorhanden."""
    parsed = urlparse(start_url)
    origin = f"{parsed.scheme}://{parsed.netloc}"
    sitemap_page = crawler.fetch(urljoin(origin, "/sitemap.xml"))
    if not sitemap_page.text and not sitemap_page.title:
        return []
    urls = re.findall(r"<loc>\s*([^<\s]+)\s*</loc>", sitemap_page.text, re.IGNORECASE)
    scored: list[str] = []
    for url in urls:
        path = urlparse(url).path.lower()
        if path.endswith(BINARY_SUFFIXES):
            continue
        if any(skip in path for skip in SKIP_PATH_HINTS):
            continue
        if any(hint in path for hint in PRODUCT_PATH_HINTS):
            clean = url.split("#")[0].rstrip("/")
            if clean not in scored:
                scored.append(clean)
    return scored[: crawler.max_pages * 2]


def _nav_candidates(home: Page, start_url: str) -> list[str]:
    """
    Produktrelevante Links aus der Startseite (Fallback zur Sitemap).

    Nutzt die beim Fetch extrahierten und mitgecachten Links - das rohe HTML
    wird bewusst nicht aufbewahrt.
    """
    base_host = urlparse(start_url).netloc.lower().removeprefix("www.")
    out: list[str] = []
    for link in home.links:
        parsed = urlparse(link)
        if parsed.netloc.lower().removeprefix("www.") != base_host:
            continue
        path = parsed.path.lower()
        if path.endswith(BINARY_SUFFIXES) or any(s in path for s in SKIP_PATH_HINTS):
            continue
        if any(hint in path for hint in PRODUCT_PATH_HINTS) and link not in out:
            out.append(link)
    return out


def _name_matches(company: str, page: Page) -> bool:
    """
    Prueft, ob der Firmenname auf der Seite vorkommt.

    Toleriert Rechtsformen und Sonderzeichen: "Makersite GmbH" matcht
    "makersite". Sehr kurze Namen (< 3 Zeichen) werden nicht geprueft, weil
    sie zu viele Falschtreffer erzeugen.
    """
    core = _name_core(company)
    if len(core) < 3:
        return True
    haystack = f"{page.title} {page.text[:8000]} {page.url}".lower()
    haystack = re.sub(r"[^a-z0-9]+", "", haystack)
    return core in haystack


def _name_core(company: str) -> str:
    text = str(company or "").lower()
    text = re.sub(
        r"\b(gmbh|ag|se|inc|ltd|llc|bv|b\.v\.|sa|s\.a\.|nv|plc|corp|corporation|"
        r"co|company|technologies|technology|software|systems|group|holding)\b",
        " ", text,
    )
    return re.sub(r"[^a-z0-9]+", "", text)
