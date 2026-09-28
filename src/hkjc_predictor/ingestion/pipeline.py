"""Backfill and incremental update of local HKJC results."""

from __future__ import annotations

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from hkjc_predictor.ingestion.client import (
    DATE_LIST_URL,
    FetchResult,
    fixture_cache_name,
    fixture_url,
    results_cache_name,
    results_url,
)
from hkjc_predictor.ingestion.discover import parse_datelist_text, parse_fixture_page
from hkjc_predictor.ingestion.models import EmptyPage, MeetingRef, ParsedRace, RunSummary, race_id
from hkjc_predictor.ingestion.parse import ParseError, is_no_information, parse_results_page
from hkjc_predictor.ingestion.store import Store

logger = logging.getLogger(__name__)

HK_TZ = ZoneInfo("Asia/Hong_Kong")
OTHER_COURSE = {"ST": "HV", "HV": "ST"}


def hk_today() -> date:
    return datetime.now(HK_TZ).date()


def backfill(
    store: Store,
    fetcher,
    *,
    start: date,
    end: date,
    force: bool = False,
) -> RunSummary:
    today = hk_today()
    if end > today:
        logger.info("clamping end date %s to Hong Kong today %s", end, today)
        end = today
    if start > end:
        raise ValueError(f"start {start} is after end {end}")
    summary = RunSummary()
    meetings = discover_meetings(fetcher, start, end, today, force=force)
    logger.info("discovered %s local meeting slots from %s to %s", len(meetings), start, end)
    for meeting in meetings:
        if summary.blocked:
            break
        _ingest_slot(store, fetcher, meeting, summary, force=force, today=today)
    return summary


def update(store: Store, fetcher, *, force: bool = False, default_start: date = date(2024, 1, 1)) -> RunSummary:
    """Finish partial meetings, then fetch anything newer than the latest complete one."""
    today = hk_today()
    summary = RunSummary()
    for meeting_date, racecourse in store.partial_meetings():
        if summary.blocked:
            break
        logger.info("retry partial meeting %s %s", meeting_date, racecourse)
        _ingest_meeting(
            store,
            fetcher,
            meeting_date,
            racecourse,
            summary,
            force=force,
            today=today,
            probe=False,
        )
    if summary.blocked:
        return summary
    last = store.max_meeting_date()
    start = default_start if last is None else last
    logger.info("update from %s through %s", start, today)
    newer = backfill(store, fetcher, start=start, end=today, force=force)
    return _merge(summary, newer)


def _merge(first: RunSummary, second: RunSummary) -> RunSummary:
    return RunSummary(
        meetings_complete=first.meetings_complete + second.meetings_complete,
        meetings_empty=first.meetings_empty + second.meetings_empty,
        meetings_partial=first.meetings_partial + second.meetings_partial,
        meetings_skipped=first.meetings_skipped + second.meetings_skipped,
        races_stored=first.races_stored + second.races_stored,
        errors=first.errors + second.errors,
        blocked=first.blocked or second.blocked,
        notes=[*first.notes, *second.notes],
    )


def discover_meetings(fetcher, start: date, end: date, today: date, *, force: bool = False) -> list[MeetingRef]:
    found: dict[tuple[date, str], MeetingRef] = {}
    year, month = start.year, start.month
    while (year, month) <= (end.year, end.month):
        refresh = force or (year, month) == (today.year, today.month)
        result = fetcher.get_text(
            fixture_url(year, month),
            cache_name=fixture_cache_name(year, month),
            force=refresh,
        )
        if not result.ok or result.text is None:
            logger.warning("fixture %04d-%02d unavailable: %s", year, month, result.error)
        else:
            try:
                for meeting in parse_fixture_page(result.text, year=year, month=month):
                    if start <= meeting.meeting_date <= end:
                        found[(meeting.meeting_date, meeting.racecourse)] = meeting
            except ValueError as exc:
                logger.warning("fixture %04d-%02d parse failed: %s", year, month, exc)
        year, month = (year + 1, 1) if month == 12 else (year, month + 1)

    date_list = fetcher.get_text(
        DATE_LIST_URL,
        cache_name="datelist/localresults.json",
        force=True,
    )
    if date_list.ok and date_list.text:
        try:
            for meeting in parse_datelist_text(date_list.text):
                if not (start <= meeting.meeting_date <= end):
                    continue
                if meeting.racecourse:
                    found[(meeting.meeting_date, meeting.racecourse)] = meeting
                elif not any(existing == meeting.meeting_date for existing, _course in found):
                    found[(meeting.meeting_date, "")] = meeting
        except ValueError as exc:
            logger.warning("date list ignored: %s", exc)

    return sorted(found.values(), key=lambda item: (item.meeting_date, item.racecourse))


