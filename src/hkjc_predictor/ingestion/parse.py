"""Parse current HKJC local-results and fixture HTML.

The live results URL is ``/en-us/local/information/localresults``. Older
meetings redirect to ``/archive/localresults`` and use the same tables.
"""

from __future__ import annotations

import re
from datetime import date
from html import unescape
from urllib.parse import parse_qs, urlparse

from selectolax.parser import HTMLParser, Node

from hkjc_predictor.ingestion.models import (
    EmptyPage,
    ParsedDividend,
    ParsedRace,
    ParsedRunner,
)

LOCAL_COURSES = {"ST", "HV"}
COURSE_NAMES = {"ST": "Sha Tin", "HV": "Happy Valley"}

# Approximate length conversions for the English results page.
# ``lbw`` keeps the original token; ``lbw_lengths`` is only a convenience.
LBW_WORDS = {
    "---": 0.0,
    "-": 0.0,
    "DH": 0.0,
    "NOSE": 0.05,
    "NS": 0.05,
    "SH": 0.1,
    "SHD": 0.1,
    "HD": 0.2,
    "HEAD": 0.2,
    "N": 0.3,
    "NK": 0.3,
    "NECK": 0.3,
}

CLASS_LINE_RE = re.compile(
    r"^(?P<race_class>.+?)\s*-\s*(?P<distance>\d+)\s*M"
    r"(?:\s*-\s*\((?P<rating>[^)]*)\))?\s*$",
    re.IGNORECASE,
)
RACE_HEADER_RE = re.compile(r"RACE\s+(\d+)\s*(?:\((\d+)\))?", re.IGNORECASE)
PRIZE_RE = re.compile(r"HK\$\s*([0-9][0-9,]*)", re.IGNORECASE)
HORSE_CODE_RE = re.compile(r"\(([A-Z]\d{3})\)")
FINISH_RE = re.compile(r"^(\d+):(\d{2})\.(\d+)$")
FRACTION_RE = re.compile(r"^(?:(\d+)-)?(\d+)/(\d+)$")
PLACING_RE = re.compile(r"^(\d+)(?:\s*DH)?$", re.IGNORECASE)


class ParseError(ValueError):
    """The page looked like a results page but a required table was unusable."""


def clean(value: str | None) -> str:
    if not value:
        return ""
    text = unescape(value).replace("\xa0", " ")
    return re.sub(r"\s+", " ", text).strip()


def is_abandoned(html: str) -> bool:
    return "declared abandoned" in html.lower()


def is_no_information(html: str) -> bool:
    tree = HTMLParser(html)
    node = tree.css_first("#errorContainer")
    if node is not None and "no information" in clean(node.text()).lower():
        return True
    return "no information." in html.lower() and "errorContainer" in html


def parse_lbw(token: str | None) -> float | None:
    text = clean(token).upper()
    if not text:
        return None
    if text in LBW_WORDS:
        return LBW_WORDS[text]
    fraction = FRACTION_RE.fullmatch(text)
    if fraction:
        whole = int(fraction.group(1) or 0)
        numerator = int(fraction.group(2))
        denominator = int(fraction.group(3))
        if denominator == 0:
            return None
        return whole + numerator / denominator
    if re.fullmatch(r"\d+", text):
        return float(text)
    return None


def parse_finish_seconds(token: str | None) -> float | None:
    text = clean(token)
    match = FINISH_RE.fullmatch(text)
    if not match:
        return None
    minutes = int(match.group(1))
    seconds = int(match.group(2))
    fraction = match.group(3)
    return minutes * 60 + seconds + int(fraction) / (10 ** len(fraction))


def parse_class_line(class_line: str | None) -> tuple[str | None, int | None, str | None]:
    text = clean(class_line)
    if not text:
        return None, None, None
    match = CLASS_LINE_RE.fullmatch(text)
    if not match:
        return text, None, None
    rating = clean(match.group("rating")) or None
    return clean(match.group("race_class")), int(match.group("distance")), rating


def parse_prize(token: str | None) -> int | None:
    match = PRIZE_RE.search(clean(token))
    if not match:
        return None
    return int(match.group(1).replace(",", ""))


def _query_param(href: str | None, key: str) -> str | None:
    if not href:
        return None
    values = parse_qs(urlparse(href).query).get(key)
    if not values:
        return None
    return values[0] or None


def _header_index(cells: list[str]) -> dict[str, int]:
    index: dict[str, int] = {}
    for position, cell in enumerate(cells):
        key = re.sub(r"[^a-z]", "", cell.lower())
        if key and key not in index:
            index[key] = position
    return index


def _cell(cells: list[Node], index: dict[str, int], *keys: str) -> Node | None:
    for key in keys:
        position = index.get(key)
        if position is not None and position < len(cells):
            return cells[position]
    return None


