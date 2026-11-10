import httpx
import pytest

from src.common.config import USER_AGENT
from src.common.http import PoliteClient, SourceBlocked

ROBOTS = "User-agent: *\nDisallow: /private/\n"


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def time(self):
        return self.now

    def sleep(self, s):
        self.sleeps.append(s)
        self.now += s


def make_client(tmp_path, handler, **kw):
    clock = FakeClock()
    calls = []

    def wrapped(request):
        calls.append(request)
        return handler(request)

    client = PoliteClient(
        "test", cache_dir=tmp_path, transport=httpx.MockTransport(wrapped),
        clock=clock.time, sleep=clock.sleep, **kw,
    )
    return client, calls, clock


def site(request):
    if request.url.path == "/robots.txt":
        return httpx.Response(200, text=ROBOTS)
    if request.url.path == "/forbidden":
        return httpx.Response(403)
    return httpx.Response(200, json={"path": request.url.path})


def test_sends_descriptive_user_agent(tmp_path):
    client, calls, _ = make_client(tmp_path, site)
    client.get("https://example.org/data")
    assert all(c.headers["user-agent"] == USER_AGENT for c in calls)


def test_robots_block_raises_and_never_fetches(tmp_path):
    client, calls, _ = make_client(tmp_path, site)
    with pytest.raises(SourceBlocked):
        client.get("https://example.org/private/x")
    assert [c.url.path for c in calls] == ["/robots.txt"]


def test_403_raises_source_blocked(tmp_path):
    client, _, _ = make_client(tmp_path, site)
    with pytest.raises(SourceBlocked):
        client.get("https://example.org/forbidden")


def test_cache_prevents_second_download(tmp_path):
    client, calls, _ = make_client(tmp_path, site)
    first = client.get("https://example.org/data", params={"offset": 0})
    second = client.get("https://example.org/data", params={"offset": 0})
    assert first.json() == second.json() and second.from_cache
    assert [c.url.path for c in calls].count("/data") == 1


def test_at_most_one_request_per_second_per_host(tmp_path):
    client, _, clock = make_client(tmp_path, site, use_cache=False)
    client.get("https://example.org/a")
    client.get("https://example.org/b")
    assert clock.sleeps and all(s <= 1.0 for s in clock.sleeps)
    assert sum(clock.sleeps) >= 2.0   # robots, /a, /b: two waits of a full second


def test_robots_longest_match_wins():
    from src.common.http import Robots

    r = Robots.parse("User-agent: *\nDisallow: /wp-admin/\nAllow: /wp-admin/admin-ajax.php\nDisallow: /*.pdf$\n")
    assert r.allows("/wp-admin/admin-ajax.php?action=case_list_data")
    assert not r.allows("/wp-admin/settings")
    assert not r.allows("/files/a.pdf") and r.allows("/files/a.pdf?x=1")


def test_robots_own_group_beats_star_and_crawl_delay_applies(tmp_path):
    def strict(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text=(
                "User-agent: *\nDisallow: /\n\n"
                "User-agent: UMich-Team1-research\nAllow: /\nCrawl-delay: 3\n"))
        return httpx.Response(200, text="ok")

    client, _, clock = make_client(tmp_path, strict, use_cache=False)
    client.get("https://example.org/a")
    client.get("https://example.org/b")
    assert max(clock.sleeps) == 3.0


def test_extra_headers_sent_but_user_agent_fixed(tmp_path):
    client, calls, _ = make_client(tmp_path, site, headers={"Authorization": "Token x", "User-Agent": "spoof"})
    client.get("https://example.org/data")
    assert calls[-1].headers["authorization"] == "Token x"
    assert calls[-1].headers["user-agent"] == USER_AGENT
    assert "Token x" not in "".join(p.read_text(errors="ignore") for p in tmp_path.rglob("*") if p.is_file())


def test_timeouts_are_retried(tmp_path):
    attempts = {"n": 0}

    def flaky(request):
        if request.url.path == "/robots.txt":
            return httpx.Response(404)
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, text="ok")

    client, _, _ = make_client(tmp_path, flaky, use_cache=False)
    assert client.get("https://example.org/page").text == "ok"
    assert attempts["n"] == 3


def test_missing_robots_means_allowed(tmp_path):
    def no_robots(request):
        return httpx.Response(404) if request.url.path == "/robots.txt" else httpx.Response(200, text="ok")

    client, _, _ = make_client(tmp_path, no_robots)
    assert client.get("https://example.org/anything").text == "ok"
