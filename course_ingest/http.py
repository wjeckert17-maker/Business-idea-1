"""A session that identifies itself, obeys robots.txt, and never hurries."""
from __future__ import annotations

import logging
import random
import time
from typing import Optional
from urllib import robotparser
from urllib.parse import urljoin, urlsplit

import requests

log = logging.getLogger(__name__)

RETRY_STATUSES = {429, 500, 502, 503, 504}


class RobotsDisallowed(Exception):
    pass


class FetchFailed(Exception):
    pass


class PoliteSession:
    """requests.Session wrapper: honest UA, robots.txt, >= min_interval between requests,
    exponential backoff with jitter on 429/5xx/connection errors, Retry-After honored."""

    def __init__(
        self,
        base_url: str,
        user_agent: str,
        min_interval: float = 1.0,
        max_retries: int = 5,
        timeout: float = 60.0,
        respect_robots: bool = True,
        sleep=time.sleep,
        clock=time.monotonic,
    ):
        self.base_url = base_url.rstrip("/") + "/"
        self.user_agent = user_agent
        self.min_interval = min_interval
        self.max_retries = max_retries
        self.timeout = timeout
        self._sleep = sleep
        self._clock = clock
        self._last_request_at: Optional[float] = None
        self.session = requests.Session()
        self.session.headers["User-Agent"] = user_agent
        self.requests_made = 0
        self.retries = 0
        self._robots: Optional[robotparser.RobotFileParser] = None
        if respect_robots:
            self._load_robots()

    # -- robots -----------------------------------------------------------
    def _load_robots(self) -> None:
        parts = urlsplit(self.base_url)
        robots_url = f"{parts.scheme}://{parts.netloc}/robots.txt"
        rp = robotparser.RobotFileParser()
        rp.set_url(robots_url)
        try:
            resp = self._raw_request("GET", robots_url)
        except FetchFailed:
            log.warning("robots.txt unreachable at %s; treating as allow-all", robots_url)
            rp.parse([])
        else:
            if resp.status_code == 200:
                rp.parse(resp.text.splitlines())
                log.info("robots.txt loaded from %s", robots_url)
            elif 400 <= resp.status_code < 500:
                rp.parse([])   # 4xx = no robots file = allow-all by convention
                log.info("robots.txt absent (%s); allow-all", resp.status_code)
            else:
                rp.parse([])
                log.warning("robots.txt returned %s; treating as allow-all", resp.status_code)
        self._robots = rp

    def allowed(self, url: str) -> bool:
        if self._robots is None:
            return True
        return self._robots.can_fetch(self.user_agent, url)

    # -- requests ---------------------------------------------------------
    def request(self, method: str, path: str, **kwargs) -> requests.Response:
        url = urljoin(self.base_url, path)
        if not self.allowed(url):
            raise RobotsDisallowed(f"robots.txt disallows {url} for {self.user_agent!r}")
        return self._raw_request(method, url, **kwargs)

    def get(self, path: str, **kwargs) -> requests.Response:
        return self.request("GET", path, **kwargs)

    def post(self, path: str, **kwargs) -> requests.Response:
        return self.request("POST", path, **kwargs)

    def _throttle(self) -> None:
        if self._last_request_at is not None:
            wait = self.min_interval - (self._clock() - self._last_request_at)
            if wait > 0:
                self._sleep(wait)
        self._last_request_at = self._clock()

    def _raw_request(self, method: str, url: str, **kwargs) -> requests.Response:
        kwargs.setdefault("timeout", self.timeout)
        attempt = 0
        while True:
            self._throttle()
            self.requests_made += 1
            try:
                resp = self.session.request(method, url, **kwargs)
            except requests.RequestException as exc:
                err: Optional[str] = f"{type(exc).__name__}: {exc}"
                resp = None
            else:
                err = f"HTTP {resp.status_code}" if resp.status_code in RETRY_STATUSES else None

            if err is None:
                return resp
            if attempt >= self.max_retries:
                raise FetchFailed(f"{method} {url} failed after {attempt + 1} attempts: {err}")

            delay = min(60.0, self.min_interval * (2 ** attempt)) + random.uniform(0, 0.5)
            if resp is not None and resp.headers.get("Retry-After", "").isdigit():
                delay = max(delay, float(resp.headers["Retry-After"]))
            attempt += 1
            self.retries += 1
            log.warning("%s %s: %s; retry %d/%d in %.1fs", method, url, err, attempt, self.max_retries, delay)
            self._sleep(delay)
