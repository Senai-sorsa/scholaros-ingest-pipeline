from __future__ import annotations

import logging
import os
from urllib.parse import quote

from pipeline.adapters.base import BaseAdapter, parse_award_range
from pipeline.filters import parse_deadline_to_iso
from pipeline.models import StagedScholarship

logger = logging.getLogger(__name__)


class CareerOneStopAdapter(BaseAdapter):
    """Official U.S. Department of Labor CareerOneStop Scholarship Finder API adapter."""

    name = "careeronestop"
    API_BASE = "https://api.careeronestop.org/v1/scholarshipfinder"

    def fetch(self) -> list[StagedScholarship]:
        user_id = os.getenv("CAREERONESTOP_USER_ID", "").strip()
        api_token = os.getenv("CAREERONESTOP_API_TOKEN", "").strip()
        if not user_id or not api_token:
            logger.info(
                "Skipping CareerOneStop API: set CAREERONESTOP_USER_ID and CAREERONESTOP_API_TOKEN."
            )
            return []

        keywords = self.config.get("keywords", ["engineering"])
        study_level = self.config.get("study_level", "Bachelor's")
        max_results = int(self.config.get("max_results_per_keyword", 25))
        results: list[StagedScholarship] = []

        for kw in keywords:
            url = (
                f"{self.API_BASE}/{quote(user_id)}/{quote(study_level)}/"
                f"0/0/{quote(kw)}/0/0/{max_results}"
            )
            try:
                resp = self.client.get(
                    url,
                    headers={"Authorization": f"Bearer {api_token}"},
                    skip_robots=True,  # Authenticated official REST API
                )
                payload = resp.json()
            except Exception as exc:
                logger.warning("CareerOneStop query '%s' failed: %s", kw, exc)
                continue

            records = payload.get("RecordList") if isinstance(payload, dict) else None
            if not records:
                # shortcut: field names are unverified against live docs; log the shape so the first real run shows it.
                keys = list(payload)[:20] if isinstance(payload, dict) else type(payload).__name__
                logger.warning("CareerOneStop '%s': no RecordList in response; top-level keys: %s", kw, keys)
                continue
            for item in records:
                title = (item.get("AwardName") or "").strip()
                sponsor = (item.get("Organization") or "Unknown Sponsor").strip()
                detail_url = (item.get("Url") or "").strip()
                award_min, award_max = parse_award_range(str(item.get("AwardAmount") or ""))
                deadline_iso = parse_deadline_to_iso(str(item.get("Deadline") or ""))
                purposes = str(item.get("Purposes") or "")
                level = str(item.get("LevelOfStudy") or "")

                results.append(
                    StagedScholarship(
                        title=title,
                        sponsor=sponsor,
                        canonical_url=detail_url,
                        raw_source_url=detail_url or url,
                        source_type="Primary",
                        source_adapter=self.name,
                        award_min_usd=award_min,
                        award_max_usd=award_max,
                        deadline_utc_iso=deadline_iso,
                        eligibility_tags=[kw.replace(" ", "_"), level.lower()],
                        raw_snippet_for_filter=f"{title} {sponsor} {purposes} {level}",
                    )
                )

        logger.info("CareerOneStop fetched %d raw candidates.", len(results))
        return results
