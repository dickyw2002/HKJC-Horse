from datetime import date
from pathlib import Path

import pytest

from hkjc_predictor.ingestion.models import ParsedRace
from hkjc_predictor.ingestion.parse import (
    ParseError,
    is_no_information,
    parse_class_line,
    parse_finish_seconds,
    parse_lbw,
    parse_results_page,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> str:
    return (FIXTURES / name).read_text(encoding="utf-8")


def test_class_line_and_lengths():
    assert parse_class_line("Class 5 - 1200M - (40-0)") == ("Class 5", 1200, "40-0")
    assert parse_class_line("Group Three - 1400M") == ("Group Three", 1400, None)
    assert parse_class_line("Class 3 (Restricted) - 1200M - (80-60)") == (
        "Class 3 (Restricted)",
        1200,
        "80-60",
    )
    assert parse_lbw("---") == 0
    assert parse_lbw("N") == 0.3
    assert parse_lbw("SH") == 0.1
    assert parse_lbw("HD") == 0.2
    assert parse_lbw("1-1/4") == 1.25
    assert parse_lbw("10-1/2") == 10.5
    assert parse_lbw("ML") is None
    assert parse_lbw("-SH") == -0.1
    assert parse_lbw("-HD") == -0.2
    assert parse_finish_seconds("1:10.17") == pytest.approx(70.17)
    assert parse_finish_seconds("0:56.89") == pytest.approx(56.89)


def test_no_information_page():
    html = '<div id="errorContainer">No information.</div>'
    assert is_no_information(html)
    assert not isinstance(parse_results_page(html), ParsedRace)


def test_sha_tin_all_weather_results_page():
    race = parse_results_page(_load("localresults_2026-09-27_ST_r1.html"))
    assert isinstance(race, ParsedRace)
    assert race.meeting_date == date(2026, 9, 27)
    assert race.racecourse == "ST"
    assert race.venue_name == "Sha Tin"
    assert race.race_no == 1
    assert race.season_race_no == 46
    assert race.race_class == "Class 5"
    assert race.distance_m == 1200
    assert race.rating_band == "40-0"
    assert race.going == "GOOD"
    assert race.course == "ALL WEATHER TRACK"
    assert race.surface == "AWT"
    assert race.rail is None
    assert race.season == "2026/27"
    assert race.race_time_splits == "(23.75) (46.40) (1:10.17)"
    assert race.n_runners == 12
    assert race.race_name == "TROPICBIRD HANDICAP"
    assert race.prize_hkd == 875_000
    assert race.sectionals and "23.75" in race.sectionals
    assert 1 in race.race_numbers and max(race.race_numbers) >= 10
    assert len(race.runners) == 12

    winner = race.runners[0]
    assert winner.placing == "1"
    assert winner.horse_no == 8
    assert winner.horse_name == "WAVE GARDEN"
    assert winner.horse_code == "K209"
    assert winner.horse_id == "HK_2024_K209"
    assert winner.jockey == "R Kingscote"
    assert winner.jockey_id == "KRW"
    assert winner.trainer == "C W Chang"
    assert winner.trainer_id == "CCW"
    assert winner.actual_weight == 124
    assert winner.declared_horse_weight == 1069
    assert winner.draw == 11
    assert winner.lbw == "---"
    assert winner.lbw_lengths == 0
    assert winner.running_positions == "9 11 1"
    assert winner.finish_time == "1:10.17"
    assert winner.finish_time_seconds == pytest.approx(70.17)
    assert winner.win_odds == 35
    assert winner.incident and "Jumped awkwardly" in winner.incident

    second = next(runner for runner in race.runners if runner.horse_no == 9)
    assert second.lbw == "1-1/4"
    assert second.lbw_lengths == 1.25
    assert second.win_odds == 7.8
    assert second.finish_time_seconds == pytest.approx(70.33)

    assert len(race.dividends) == 13
    win = race.dividends[0]
    assert win.pool == "WIN"
    assert win.winning_combination == "8"
    assert win.dividend_hkd == pytest.approx(358.50)
    places = [row for row in race.dividends if row.pool == "PLACE"]
    assert [row.winning_combination for row in places] == ["8", "9", "3"]
    assert places[2].dividend_hkd == pytest.approx(20.00)
    quartet = next(row for row in race.dividends if row.pool == "QUARTET")
    assert quartet.winning_combination == "8,9,3,6"
    assert quartet.dividend_hkd == pytest.approx(161836.00)


def test_happy_valley_turf_results_page():
    race = parse_results_page(_load("localresults_2026-09-23_HV_r1.html"))
    assert isinstance(race, ParsedRace)
    assert race.meeting_date == date(2026, 9, 23)
    assert race.racecourse == "HV"
    assert race.venue_name == "Happy Valley"
    assert race.race_class == "Class 5"
    assert race.distance_m == 1650
    assert race.rating_band == "40-0"
    assert race.going == "GOOD"
    assert race.course == 'TURF - "C" Course'
    assert race.surface == "Turf"
    assert race.rail == "C"
    assert race.race_name == "NAM FUNG HANDICAP"
    assert race.prize_hkd == 875_000
    assert len(race.runners) == 12
    neck = next(runner for runner in race.runners if runner.lbw == "N")
    assert neck.placing_num == 2
    assert neck.lbw_lengths == 0.3
    assert any(row.pool == "WIN" and row.dividend_hkd == pytest.approx(45.50) for row in race.dividends)


def test_archive_sha_tin_results_page():
    race = parse_results_page(_load("localresults_2024-09-08_ST_r1.html"))
    assert isinstance(race, ParsedRace)
    assert race.meeting_date == date(2024, 9, 8)
    assert race.racecourse == "ST"
    assert race.season_race_no == 1
    assert race.race_class == "Class 5"
    assert race.distance_m == 1600
    assert race.going == "GOOD TO YIELDING"
    assert race.course == 'TURF - "A" Course'
    assert race.race_name == "KOWLOON PEAK HANDICAP"
    assert race.runners
    assert race.runners[0].placing_num == 1
    assert race.dividends


def test_synthetic_non_finisher_and_dead_heat():
    html = """
    <div class="raceMeeting_select"><span class="f_fl">Race Meeting: 01/01/2024 Sha Tin</span></div>
    <div class="race_tab"><table>
      <thead><tr><td>RACE 2 (20)</td></tr></thead>
      <tbody>
        <tr><td>Class 4 - 1000M - (60-40)</td><td>Going :</td><td>WET SLOW</td></tr>
        <tr><td>TRIAL HANDICAP</td><td>Course :</td><td>TURF - "B" Course</td></tr>
        <tr><td>HK$ 1,170,000</td><td>Time :</td><td>(0:57.10)</td></tr>
      </tbody>
    </table></div>
    <table class="f_tac table_bd draggable">
      <thead><tr>
        <td>Pla.</td><td>Horse No.</td><td>Horse</td><td>Jockey</td><td>Trainer</td>
        <td>Act. Wt.</td><td>Declar. Horse Wt.</td><td>Dr.</td><td>LBW</td>
        <td>Running<br/>Position</td><td>Finish Time</td><td>Win Odds</td>
      </tr></thead>
      <tbody>
        <tr>
          <td>1 DH</td><td>1</td>
          <td><a href="/en-us/local/information/horse?horseid=HK_2020_E001">ALPHA</a> (E001)</td>
          <td><a href="/jockeyprofile?jockeyid=PZ">Z Purton</a></td>
          <td><a href="/trainerprofile?trainerid=CAS">A S Cruz</a></td>
          <td>135</td><td>1100</td><td>3</td><td>DH</td>
          <td><div><div>1</div><div>1</div></div></td>
          <td>0:57.10</td><td>2.5</td>
        </tr>
        <tr>
          <td>WV</td><td>2</td>
          <td><a href="/en-us/local/information/horse?horseid=HK_2019_D002">BRAVO</a> (D002)</td>
          <td>No rider</td><td>No trainer</td>
          <td>---</td><td>---</td><td></td><td>---</td>
          <td></td><td>---</td><td>---</td>
        </tr>
      </tbody>
    </table>
    <div class="dividend_tab"><table><tbody>
      <tr><td rowspan="1">WIN</td><td>1</td><td>15.00</td></tr>
      <tr><td>QUINELLA</td><td>1,3 F</td><td>REFUND</td></tr>
    </tbody></table></div>
    <div class="race_incident_report"><table>
      <thead><tr><td>Pla.</td><td>Horse No.</td><td>Horse</td><td>Incident</td></tr></thead>
      <tbody><tr><td>WV</td><td>2</td><td>BRAVO</td><td>Withdrawn.</td></tr></tbody>
    </table></div>
    """
    race = parse_results_page(html)
    assert isinstance(race, ParsedRace)
    assert race.race_no == 2
    assert race.distance_m == 1000
    assert race.course == 'TURF - "B" Course'
    assert race.prize_hkd == 1_170_000
    winner, scratched = race.runners
    assert winner.placing == "1 DH"
    assert winner.placing_num == 1
    assert winner.lbw_lengths == 0
    assert winner.running_positions == "1 1"
    assert scratched.placing == "WV"
    assert scratched.placing_num is None
    assert scratched.actual_weight is None
    assert scratched.win_odds is None
    assert scratched.finish_time_seconds is None
    assert scratched.incident == "Withdrawn."
    assert race.dividends[1].winning_combination == "1,3 F"
    assert race.dividends[1].dividend_hkd is None
    assert race.dividends[1].dividend_text == "REFUND"


def test_blank_horse_number_two_letter_brand_and_void_columns():
    html = """
    <div class="raceMeeting_select"><span class="f_fl">Race Meeting: 15/11/2025 Sha Tin</span></div>
    <div class="race_tab"><table>
      <thead><tr><td>RACE 8 (100)</td></tr></thead>
      <tbody>
        <tr><td>Class 4 - 1200M</td><td>Going :</td><td></td></tr>
        <tr><td>VOID HANDICAP</td><td>Course :</td><td>TURF - "B+2" Course</td></tr>
        <tr><td>HK$ 1,170,000</td><td>Time :</td><td></td></tr>
      </tbody>
    </table></div>
    <table class="draggable"><thead><tr>
        <td>Pla.</td><td>Horse No.</td><td>Horse</td><td>Jockey</td><td>Trainer</td>
        <td>Act. Wt.</td><td>Declar. Horse Wt.</td><td>Dr.</td><td>LBW</td><td>Finish Time</td>
    </tr></thead><tbody>
        <tr>
          <td>VOID</td><td></td>
          <td><a href="/horse?horseid=HK_2023_J313">BEAR CHAMP</a> (AJ313)</td>
          <td>A Hamelin</td><td>J Size</td>
          <td>120</td><td>---</td><td></td><td>---</td><td>---</td>
        </tr>
        <tr>
          <td>3 DH</td><td>4</td>
          <td><a href="/horse?horseid=HK_2022_H170">EIGHTY LIGHT YEARS</a> (H170)</td>
          <td>Z Purton</td><td>A S Cruz</td>
          <td>133</td><td>1100</td><td>2</td><td>DH</td><td>1:09.20</td>
        </tr>
    </tbody></table>
    """
    race = parse_results_page(html)
    assert isinstance(race, ParsedRace)
    assert race.surface == "Turf"
    assert race.rail == "B+2"
    assert race.going is None
    void, dead_heat = race.runners
    assert void.placing == "VOID"
    assert void.placing_num is None
    assert void.horse_no is None
    assert void.horse_code == "AJ313"
    assert void.horse_id == "HK_2023_J313"
    assert void.win_odds is None
    assert void.running_positions is None
    assert void.declared_horse_weight is None
    assert dead_heat.placing == "3 DH"
    assert dead_heat.placing_num == 3
    assert dead_heat.horse_no == 4


def test_abandoned_race_keeps_refund_dividends():
    race = parse_results_page(_load("localresults_2024-11-13_HV_r7_abandoned.html"))
    assert isinstance(race, ParsedRace)
    assert race.abandoned is True
    assert race.meeting_date == date(2024, 11, 13)
    assert race.racecourse == "HV"
    assert race.race_no == 7
    assert race.runners == ()
    assert race.race_numbers[0] == 1
    assert 7 in race.race_numbers
    win = next(row for row in race.dividends if row.pool == "WIN")
    assert win.winning_combination == "-"
    assert win.dividend_hkd is None
    assert win.dividend_text == "REFUND"
    double = next(row for row in race.dividends if row.pool == "6TH DOUBLE")
    assert double.winning_combination == "7/F"
    assert double.dividend_hkd == pytest.approx(27.0)


def test_results_page_without_tables_raises():
    html = '<div class="raceMeeting_select"><span class="f_fl">Race Meeting: 01/01/2024 Sha Tin</span></div>'
    with pytest.raises(ParseError):
        parse_results_page(html)
