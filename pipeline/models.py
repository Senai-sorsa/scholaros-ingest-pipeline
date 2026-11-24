from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

STAGING_HEADERS = [
    "staging_id",
    "fingerprint_sha16",
    "canonical_url",
    "raw_source_url",
    "source_type",
    "source_adapter",
    "title",
    "sponsor",
    "award_min_usd",
    "award_max_usd",
    "deadline_utc_iso",
    "recurrence_type",
    "cycle_year",
    "competition_scope",
    "portal_access",
    "eligibility_tags",
    "pre_filter_status",
    "fetched_at_utc",
    "gemini_review_status",
    "review_notes",
]

TRACKING_PARAMS = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
    "ref",
    "source",
}


def canonicalize_url(raw_url: str) -> str:
    """Normalize URL per RFC 3986 while stripping tracking query parameters."""
    raw_url = (raw_url or "").strip()
    if not raw_url:
        return ""
    parsed = urlparse(raw_url)
    scheme = (parsed.scheme or "https").lower()
    netloc = parsed.netloc.lower()
    if netloc.startswith("www."):
        netloc = netloc[4:]
    path = re.sub(r"/+$", "", parsed.path or "")
    clean_qs = [
        (k, v)
        for k, v in parse_qsl(parsed.query, keep_blank_values=False)
        if k.lower() not in TRACKING_PARAMS
    ]
    clean_qs.sort()
    return urlunparse((scheme, netloc, path, "", urlencode(clean_qs), ""))


def compute_fingerprint(sponsor: str, title: str, cycle_year: int | str) -> str:
    """Compute ScholarOS SHA256(sponsor|title|cycle_year)[:16] dedup key."""
    norm = f"{(sponsor or '').strip().lower()}|{(title or '').strip().lower()}|{str(cycle_year).strip()}"
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def sanitize_sheet_cell(value: object) -> str | int | float:
    """Prevent Google Sheets / Excel formula injection on untrusted strings."""
    if value is None:
        return ""
    if isinstance(value, (int, float)):
        return value
    text = str(value).strip()
    if text and text[0] in ("=", "+", "-", "@"):
        return "'" + text
    return text


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class StagedScholarship:
    """Fact-only scholarship record mapped 1:1 to ScholarOS Ingest_Staging."""

    title: str
    sponsor: str
    raw_source_url: str
    source_type: str  # Primary | Aggregator
    source_adapter: str  # careeronestop | bold | unigo | niche
    canonical_url: str = ""
    award_min_usd: int | None = None
    award_max_usd: int | None = None
    deadline_utc_iso: str = ""
    recurrence_type: str = "annual"
    cycle_year: int = field(default_factory=lambda: datetime.now(timezone.utc).year)
    competition_scope: str = "National"
    portal_access: str = "Public"
    eligibility_tags: list[str] = field(default_factory=list)
    pre_filter_status: str = "PASS"
    fetched_at_utc: str = field(default_factory=utc_now_iso)
    gemini_review_status: str = "Pending_Review"
    review_notes: str = ""
    raw_snippet_for_filter: str = ""  # In-memory only; never written to Sheets

    def finalize(self) -> "StagedScholarship":
        self.canonical_url = canonicalize_url(self.canonical_url or self.raw_source_url)
        if self.deadline_utc_iso:
            try:
                self.cycle_year = int(self.deadline_utc_iso[:4])
            except ValueError:
                pass
        return self

    @property
    def fingerprint_sha16(self) -> str:
        return compute_fingerprint(self.sponsor, self.title, self.cycle_year)

    @property
    def staging_id(self) -> str:
        return f"stg_{self.source_adapter}_{self.fingerprint_sha16[:10]}"

    def to_sheet_row(self) -> list[str | int | float]:
        self.finalize()
        tags_str = "|".join(sorted({t.strip().lower() for t in self.eligibility_tags if t.strip()}))
        return [
            sanitize_sheet_cell(self.staging_id),
            sanitize_sheet_cell(self.fingerprint_sha16),
            sanitize_sheet_cell(self.canonical_url),
            sanitize_sheet_cell(self.raw_source_url),
            sanitize_sheet_cell(self.source_type),
            sanitize_sheet_cell(self.source_adapter),
            sanitize_sheet_cell(self.title),
            sanitize_sheet_cell(self.sponsor),
            self.award_min_usd if self.award_min_usd is not None else "",
            self.award_max_usd if self.award_max_usd is not None else "",
            sanitize_sheet_cell(self.deadline_utc_iso),
            sanitize_sheet_cell(self.recurrence_type),
            self.cycle_year,
            sanitize_sheet_cell(self.competition_scope),
            sanitize_sheet_cell(self.portal_access),
            sanitize_sheet_cell(tags_str),
            sanitize_sheet_cell(self.pre_filter_status),
            sanitize_sheet_cell(self.fetched_at_utc),
            sanitize_sheet_cell(self.gemini_review_status),
            sanitize_sheet_cell(self.review_notes),
        ]

    def to_dict(self) -> dict[str, object]:
        row = self.to_sheet_row()
        return dict(zip(STAGING_HEADERS, row))
