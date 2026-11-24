from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup
from dateutil import parser as date_parser

from pipeline.adapters.base import BaseAdapter, parse_award_range
from pipeline.compliance import ComplianceBlockedError
from pipeline.models import StagedScholarship

logger = logging.getLogger(__name__)

MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
DATE = rf"(?:{MONTH}\s+\d{{1,2}}(?:st|nd|rd|th)?(?:,?\s+20\d{{2}})?|\d{{1,2}}/\d{{1,2}}/20\d{{2}})"
# "<keyword> ... <date>" within a short window, optionally preceded by a cycle year ("2027 Deadline: Feb. 1").
DEADLINE_RE = re.compile(
    rf"(?:(?P<year>20\d{{2}})\s+)?(?:deadline|due date|applications?\s+(?:close[sd]?|due)|closes)"
    rf"[^.]{{0,40}}?(?P<date>{DATE})",
    re.IGNORECASE,
)
CLOSED_RE = re.compile(r"\b(?:scholarships?|applications?)\s+(?:are|is)\s+(?:now\s+)?closed\b", re.IGNORECASE)


def extract_deadline(text: str, now: datetime | None = None) -> tuple[str, str]:
    """Return (deadline ISO, evidence snippet). Prefers the earliest future deadline, else the latest past one."""
    now = now or datetime.now(timezone.utc)
    found: list[tuple[datetime, str]] = []
    for m in DEADLINE_RE.finditer(text or ""):
        raw = m.group("date")
        has_year = re.search(r"20\d{2}", raw)
        if not has_year and m.group("year"):
            raw = f"{raw} {m.group('year')}"
        try:
            dt = date_parser.parse(raw.replace(".", ""), default=now.replace(tzinfo=None)).replace(tzinfo=timezone.utc)
        except (ValueError, OverflowError):
            continue
        if not has_year and not m.group("year") and dt.date() < now.date():
            dt = dt.replace(year=dt.year + 1)  # yearless date already passed: assume next cycle
        found.append((dt, m.group(0)))
    if not found:
        return "", ""
    future = sorted(f for f in found if f[0].date() >= now.date())
    dt, snippet = future[0] if future else max(found)
    return dt.strftime("%Y-%m-%dT23:59:00Z"), " ".join(snippet.split())


class WatchlistAdapter(BaseAdapter):
    """Checks a curated list of primary-source scholarship pages (sponsor sites whose robots.txt and
    Terms of Use permit automated access). One polite request per page per run; stores facts + URL only."""

    name = "watchlist"

    def fetch(self) -> list[StagedScholarship]:
        results: list[StagedScholarship] = []
        for page in self.config.get("pages", []) or []:
            url = (page.get("url") or "").strip()
            if not url or not page.get("title"):
                logger.warning("[watchlist] Skipping entry missing title/url: %s", page)
                continue
            try:
                resp = self.client.get(url)
            except ComplianceBlockedError as exc:
                logger.warning("[watchlist] Compliance stop for %s: %s", url, exc)
                continue
            except Exception as exc:
                logger.warning("[watchlist] Failed to fetch %s: %s", url, exc)
                continue

            text = " ".join(BeautifulSoup(resp.text, "lxml").get_text(" ").split())
            deadline_iso, evidence = extract_deadline(text)
            notes = [f"auto: deadline from page text \"{evidence[:120]}\"" if evidence else "auto: no deadline found on page"]
            if CLOSED_RE.search(text):
                notes.append("page says applications are closed")
            award_min, award_max = parse_award_range(str(page.get("award", "")))

            results.append(
                StagedScholarship(
                    title=page["title"],
                    sponsor=page.get("sponsor", ""),
                    canonical_url=url,
                    raw_source_url=url,
                    source_type="Primary",
                    source_adapter=self.name,
                    award_min_usd=award_min,
                    award_max_usd=award_max,
                    deadline_utc_iso=deadline_iso,
                    competition_scope=page.get("scope", "National"),
                    eligibility_tags=list(page.get("tags", [])),
                    review_notes="; ".join(notes),
                    raw_snippet_for_filter=f"{page['title']} {page.get('sponsor', '')}",
                )
            )
        logger.info("[watchlist] Fetched %d raw candidates.", len(results))
        return results