def _parse_int(token: str | None) -> int | None:
    text = clean(token).replace(",", "")
    if re.fullmatch(r"-?\d+", text):
        return int(text)
    return None


def _parse_odds(token: str | None) -> float | None:
    text = clean(token).replace(",", "")
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    return None


def _parse_money(token: str | None) -> float | None:
    text = clean(token).replace(",", "").replace("$", "")
    if re.fullmatch(r"\d+(?:\.\d+)?", text):
        return float(text)
    return None


def _element_children(node: Node, tag: str) -> list[Node]:
    children: list[Node] = []
    child = node.child
    while child is not None:
        if child.tag == tag:
            children.append(child)
        child = child.next
    return children


def _running_positions(cell: Node | None) -> str | None:
    if cell is None:
        return None
    # selectolax css("div") also matches the node itself, so leaf divs are
    # found by walking element children instead.
    leaves = [node for node in cell.css("div") if not _element_children(node, "div")]
    numbers: list[str] = []
    for leaf in leaves:
        for match in re.findall(r"\d+", clean(leaf.text(deep=False) or leaf.text())):
            numbers.append(match)
    if not numbers:
        numbers = re.findall(r"\d+", clean(cell.text()))
    if not numbers:
        return None
    return " ".join(numbers)


def _horse_fields(cell: Node | None) -> tuple[str | None, str | None, str | None]:
    if cell is None:
        return None, None, None
    link = cell.css_first("a")
    href = link.attributes.get("href") if link is not None else None
    horse_id = _query_param(href, "horseid")
    full = clean(cell.text())
    code_match = HORSE_CODE_RE.search(full)
    code = code_match.group(1) if code_match else None
    if link is not None:
        name = clean(link.text()) or None
    else:
        name = HORSE_CODE_RE.sub("", full).strip(" -") or None
    return name, code, horse_id


def _person_fields(cell: Node | None, id_param: str) -> tuple[str | None, str | None]:
    if cell is None:
        return None, None
    link = cell.css_first("a")
    href = link.attributes.get("href") if link is not None else None
    name = clean(link.text()) if link is not None else clean(cell.text())
    return name or None, _query_param(href, id_param)


def _placing(token: str | None) -> tuple[str | None, int | None]:
    text = clean(token)
    if not text:
        return None, None
    match = PLACING_RE.fullmatch(text)
    placing_num = int(match.group(1)) if match else None
    return text, placing_num


def _race_info(tree: HTMLParser) -> dict[str, str | None]:
    table = tree.css_first("div.race_tab table")
    if table is None:
        raise ParseError("results page has no race header table")
    header = clean(table.css_first("thead").text() if table.css_first("thead") else "")
    header_match = RACE_HEADER_RE.search(header)
    if not header_match:
        raise ParseError(f"could not read race number from header {header!r}")

    info: dict[str, str | None] = {
        "race_no": header_match.group(1),
        "season_race_no": header_match.group(2),
        "class_line": None,
        "going": None,
        "race_name": None,
        "course": None,
        "prize": None,
        "sectionals": None,
    }
    for row in table.css("tbody tr"):
        cells = [clean(td.text()) for td in row.css("td")]
        if len(cells) < 2:
            continue
        label = cells[1].rstrip(":").strip().lower()
        value = cells[2] if len(cells) > 2 else ""
        if label == "going":
            info["class_line"] = cells[0] or None
            info["going"] = value or None
        elif label == "course":
            info["race_name"] = cells[0] or None
            info["course"] = value or None
        elif label == "time":
            info["prize"] = cells[0] or None
        elif "sectional" in label:
            parts = [cell for cell in cells[2:] if cell]
            info["sectionals"] = " | ".join(parts) or None
    return info


def _meeting_identity(tree: HTMLParser) -> tuple[date | None, str | None, str | None]:
    span = tree.css_first("div.raceMeeting_select span.f_fl")
    text = clean(span.text() if span is not None else "")
    date_match = re.search(r"(\d{2})/(\d{2})/(\d{4})", text)
    meeting_date = None
    if date_match:
        day, month, year = (int(part) for part in date_match.groups())
        meeting_date = date(year, month, day)
    lowered = text.lower()
    if "happy valley" in lowered:
        return meeting_date, "HV", "Happy Valley"
    if "sha tin" in lowered:
        return meeting_date, "ST", "Sha Tin"
    return meeting_date, None, None


