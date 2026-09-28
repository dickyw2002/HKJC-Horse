"""Polite HTTP client for public HKJC pages.

Requests are spaced at about one per second, retried with backoff, and cached
on disk so a rerun does not download a page again.
"""

from __future__ import annotations

import logging
import random
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import httpx

logger = logging.getLogger(__name__)

USER_AGENT = (
    "HKJCResearchCollector/0.1 "
    "(private research; no republication; no betting; about 1 request/second)"
)

RESULTS_PAGE = "https://racing.hkjc.com/en-us/local/information/localresults"
FIXTURE_PAGE = "https://racing.hkjc.com/en-us/local/information/fixture"
DATE_LIST_URL = (
    "https://racing.hkjc.com/racing/information/json/DateList/LocalResults.aspx"
    "?lang=en-us"
)

RETRY_STATUSES = {429, 500, 502, 503, 504}
BLOCK_STATUSES = {401, 403}
MAX_BODY_BYTES = 5_000_000


def results_url(meeting_date, racecourse: str, race_no: int) -> str:
    return (
        f"{RESULTS_PAGE}?racedate={meeting_date:%Y/%m/%d}"
        f"&Racecourse={racecourse}&RaceNo={race_no}"
    )


def fixture_url(year: int, month: int) -> str:
    return f"{FIXTURE_PAGE}?calyear={year}&calmonth={month:02d}"


def results_cache_name(meeting_date, racecourse: str, race_no: int) -> str:
    return f"results/{meeting_date.isoformat()}_{racecourse}_r{race_no:02d}.html"


def fixture_cache_name(year: int, month: int) -> str:
    return f"fixture/{year}-{month:02d}.html"


@dataclass
class FetchResult:
    url: str
    final_url: str
    status: int | None
    text: str | None
    from_cache: bool
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None and self.text is not None and self.status == 200


class PoliteFetcher:
    """HTTP getter with a disk cache, a minimum interval, and retries."""

    def __init__(
        self,
        cache_dir: Path,
        *,
        min_interval: float = 1.0,
        jitter: float = 0.35,
        max_retries: int = 4,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] | None = None,
        user_agent: str = USER_AGENT,
    ) -> None:
        if min_interval < 1.0:
            logger.warning("Raising request interval from %s to 1.0s", min_interval)
            min_interval = 1.0
        self.cache_dir = cache_dir
        self.min_interval = min_interval
        self.jitter = jitter
        self.max_retries = max_retries
        self._sleep = sleep or time.sleep
        self._earliest = 0.0
        self._request_started = 0.0
        self.http_requests = 0
        self.cache_hits = 0
        self._client = httpx.Client(
            transport=transport,
            follow_redirects=True,
            timeout=httpx.Timeout(30.0, connect=10.0),
            headers={
                "User-Agent": user_agent,
                "Accept": "text/html,application/xhtml+xml,application/json;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
            },
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> PoliteFetcher:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_text(self, url: str, *, cache_name: str, force: bool = False) -> FetchResult:
        path = self.cache_dir / cache_name
        if path.exists() and not force:
            self.cache_hits += 1
            text = path.read_text(encoding="utf-8", errors="replace")
            return FetchResult(
                url=url,
                final_url=url,
                status=200,
                text=text,
                from_cache=True,
            )

        last_error = "no response"
        for attempt in range(self.max_retries):
            self._pace()
            try:
                response = self._client.get(url)
            except httpx.HTTPError as exc:
                self._finish_slot()
                last_error = f"{type(exc).__name__}: {exc}"
                logger.warning("request failed %s (%s)", url, last_error)
                if attempt < self.max_retries - 1:
                    self._backoff(attempt)
                continue

            self.http_requests += 1
            self._finish_slot()
            status = response.status_code
            if status == 200:
                raw = response.content
                if len(raw) > MAX_BODY_BYTES:
                    return FetchResult(
                        url=url,
                        final_url=str(response.url),
                        status=status,
                        text=None,
                        from_cache=False,
                        error="response too large",
                    )
                text = response.text
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(text, encoding="utf-8")
                return FetchResult(
                    url=url,
                    final_url=str(response.url),
                    status=status,
                    text=text,
                    from_cache=False,
                )

            last_error = f"HTTP {status}"
            logger.warning("request %s -> %s", url, status)
            if status in BLOCK_STATUSES:
                return FetchResult(
                    url=url,
                    final_url=str(response.url),
                    status=status,
                    text=None,
                    from_cache=False,
                    error=f"blocked {status}",
                )
            if status in RETRY_STATUSES and attempt < self.max_retries - 1:
                self._backoff(attempt, response)
                continue
            return FetchResult(
                url=url,
                final_url=str(response.url),
                status=status,
                text=None,
                from_cache=False,
                error=last_error,
            )

        return FetchResult(
            url=url,
            final_url=url,
            status=None,
            text=None,
            from_cache=False,
            error=last_error,
        )

    def _pace(self) -> None:
        wait = self._earliest - time.monotonic()
        if wait > 0:
            self._sleep(wait)
        self._request_started = time.monotonic()

    def _finish_slot(self) -> None:
        elapsed = time.monotonic() - self._request_started
        gap = self.min_interval + random.uniform(0, self.jitter)
        extra = gap - elapsed
        self._earliest = time.monotonic() + max(0.0, extra)

    def _backoff(self, attempt: int, response: httpx.Response | None = None) -> None:
        delay = min(60.0, float(2 ** (attempt + 1)))
        if response is not None:
            retry_after = response.headers.get("Retry-After", "")
            if retry_after.isdigit():
                delay = min(60.0, max(delay, float(retry_after)))
        logger.info("backing off %.1fs", delay)
        self._sleep(delay)
        self._earliest = time.monotonic()
