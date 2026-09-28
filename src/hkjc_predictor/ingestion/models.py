"""Parsed records and run summaries."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


def meeting_id(meeting_date: date, racecourse: str) -> str:
    return f"{meeting_date.isoformat()}_{racecourse}"


def race_id(meeting_date: date, racecourse: str, race_no: int) -> str:
    return f"{meeting_date.isoformat()}_{racecourse}_{race_no:02d}"


@dataclass(frozen=True)
class MeetingRef:
    meeting_date: date
    racecourse: str


@dataclass(frozen=True)
class ParsedRunner:
    horse_no: int
    placing: str | None
    placing_num: int | None
    horse_name: str | None
    horse_code: str | None
    horse_id: str | None
    jockey: str | None
    jockey_id: str | None
    trainer: str | None
    trainer_id: str | None
    actual_weight: int | None
    declared_horse_weight: int | None
    draw: int | None
    lbw: str | None
    lbw_lengths: float | None
    running_positions: str | None
    finish_time: str | None
    finish_time_seconds: float | None
    win_odds: float | None
    incident: str | None = None


@dataclass(frozen=True)
class ParsedDividend:
    row_order: int
    pool: str
    winning_combination: str
    dividend_hkd: float | None
    dividend_text: str | None


@dataclass(frozen=True)
class ParsedRace:
    meeting_date: date
    racecourse: str
    venue_name: str | None
    race_no: int
    season_race_no: int | None
    race_class: str | None
    class_line: str | None
    distance_m: int | None
    rating_band: str | None
    going: str | None
    course: str | None
    race_name: str | None
    prize_hkd: int | None
    sectionals: str | None
    runners: tuple[ParsedRunner, ...]
    dividends: tuple[ParsedDividend, ...]
    race_numbers: tuple[int, ...]
    source_url: str | None = None

    @property
    def meeting_key(self) -> str:
        return meeting_id(self.meeting_date, self.racecourse)

    @property
    def race_key(self) -> str:
        return race_id(self.meeting_date, self.racecourse, self.race_no)


@dataclass(frozen=True)
class EmptyPage:
    reason: str


@dataclass
class RunSummary:
    meetings_complete: int = 0
    meetings_empty: int = 0
    meetings_partial: int = 0
    meetings_skipped: int = 0
    races_stored: int = 0
    errors: int = 0
    blocked: bool = False
    notes: list[str] = field(default_factory=list)
