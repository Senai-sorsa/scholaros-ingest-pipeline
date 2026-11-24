from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from bs4 import BeautifulSoup

from pipeline.adapters.base import BaseAdapter, parse_award_range
from pipeline.adapters.watchlist import extract_deadline
from pipeline.compliance import ComplianceBlockedError
from pipeline.models import StagedScholarship

logger = logging.getLogger(__name__)


class CollegeScholarshipsAdapter(BaseAdapter):
    """CollegeScholarships.org database (~23k awards, 30 per listing page). Checked 2026-11-24: robots.txt allows
    /financial-aid?page=N and the site has no Terms of Use (privacy policy only, no access/copy clause).
    Reads listing pages only (one request per 30 awards) and keeps the awards that fit the profile in config."""

    name = "collegescholarships"
    BASE = "https://www.collegescholarships.org/financial-aid/"

    def fetch(self) -> list[StagedScholarship]:
        cfg = self.config
        max_pages = int(cfg.get("max_pages_per_run", 200))
        states = re.compile(cfg.get("allowed_states_regex", r"no geographic restrictions"), re.I)
        levels = re.compile(cfg.get("allowed_levels_regex", r"no restrictions|college"), re.I)
        majors = re.compile(cfg.get("allowed_majors_regex", r"all majors"), re.I)
        exclude = re.compile(cfg.get("exclude_regex", r"$^"), re.I)
        # The listing has no sponsor name; the detail page does. Awards run by another college are dropped there.
        sponsor_exclude = re.compile(cfg.get("sponsor_exclude_regex", r"universit|college"), re.I)
        detail_budget = int(cfg.get("max_detail_fetches_per_run", 200))

        first = self._get(self.BASE)
        if first is None:
            return []
        total = max([int(n) for n in re.findall(r"[?&]page=(\d+)", first)] or [1])
        # Rotate through the whole database: a different block of pages each week, all of it every few weeks.
        start = (datetime.now(timezone.utc).isocalendar()[1] * max_pages) % total + 1
        pages = [((start - 1 + i) % total) + 1 for i in range(min(max_pages, total))]

        results: list[StagedScholarship] = []
        seen = read_pages = 0
        for n in pages:
            if detail_budget <= 0:
                break  # keeps run time bounded; the rest of the database comes up in later weeks
            html = first if n == 1 else self._get(f"{self.BASE}?page={n}")
            if html is None:
                break  # compliance stop or site error: don't keep hammering
            read_pages += 1
            for item in parse_listing(html):
                seen += 1
                state, level, major = item["state"], item["level"], item["major"]
                text = f"{item['title']} {item['description']}"
                if not (states.search(state) and levels.search(level) and majors.search(major)) or exclude.search(text):
                    continue
                if detail_budget <= 0:
                    break
                detail_budget -= 1
                try:
                    sponsor = parse_sponsor(self.client.get(item["url"]).text)
                except ComplianceBlockedError as exc:
                    logger.warning("[collegescholarships] Compliance stop: %s", exc)
                    return results  # no more requests to this site this run
                except Exception as exc:
                    logger.warning("[collegescholarships] Skipping %s: %s", item["url"], exc)
                    continue  # e.g. one dead detail page
                if sponsor_exclude.search(sponsor):
                    continue
                award_min, award_max = parse_award_range(item["award"])
                deadline_iso, _ = extract_deadline(f"Deadline: {item['deadline']}")
                results.append(StagedScholarship(
                    title=item["title"],
                    sponsor=sponsor,
                    canonical_url=item["url"],
                    raw_source_url=item["url"],
                    source_type="Aggregator",
                    source_adapter=self.name,
                    award_min_usd=award_min,
                    award_max_usd=award_max,
                    deadline_utc_iso=deadline_iso,
                    competition_scope="State" if "texas" in state.lower() else "National",
                    eligibility_tags=[t for t in (state, level, major) if t][:3],
                    review_notes=f"auto: collegescholarships.org listing; {item['description'][:200]}",
                    raw_snippet_for_filter=f"{text} {level}",
                ))
        logger.info("[collegescholarships] %d pages, %d awards read, %d kept for profile.", read_pages, seen, len(results))
        return results

    def _get(self, url: str) -> str | None:
        try:
            return self.client.get(url).text
        except ComplianceBlockedError as exc:
            logger.warning("[collegescholarships] Compliance stop for %s: %s", url, exc)
        except Exception as exc:
            logger.warning("[collegescholarships] Failed to fetch %s: %s", url, exc)
        return None


def parse_sponsor(html: str) -> str:
    name = BeautifulSoup(html, "lxml").select_one(".sponsor strong")
    return name.get_text(" ", strip=True) if name else ""


def parse_listing(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "lxml")
    items = []
    for row in soup.select(".scholarship-list > .row"):
        link = row.select_one(".scholarship-description h4 a")
        if not link:
            continue
        summary = [s.get_text(" ", strip=True) for s in row.select(".scholarship-summary strong")]
        desc = row.select_one(".scholarship-description > p:not(.visible-xs)")
        facts = {}
        for li in row.select(".scholarship-description li"):
            icon = li.find("i")
            kind = next((k for k in ("map-marker", "graduation-cap", "book") if icon and k in " ".join(icon.get("class", []))), None)
            if kind:
                facts[kind] = li.get_text(" ", strip=True)
        items.append({
            "title": link.get_text(" ", strip=True),
            "url": link["href"],
            "award": summary[0] if summary else "",
            "deadline": summary[1] if len(summary) > 1 else "",
            "description": desc.get_text(" ", strip=True) if desc else "",
            "state": facts.get("map-marker", ""),
            "level": facts.get("graduation-cap", ""),
            "major": facts.get("book", ""),
        })
    return items
