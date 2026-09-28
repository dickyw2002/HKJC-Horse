from datetime import date

import pytest

from hkjc_predictor.ingestion.client import PoliteFetcher
from hkjc_predictor.ingestion.pipeline import backfill
from hkjc_predictor.ingestion.store import Store

pytestmark = pytest.mark.live


def test_live_sha_tin_meeting_2026_09_27(tmp_path, capsys):
    """Fetch one recent Sha Tin meeting from the public results pages."""
    store = Store(tmp_path / "hkjc.duckdb")
    with PoliteFetcher(tmp_path / "cache", min_interval=1.0, jitter=0.15) as fetcher:
        summary = backfill(
            store,
            fetcher,
            start=date(2026, 9, 27),
            end=date(2026, 9, 27),
        )
    totals = store.totals()
    print(
        f"live smoke 2026-09-27 ST races={totals['races']} "
        f"runners={totals['runners']} dividends={totals['dividends']} "
        f"errors={summary.errors}"
    )
    captured = capsys.readouterr()
    assert "live smoke 2026-09-27 ST" in captured.out
    assert summary.errors == 0
    assert summary.blocked is False
    assert totals["races"] >= 8
    assert totals["runners"] >= 80
    assert totals["dividends"] >= 50
    winner = store.con.execute(
        """
        SELECT horse_name, horse_id, win_odds
        FROM runners
        WHERE meeting_date = DATE '2026-09-27' AND racecourse = 'ST' AND race_no = 1 AND finish_position_num = 1
        """
    ).fetchone()
    assert winner is not None
    assert winner[0] == "WAVE GARDEN"
    assert winner[1] == "HK_2024_K209"
    store.close()
