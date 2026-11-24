"""Check candidate source URLs the way the pipeline would: robots.txt for our user agent, then one polite GET.

Usage: python scripts/check_source.py [--terms] URL [URL ...]
Prints one line per URL: ALLOWED/BLOCKED, HTTP status, and the deadline the watchlist extractor would find.
--terms also finds the site's terms/legal pages (linked from the URL's page) and prints every sentence that
mentions robots, scraping, automated access or reproduction. A person/agent still has to judge those sentences.
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from urllib.parse import urljoin, urlparse

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

from pipeline.adapters.watchlist import CLOSED_RE, extract_deadline  # noqa: E402
from pipeline.compliance import ComplianceBlockedError, CompliantHttpClient  # noqa: E402


TERMS_LINK_RE = re.compile(r"terms|legal|conditions|terms-of-use|tos\b", re.IGNORECASE)
CLAUSE_RE = re.compile(r"robot|spider|scrap|crawl|data.?min|harvest|automat|bot\b|reproduc|copy|republish|framing|mirror",
                       re.IGNORECASE)


def scan_terms(client: CompliantHttpClient, page_url: str, html: str) -> None:
    soup = BeautifulSoup(html, "lxml")
    host = urlparse(page_url).netloc.removeprefix("www.")
    links = []
    for a in soup.find_all("a", href=True):
        href = urljoin(page_url, a["href"])
        if TERMS_LINK_RE.search(f"{a.get_text(' ')} {a['href']}") and host in urlparse(href).netloc and href not in links:
            links.append(href)
    home = f"{urlparse(page_url).scheme}://{urlparse(page_url).netloc}/"
    if not links and page_url.rstrip("/") + "/" != home:
        try:
            return scan_terms(client, home, client.get(home).text)  # footer links usually live on the homepage
        except Exception as exc:
            print(f"    terms: homepage {home} -> {type(exc).__name__}")
    if not links:
        print("    terms: no terms/legal link found (check the footer by hand; no terms is OK once confirmed)")
    for href in links[:3]:
        try:
            text = " ".join(BeautifulSoup(client.get(href).text, "lxml").get_text(" ").split())
        except Exception as exc:
            print(f"    terms: {href} -> {type(exc).__name__}: {str(exc)[:80]}")
            continue
        hits = [s for s in re.split(r"(?<=[.;])\s+", text) if CLAUSE_RE.search(s)]
        print(f"    terms: {href} ({len(text)} chars, {len(hits)} flagged sentences)")
        for sentence in hits[:12]:
            print(f"      - {sentence[:300]}")


def main(urls: list[str]) -> int:
    terms = "--terms" in urls
    urls = [u.strip() for u in urls if u.strip() and u != "--terms"]
    sys.stdout.reconfigure(errors="replace")  # Windows consoles can't print every page character
    cfg = yaml.safe_load((Path(__file__).resolve().parents[1] / "config.yaml").read_text())["compliance"]
    with CompliantHttpClient(user_agent=cfg["user_agent"], min_delay=cfg["min_delay_seconds"],
                             max_delay=cfg["max_delay_seconds"]) as client:
        for url in urls:
            try:
                resp = client.get(url)
            except ComplianceBlockedError as exc:
                print(f"BLOCKED  {url}  ({exc})")
                continue
            except Exception as exc:  # network errors, 404, 5xx
                print(f"ERROR    {url}  ({type(exc).__name__}: {str(exc)[:80]})")
                continue
            text = " ".join(BeautifulSoup(resp.text, "lxml").get_text(" ").split())
            iso, ev = extract_deadline(text)
            closed = " CLOSED" if CLOSED_RE.search(text) else ""
            print(f"ALLOWED  {url}  HTTP {resp.status_code} len={len(text)} deadline={iso or '-'}{closed}  {ev[:60]!r}")
            if terms:
                scan_terms(client, url, resp.text)
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
