import csv
from datetime import date
from pathlib import Path

from hkjc_predictor.ingestion.reference import import_reference
from hkjc_predictor.ingestion.store import Store

ROOT = Path(__file__).resolve().parents[1]


def _write(path: Path, rows: list[dict[str, str]], columns: list[str]) -> None:
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        writer.writerows(rows)


def test_import_keeps_position_codes_and_blank_horse_numbers(tmp_path):
    race_columns = [
        "date", "season", "racecourse", "race_no", "race_index", "race_name", "class",
        "distance_m", "rating_band", "going", "course", "surface", "rail", "prize_hkd",
        "n_runners", "class_raw", "race_time_splits", "sectional_times",
    ]
    runner_columns = [
        "date", "season", "racecourse", "race_no", "finishing_position", "horse_no",
        "horse_name", "brand_no", "horse_id", "jockey", "trainer", "actual_weight_lb",
        "declared_horse_weight_lb", "draw", "lengths_behind", "running_positions",
        "finish_time", "win_odds",
    ]
    races = tmp_path / "races.csv"
    runners = tmp_path / "runners.csv"
    _write(
        races,
        [
            {
                "date": "2024-01-01",
                "season": "2023/24",
                "racecourse": "ST",
                "race_no": "1",
                "race_index": "297",
                "race_name": "FLAME TREE HANDICAP",
                "class": "Class 4",
                "distance_m": "1200",
                "rating_band": "60-40",
                "going": "GOOD",
                "course": 'TURF - "A" Course',
                "surface": "Turf",
                "rail": "A",
                "prize_hkd": "1170000",
                "n_runners": "2",
                "class_raw": "Class 4 - 1200M - (60-40)",
                "race_time_splits": "(23.45) (1:09.80)",
                "sectional_times": "23.45 | 22.50",
            }
        ],
        race_columns,
    )
    _write(
        runners,
        [
            {
                "date": "2024-01-01",
                "season": "2023/24",
                "racecourse": "ST",
                "race_no": "1",
                "finishing_position": "3 DH",
                "horse_no": "1",
                "horse_name": "EIGHTY LIGHT YEARS",
                "brand_no": "H170",
                "horse_id": "HK_2022_H170",
                "jockey": "Z Purton",
                "trainer": "A S Cruz",
                "actual_weight_lb": "125",
                "declared_horse_weight_lb": "1266",
                "draw": "1",
                "lengths_behind": "DH",
                "running_positions": "2 1 1",
                "finish_time": "1:09.80",
                "win_odds": "2.3",
            },
            {
                "date": "2024-01-01",
                "season": "2023/24",
                "racecourse": "ST",
                "race_no": "1",
                "finishing_position": "WV",
                "horse_no": "",
                "horse_name": "BEAR CHAMP",
                "brand_no": "AJ313",
                "horse_id": "HK_2023_J313",
                "jockey": "A Hamelin",
                "trainer": "J Size",
                "actual_weight_lb": "---",
                "declared_horse_weight_lb": "---",
                "draw": "",
                "lengths_behind": "---",
                "running_positions": "13   ",
                "finish_time": "---",
                "win_odds": "---",
            },
        ],
        runner_columns,
    )
    store = Store(tmp_path / "hkjc.duckdb")
    imported = import_reference(store, races, runners)
    again = import_reference(store, races, runners)
    assert imported == again
    assert imported.meetings == 1
    assert imported.races == 1
    assert imported.runners == 2
    rows = {
        row[4]: row
        for row in store.con.execute(
            """
            SELECT finish_position, finish_position_num, horse_no, horse_code, horse_id,
                   lbw, lbw_lengths, running_positions, win_odds, actual_weight, season
            FROM runners
            """
        ).fetchall()
    }
    assert rows["HK_2023_J313"] == (
        "WV",
        None,
        None,
        "AJ313",
        "HK_2023_J313",
        "---",
        0.0,
        "13",
        None,
        None,
        "2023/24",
    )
    assert rows["HK_2022_H170"][0] == "3 DH"
    assert rows["HK_2022_H170"][1] == 3
    assert rows["HK_2022_H170"][2] == 1
    race = store.con.execute(
        "SELECT season, surface, rail, season_race_no, race_class, abandoned FROM races"
    ).fetchone()
    assert race == ("2023/24", "Turf", "A", 297, "Class 4", False)
    meeting = store.con.execute(
        "SELECT status, race_count FROM meetings WHERE meeting_date = ?",
        [date(2024, 1, 1)],
    ).fetchone()
    assert meeting == ("complete", 1)
    store.close()


def test_checked_in_reference_dataset_loads(tmp_path):
    races = ROOT / "data" / "races.csv"
    runners = ROOT / "data" / "runners.csv"
    with races.open(encoding="utf-8") as handle:
        header = handle.readline()
    assert header.startswith("date,season,racecourse,")
    store = Store(tmp_path / "hkjc.duckdb")
    imported = import_reference(store, races, runners)
    assert imported.meetings == 238
    assert imported.races == 2304
    assert imported.runners == 28718
    codes = {
        row[0]
        for row in store.con.execute(
            """
            SELECT finish_position
            FROM runners
            WHERE finish_position IN ('WV', 'PU', 'DNF', 'VOID', '3 DH')
            GROUP BY finish_position
            """
        ).fetchall()
    }
    assert codes == {"WV", "PU", "DNF", "VOID", "3 DH"}
    blank = store.con.execute("SELECT count(*) FROM runners WHERE horse_no IS NULL").fetchone()[0]
    assert blank == 209
    brand = store.con.execute(
        "SELECT horse_code, horse_id FROM runners WHERE horse_code = 'AJ313'"
    ).fetchall()
    assert brand
    assert all(row[1] == "HK_2023_J313" for row in brand)
    store.close()
