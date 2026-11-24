"""Check candidate source URLs the way the pipeline would: robots.txt for our user agent, then one polite GET.

Usage: python scripts/check_source.py URL [URL ...]
Prints one line per URL: ALLOWED/BLOCKED, HTTP status, and the deadline the watchlist extractor would find.
Robots/HTTP only -- a site's Terms of Use still have to be read by a person/agent before adding it.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import yaml  # noqa: E402
from bs4 import BeautifulSoup  # noqa: E402

from pipeline.adapters.watchlist import CLOSED_RE, extract_deadline  # noqa: E402
from pipeline.compliance import ComplianceBlockedError, CompliantHttpClient  # noqa: E402


def main(urls: list[str]) -> int:
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
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
