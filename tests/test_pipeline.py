from datetime import datetime, timedelta, timezone

import httpx
import pytest

from pipeline.adapters.base import parse_award_range
from pipeline.compliance import ComplianceBlockedError, CompliantHttpClient
from pipeline.filters import filter_and_deduplicate, parse_deadline_to_iso
from pipeline.models import (
    STAGING_HEADERS,
    StagedScholarship,
    canonicalize_url,
    compute_fingerprint,
    sanitize_sheet_cell,
)

# Gemini's /scholarshipsearch Phase 2b reads Ingest_Staging by this exact schema. Never change it.
EXPECTED_HEADERS = [
    "staging_id", "fingerprint_sha16", "canonical_url", "raw_source_url", "source_type",
    "source_adapter", "title", "sponsor", "award_min_usd", "award_max_usd", "deadline_utc_iso",
    "recurrence_type", "cycle_year", "competition_scope", "portal_access", "eligibility_tags",
    "pre_filter_status", "fetched_at_utc", "gemini_review_status", "review_notes",
]


def future(days=60):
    return (datetime.now(timezone.utc) + timedelta(days=days)).strftime("%Y-%m-%dT23:59:00Z")


def make(**kw):
    base = dict(title="STEM Merit Award", sponsor="Acme Foundation",
                raw_source_url="https://acme.org/award", source_type="Primary",
                source_adapter="careeronestop", award_max_usd=2000, deadline_utc_iso=future())
    base.update(kw)
    return StagedScholarship(**base)


def run(items, **kw):
    args = dict(existing_urls=set(), existing_fingerprints=set(), reject_keywords=["sweepstakes"])
    args.update(kw)
    return filter_and_deduplicate(items, **args)


def test_schema_is_locked():
    assert STAGING_HEADERS == EXPECTED_HEADERS
    row = make().to_sheet_row()
    assert len(row) == 20
    assert list(make().to_dict()) == EXPECTED_HEADERS


def test_canonicalize_strips_tracking_www_and_slash():
    assert canonicalize_url("HTTPS://WWW.Acme.org/award/?utm_source=x&b=2&a=1#frag") == "https://acme.org/award?a=1&b=2"
    assert canonicalize_url("") == ""


def test_fingerprint_is_case_and_space_insensitive():
    assert compute_fingerprint(" Acme ", "Award", 2027) == compute_fingerprint("acme", "AWARD", "2027")
    assert len(compute_fingerprint("a", "b", 1)) == 16


@pytest.mark.parametrize("raw", ["=HYPERLINK(\"x\")", "+1", "-cmd", "@SUM(A1)"])
def test_formula_injection_escaped(raw):
    assert sanitize_sheet_cell(raw).startswith("'")


def test_award_range():
    assert parse_award_range("$1,000 - $5,000") == (1000, 5000)
    assert parse_award_range("Varies") == (None, None)


def test_deadline_parsing():
    assert parse_deadline_to_iso("March 1, 2027") == "2027-03-01T23:59:00Z"
    assert parse_deadline_to_iso("") == ""
    assert parse_deadline_to_iso("Varies") == ""


def test_cycle_year_follows_deadline():
    item = make(deadline_utc_iso="2028-02-01T23:59:00Z").finalize()
    assert item.cycle_year == 2028


def test_filter_accepts_valid():
    accepted, rejected = run([make()])
    assert len(accepted) == 1 and not rejected


@pytest.mark.parametrize("item,reason", [
    (make(title=""), "missing_title_or_url"),
    (make(raw_snippet_for_filter="enter our sweepstakes"), "blocked_keyword:sweepstakes"),
    (make(award_max_usd=100), "below_min_award:100"),
    (make(deadline_utc_iso="2020-01-01T23:59:00Z"), "expired_deadline"),
])
def test_filter_rejections(item, reason):
    accepted, rejected = run([item])
    assert not accepted
    assert rejected[0][1].startswith(reason)


def test_dedup_within_batch_and_against_sheet():
    accepted, rejected = run([make(), make(raw_source_url="https://acme.org/award?utm_source=x")])
    assert len(accepted) == 1 and rejected[0][1] == "duplicate_existing_or_batch"
    accepted, _ = run([make()], existing_urls={"https://acme.org/award"})
    assert not accepted