def _linked_race_numbers(tree: HTMLParser, racecourse: str, current: int) -> tuple[int, ...]:
    numbers = {current}
    for anchor in tree.css("a"):
        href = anchor.attributes.get("href") or ""
        if "localresults" not in href.lower() or "raceno=" not in href.lower():
            continue
        query = parse_qs(urlparse(href).query)
        course_values = query.get("Racecourse") or query.get("racecourse") or []
        race_values = query.get("RaceNo") or query.get("raceno") or []
        if not course_values or not race_values:
            continue
        if course_values[0].upper() != racecourse:
            continue
        if race_values[0].isdigit():
            numbers.add(int(race_values[0]))
    return tuple(sorted(numbers))


def _parse_abandoned_page(
    html: str,
    *,
    fallback_date: date | None,
    fallback_course: str | None,
    source_url: str | None,
) -> ParsedRace:
    """An abandoned race has no runner table. Refund dividends are still published."""
    tree = HTMLParser(html)
    meeting_date, racecourse, venue_name = _meeting_identity(tree)
    meeting_date = meeting_date or fallback_date
    racecourse = racecourse or (fallback_course.upper() if fallback_course else None)
    if meeting_date is None or racecourse not in LOCAL_COURSES:
        raise ParseError("abandoned results page is missing the local meeting banner")
    race_no = _selected_race_no(tree)
    if race_no is None:
        raise ParseError("abandoned results page has no selected race tab")
    return ParsedRace(
        meeting_date=meeting_date,
        racecourse=racecourse,
        venue_name=venue_name or COURSE_NAMES.get(racecourse),
        race_no=race_no,
        season_race_no=None,
        race_class=None,
        class_line=None,
        distance_m=None,
        rating_band=None,
        going=None,
        course=None,
        race_name=None,
        prize_hkd=None,
        sectionals=None,
        runners=(),
        dividends=_dividends(tree),
        race_numbers=_linked_race_numbers(tree, racecourse, race_no),
        source_url=source_url,
        abandoned=True,
    )


def _results_table(tree: HTMLParser) -> Node:
    for table in tree.css("table.draggable"):
        header = clean(table.text())
        if "Horse" in header and "Jockey" in header:
            return table
    raise ParseError("results page has no runner table")


def _incidents(tree: HTMLParser) -> dict[int, str]:
    container = tree.css_first("div.race_incident_report")
    if container is None:
        return {}
    table = container.css_first("table")
    if table is None:
        return {}
    header_row = table.css_first("thead tr")
    headers = [clean(td.text()) for td in header_row.css("td")] if header_row else []
    index = _header_index(headers)
    incidents: dict[int, str] = {}
    for row in table.css("tbody tr"):
        cells = row.css("td")
        if not cells:
            continue
        texts = [clean(td.text()) for td in cells]
        horse_cell = _cell(cells, index, "horseno")
        incident_cell = _cell(cells, index, "incident")
        horse_no = _parse_int(clean(horse_cell.text()) if horse_cell else (texts[1] if len(texts) > 1 else ""))
        incident = clean(incident_cell.text()) if incident_cell is not None else (texts[-1] if texts else "")
        if horse_no is not None and incident:
            incidents[horse_no] = incident
    return incidents


def _runners(table: Node, incidents: dict[int, str]) -> tuple[ParsedRunner, ...]:
    header_row = table.css_first("thead tr")
    if header_row is None:
        raise ParseError("runner table has no header")
    index = _header_index([clean(td.text()) for td in header_row.css("td")])
    runners: list[ParsedRunner] = []
    seen: set[int] = set()
    for row in table.css("tbody tr"):
        cells = row.css("td")
        if not cells:
            continue
        horse_cell_no = _cell(cells, index, "horseno")
        horse_no = _parse_int(clean(horse_cell_no.text()) if horse_cell_no else "")
        if horse_no is None or horse_no in seen:
            continue
        seen.add(horse_no)
        placing, placing_num = _placing(clean(_cell(cells, index, "pla").text()) if _cell(cells, index, "pla") else "")
        horse_name, horse_code, horse_id = _horse_fields(_cell(cells, index, "horse"))
        jockey, jockey_id = _person_fields(_cell(cells, index, "jockey"), "jockeyid")
        trainer, trainer_id = _person_fields(_cell(cells, index, "trainer"), "trainerid")
        actual = _parse_int(clean(_cell(cells, index, "actwt").text()) if _cell(cells, index, "actwt") else "")
        declared = _parse_int(
            clean(_cell(cells, index, "declarhorsewt").text()) if _cell(cells, index, "declarhorsewt") else ""
        )
        draw = _parse_int(clean(_cell(cells, index, "dr").text()) if _cell(cells, index, "dr") else "")
        lbw_cell = _cell(cells, index, "lbw")
        lbw = clean(lbw_cell.text()) if lbw_cell is not None else ""
        finish_cell = _cell(cells, index, "finishtime")
        finish_time = clean(finish_cell.text()) if finish_cell is not None else ""
        odds_cell = _cell(cells, index, "winodds")
        win_odds = _parse_odds(clean(odds_cell.text()) if odds_cell is not None else "")
        runners.append(
            ParsedRunner(
                horse_no=horse_no,
                placing=placing,
                placing_num=placing_num,
                horse_name=horse_name,
                horse_code=horse_code,
                horse_id=horse_id,
                jockey=jockey,
                jockey_id=jockey_id,
                trainer=trainer,
                trainer_id=trainer_id,
                actual_weight=actual,
                declared_horse_weight=declared,
                draw=draw,
                lbw=lbw or None,
                lbw_lengths=parse_lbw(lbw),
                running_positions=_running_positions(_cell(cells, index, "runningposition")),
                finish_time=finish_time or None,
                finish_time_seconds=parse_finish_seconds(finish_time),
                win_odds=win_odds,
                incident=incidents.get(horse_no),
            )
        )
    return tuple(runners)


