from __future__ import annotations

from datetime import datetime, timezone

from dateutil import parser as date_parser

from pipeline.models import StagedScholarship


def parse_deadline_to_iso(raw_date: str) -> str:
    """Convert common date strings into UTC ISO-8601 (YYYY-MM-DDT23:59:00Z)."""
    raw_date = (raw_date or "").strip()
    if not raw_date:
        return ""
    try:
        dt = date_parser.parse(raw_date, fuzzy=True)
        return dt.strftime("%Y-%m-%dT23:59:00Z")
    except Exception:
        return ""


def filter_and_deduplicate(
    candidates: list[StagedScholarship],
    existing_urls: set[str],
    existing_fingerprints: set[str],
    reject_keywords: list[str],
    min_award_usd: int = 500,
    require_future_deadline: bool = True,
) -> tuple[list[StagedScholarship], list[tuple[StagedScholarship, str]]]:
    """Filter-Before-Write gatekeeper matching ScholarOS hard rules."""
    accepted: list[StagedScholarship] = []
    rejected: list[tuple[StagedScholarship, str]] = []
    seen_urls = set(existing_urls)
    seen_fps = set(existing_fingerprints)
    now_utc = datetime.now(timezone.utc)
    lowered_rejects = [kw.lower() for kw in reject_keywords]

    for item in candidates:
        item.finalize()

        if not item.title or not item.canonical_url:
            rejected.append((item, "missing_title_or_url"))
            continue

        if (item.dedup_by_url and item.canonical_url in seen_urls) or item.fingerprint_sha16 in seen_fps:
            rejected.append((item, "duplicate_existing_or_batch"))
            continue

        haystack = " ".join(
            [
                item.title,
                item.sponsor,
                " ".join(item.eligibility_tags),
                item.raw_snippet_for_filter,
            ]
        ).lower()

        matched_kw = next((kw for kw in lowered_rejects if kw in haystack), None)
        if matched_kw:
            rejected.append((item, f"blocked_keyword:{matched_kw}"))
            continue

        max_award = item.award_max_usd or item.award_min_usd
        if max_award is not None and max_award < min_award_usd:
            rejected.append((item, f"below_min_award:{max_award}"))
            continue

        if require_future_deadline and item.deadline_utc_iso:
            try:
                dl = datetime.fromisoformat(item.deadline_utc_iso.replace("Z", "+00:00"))
                if dl < now_utc:
                    rejected.append((item, f"expired_deadline:{item.deadline_utc_iso}"))
                    continue
            except ValueError:
                pass

        seen_urls.add(item.canonical_url)
        seen_fps.add(item.fingerprint_sha16)
        item.pre_filter_status = "PASS"
        item.gemini_review_status = "Pending_Review"
        accepted.append(item)

    return accepted, rejected
