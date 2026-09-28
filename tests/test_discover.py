from datetime import date
from pathlib import Path

from hkjc_predictor.ingestion.discover import parse_datelist, parse_fixture_page
from hkjc_predictor.ingestion.models import MeetingRef

FIXTURES = Path(__file__).parent / "fixtures"


def test_january_2024_fixture_lists_local_meetings():
    html = (FIXTURES / "fixture_2024-01.html").read_text(encoding="utf-8")
    meetings = parse_fixture_page(html, year=2024, month=1)
    assert meetings[0] == MeetingRef(date(2024, 1, 1), "ST")
    assert MeetingRef(date(2024, 1, 4), "HV") in meetings
    assert MeetingRef(date(2024, 1, 31), "HV") in meetings
    assert len(meetings) == 10
    assert {meeting.racecourse for meeting in meetings} <= {"ST", "HV"}


def test_datelist_without_venues_and_with_venues():
    unknown = parse_datelist(
        {"MeetingDateList": [{"Key": "2026-09-27T00:00:00", "Value": None}]}
    )
    assert unknown == [MeetingRef(date(2026, 9, 27), "")]
    known = parse_datelist(
        {"MeetingDateList": [{"Key": "2026-09-23T00:00:00", "Value": {"HV": "Happy Valley"}}]}
    )
    assert known == [MeetingRef(date(2026, 9, 23), "HV")]
