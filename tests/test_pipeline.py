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
    assert parse_award_range("Up to $5,000") == (None, 5000)
    assert parse_award_range("$5,000") == (5000, 5000)


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


def test_fixed_page_new_cycle_passes_url_dedup():
    # A watchlist page keeps its URL every year: last cycle's row must not block this cycle's deadline.
    last = make(deadline_utc_iso="", cycle_year=2026, dedup_by_url=False).finalize()
    new = make(deadline_utc_iso=future(400), dedup_by_url=False)
    accepted, _ = run([new], existing_urls={"https://acme.org/award"}, existing_fingerprints={last.fingerprint_sha16})
    assert len(accepted) == 1
    accepted, _ = run([make(dedup_by_url=False)], existing_fingerprints={make().finalize().fingerprint_sha16})
    assert not accepted  # same cycle is still a duplicate


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


def test_robots_requested_as_plain_text():
    # Servers like smeef.org / ttu.edu answer 406 to an HTML-only Accept header, which used to hide their rules.
    def handler(request):
        if request.url.path == "/robots.txt":
            if "text/plain" not in request.headers.get("accept", ""):
                return httpx.Response(406)
            return httpx.Response(200, text="User-agent: *\nDisallow: /private/")
        return httpx.Response(200, text="ok")
    with pytest.raises(ComplianceBlockedError):
        _client(handler).get("https://x.org/private/page")


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
    # windows: the end date is the deadline (EAA, AFA, AISES, AOPA, UH)
    ("Applications are open October 1 - December 16 of each year.", "2026-12-16T23:59:00Z"),
    ("Applications are accepted December 1 to April 30 each year.", "2027-04-30T23:59:00Z"),
    ("Scholarships open on December 15 and close on April 30 of each period.", "2027-04-30T23:59:00Z"),
    ("Fall Application Period : October 1–December 31, 11:59 p.m. ET", "2026-12-31T23:59:00Z"),
    ("Application Deadline: Applications may be filed February 16 – April 10, 2026.", "2026-04-10T23:59:00Z"),
    # "by" phrasing, incl. "p.m." inside the window (TBP, ASCE)
    ("When is the application deadline? The application must be submitted by 11:59 p.m. EDT April 1.", "2027-04-01T23:59:00Z"),
    ("Applications must be submitted online no later than January 15 of the year of award.", "2027-01-15T23:59:00Z"),
    # membership / redemption dates are not application deadlines (Whirly-Girls, OBAP)
    ("must have joined the Whirly-Girls no later than October 14, 2026 and have redeemed the scholarship no later than July 1, 2027.", ""),
    ("must have been an active, dues-paying member no later than January 1st.", ""),
    ("Members must submit applications no later than March 1, 2027.", "2027-03-01T23:59:00Z"),
    # day-first dates (AIST) used to be read as "October 20"
    ("Completed applications should be submitted by 5 October 2026. Recipients announced mid-December.", "2026-10-05T23:59:00Z"),
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
        {"title": "Off", "url": "https://ok.org/off", "enabled": False},
    ]
    items = WatchlistAdapter(client=_client(handler), config={"pages": pages}).fetch()
    assert len(items) == 1
    item = items[0]
    assert (item.deadline_utc_iso, item.award_min_usd, item.award_max_usd) == ("2099-02-01T23:59:00Z", 4000, 6000)
    assert item.source_type == "Primary" and "closed" in item.review_notes
    accepted, _ = run(items)
    assert len(accepted) == 1 and len(accepted[0].to_sheet_row()) == 20


# --- CollegeScholarships.org listing adapter (markup copied from the live listing page, 2026-11-24) ---
from pipeline.adapters.collegescholarships import CollegeScholarshipsAdapter, parse_listing  # noqa: E402


def _cs_row(slug, title, award, deadline, desc, state, level, major):
    return f"""<div class="row"><div class="col-md-2"><div class="scholarship-summary">
      <p><span class="small">Award</span><br/><span class="lead"><strong>{award}</strong></span></p>
      <p><span class="small">Deadline</span><br/><strong>{deadline}</strong></p>
      <a href="https://www.collegescholarships.org/financial-aid/scholarship/{slug}">More info</a></div></div>
      <div class="col-md-10"><div class="scholarship-description">
      <h4><a href="https://www.collegescholarships.org/financial-aid/scholarship/{slug}">{title}</a></h4>
      <p class="visible-xs">Award: <strong>{award}</strong></p><p>{desc}</p>
      <ul class="fa-ul"><li><i class="fa fa-li fa-map-marker"></i><span>{state}</span></li>
      <li><i class="fa fa-li fa-graduation-cap"></i><span>{level}</span></li>
      <li><i class="fa fa-li fa-book"></i><span>{major}</span></li></ul></div></div></div>"""


