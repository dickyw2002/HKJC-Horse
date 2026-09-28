"""Load the checked-in reference results CSVs into DuckDB.

``data/races.csv`` and ``data/runners.csv`` are a validated extract. Importing
them does not download anything. Finish-position codes stay text. Withdrawn
rows may have a blank horse number; runners are keyed by horse id.
"""

from __future__ import annotations

import csv
import re
from collections import defaultdict
from dataclasses import dataclass
from datetime import date
from pathlib import Path

from hkjc_predictor.ingestion.models import meeting_id, race_id, season_of
from hkjc_predictor.ingestion.parse import _parse_int, _parse_odds, _placing, parse_finish_seconds, parse_lbw
from hkjc_predictor.ingestion.store import Store

RACE_COLUMNS = (
    "date",
    "season",
    "racecourse",
    "race_no",
    "race_index",
    "race_name",
    "class",
    "distance_m",
    "rating_band",
    "going",
    "course",
    "surface",
    "rail",
    "prize_hkd",
    "n_runners",
    "class_raw",
    "race_time_splits",
    "sectional_times",
)
RUNNER_COLUMNS = (
    "date",
    "season",
    "racecourse",
    "race_no",
    "finishing_position",
    "horse_no",
    "horse_name",
    "brand_no",
    "horse_id",
    "jockey",
    "trainer",
    "actual_weight_lb",
    "declared_horse_weight_lb",
    "draw",
    "lengths_behind",
    "running_positions",
    "finish_time",
    "win_odds",
)
VENUE_NAMES = {"ST": "Sha Tin", "HV": "Happy Valley"}


@dataclass(frozen=True)
class ImportSummary:
    meetings: int
    races: int
    runners: int


def import_reference(store: Store, races_path: Path, runners_path: Path) -> ImportSummary:
    races = _read_csv(races_path, RACE_COLUMNS)
    runners = _read_csv(runners_path, RUNNER_COLUMNS)
    if not races:
        raise ValueError(f"{races_path} has no race rows")
    if not runners:
        raise ValueError(f"{runners_path} has no runner rows")

    race_rows = [_race_record(row) for row in races]
    race_keys = {row[0] for row in race_rows}
    if len(race_keys) != len(race_rows):
        raise ValueError("reference races.csv has duplicate race keys")

    runners_by_race: dict[tuple[str, str, int], list[dict[str, str]]] = defaultdict(list)
    for row in runners:
        runners_by_race[_runner_race_key(row)].append(row)

    runner_rows = []
    for row in runners:
        record = _runner_record(row)
        if record[0] not in race_keys:
            raise ValueError(f"runner {record[9]} has no matching race {record[0]}")
        runner_rows.append(record)

    seen_runners: set[tuple[str, str]] = set()
    for record in runner_rows:
        key = (record[0], record[9])
        if key in seen_runners:
            raise ValueError(f"duplicate runner {key[1]} in {key[0]}")
        seen_runners.add(key)

    for race in race_rows:
        counted = len(runners_by_race[(race[2].isoformat(), race[3], race[4])])
        declared = race[20]
        if declared is not None and declared != counted:
            raise ValueError(
                f"{race[0]} n_runners is {declared} but {counted} runner rows were read"
            )

    meetings: dict[tuple[date, str], list[int]] = defaultdict(list)
    for race in race_rows:
        meetings[(race[2], race[3])].append(race[4])

    store.con.execute("BEGIN")
    try:
        store.con.executemany(
            """
            INSERT INTO races (
                race_id, meeting_id, meeting_date, racecourse, race_no, season_race_no,
                race_class, class_line, distance_m, rating_band, going, course,
                race_name, prize_hkd, sectionals, source_url, abandoned,
                season, surface, rail, n_runners, race_time_splits
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (race_id) DO UPDATE SET
                meeting_id = excluded.meeting_id,
                meeting_date = excluded.meeting_date,
                racecourse = excluded.racecourse,
                race_no = excluded.race_no,
                season_race_no = excluded.season_race_no,
                race_class = excluded.race_class,
                class_line = excluded.class_line,
                distance_m = excluded.distance_m,
                rating_band = excluded.rating_band,
                going = excluded.going,
                course = excluded.course,
                race_name = excluded.race_name,
                prize_hkd = excluded.prize_hkd,
                sectionals = excluded.sectionals,
                source_url = excluded.source_url,
                abandoned = excluded.abandoned,
                season = excluded.season,
                surface = excluded.surface,
                rail = excluded.rail,
                n_runners = excluded.n_runners,
                race_time_splits = excluded.race_time_splits
            """,
            race_rows,
        )
        store.con.execute("DROP TABLE IF EXISTS import_race_ids")
        store.con.execute("CREATE TEMP TABLE import_race_ids (race_id VARCHAR)")
        store.con.executemany(
            "INSERT INTO import_race_ids VALUES (?)",
            [(row[0],) for row in race_rows],
        )
        store.con.execute(
            "DELETE FROM runners WHERE race_id IN (SELECT race_id FROM import_race_ids)"
        )
        store.con.executemany(
            """
            INSERT INTO runners (
                race_id, meeting_date, racecourse, race_no, horse_no,
                finish_position, finish_position_num, horse_name, horse_code, horse_id,
                jockey, jockey_id, trainer, trainer_id, actual_weight,
                declared_horse_weight, draw, lbw, lbw_lengths, running_positions,
                finish_time, finish_time_seconds, win_odds, incident, season
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
            )
            """,
            runner_rows,
        )
        for (meeting_date, racecourse), numbers in meetings.items():
            ordered = sorted(set(numbers))
            store.con.execute(
                """
                INSERT INTO meetings (
                    meeting_id, meeting_date, racecourse, venue_name, status,
                    race_count, race_numbers, source_url, updated_at
                ) VALUES (?, ?, ?, ?, 'complete', ?, ?, NULL, current_timestamp)
                ON CONFLICT (meeting_id) DO UPDATE SET
                    venue_name = excluded.venue_name,
                    status = 'complete',
                    race_count = excluded.race_count,
                    race_numbers = excluded.race_numbers,
                    updated_at = excluded.updated_at
                """,
                [
                    meeting_id(meeting_date, racecourse),
                    meeting_date,
                    racecourse,
                    VENUE_NAMES.get(racecourse),
                    len(ordered),
                    ",".join(str(number) for number in ordered),
                ],
            )
        store.con.execute("COMMIT")
    except Exception:
        store.con.execute("ROLLBACK")
        raise
    return ImportSummary(meetings=len(meetings), races=len(race_rows), runners=len(runner_rows))


