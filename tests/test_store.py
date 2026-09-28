from datetime import date

from hkjc_predictor.ingestion.models import ParsedDividend, ParsedRace, ParsedRunner
from hkjc_predictor.ingestion.store import Store


def _race() -> ParsedRace:
    runner = ParsedRunner(
        horse_no=1,
        placing="1",
        placing_num=1,
        horse_name="ALPHA",
        horse_code="E001",
        horse_id="HK_2020_E001",
        jockey="Z Purton",
        jockey_id="PZ",
        trainer="A S Cruz",
        trainer_id="CAS",
        actual_weight=133,
        declared_horse_weight=1100,
        draw=4,
        lbw="---",
        lbw_lengths=0.0,
        running_positions="1 1 1",
        finish_time="1:09.20",
        finish_time_seconds=69.20,
        win_odds=3.5,
        incident=None,
    )
    return ParsedRace(
        meeting_date=date(2024, 1, 1),
        racecourse="ST",
        venue_name="Sha Tin",
        race_no=1,
        season_race_no=1,
        race_class="Class 5",
        class_line="Class 5 - 1200M - (40-0)",
        distance_m=1200,
        rating_band="40-0",
        going="GOOD",
        course="TURF - \"A\" Course",
        race_name="SAMPLE",
        prize_hkd=875000,
        sectionals="24.00 | 23.00",
        runners=(runner,),
        dividends=(
            ParsedDividend(1, "WIN", "1", 25.0, "25.00"),
        ),
        race_numbers=(1,),
        source_url="https://racing.hkjc.com/example",
    )


def test_upsert_is_idempotent_and_export_writes_files(tmp_path):
    store = Store(tmp_path / "hkjc.duckdb")
    race = _race()
    store.upsert_meeting(
        meeting_date=race.meeting_date,
        racecourse="ST",
        venue_name="Sha Tin",
        status="complete",
        race_numbers=[1],
        source_url=race.source_url,
    )
    store.upsert_race(race)
    store.upsert_race(race)
    totals = store.totals()
    assert totals["meetings"] == 1
    assert totals["races"] == 1
    assert totals["runners"] == 1
    assert totals["dividends"] == 1
    assert totals["odds_snapshots"] == 0
    assert store.max_meeting_date() == date(2024, 1, 1)
    assert store.race_exists(race.race_key)

    paths = store.export(tmp_path / "out")
    names = {path.name for path in paths}
    assert "runners.csv" in names
    assert "runners.parquet" in names
    assert "odds_snapshots.csv" in names
    csv = (tmp_path / "out" / "runners.csv").read_text(encoding="utf-8")
    assert "ALPHA" in csv
    assert csv.count("\n") == 2

    protected = tmp_path / "reference"
    protected.mkdir()
    races_csv = protected / "races.csv"
    runners_csv = protected / "runners.csv"
    races_csv.write_text(
        "date,season,racecourse,race_no\n2024-01-01,2023/24,ST,1\n",
        encoding="utf-8",
    )
    runners_csv.write_text(
        "date,season,racecourse,race_no,finishing_position\n",
        encoding="utf-8",
    )
    (protected / "races.parquet").write_bytes(b"keep")
    store.export(protected)
    assert races_csv.read_text(encoding="utf-8").startswith("date,season,racecourse,")
    assert (protected / "races.parquet").read_bytes() == b"keep"
    assert (protected / "races.normalized.csv").exists()
    assert "ALPHA" in (protected / "runners.normalized.csv").read_text(encoding="utf-8")
    store.close()
