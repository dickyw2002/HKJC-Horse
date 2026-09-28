"""Find local Sha Tin and Happy Valley meeting dates."""

from __future__ import annotations

import json
import logging
import re
from datetime import date

from selectolax.parser import HTMLParser

from hkjc_predictor.ingestion.models import MeetingRef

logger = logging.getLogger(__name__)

LOCAL_COURSES = {"ST", "HV"}
MONTH_RE = re.compile(r"(\d{1,2})/(\d{4})")


def parse_fixture_page(html: str, *, year: int | None = None, month: int | None = None) -> list[MeetingRef]:
    """Read local meetings from one fixture calendar month.

    Racing days are ``td.calendar`` cells. The ST/HV image ``alt`` is the
    racecourse. Days carried over from the adjacent month are not calendar cells.
    """
    tree = HTMLParser(html)
    header_year, header_month = _month_header(tree)
    year = header_year or year
    month = header_month or month
    if year is None or month is None:
        raise ValueError("fixture page has no month header")

    meetings: list[MeetingRef] = []
    seen: set[tuple[date, str]] = set()
    for cell in tree.css("td.calendar"):
        day_node = cell.css_first("span.f_fl")
        if day_node is None:
            continue
        day_text = re.sub(r"\D", "", day_node.text() or "")
        if not day_text:
            continue
        try:
            meeting_date = date(year, month, int(day_text))
        except ValueError:
            logger.warning("ignoring invalid fixture day %s-%s-%s", year, month, day_text)
            continue
        venues: list[str] = []
        for image in cell.css("img"):
            alt = (image.attributes.get("alt") or "").upper()
            if alt in LOCAL_COURSES and alt not in venues:
                venues.append(alt)
        for venue in venues:
            key = (meeting_date, venue)
            if key in seen:
                continue
            seen.add(key)
            meetings.append(MeetingRef(meeting_date, venue))
    meetings.sort(key=lambda item: (item.meeting_date, item.racecourse))
    return meetings


def _month_header(tree: HTMLParser) -> tuple[int | None, int | None]:
    for cell in tree.css("thead td"):
        if cell.attributes.get("colspan") != "7":
            continue
        match = MONTH_RE.fullmatch(re.sub(r"\s+", "", cell.text() or ""))
        if match:
            month = int(match.group(1))
            year = int(match.group(2))
            if 1 <= month <= 12:
                return year, month
    return None, None


def parse_datelist(payload: dict) -> list[MeetingRef]:
    """Parse ``LocalResults.aspx`` date list JSON.

    Current responses leave ``Value`` null, so the racecourse is unknown and
    returned as an empty string. A future payload that fills ``Value`` with
    venue codes is honoured.
    """
    meetings: list[MeetingRef] = []
    for item in payload.get("MeetingDateList") or []:
        key = str(item.get("Key") or "")
        if len(key) < 10:
            continue
        try:
            meeting_date = date.fromisoformat(key[:10])
        except ValueError:
            continue
        venues = _venues_from_value(item.get("Value"))
        if not venues:
            meetings.append(MeetingRef(meeting_date, ""))
            continue
        for venue in venues:
            meetings.append(MeetingRef(meeting_date, venue))
    return meetings


def _venues_from_value(value: object) -> list[str]:
    if isinstance(value, dict):
        venues = [str(key).upper() for key in value if str(key).upper() in LOCAL_COURSES]
        return venues
    if isinstance(value, str) and value.upper() in LOCAL_COURSES:
        return [value.upper()]
    return []


def parse_datelist_text(text: str) -> list[MeetingRef]:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError("date list was not JSON") from exc
    if not isinstance(payload, dict):
        raise ValueError("date list JSON was not an object")
    return parse_datelist(payload)