def _dividends(tree: HTMLParser) -> tuple[ParsedDividend, ...]:
    container = tree.css_first("div.dividend_tab")
    if container is None:
        return ()
    table = container.css_first("table")
    if table is None:
        return ()
    dividends: list[ParsedDividend] = []
    current_pool: str | None = None
    order = 0
    for row in table.css("tbody tr"):
        tds = row.css("td")
        texts = [clean(td.text()) for td in tds]
        if not any(texts):
            continue
        if texts[0].lower() in {"pool", "dividend"}:
            continue
        if len(tds) >= 3:
            pool = texts[0] or current_pool
            combination = texts[1] if len(texts) > 1 else ""
            dividend_text = texts[2] if len(texts) > 2 else ""
            if texts[0]:
                current_pool = texts[0]
        elif len(tds) == 2 and current_pool:
            pool = current_pool
            combination = texts[0]
            dividend_text = texts[1]
        else:
            continue
        if not pool or not combination:
            continue
        order += 1
        dividends.append(
            ParsedDividend(
                row_order=order,
                pool=pool,
                winning_combination=combination,
                dividend_hkd=_parse_money(dividend_text),
                dividend_text=dividend_text or None,
            )
        )
    return tuple(dividends)


def _selected_race_no(tree: HTMLParser) -> int | None:
    """The on-screen race tab uses ``racecard_rt_N_o.gif``."""
    for image in tree.css("img"):
        src = image.attributes.get("src") or ""
        match = re.search(r"racecard_rt_(\d+)_o\.gif", src)
        if match:
            return int(match.group(1))
    return None


def parse_results_page(
    html: str,
    *,
    fallback_date: date | None = None,
    fallback_course: str | None = None,
    source_url: str | None = None,
) -> ParsedRace | EmptyPage:
    """Parse one local results page.

    ``fallback_date`` / ``fallback_course`` are used only when the meeting
    banner cannot be read. The banner is preferred because a wrong racecourse
    query returns an empty page rather than another venue's results.
    """
    if is_no_information(html):
        return EmptyPage("No information.")
    if is_abandoned(html):
        return _parse_abandoned_page(
            html,
            fallback_date=fallback_date,
            fallback_course=fallback_course,
            source_url=source_url,
        )
    tree = HTMLParser(html)
    meeting_date, racecourse, venue_name = _meeting_identity(tree)
    meeting_date = meeting_date or fallback_date
    racecourse = racecourse or (fallback_course.upper() if fallback_course else None)
    if meeting_date is None or racecourse not in LOCAL_COURSES:
        raise ParseError("results page is missing the local meeting banner")
    venue_name = venue_name or COURSE_NAMES.get(racecourse)

    info = _race_info(tree)
    race_no = int(info["race_no"] or 0)
    if race_no <= 0:
        raise ParseError("race number was not positive")
    race_class, distance_m, rating_band = parse_class_line(info["class_line"])
    runners = _runners(_results_table(tree), _incidents(tree))
    if not runners:
        raise ParseError(f"race {race_no} parsed no runners")
    season = int(info["season_race_no"]) if info["season_race_no"] else None
    return ParsedRace(
        meeting_date=meeting_date,
        racecourse=racecourse,
        venue_name=venue_name,
        race_no=race_no,
        season_race_no=season,
        race_class=race_class,
        class_line=info["class_line"],
        distance_m=distance_m,
        rating_band=rating_band,
        going=info["going"],
        course=info["course"],
        race_name=info["race_name"],
        prize_hkd=parse_prize(info["prize"]),
        sectionals=info["sectionals"],
        runners=runners,
        dividends=_dividends(tree),
        race_numbers=_linked_race_numbers(tree, racecourse, race_no),
        source_url=source_url,
    )