def _client(handler, **kw):
    c = CompliantHttpClient(user_agent="TestBot/1.0", min_delay=0, max_delay=0, **kw)
    c._client = httpx.Client(transport=httpx.MockTransport(handler))
    return c


def _robots(status, body=""):
    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(status, text=body)
        return httpx.Response(200, text="ok")
    return handler


def test_robots_disallow_blocks():
    c = _client(_robots(200, "User-agent: *\nDisallow: /private/"))
    assert c.get("https://x.org/public").text == "ok"
    with pytest.raises(ComplianceBlockedError):
        c.get("https://x.org/private/page")


@pytest.mark.parametrize("status", [401, 403, 429, 503])
def test_restricted_or_failing_robots_means_disallow(status):
    with pytest.raises(ComplianceBlockedError):
        _client(_robots(status)).get("https://x.org/page")


def test_missing_robots_allows():
    assert _client(_robots(404)).get("https://x.org/page").text == "ok"


def test_abort_status_blocks_domain_for_rest_of_run():
    calls = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        calls.append(request.url.path)
        return httpx.Response(429)

    c = _client(handler)
    for _ in range(2):
        with pytest.raises(ComplianceBlockedError):
            c.get("https://x.org/page")
    assert calls == ["/page"]  # second call never hit the network


def test_crawl_delay_is_honored(monkeypatch):
    slept = []
    monkeypatch.setattr("pipeline.compliance.time.sleep", slept.append)
    c = _client(_robots(200, "User-agent: *\nCrawl-delay: 5\n"))
    c.get("https://x.org/a")
    c.get("https://x.org/b")
    assert slept and slept[0] > 4


# --- watchlist adapter (patterns taken from real sponsor pages) ---
from pipeline.adapters.watchlist import WatchlistAdapter, extract_deadline  # noqa: E402

NOW = datetime(2026, 11, 24, tzinfo=timezone.utc)


@pytest.mark.parametrize("text,expected", [
    ("Open date: December 1, 2026 Deadline: February 1, 2027 Interest Form", "2027-02-01T23:59:00Z"),  # SWE
    ("contributors. 2027 Deadline: Feb. 1 \"By empowering", "2027-02-01T23:59:00Z"),  # VFS, year before keyword
    ("Application opens: February 2, 2026 Application closes: February 16, 2026", "2026-02-16T23:59:00Z"),  # SHPE (past)
    ("Deadline: March 3", "2027-03-03T23:59:00Z"),  # yearless & past -> next cycle
    ("Applications due 1/15/2027.", "2027-01-15T23:59:00Z"),
    ("Applications open 8/31. Learn more", ""),  # open date is not a deadline
])
def test_extract_deadline(text, expected):
    assert extract_deadline(text, now=NOW)[0] == expected


def test_extract_deadline_prefers_next_future():
    text = "Deadline: February 1, 2026 ... Deadline: February 1, 2027 ... Deadline: March 31, 2027"
    assert extract_deadline(text, now=NOW)[0] == "2027-02-01T23:59:00Z"


def test_watchlist_adapter_builds_record_and_skips_blocked():
    html = "<html><body><h1>VFF</h1><p>2099 Deadline: Feb. 1</p><p>Applications are closed</p></body></html>"

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if request.url.host == "blocked.org":
            return httpx.Response(403)
        return httpx.Response(200, text=html)

    pages = [
        {"title": "VFF", "sponsor": "VFS", "url": "https://ok.org/vff", "award": "$4,000 - $6,000", "tags": ["aero"]},
        {"title": "Blocked", "url": "https://blocked.org/x"},
        {"title": "", "url": "https://ok.org/missing-title"},
    ]
    items = WatchlistAdapter(client=_client(handler), config={"pages": pages}).fetch()
    assert len(items) == 1
    item = items[0]
    assert (item.deadline_utc_iso, item.award_min_usd, item.award_max_usd) == ("2099-02-01T23:59:00Z", 4000, 6000)
    assert item.source_type == "Primary" and "closed" in item.review_notes
    accepted, _ = run(items)
    assert len(accepted) == 1 and len(accepted[0].to_sheet_row()) == 20
