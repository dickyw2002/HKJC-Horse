"""DuckDB schema, idempotent upserts, and CSV/Parquet export."""

from __future__ import annotations

from datetime import date, datetime, timezone
from pathlib import Path

import duckdb

from hkjc_predictor.ingestion.models import ParsedRace, meeting_id, season_of

TABLES = ("meetings", "races", "runners", "dividends", "odds_snapshots", "fetched_pages")
EXPORT_TABLES = ("meetings", "races", "runners", "dividends", "odds_snapshots")

SCHEMA = """
CREATE TABLE IF NOT EXISTS meetings (
    meeting_id VARCHAR PRIMARY KEY,
    meeting_date DATE NOT NULL,
    racecourse VARCHAR NOT NULL,
    venue_name VARCHAR,
    status VARCHAR NOT NULL,
    race_count INTEGER,
    race_numbers VARCHAR,
    source_url VARCHAR,
    updated_at TIMESTAMP NOT NULL
);

CREATE TABLE IF NOT EXISTS races (
    race_id VARCHAR PRIMARY KEY,
    meeting_id VARCHAR NOT NULL,
    meeting_date DATE NOT NULL,
    racecourse VARCHAR NOT NULL,
    race_no INTEGER NOT NULL,
    season_race_no INTEGER,
    race_class VARCHAR,
    class_line VARCHAR,
    distance_m INTEGER,
    rating_band VARCHAR,
    going VARCHAR,
    course VARCHAR,
    race_name VARCHAR,
    prize_hkd BIGINT,
    sectionals VARCHAR,
    source_url VARCHAR,
    abandoned BOOLEAN DEFAULT FALSE,
    season VARCHAR,
    surface VARCHAR,
    rail VARCHAR,
    n_runners INTEGER,
    race_time_splits VARCHAR,
    UNIQUE (meeting_date, racecourse, race_no)
);

CREATE TABLE IF NOT EXISTS runners (
    race_id VARCHAR NOT NULL,
    meeting_date DATE NOT NULL,
    racecourse VARCHAR NOT NULL,
    race_no INTEGER NOT NULL,
    horse_no INTEGER,
    finish_position VARCHAR,
    finish_position_num INTEGER,
    horse_name VARCHAR,
    horse_code VARCHAR,
    horse_id VARCHAR NOT NULL,
    jockey VARCHAR,
    jockey_id VARCHAR,
    trainer VARCHAR,
    trainer_id VARCHAR,
    actual_weight INTEGER,
    declared_horse_weight INTEGER,
    draw INTEGER,
    lbw VARCHAR,
    lbw_lengths DOUBLE,
    running_positions VARCHAR,
    finish_time VARCHAR,
    finish_time_seconds DOUBLE,
    win_odds DOUBLE,
    incident VARCHAR,
    season VARCHAR,
    PRIMARY KEY (race_id, horse_id)
);

CREATE TABLE IF NOT EXISTS dividends (
    race_id VARCHAR NOT NULL,
    meeting_date DATE NOT NULL,
    racecourse VARCHAR NOT NULL,
    race_no INTEGER NOT NULL,
    row_order INTEGER NOT NULL,
    pool VARCHAR NOT NULL,
    winning_combination VARCHAR NOT NULL,
    dividend_hkd DOUBLE,
    dividend_text VARCHAR,
    PRIMARY KEY (race_id, row_order)
);

CREATE TABLE IF NOT EXISTS odds_snapshots (
    snapshot_id VARCHAR PRIMARY KEY,
    captured_at TIMESTAMP,
    meeting_date DATE,
    racecourse VARCHAR,
    race_no INTEGER,
    race_id VARCHAR,
    horse_no INTEGER,
    horse_id VARCHAR,
    odds_type VARCHAR,
    odds DOUBLE,
    source VARCHAR,
    note VARCHAR
);

CREATE TABLE IF NOT EXISTS fetched_pages (
    cache_key VARCHAR PRIMARY KEY,
    url VARCHAR NOT NULL,
    fetched_at TIMESTAMP NOT NULL,
    http_status INTEGER,
    outcome VARCHAR NOT NULL,
    from_cache BOOLEAN,
    byte_length INTEGER
);

CREATE INDEX IF NOT EXISTS idx_races_meeting ON races(meeting_id);
CREATE INDEX IF NOT EXISTS idx_runners_race ON runners(race_id);
CREATE INDEX IF NOT EXISTS idx_runners_horse ON runners(horse_id);
CREATE INDEX IF NOT EXISTS idx_dividends_race ON dividends(race_id);
"""


