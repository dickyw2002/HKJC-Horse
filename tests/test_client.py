import httpx

from hkjc_predictor.ingestion.client import PoliteFetcher


def test_cache_skips_second_download_and_paces_requests(tmp_path):
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(200, text=f"body {request.url}")

    fetcher = PoliteFetcher(
        tmp_path,
        min_interval=1.0,
        jitter=0,
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    )
    first = fetcher.get_text("https://example.test/a", cache_name="a.html")
    second = fetcher.get_text("https://example.test/a", cache_name="a.html")
    third = fetcher.get_text("https://example.test/b", cache_name="b.html")
    fetcher.close()

    assert first.text == "body https://example.test/a"
    assert second.from_cache is True
    assert second.text == first.text
    assert third.text == "body https://example.test/b"
    assert calls["n"] == 2
    assert fetcher.cache_hits == 1
    assert sleeps and sleeps[0] >= 0.9


def test_retries_server_errors_with_backoff(tmp_path):
    calls = {"n": 0}
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        if calls["n"] == 1:
            return httpx.Response(503, text="busy")
        return httpx.Response(200, text="ok")

    fetcher = PoliteFetcher(
        tmp_path,
        min_interval=1.0,
        jitter=0,
        max_retries=3,
        transport=httpx.MockTransport(handler),
        sleep=sleeps.append,
    )
    result = fetcher.get_text("https://example.test/race", cache_name="race.html")
    fetcher.close()
    assert result.ok
    assert result.text == "ok"
    assert calls["n"] == 2
    assert any(delay >= 2 for delay in sleeps)
