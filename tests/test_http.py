from unittest import mock

import pytest
import requests

from course_ingest.http import FetchFailed, PoliteSession, RobotsDisallowed


class FakeClock:
    def __init__(self):
        self.t = 0.0
        self.sleeps = []

    def now(self):
        return self.t

    def sleep(self, s):
        self.sleeps.append(s)
        self.t += s


def resp(status=200, text="", headers=None):
    r = requests.Response()
    r.status_code = status
    r._content = text.encode()
    r.headers.update(headers or {})
    return r


def make(clock, responses, robots=False):
    s = PoliteSession("https://x.example/app/", "test-bot (+mailto:t@example.com)", respect_robots=robots,
                      max_retries=2, sleep=clock.sleep, clock=clock.now)
    s.session.request = mock.Mock(side_effect=responses)
    return s


def test_rate_limit_one_per_second():
    clock = FakeClock()
    s = make(clock, [resp(), resp(), resp()])
    for _ in range(3):
        s.get("a")
        clock.t += 0.2   # simulated processing time
    assert [round(x, 2) for x in clock.sleeps] == [0.8, 0.8]
    assert s.session.request.call_args.kwargs.get("timeout") == 60.0


def test_backoff_then_success():
    clock = FakeClock()
    s = make(clock, [resp(503), resp(429, headers={"Retry-After": "7"}), resp(200, "ok")])
    r = s.get("a")
    assert r.text == "ok" and s.retries == 2 and s.requests_made == 3
    backoffs = [x for x in clock.sleeps if x >= 1]
    assert 1.0 <= backoffs[0] < 1.6          # 1s * 2^0 + jitter
    assert backoffs[1] >= 7.0                # Retry-After honored


def test_gives_up_after_max_retries():
    clock = FakeClock()
    s = make(clock, [requests.ConnectionError("boom")] * 3)
    with pytest.raises(FetchFailed):
        s.get("a")
    assert s.requests_made == 3


def test_robots_disallow_blocks_request():
    clock = FakeClock()
    robots = "User-agent: *\nDisallow: /app/searchResults/\n"
    with mock.patch.object(PoliteSession, "_raw_request", return_value=resp(200, robots)):
        s = PoliteSession("https://x.example/app/", "test-bot", sleep=clock.sleep, clock=clock.now)
    assert s.allowed("https://x.example/app/classSearch/getTerms")
    assert not s.allowed("https://x.example/app/searchResults/searchResults?x=1")
    with pytest.raises(RobotsDisallowed):
        s.get("searchResults/searchResults")


def test_user_agent_identifies_bot():
    clock = FakeClock()
    s = make(clock, [resp()])
    assert "mailto:t@example.com" in s.session.headers["User-Agent"]
