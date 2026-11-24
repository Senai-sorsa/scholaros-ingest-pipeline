from __future__ import annotations

import logging
import random
import time
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

import httpx

logger = logging.getLogger(__name__)


class ComplianceBlockedError(RuntimeError):
    """Raised when a domain blocks scraping via robots.txt or 403/429."""


class CompliantHttpClient:
    """Polite, logged-out HTTP client enforcing robots.txt and per-domain rate limits."""

    def __init__(
        self,
        user_agent: str,
        min_delay: float = 2.5,
        max_delay: float = 5.0,
        timeout: float = 20.0,
        respect_robots: bool = True,
        abort_codes: tuple[int, ...] = (403, 429),
    ) -> None:
        self.user_agent = user_agent
        self.min_delay = min_delay
        self.max_delay = max_delay
        self.respect_robots = respect_robots
        self.abort_codes = set(abort_codes)
        self._last_request_by_domain: dict[str, float] = {}
        self._robots_cache: dict[str, RobotFileParser | None] = {}
        self._blocked_domains: set[str] = set()
        self._client = httpx.Client(
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9",
                "Accept-Language": "en-US,en;q=0.9",
            },
            timeout=timeout,
            follow_redirects=True,
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> "CompliantHttpClient":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    def _get_robots(self, url: str) -> RobotFileParser | None:
        parsed = urlparse(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        if origin in self._robots_cache:
            return self._robots_cache[origin]

        robots_url = f"{origin}/robots.txt"
        rp = RobotFileParser()
        try:
            # robots.txt is text/plain; the default HTML Accept header gets 406 from some servers, hiding their rules.
            resp = self._client.get(robots_url, headers={"Accept": "text/plain, */*;q=0.8"})
        except Exception as exc:
            # RFC 9309 §2.3.1.4: unreachable robots.txt means assume full disallow.
            logger.warning("Could not fetch robots.txt for %s (treating as disallow): %s", origin, exc)
            rp.disallow_all = True
            self._robots_cache[origin] = rp
            return rp

        if resp.status_code == 200:
            rp.parse(resp.text.splitlines())
        elif resp.status_code in (401, 403, 429) or resp.status_code >= 500:
            # Access-restricted / bot-challenged / server error: do not crawl.
            rp.disallow_all = True
        else:
            # 404 and other 4xx: no robots rules, crawling allowed (RFC 9309 §2.3.1.3).
            rp.allow_all = True
        self._robots_cache[origin] = rp
        return rp

    def _check_robots(self, url: str) -> None:
        if not self.respect_robots:
            return
        rp = self._get_robots(url)
        if rp is not None and not rp.can_fetch(self.user_agent, url):
            raise ComplianceBlockedError(f"Disallowed by robots.txt: {url}")

    def _sleep_politely(self, domain: str, url: str = "") -> None:
        now = time.monotonic()
        last = self._last_request_by_domain.get(domain)
        if last is not None:
            delay = random.uniform(self.min_delay, self.max_delay)
            rp = self._robots_cache.get(f"{urlparse(url).scheme}://{urlparse(url).netloc}") if url else None
            crawl_delay = rp.crawl_delay(self.user_agent) if rp is not None else None
            if crawl_delay:
                delay = max(delay, float(crawl_delay))  # robots.txt Crawl-delay is a floor
            remaining = delay - (now - last)
            if remaining > 0:
                time.sleep(remaining)
        self._last_request_by_domain[domain] = time.monotonic()

    def get(self, url: str, headers: dict[str, str] | None = None, skip_robots: bool = False) -> httpx.Response:
        domain = urlparse(url).netloc.lower()
        if domain in self._blocked_domains:
            raise ComplianceBlockedError(f"Domain {domain} previously returned abort status; skipping.")

        if not skip_robots:
            self._check_robots(url)

        self._sleep_politely(domain, url)
        resp = self._client.get(url, headers=headers)
        if resp.status_code in self.abort_codes:
            self._blocked_domains.add(domain)
            raise ComplianceBlockedError(
                f"Domain {domain} returned HTTP {resp.status_code}; aborting adapter cleanly."
            )
        resp.raise_for_status()
        return resp