CS_PAGE = "<html><body><a href='https://www.collegescholarships.org/financial-aid/?page=3'>3</a><div class='scholarship-list'>" + "".join([
    _cs_row("aero", "Aero Award", "$2,500", "March 1", "Open to engineering undergraduates.", "No Geographic Restrictions", "College junior, College senior", "Aerospace Engineering"),
    _cs_row("tx", "Texas Any Major", "-", "None", "Texas residents.", "Texas", "No Restrictions", "All Majors Eligible"),
    _cs_row("women", "Women Award", "$1,000", "May 1", "Applicant must be female.", "No Geographic Restrictions", "College junior", "Engineering"),
    _cs_row("mi", "Michigan Award", "$1,000", "May 1", "Michigan only.", "Michigan", "College junior", "All Majors Eligible"),
    _cs_row("hs", "HS Award", "$1,000", "May 1", "Seniors.", "No Geographic Restrictions", "High school senior", "All Majors Eligible"),
    _cs_row("nurse", "Nursing Award", "$1,000", "May 1", "Nurses.", "No Geographic Restrictions", "College junior", "Nursing"),
    _cs_row("adams", "Rotary Award", "$1,000", "May 1", "Full-time.", "No Geographic Restrictions", "College junior", "All Majors Eligible"),
]) + "</div></body></html>"
CS_SPONSORS = {"aero": "Aero Foundation, Inc.", "tx": "Texas A&M University", "adams": "Adams State University"}


def test_cs_parse_listing():
    items = parse_listing(CS_PAGE)
    assert len(items) == 7
    assert items[0] == {"title": "Aero Award", "url": "https://www.collegescholarships.org/financial-aid/scholarship/aero",
                        "award": "$2,500", "deadline": "March 1", "description": "Open to engineering undergraduates.",
                        "state": "No Geographic Restrictions", "level": "College junior, College senior",
                        "major": "Aerospace Engineering"}


def test_cs_adapter_filters_profile_and_visits_every_page_once(tmp_path):
    import yaml
    cfg = yaml.safe_load(open("config.yaml", encoding="utf-8"))["sources"]["collegescholarships"]
    cfg["sponsor_cache_path"] = str(tmp_path / "sponsors.json")
    visited = []

    def handler(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        if "/scholarship/" in request.url.path:
            slug = request.url.path.rsplit("/", 1)[1]
            details.append(slug)
            return httpx.Response(200, text=f"<div class='sponsor'><h4>Sponsor Information</h4><p><strong>{CS_SPONSORS[slug]}</strong></p></div>")
        visited.append(request.url.params.get("page", "1"))
        return httpx.Response(200, text=CS_PAGE)

    details = []
    items = CollegeScholarshipsAdapter(client=_client(handler), config=dict(cfg, max_detail_fetches_per_run=200)).fetch()
    assert sorted(visited) == ["1", "2", "3"]  # page count read from the pagination links; capped by total
    assert sorted(set(details)) == ["adams", "aero", "tx"]  # detail pages only for listing matches
    assert {i.title for i in items} == {"Aero Award", "Texas Any Major"}  # Adams State's own award dropped by sponsor
    aero = next(i for i in items if i.title == "Aero Award")
    assert (aero.award_min_usd, aero.award_max_usd, aero.source_type, aero.sponsor) == (2500, 2500, "Aggregator", "Aero Foundation, Inc.")
    assert sorted(details) == ["adams", "aero", "tx"]  # each sponsor looked up once; pages 2-3 hit the cache
    details.clear(); visited.clear()
    again = CollegeScholarshipsAdapter(client=_client(handler), config=dict(cfg, max_detail_fetches_per_run=0)).fetch()
    assert details == [] and sorted(visited) == ["1", "2", "3"]  # next run: sponsors come from the saved cache
    assert {i.title for i in again} == {"Aero Award", "Texas Any Major"}
    cold = dict(cfg, sponsor_cache_path=str(tmp_path / "empty.json"))
    details.clear()
    CollegeScholarshipsAdapter(client=_client(handler), config=dict(cold, max_detail_fetches_per_run=2)).fetch()
    assert details == ["aero", "tx"]  # stops once the detail budget is spent
    details.clear()
    assert {i.title for i in CollegeScholarshipsAdapter(client=_client(handler), config=dict(cold, max_detail_fetches_per_run=0)).fetch()} == {"Aero Award", "Texas Any Major"}  # adams still unknown: waits, not staged
    assert aero.deadline_utc_iso.endswith("-03-01T23:59:00Z")
    tx = next(i for i in items if i.title == "Texas Any Major")
    assert (tx.award_max_usd, tx.deadline_utc_iso, tx.competition_scope) == (None, "", "State")
    accepted, _ = run(items)
    assert len(accepted) == 2 and len(accepted[0].to_sheet_row()) == 20  # same URL on each page is deduped
