from __future__ import annotations

import logging
import re
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from pipeline.adapters.base import BaseAdapter, parse_award_range
from pipeline.compliance import ComplianceBlockedError
from pipeline.filters import parse_deadline_to_iso
from pipeline.models import StagedScholarship

logger = logging.getLogger(__name__)

DATE_RE = re.compile(
    r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?\s+\d{1,2},?\s+20\d{2}",
    re.IGNORECASE,
)


class PublicAggregatorAdapter(BaseAdapter):
    """Logged-out, fact-only public listing reader (strips prose, keeps facts + URL only)."""

    link_path_hint: str = "/scholarship"

    def fetch(self) -> list[StagedScholarship]:
        urls = self.config.get("public_listing_urls", [])
        max_items = int(self.config.get("max_items_per_url", 20))
        results: list[StagedScholarship] = []

        for page_url in urls:
            try:
                resp = self.client.get(page_url)
            except ComplianceBlockedError as exc:
                logger.warning("[%s] Compliance stop: %s", self.name, exc)
                break
            except Exception as exc:
                logger.warning("[%s] Failed to fetch %s: %s", self.name, page_url, exc)
                continue

            soup = BeautifulSoup(resp.text, "lxml")
            extracted = self._extract_cards(soup, page_url, max_items)
            if not extracted:
                logger.warning(
                    "[%s] Parsed 0 cards from %s (site layout may have changed or requires JS).",
                    self.name,
                    page_url,
                )
            results.extend(extracted)

        logger.info("[%s] Fetched %d raw candidates.", self.name, len(results))
        return results

    def _extract_cards(
        self, soup: BeautifulSoup, page_url: str, max_items: int
    ) -> list[StagedScholarship]:
        items: list[StagedScholarship] = []
        seen_hrefs: set[str] = set()

        for anchor in soup.find_all("a", href=True):
            href = urljoin(page_url, anchor["href"])
            if self.link_path_hint not in href.lower() or href in seen_hrefs:
                continue

            card = anchor.find_parent(["article", "li", "section", "div"]) or anchor
            card_text = " ".join(card.get_text(" ", strip=True).split())
            title = " ".join(anchor.get_text(" ", strip=True).split())
            if len(title) < 6 or len(title) > 140:
                continue

            seen_hrefs.add(href)
            award_min, award_max = parse_award_range(card_text)
            date_match = DATE_RE.search(card_text)
            deadline_iso = parse_deadline_to_iso(date_match.group(0)) if date_match else ""

            items.append(
                StagedScholarship(
                    title=title,
                    sponsor=f"Verify via {self.name.capitalize()}",
                    canonical_url=href,
                    raw_source_url=page_url,
                    source_type="Aggregator",
                    source_adapter=self.name,
                    award_min_usd=award_min,
                    award_max_usd=award_max,
                    deadline_utc_iso=deadline_iso,
                    eligibility_tags=[self.name, "public_listing"],
                    raw_snippet_for_filter=card_text[:400],
                )
            )
            if len(items) >= max_items:
                break

        return items


class BoldAdapter(PublicAggregatorAdapter):
    name = "bold"
    link_path_hint = "bold.org/scholarships/"


class UnigoAdapter(PublicAggregatorAdapter):
    name = "unigo"
    link_path_hint = "unigo.com/scholarships/"


class NicheAdapter(PublicAggregatorAdapter):
    name = "niche"
    link_path_hint = "niche.com/colleges/scholarship/"