def _ingest_slot(store: Store, fetcher, meeting: MeetingRef, summary: RunSummary, *, force: bool, today: date) -> None:
    if meeting.racecourse:
        _ingest_meeting(store, fetcher, meeting.meeting_date, meeting.racecourse, summary, force=force, today=today, probe=True)
        return
    logger.info("date list %s has no venue; probing Sha Tin then Happy Valley", meeting.meeting_date)
    for course in ("ST", "HV"):
        if summary.blocked:
            return
        before = summary.meetings_complete
        _ingest_meeting(
            store,
            fetcher,
            meeting.meeting_date,
            course,
            summary,
            force=force,
            today=today,
            probe=False,
        )
        if summary.meetings_complete > before:
            return


def _ingest_meeting(
    store: Store,
    fetcher,
    meeting_date: date,
    racecourse: str,
    summary: RunSummary,
    *,
    force: bool,
    today: date,
    probe: bool,
) -> None:
    key = f"{meeting_date.isoformat()}_{racecourse}"
    existing = store.get_meeting(key)
    if existing and not force:
        if existing["status"] == "complete":
            summary.meetings_skipped += 1
            logger.info("skip complete meeting %s", key)
            return
        if existing["status"] == "empty" and meeting_date < today:
            summary.meetings_skipped += 1
            logger.info("skip empty meeting %s", key)
            return

    numbers = _stored_numbers(existing)
    page: FetchResult | None = None
    parsed: ParsedRace | None = None
    if not numbers or force:
        page = _fetch_race(fetcher, meeting_date, racecourse, 1, force=force)
        if _blocked(page, summary):
            return
        if page.error or page.text is None:
            summary.errors += 1
            summary.notes.append(f"{key} race 1: {page.error}")
            store.upsert_meeting(
                meeting_date=meeting_date,
                racecourse=racecourse,
                venue_name=None,
                status="partial",
                race_numbers=numbers or None,
                source_url=page.final_url,
            )
            summary.meetings_partial += 1
            return
        store.record_page(
            cache_key=results_cache_name(meeting_date, racecourse, 1),
            url=page.final_url or page.url,
            outcome="empty" if is_no_information(page.text) else "ok",
            http_status=page.status,
            from_cache=page.from_cache,
            byte_length=len(page.text),
        )
        if is_no_information(page.text):
            if probe:
                other = OTHER_COURSE[racecourse]
                logger.info("%s has no %s results; trying %s", meeting_date, racecourse, other)
                _ingest_meeting(
                    store,
                    fetcher,
                    meeting_date,
                    other,
                    summary,
                    force=force,
                    today=today,
                    probe=False,
                )
                other_row = store.get_meeting(f"{meeting_date.isoformat()}_{other}")
                if other_row and other_row["status"] == "complete":
                    store.upsert_meeting(
                        meeting_date=meeting_date,
                        racecourse=racecourse,
                        venue_name=None,
                        status="empty",
                        race_numbers=[],
                        source_url=page.final_url,
                    )
                    summary.meetings_empty += 1
                    return
            store.upsert_meeting(
                meeting_date=meeting_date,
                racecourse=racecourse,
                venue_name=None,
                status="empty",
                race_numbers=[],
                source_url=page.final_url,
            )
            summary.meetings_empty += 1
            logger.info("no results for %s", key)
            return
        try:
            outcome = parse_results_page(
                page.text,
                fallback_date=meeting_date,
                fallback_course=racecourse,
                source_url=page.final_url,
            )
        except ParseError as exc:
            summary.errors += 1
            summary.notes.append(f"{key} race 1 parse: {exc}")
            logger.warning("parse failed %s race 1: %s", key, exc)
            summary.meetings_partial += 1
            store.upsert_meeting(
                meeting_date=meeting_date,
                racecourse=racecourse,
                venue_name=None,
                status="partial",
                race_numbers=None,
                source_url=page.final_url,
            )
            return
        if isinstance(outcome, EmptyPage):
            store.upsert_meeting(
                meeting_date=meeting_date,
                racecourse=racecourse,
                venue_name=None,
                status="empty",
                race_numbers=[],
                source_url=page.final_url,
            )
            summary.meetings_empty += 1
            return
        parsed = outcome
        if parsed.racecourse != racecourse:
            logger.info("page venue %s differs from requested %s; storing %s", parsed.racecourse, racecourse, parsed.racecourse)
            racecourse = parsed.racecourse
            key = parsed.meeting_key
        numbers = list(parsed.race_numbers)
        if not store.race_exists(parsed.race_key) or force:
            store.upsert_race(parsed)
            summary.races_stored += 1
            logger.info(
                "stored %s R%s %s runners=%s dividends=%s",
                parsed.meeting_date,
                parsed.race_no,
                parsed.race_name,
                len(parsed.runners),
                len(parsed.dividends),
            )

    if not numbers:
        summary.errors += 1
        summary.notes.append(f"{key} has no race numbers")
        summary.meetings_partial += 1
        return

    unresolved = False
    for race_no in numbers:
        if parsed is not None and race_no == parsed.race_no and store.race_exists(parsed.race_key):
            continue
        race_key = race_id(meeting_date, racecourse, race_no)
        cache_key = results_cache_name(meeting_date, racecourse, race_no)
        if store.race_exists(race_key) and not force:
            continue
        if (
            not force
            and meeting_date < today
            and store.page_outcome(cache_key) == "empty"
        ):
            continue
        result = _fetch_race(fetcher, meeting_date, racecourse, race_no, force=force)
        if _blocked(result, summary):
            unresolved = True
            break
        if result.error or result.text is None:
            summary.errors += 1
            summary.notes.append(f"{key} race {race_no}: {result.error}")
            unresolved = True
            continue
        empty = is_no_information(result.text)
        store.record_page(
            cache_key=cache_key,
            url=result.final_url or result.url,
            outcome="empty" if empty else "ok",
            http_status=result.status,
            from_cache=result.from_cache,
            byte_length=len(result.text),
        )
        if empty:
            if meeting_date >= today:
                unresolved = True
            logger.info("no information for %s race %s", key, race_no)
            continue
        try:
            outcome = parse_results_page(
                result.text,
                fallback_date=meeting_date,
                fallback_course=racecourse,
                source_url=result.final_url,
            )
        except ParseError as exc:
            summary.errors += 1
            summary.notes.append(f"{key} race {race_no} parse: {exc}")
            logger.warning("parse failed %s race %s: %s", key, race_no, exc)
            unresolved = True
            continue
        if isinstance(outcome, EmptyPage):
            if meeting_date >= today:
                unresolved = True
            continue
        if outcome.racecourse != racecourse or outcome.meeting_date != meeting_date:
            summary.errors += 1
            summary.notes.append(
                f"{key} race {race_no} returned {outcome.meeting_date} {outcome.racecourse}"
            )
            unresolved = True
            continue
        store.upsert_race(outcome)
        summary.races_stored += 1
        logger.info(
            "stored %s R%s %s runners=%s dividends=%s",
            outcome.meeting_date,
            outcome.race_no,
            outcome.race_name,
            len(outcome.runners),
            len(outcome.dividends),
        )

    status = "partial" if unresolved else "complete"
    venue_name = parsed.venue_name if parsed is not None else (existing or {}).get("venue_name")
    source_url = parsed.source_url if parsed is not None else (page.final_url if page else None)
    store.upsert_meeting(
        meeting_date=meeting_date,
        racecourse=racecourse,
        venue_name=venue_name,
        status=status,
        race_numbers=numbers,
        source_url=source_url,
    )
    if status == "complete":
        summary.meetings_complete += 1
    else:
        summary.meetings_partial += 1
    logger.info("meeting %s %s (%s races)", key, status, len(numbers))


def _stored_numbers(existing: dict | None) -> list[int]:
    if not existing or not existing.get("race_numbers"):
        return []
    return [int(part) for part in str(existing["race_numbers"]).split(",") if part]


def _fetch_race(fetcher, meeting_date: date, racecourse: str, race_no: int, *, force: bool) -> FetchResult:
    url = results_url(meeting_date, racecourse, race_no)
    logger.info("fetch %s", url)
    return fetcher.get_text(
        url,
        cache_name=results_cache_name(meeting_date, racecourse, race_no),
        force=force,
    )


def _blocked(result: FetchResult, summary: RunSummary) -> bool:
    if result.error and result.error.startswith("blocked"):
        summary.blocked = True
        summary.errors += 1
        summary.notes.append(result.error)
        logger.error("stopping because the site blocked a request: %s", result.error)
        return True
    return False