class Store:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        self.con = duckdb.connect(str(path))
        self.con.execute(SCHEMA)
        self._ensure_columns()

    def _ensure_columns(self) -> None:
        race_columns = self._column_names("races")
        for name, ddl in {
            "abandoned": "BOOLEAN DEFAULT FALSE",
            "season": "VARCHAR",
            "surface": "VARCHAR",
            "rail": "VARCHAR",
            "n_runners": "INTEGER",
            "race_time_splits": "VARCHAR",
        }.items():
            if name not in race_columns:
                self.con.execute(f"ALTER TABLE races ADD COLUMN {name} {ddl}")
        runner_columns = self._column_names("runners")
        if "season" not in runner_columns:
            self.con.execute("ALTER TABLE runners ADD COLUMN season VARCHAR")
        self._migrate_runner_key()

    def _column_names(self, table: str) -> set[str]:
        rows = self.con.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = ?
            """,
            [table],
        ).fetchall()
        return {row[0] for row in rows}

    def _migrate_runner_key(self) -> None:
        """Allow a blank horse number and key runners by horse id.

        Older databases used PRIMARY KEY (race_id, horse_no), which dropped
        withdrawn rows. horse_id is present on every reference-file row.
        """
        info = self.con.execute("PRAGMA table_info('runners')").fetchall()
        by_name = {row[1]: row for row in info}
        horse_no = by_name.get("horse_no")
        horse_id = by_name.get("horse_id")
        if horse_no is None or horse_id is None:
            return
        horse_no_required = bool(horse_no[3])
        horse_id_is_key = int(horse_id[5] or 0) > 0
        if not horse_no_required and horse_id_is_key:
            return
        self.con.execute("ALTER TABLE runners RENAME TO runners_legacy")
        self.con.execute(
            """
            CREATE TABLE runners (
                race_id VARCHAR NOT NULL,
                meeting_date DATE NOT NULL,
                racecourse VARCHAR NOT NULL,
                race_no INTEGER NOT NULL,
                horse_no INTEGER,
                finish_position VARCHAR,
                finish_position_num INTEGER,
                horse_name VARCHAR,
                horse_code VARCHAR,
                horse_id VARCHAR NOT NULL,
                jockey VARCHAR,
                jockey_id VARCHAR,
                trainer VARCHAR,
                trainer_id VARCHAR,
                actual_weight INTEGER,
                declared_horse_weight INTEGER,
                draw INTEGER,
                lbw VARCHAR,
                lbw_lengths DOUBLE,
                running_positions VARCHAR,
                finish_time VARCHAR,
                finish_time_seconds DOUBLE,
                win_odds DOUBLE,
                incident VARCHAR,
                season VARCHAR,
                PRIMARY KEY (race_id, horse_id)
            )
            """
        )
        legacy_columns = {
            row[0]
            for row in self.con.execute(
                """
                SELECT column_name
                FROM information_schema.columns
                WHERE table_name = 'runners_legacy'
                """
            ).fetchall()
        }
        season_sql = "season" if "season" in legacy_columns else "NULL"
        self.con.execute(
            f"""
            INSERT INTO runners
            SELECT
                race_id, meeting_date, racecourse, race_no, horse_no,
                finish_position, finish_position_num, horse_name, horse_code,
                COALESCE(NULLIF(horse_id, ''), 'NOID_' || CAST(horse_no AS VARCHAR)),
                jockey, jockey_id, trainer, trainer_id, actual_weight,
                declared_horse_weight, draw, lbw, lbw_lengths, running_positions,
                finish_time, finish_time_seconds, win_odds, incident,
                {season_sql}
            FROM runners_legacy
            """
        )
        self.con.execute("DROP TABLE runners_legacy")
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_runners_race ON runners(race_id)")
        self.con.execute("CREATE INDEX IF NOT EXISTS idx_runners_horse ON runners(horse_id)")

    def close(self) -> None:
        self.con.close()

    def __enter__(self) -> Store:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def partial_meetings(self) -> list[tuple[date, str]]:
        rows = self.con.execute(
            """
            SELECT meeting_date, racecourse
            FROM meetings
            WHERE status = 'partial'
            ORDER BY meeting_date, racecourse
            """
        ).fetchall()
        result: list[tuple[date, str]] = []
        for meeting_date, racecourse in rows:
            if isinstance(meeting_date, datetime):
                meeting_date = meeting_date.date()
            result.append((meeting_date, racecourse))
        return result

    def max_meeting_date(self) -> date | None:
        row = self.con.execute(
            "SELECT MAX(meeting_date) FROM meetings WHERE status = 'complete'"
        ).fetchone()
        if row is None or row[0] is None:
            return None
        value = row[0]
        if isinstance(value, datetime):
            return value.date()
        return value

    def get_meeting(self, key: str) -> dict | None:
        row = self.con.execute(
            """
            SELECT status, race_numbers, venue_name
            FROM meetings
            WHERE meeting_id = ?
            """,
            [key],
        ).fetchone()
        if row is None:
            return None
        return {"status": row[0], "race_numbers": row[1], "venue_name": row[2]}

    def race_exists(self, key: str) -> bool:
        row = self.con.execute("SELECT 1 FROM races WHERE race_id = ?", [key]).fetchone()
        return row is not None

    def page_outcome(self, cache_key: str) -> str | None:
        row = self.con.execute(
            "SELECT outcome FROM fetched_pages WHERE cache_key = ?",
            [cache_key],
        ).fetchone()
        return None if row is None else row[0]

    def record_page(
        self,
        *,
        cache_key: str,
        url: str,
        outcome: str,
        http_status: int | None,
        from_cache: bool,
        byte_length: int | None,
    ) -> None:
        self.con.execute(
            """
            INSERT INTO fetched_pages (
                cache_key, url, fetched_at, http_status, outcome, from_cache, byte_length
            ) VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (cache_key) DO UPDATE SET
                url = excluded.url,
                fetched_at = excluded.fetched_at,
                http_status = excluded.http_status,
                outcome = excluded.outcome,
                from_cache = excluded.from_cache,
                byte_length = excluded.byte_length
            """,
            [
                cache_key,
                url,
                _now(),
                http_status,
                outcome,
                from_cache,
                byte_length,
            ],
        )

    def upsert_meeting(
        self,
        *,
        meeting_date: date,
        racecourse: str,
        venue_name: str | None,
        status: str,
        race_numbers: list[int] | None,
        source_url: str | None,
    ) -> None:
        numbers = ",".join(str(number) for number in race_numbers) if race_numbers else None
        self.con.execute(
            """
            INSERT INTO meetings (
                meeting_id, meeting_date, racecourse, venue_name, status,
                race_count, race_numbers, source_url, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT (meeting_id) DO UPDATE SET
                venue_name = excluded.venue_name,
                status = excluded.status,
                race_count = excluded.race_count,
                race_numbers = excluded.race_numbers,
                source_url = COALESCE(excluded.source_url, meetings.source_url),
                updated_at = excluded.updated_at
            """,
            [
                meeting_id(meeting_date, racecourse),
                meeting_date,
                racecourse,
                venue_name,
                status,
                len(race_numbers) if race_numbers else 0,
                numbers,
                source_url,
                _now(),
            ],
        )

    def upsert_race(self, race: ParsedRace) -> None:
        self.con.execute("BEGIN")
        try:
            self.con.execute(
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
                [
                    race.race_key,
                    race.meeting_key,
                    race.meeting_date,
                    race.racecourse,
                    race.race_no,
                    race.season_race_no,
                    race.race_class,
                    race.class_line,
                    race.distance_m,
                    race.rating_band,
                    race.going,
                    race.course,
                    race.race_name,
                    race.prize_hkd,
                    race.sectionals,
                    race.source_url,
                    race.abandoned,
                    race.season or season_of(race.meeting_date),
                    race.surface,
                    race.rail,
                    race.n_runners if race.n_runners is not None else len(race.runners),
                    race.race_time_splits,
                ],
            )
            self.con.execute("DELETE FROM runners WHERE race_id = ?", [race.race_key])
            self.con.execute("DELETE FROM dividends WHERE race_id = ?", [race.race_key])
            runner_rows = []
            for runner in race.runners:
                horse_id = runner.horse_id or (
                    f"NOID_{runner.horse_no}" if runner.horse_no is not None else None
                )
                if not horse_id:
                    raise ValueError(f"{race.race_key} has a runner with no horse id or horse number")
                runner_rows.append(
                    (
                        race.race_key,
                        race.meeting_date,
                        race.racecourse,
                        race.race_no,
                        runner.horse_no,
                        runner.placing,
                        runner.placing_num,
                        runner.horse_name,
                        runner.horse_code,
                        horse_id,
                        runner.jockey,
                        runner.jockey_id,
                        runner.trainer,
                        runner.trainer_id,
                        runner.actual_weight,
                        runner.declared_horse_weight,
                        runner.draw,
                        runner.lbw,
                        runner.lbw_lengths,
                        runner.running_positions,
                        runner.finish_time,
                        runner.finish_time_seconds,
                        runner.win_odds,
                        runner.incident,
                        race.season or season_of(race.meeting_date),
                    )
                )
            if runner_rows:
                self.con.executemany(
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
            if race.dividends:
                self.con.executemany(
                    """
                    INSERT INTO dividends VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            race.race_key,
                            race.meeting_date,
                            race.racecourse,
                            race.race_no,
                            dividend.row_order,
                            dividend.pool,
                            dividend.winning_combination,
                            dividend.dividend_hkd,
                            dividend.dividend_text,
                        )
                        for dividend in race.dividends
                    ],
                )
            self.con.execute("COMMIT")
        except Exception:
            self.con.execute("ROLLBACK")
            raise

    def totals(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for table in TABLES:
            counts[table] = self.con.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
        return counts

    def export(self, out_dir: Path) -> list[Path]:
        out_dir.mkdir(parents=True, exist_ok=True)
        written: list[Path] = []
        order = {
            "meetings": "meeting_date, racecourse",
            "races": "meeting_date, racecourse, race_no",
            "runners": "race_id, horse_id",
            "dividends": "race_id, row_order",
            "odds_snapshots": "snapshot_id",
        }
        for table in EXPORT_TABLES:
            csv_path = out_dir / f"{table}.csv"
            parquet_path = out_dir / f"{table}.parquet"
            # The checked-in reference extract uses a different header. Leave
            # those files in place and write the database shape beside them.
            if table in {"races", "runners"} and _is_reference_results_csv(csv_path):
                csv_path = out_dir / f"{table}.normalized.csv"
                parquet_path = out_dir / f"{table}.normalized.parquet"
            for path in (csv_path, parquet_path):
                if path.exists():
                    path.unlink()
            query = f"SELECT * FROM {table} ORDER BY {order[table]}"
            self.con.execute(
                f"COPY ({query}) TO '{_sql_path(csv_path)}' (HEADER, DELIMITER ',')"
            )
            self.con.execute(
                f"COPY ({query}) TO '{_sql_path(parquet_path)}' (FORMAT PARQUET)"
            )
            written.extend((csv_path, parquet_path))
        return written


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _sql_path(path: Path) -> str:
    return str(path.resolve()).replace("'", "''")


def _is_reference_results_csv(path: Path) -> bool:
    if not path.is_file():
        return False
    with path.open(encoding="utf-8") as handle:
        header = handle.readline()
    return header.startswith("date,season,racecourse,")
