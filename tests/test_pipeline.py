from datetime import date

from hkjc_predictor.ingestion.client import (
    DATE_LIST_URL,
    FetchResult,
    fixture_url,
    prefer_archive,
    results_url,
)
from hkjc_predictor.ingestion.pipeline import backfill
from hkjc_predictor.ingestion.store import Store

RACE_HTML = """
<html><body>
<div class="raceMeeting_select"><span class="f_fl">Race Meeting: 27/09/2026 Sha Tin</span></div>
<div class="race_tab"><table>
<thead><tr><td>RACE 1 (46)</td></tr></thead>
<tbody>
<tr><td>Class 5 - 1200M - (40-0)</td><td>Going :</td><td>GOOD</td></tr>
<tr><td>SAMPLE HANDICAP</td><td>Course :</td><td>ALL WEATHER TRACK</td></tr>
<tr><td>HK$ 875,000</td><td>Time :</td><td>(1:10.17)</td></tr>
</tbody></table></div>
<table class="draggable"><thead><tr>
<td>Pla.</td><td>Horse No.</td><td>Horse</td><td>Jockey</td><td>Trainer</td>
<td>Act. Wt.</td><td>Declar. Horse Wt.</td><td>Dr.</td><td>LBW</td>
<td>Running Position</td><td>Finish Time</td><td>Win Odds</td>
</tr></thead><tbody><tr>
<td>1</td><td>8</td>
<td><a href="/horse?horseid=HK_2024_K209">WAVE GARDEN</a> (K209)</td>
<td><a href="/j?jockeyid=KRW">R Kingscote</a></td>
<td><a href="/t?trainerid=CCW">C W Chang</a></td>
<td>124</td><td>1069</td><td>11</td><td>---</td>
<td><div><div>9</div><div>11</div><div>1</div></div></td>
<td>1:10.17</td><td>35</td>
</tr></tbody></table>
<div class="dividend_tab"><table><tbody>
<tr><td>WIN</td><td>8</td><td>358.50</td></tr>
<tr><td rowspan="2">PLACE</td><td>8</td><td>97.50</td></tr>
<tr><td>9</td><td>30.00</td></tr>
</tbody></table></div>
</body></html>
"""

FIXTURE_HTML = """
<table><thead><tr class="bg_blue"><td colspan="7">9/2026</td></tr></thead>
<tbody><tr><td class="calendar">
<span class="f_fl f_fs14">27</span><img alt="ST" src="/st.gif" />
</td></tr></tbody></table>
"""

DATE_LIST = '{"MeetingDateList":[{"Key":"2026-09-27T00:00:00","Value":null},{"Key":"2026-10-01T00:00:00","Value":null}]}'
EMPTY = '<div id="errorContainer">No information.</div>'


class MapFetcher:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def get_text(self, url: str, *, cache_name: str, force: bool = False) -> FetchResult:
        self.calls.append(url)
        if "fixture" in url:
            text = FIXTURE_HTML
        elif "DateList" in url:
            text = DATE_LIST
        elif "RaceNo=1" in url and "Racecourse=ST" in url:
            text = RACE_HTML
        else:
            text = EMPTY
        return FetchResult(url=url, final_url=url, status=200, text=text, from_cache=False)


def test_backfill_is_idempotent(tmp_path):
    store = Store(tmp_path / "hkjc.duckdb")
    fetcher = MapFetcher()
    day = date(2026, 9, 27)
    summary = backfill(store, fetcher, start=day, end=day)
    assert summary.meetings_complete == 1
    assert summary.errors == 0
    assert store.totals()["runners"] == 1
    assert store.totals()["dividends"] == 3
    pools = [row[0] for row in store.con.execute("SELECT pool FROM dividends ORDER BY row_order").fetchall()]
    assert pools == ["WIN", "PLACE", "PLACE"]
    race_calls = [url for url in fetcher.calls if "localresults" in url]
    assert race_calls == [results_url(day, "ST", 1)]

    again = backfill(store, fetcher, start=day, end=day)
    assert again.meetings_skipped == 1
    assert again.races_stored == 0
    assert [url for url in fetcher.calls if "localresults" in url] == race_calls
    assert fixture_url(2026, 9) in fetcher.calls
    assert DATE_LIST_URL in fetcher.calls
    store.close()


def test_old_meeting_uses_archive_url_and_redirect_sticks(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "hkjc_predictor.ingestion.pipeline.hk_today",
        lambda: date(2026, 9, 28),
    )
    assert prefer_archive(date(2024, 1, 1), date(2026, 9, 28))
    assert not prefer_archive(date(2026, 9, 27), date(2026, 9, 28))

    class RedirectFetcher:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get_text(self, url: str, *, cache_name: str, force: bool = False) -> FetchResult:
            self.calls.append(url)
            if "fixture" in url:
                text = '<table><thead><tr><td colspan="7">9/2026</td></tr></thead></table>'
            elif "DateList" in url:
                text = '{"MeetingDateList":[{"Key":"2026-09-27T00:00:00","Value":{"ST":"Sha Tin"}}]}'
            else:
                text = RACE_HTML.replace("RACE 1 (46)", "RACE 1 (46)").replace(
                    "SAMPLE HANDICAP",
                    "SAMPLE HANDICAP",
                )
                text = text.replace(
                    "</div>\n<table class=\"draggable\">",
                    '<a href="/localresults?racedate=2026/09/27&Racecourse=ST&RaceNo=2">2</a></div>\n<table class="draggable">',
                )
            final = url
            if "RaceNo=1" in url and "/archive/" not in url:
                final = (
                    "https://racing.hkjc.com/en-us/local/information/archive/localresults"
                    "?racedate=2026/09/27&racecourse=ST&RaceNo=1"
                )
            if "RaceNo=2" in url:
                text = text.replace("RACE 1 (46)", "RACE 2 (47)")
            return FetchResult(url=url, final_url=final, status=200, text=text, from_cache=False)

    store = Store(tmp_path / "archive.duckdb")
    fetcher = RedirectFetcher()
    summary = backfill(store, fetcher, start=date(2026, 9, 27), end=date(2026, 9, 27))
    assert summary.errors == 0
    race_calls = [url for url in fetcher.calls if "localresults" in url]
    assert race_calls[0] == results_url(date(2026, 9, 27), "ST", 1)
    assert "/archive/" in race_calls[1]
    assert "racecourse=ST" in race_calls[1]
    assert race_calls[1].endswith("RaceNo=2")
    store.close()