def _read_csv(path: Path, columns: tuple[str, ...]) -> list[dict[str, str]]:
    if not path.is_file():
        raise ValueError(f"reference file not found: {path}")
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.DictReader(handle)
        header = tuple(reader.fieldnames or ())
        if header != columns:
            raise ValueError(
                f"{path} columns {header} do not match the reference layout {columns}"
            )
        return list(reader)


def _blank(value: str | None) -> str | None:
    if value is None:
        return None
    text = value.strip()
    return text or None


def _race_record(row: dict[str, str]) -> tuple:
    meeting_date = date.fromisoformat(row["date"])
    racecourse = row["racecourse"].strip().upper()
    if racecourse not in VENUE_NAMES:
        raise ValueError(f"unknown racecourse {racecourse!r} on {row['date']}")
    race_no = int(row["race_no"])
    season_race = _blank(row["race_index"])
    n_runners = _blank(row["n_runners"])
    return (
        race_id(meeting_date, racecourse, race_no),
        meeting_id(meeting_date, racecourse),
        meeting_date,
        racecourse,
        race_no,
        int(season_race) if season_race else None,
        _blank(row["class"]),
        _blank(row["class_raw"]),
        _parse_int(row["distance_m"]),
        _blank(row["rating_band"]),
        _blank(row["going"]),
        _blank(row["course"]),
        _blank(row["race_name"]),
        _parse_int(row["prize_hkd"]),
        _blank(row["sectional_times"]),
        None,
        False,
        _blank(row["season"]) or season_of(meeting_date),
        _blank(row["surface"]),
        _blank(row["rail"]),
        int(n_runners) if n_runners else None,
        _blank(row["race_time_splits"]),
    )


def _runner_race_key(row: dict[str, str]) -> tuple[str, str, int]:
    return (row["date"].strip(), row["racecourse"].strip().upper(), int(row["race_no"]))


def _runner_record(row: dict[str, str]) -> tuple:
    meeting_date = date.fromisoformat(row["date"])
    racecourse = row["racecourse"].strip().upper()
    race_no = int(row["race_no"])
    horse_id = _blank(row["horse_id"])
    horse_no = _parse_int(row["horse_no"])
    if not horse_id:
        if horse_no is None:
            raise ValueError(f"{meeting_date} {racecourse} R{race_no} runner has no horse id")
        horse_id = f"NOID_{horse_no}"
    placing, placing_num = _placing(row["finishing_position"])
    lbw = _blank(row["lengths_behind"])
    finish_time = _blank(row["finish_time"])
    positions = _blank(row["running_positions"])
    if positions:
        positions = re.sub(r"\s+", " ", positions)
    return (
        race_id(meeting_date, racecourse, race_no),
        meeting_date,
        racecourse,
        race_no,
        horse_no,
        placing,
        placing_num,
        _blank(row["horse_name"]),
        _blank(row["brand_no"]),
        horse_id,
        _blank(row["jockey"]),
        None,
        _blank(row["trainer"]),
        None,
        _parse_int(row["actual_weight_lb"]),
        _parse_int(row["declared_horse_weight_lb"]),
        _parse_int(row["draw"]),
        lbw,
        parse_lbw(lbw),
        positions,
        finish_time,
        parse_finish_seconds(finish_time),
        _parse_odds(row["win_odds"]),
        None,
        _blank(row["season"]) or season_of(meeting_date),
    )
