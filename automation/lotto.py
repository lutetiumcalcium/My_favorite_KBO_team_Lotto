"""KBO 등록 명단으로 팀별 주간 번호를 생성하고 저장한다."""

from __future__ import annotations

import json
import logging
import random
import re
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Iterator
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup, Tag
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry


ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = ROOT.parent
DATABASE_PATH = ROOT / "lotto_history.db"
TEAM_COLORS = json.loads((PROJECT_ROOT / "kbo_team_colors.json").read_text(encoding="utf-8"))
PERMANENT_NUMBERS = json.loads((PROJECT_ROOT / "kbo_permanant_numbers.json").read_text(encoding="utf-8"))
TEAMS = tuple(team for team in TEAM_COLORS if team != "KBO")
MODES = tuple(str(number) for number in range(7)) + ("all",)
KST = ZoneInfo("Asia/Seoul")

FIRST_URL = "https://www.koreabaseball.com/Player/RegisterAll.aspx"
FUTURES_URL = "https://www.koreabaseball.com/Futures/Player/Register.aspx"
RECORD_ID = "cphContents_cphContents_cphContents_udpRecord"
DATE_ID = "cphContents_cphContents_cphContents_lblGameDate"
POSTBACK_PREFIX = "ctl00$ctl00$ctl00$cphContents$cphContents$cphContents$"
NUMBER_PATTERN = re.compile(r"\((\d+)\)")
TEAM_CODE_PATTERN = re.compile(r"fnSearchChange\('([A-Z]+)'\)")
DATE_PATTERN = re.compile(r"(\d{4})\.(\d{2})\.(\d{2})")
VALID_NUMBERS = set(range(1, 46))
DAY_NAMES = ("월", "화", "수", "목", "금", "토", "일")
PLAYER_POSITIONS = {"투수", "포수", "내야수", "외야수"}


class KBODataError(RuntimeError):
    """등록 페이지에서 필요한 날짜, 팀 또는 선수 번호를 읽지 못했다."""


def valid_jersey_number(raw: str) -> int | None:
    """0으로 시작하는 임시 번호와 로또 범위 밖 번호를 제외한다."""
    raw = raw.strip()
    if not re.fullmatch(r"[1-9]\d*", raw):
        return None
    number = int(raw)
    return number if number in VALID_NUMBERS else None


def page_date(soup: BeautifulSoup) -> date:
    label = soup.find(id=DATE_ID)
    match = DATE_PATTERN.search(label.get_text(" ", strip=True)) if label else None
    if not match:
        raise KBODataError("KBO 기준 날짜를 읽지 못했습니다.")
    return date(*(int(part) for part in match.groups()))


def first_roster_numbers(soup: BeautifulSoup, team: str) -> set[int]:
    return set(first_roster_players(soup, team))


def first_roster_players(soup: BeautifulSoup, team: str) -> dict[int, str]:
    area = soup.find(id=RECORD_ID)
    if not isinstance(area, Tag):
        raise KBODataError("1군 등록 명단을 찾지 못했습니다.")
    for row in area.select("table tbody tr"):
        header = row.find("th", recursive=False)
        if header is None or next(header.stripped_strings, "") != team:
            continue
        cells = row.find_all("td", recursive=False)
        if len(cells) < 6:
            raise KBODataError(f"{team} 1군 등록 명단의 열 수가 예상과 다릅니다.")
        players: dict[int, str] = {}
        # 감독·코치 열을 건너뛰고 투수·포수·내야수·외야수만 읽는다.
        for cell in cells[2:6]:
            for item in cell.select("li"):
                text = item.get_text(" ", strip=True)
                match = NUMBER_PATTERN.search(text)
                number = valid_jersey_number(match.group(1)) if match else None
                if number is not None:
                    name = text[:match.start()].strip()
                    if name:
                        players[number] = name
        if not players:
            raise KBODataError(f"{team}의 1~45 등번호를 찾지 못했습니다.")
        return players
    raise KBODataError(f"1군 등록 명단에서 {team} 팀을 찾지 못했습니다.")


def futures_team_codes(soup: BeautifulSoup) -> dict[str, str]:
    codes: dict[str, str] = {}
    for anchor in soup.find_all("a", href=lambda href: href and "fnSearchChange" in href):
        span = anchor.find("span")
        match = TEAM_CODE_PATTERN.search(anchor.get("href", ""))
        if span and match:
            codes[span.get_text(" ", strip=True)] = match.group(1)
    return codes


def futures_roster_numbers(soup: BeautifulSoup, team: str) -> set[int]:
    return set(futures_roster_players(soup, team))


def futures_roster_players(soup: BeautifulSoup, team: str) -> dict[int, str]:
    futures_name = "고양" if team == "키움" else team
    area = soup.find(id=RECORD_ID)
    if not isinstance(area, Tag):
        raise KBODataError("퓨처스 등록 명단을 찾지 못했습니다.")
    heading = area.find("h6")
    if not heading or futures_name not in heading.get_text(" ", strip=True):
        raise KBODataError(f"퓨처스 등록 명단에서 {futures_name} 팀을 찾지 못했습니다.")
    players: dict[int, str] = {}
    for table in area.select("table.tbl"):
        headers = table.select("thead th")
        if len(headers) < 2 or headers[1].get_text(" ", strip=True) not in PLAYER_POSITIONS:
            continue
        for row in table.select("tbody tr"):
            cells = row.find_all("td", recursive=False)
            number = valid_jersey_number(cells[0].get_text(" ", strip=True)) if cells else None
            name = cells[1].get_text(" ", strip=True) if len(cells) > 1 else ""
            if number is not None and name:
                players[number] = name
    if not players:
        raise KBODataError(f"{futures_name} 퓨처스 선수의 1~45 등번호를 찾지 못했습니다.")
    return players


def build_session() -> requests.Session:
    retry = Retry(
        total=2,
        connect=2,
        read=2,
        backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET", "POST"}),
    )
    session = requests.Session()
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers.update({
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/131.0 Safari/537.36",
        "Accept-Language": "ko-KR,ko;q=0.9,en;q=0.7",
    })
    return session


class RosterClient:
    def __init__(self, timeout: float = 20.0) -> None:
        self.session = build_session()
        self.timeout = timeout
        self.first_soup: BeautifulSoup | None = None
        self.future_soup: BeautifulSoup | None = None
        self.first_cache: dict[date, BeautifulSoup] = {}
        self.future_cache: dict[tuple[date, str], BeautifulSoup] = {}

    def __enter__(self) -> RosterClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.session.close()

    def _get(self, url: str) -> BeautifulSoup:
        response = self.session.get(url, timeout=self.timeout)
        response.raise_for_status()
        return BeautifulSoup(response.content, "html.parser")

    def _post(self, url: str, soup: BeautifulSoup, event_target: str, values: dict[str, str]) -> BeautifulSoup:
        fields = {
            item["name"]: item.get("value", "")
            for item in soup.select("input[type='hidden'][name]")
        }
        fields.update({"__EVENTTARGET": event_target, "__EVENTARGUMENT": ""})
        fields.update(values)
        response = self.session.post(url, data=fields, timeout=self.timeout)
        response.raise_for_status()
        return BeautifulSoup(response.content, "html.parser")

    def current_date(self) -> date:
        if self.first_soup is None:
            self.first_soup = self._get(FIRST_URL)
            self.first_cache[page_date(self.first_soup)] = self.first_soup
        return page_date(self.first_soup)

    def first_numbers(self, target_date: date, team: str) -> set[int]:
        return set(self.first_players(target_date, team))

    def first_players(self, target_date: date, team: str) -> dict[int, str]:
        self.current_date()
        if target_date not in self.first_cache:
            assert self.first_soup is not None
            self.first_soup = self._post(
                FIRST_URL,
                self.first_soup,
                POSTBACK_PREFIX + "btnSearch",
                {POSTBACK_PREFIX + "hfSearchDate": target_date.strftime("%Y%m%d")},
            )
            if page_date(self.first_soup) != target_date:
                raise KBODataError(f"요청한 1군 날짜 {target_date}와 응답 날짜가 다릅니다.")
            self.first_cache[target_date] = self.first_soup
        return first_roster_players(self.first_cache[target_date], team)

    def futures_numbers(self, target_date: date, team: str) -> set[int]:
        return set(self.futures_players(target_date, team))

    def futures_players(self, target_date: date, team: str) -> dict[int, str]:
        key = (target_date, team)
        if key in self.future_cache:
            return futures_roster_players(self.future_cache[key], team)
        if self.future_soup is None:
            self.future_soup = self._get(FUTURES_URL)
        futures_name = "고양" if team == "키움" else team
        code = futures_team_codes(self.future_soup).get(futures_name)
        if not code:
            raise KBODataError(f"퓨처스 팀 목록에서 {futures_name}을 찾지 못했습니다.")
        current_code = self.future_soup.find(id="cphContents_cphContents_cphContents_hfSearchTeam")
        if page_date(self.future_soup) != target_date or not current_code or current_code.get("value") != code:
            self.future_soup = self._post(
                FUTURES_URL,
                self.future_soup,
                POSTBACK_PREFIX + "btnCalendarSelect",
                {
                    POSTBACK_PREFIX + "hfSearchTeam": code,
                    POSTBACK_PREFIX + "hfSearchDate": target_date.strftime("%Y%m%d"),
                },
            )
        if page_date(self.future_soup) != target_date:
            raise KBODataError(f"요청한 퓨처스 날짜 {target_date}와 응답 날짜가 다릅니다.")
        players = futures_roster_players(self.future_soup, team)
        self.future_cache[key] = self.future_soup
        return players


def validate_selection(team: str, mode: str) -> None:
    if team not in TEAMS:
        raise ValueError("팀을 선택해 주세요.")
    if mode not in MODES:
        raise ValueError("1군 선수 수는 0명~6명 또는 전체로 선택해 주세요.")


def first_pool_for_selection(team: str, registered: set[int], include_permanent: bool) -> set[int]:
    """예를 선택한 경우 영구결번을 1군 번호 후보에 더한다."""
    pool = set(registered) & VALID_NUMBERS
    if include_permanent:
        pool.update(set(permanent_player_map(team)) & VALID_NUMBERS)
    return pool


def permanent_player_map(team: str) -> dict[int, str]:
    """영구결번 설정을 등번호와 선수 이름의 사전으로 변환한다."""
    players: dict[int, str] = {}
    for item in PERMANENT_NUMBERS.get(team, []):
        if isinstance(item, int):
            players[item] = "영구결번"
            continue
        if not isinstance(item, dict):
            continue
        number, name = item.get("number"), item.get("name")
        if isinstance(number, int) and isinstance(name, str) and name.strip():
            players[number] = name.strip()
    return players


def storage_mode(mode: str, include_permanent: bool) -> str:
    """기존 DB의 '아니오' 기록을 유지하며 예/아니오 결과를 분리한다."""
    return f"{mode}|permanent" if include_permanent else mode


def draw_numbers(
    first_numbers: set[int],
    futures_numbers: set[int],
    mode: str,
    rng: random.Random | random.SystemRandom | None = None,
) -> list[dict[str, int | str]]:
    generator = rng or random.SystemRandom()
    first = set(first_numbers) & VALID_NUMBERS
    futures = set(futures_numbers) & VALID_NUMBERS
    if mode == "all":
        pool = sorted(first | futures)
        if len(pool) < 6:
            raise ValueError("1군·퓨처스 등록 번호가 6개보다 적습니다.")
        selected = generator.sample(pool, 6)
    else:
        if mode not in {str(number) for number in range(7)}:
            raise ValueError("올바른 1군 선수 수가 아닙니다.")
        first_count = int(mode)
        other = sorted(VALID_NUMBERS - first)
        if len(first) < first_count or len(other) < 6 - first_count:
            raise ValueError("선택한 1군 선수 수에 필요한 등번호가 부족합니다.")
        selected = generator.sample(sorted(first), first_count) + generator.sample(other, 6 - first_count)
    return [
        {"number": number, "source": "first" if number in first else "other"}
        for number in sorted(selected)
    ]


def add_player_names(
    numbers: list[dict[str, int | str]],
    first_players: dict[int, str],
    futures_players: dict[int, str],
    team: str,
    include_permanent: bool,
) -> list[dict[str, int | str]]:
    """번호마다 현재 명단의 선수 이름 또는 번호 상태를 붙인다."""
    permanent = permanent_player_map(team) if include_permanent else {}
    named: list[dict[str, int | str]] = []
    for item in numbers:
        number = int(item["number"])
        if number in first_players:
            name = first_players[number]
        elif number in permanent:
            name = permanent[number]
        elif number in futures_players:
            name = futures_players[number]
        else:
            name = "1군 미등록"
        named.append({**item, "name": name})
    return named


def draw_has_names(row: sqlite3.Row) -> bool:
    return all(isinstance(item.get("name"), str) and item["name"] for item in json.loads(row["numbers_json"]))


def update_draw_details(
    path: Path,
    draw_date: date,
    team: str,
    mode: str,
    numbers: list[dict[str, int | str]],
    first_pool: set[int],
    futures_pool: set[int],
) -> None:
    with database_connection(path) as connection:
        connection.execute(
            """UPDATE draws
               SET numbers_json = ?, first_pool_json = ?, futures_pool_json = ?, updated_at = ?
               WHERE draw_date = ? AND team = ? AND mode = ?""",
            (
                json.dumps(numbers), json.dumps(sorted(first_pool)), json.dumps(sorted(futures_pool)),
                datetime.now(KST).isoformat(timespec="seconds"), draw_date.isoformat(), team, mode,
            ),
        )


def week_dates(reference: date) -> list[date]:
    sunday = reference - timedelta(days=(reference.weekday() + 1) % 7)
    return [sunday + timedelta(days=offset) for offset in range(7) if offset != 1]


def kst_today() -> date:
    return datetime.now(KST).date()


@contextmanager
def database_connection(path: Path) -> Iterator[sqlite3.Connection]:
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path, timeout=20)
    connection.row_factory = sqlite3.Row
    try:
        with connection:
            connection.execute("""
                CREATE TABLE IF NOT EXISTS draws (
                    draw_date TEXT NOT NULL,
                    team TEXT NOT NULL,
                    mode TEXT NOT NULL,
                    numbers_json TEXT NOT NULL,
                    first_pool_json TEXT NOT NULL,
                    futures_pool_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    PRIMARY KEY (draw_date, team, mode)
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS rollover_state (
                    name TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                )
            """)
            connection.execute("""
                CREATE TABLE IF NOT EXISTS rollover_days (
                    draw_date TEXT PRIMARY KEY,
                    completed_at TEXT NOT NULL
                )
            """)
            yield connection
    finally:
        connection.close()


def stored_draw(path: Path, draw_date: date, team: str, mode: str) -> sqlite3.Row | None:
    with database_connection(path) as connection:
        return connection.execute(
            "SELECT * FROM draws WHERE draw_date = ? AND team = ? AND mode = ?",
            (draw_date.isoformat(), team, mode),
        ).fetchone()


def save_draw(
    path: Path,
    draw_date: date,
    team: str,
    mode: str,
    numbers: list[dict[str, int | str]],
    first_pool: set[int],
    futures_pool: set[int],
) -> None:
    stamp = datetime.now().isoformat(timespec="seconds")
    with database_connection(path) as connection:
        connection.execute(
            """INSERT OR IGNORE INTO draws
               (draw_date, team, mode, numbers_json, first_pool_json, futures_pool_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                draw_date.isoformat(), team, mode, json.dumps(numbers),
                json.dumps(sorted(first_pool)), json.dumps(sorted(futures_pool)), stamp, stamp,
            ),
        )


def freeze_day(day: date, client: RosterClient, path: Path = DATABASE_PATH) -> bool:
    """지난 날짜의 모든 팀·모드·영구결번 조합을 생성하고 기존 번호를 보존한다."""
    if day.weekday() == 0:
        return True
    expected = {(team, storage_mode(mode, permanent)) for team in TEAMS for permanent in (False, True) for mode in MODES}
    with database_connection(path) as connection:
        existing = {
            (row["team"], row["mode"])
            for row in connection.execute("SELECT team, mode FROM draws WHERE draw_date = ?", (day.isoformat(),))
        }
    if expected <= existing:
        return True

    stamp = datetime.now(KST).isoformat(timespec="seconds")
    pending: list[tuple[str, str, str, str, str, str, str, str]] = []
    for team in TEAMS:
        missing = {(mode, permanent) for permanent in (False, True) for mode in MODES
                   if (team, storage_mode(mode, permanent)) not in existing}
        if not missing:
            continue
        try:
            first_players = client.first_players(day, team)
        except (requests.RequestException, KBODataError) as error:
            logging.warning("%s %s 1군 명단 수집 실패: %s", day, team, error)
            continue
        futures_players: dict[int, str] | None = {}
        if any(mode == "all" for mode, _ in missing):
            try:
                futures_players = client.futures_players(day, team)
            except (requests.RequestException, KBODataError) as error:
                logging.warning("%s %s 퓨처스 명단 수집 실패: %s", day, team, error)
                futures_players = None
        for permanent in (False, True):
            first = first_pool_for_selection(team, set(first_players), permanent)
            futures = set(futures_players or {})
            for mode in MODES:
                if (mode, permanent) not in missing or (mode == "all" and futures_players is None):
                    continue
                try:
                    numbers = add_player_names(
                        draw_numbers(first, futures, mode), first_players, futures_players or {}, team, permanent
                    )
                except ValueError as error:
                    logging.warning("%s %s %s 추첨 실패: %s", day, team, mode, error)
                    continue
                pending.append((
                    day.isoformat(), team, storage_mode(mode, permanent), json.dumps(numbers),
                    json.dumps(sorted(first)), json.dumps(sorted(futures)), stamp, stamp,
                ))
    with database_connection(path) as connection:
        connection.executemany(
            """INSERT OR IGNORE INTO draws
               (draw_date, team, mode, numbers_json, first_pool_json, futures_pool_json, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            pending,
        )
        saved = {
            (row["team"], row["mode"])
            for row in connection.execute("SELECT team, mode FROM draws WHERE draw_date = ?", (day.isoformat(),))
        }
    return expected <= saved


def freeze_pending_days(
    path: Path = DATABASE_PATH, *, today: date | None = None, client_factory=RosterClient
) -> bool:
    """자정에 서버가 꺼져 있었더라도 아직 완성되지 않은 날짜를 채운다."""
    today = today or kst_today()
    yesterday = today - timedelta(days=1)
    with database_connection(path) as connection:
        row = connection.execute("SELECT value FROM rollover_state WHERE name = 'start_date'").fetchone()
        if row is None:
            start = week_dates(yesterday)[0]
            connection.execute(
                "INSERT OR IGNORE INTO rollover_state (name, value) VALUES ('start_date', ?)",
                (start.isoformat(),),
            )
        else:
            start = date.fromisoformat(row["value"])
        completed = {
            row["draw_date"]
            for row in connection.execute("SELECT draw_date FROM rollover_days")
        }
    pending = [start + timedelta(days=offset) for offset in range((yesterday - start).days + 1)
               if (start + timedelta(days=offset)).isoformat() not in completed]
    if not pending:
        return True

    all_complete = True
    with client_factory() as client:
        for day in pending:
            if day.weekday() == 0 or freeze_day(day, client, path):
                with database_connection(path) as connection:
                    connection.execute(
                        "INSERT OR IGNORE INTO rollover_days (draw_date, completed_at) VALUES (?, ?)",
                        (day.isoformat(), datetime.now(KST).isoformat(timespec="seconds")),
                    )
                logging.info("%s 날짜의 모든 추첨 조건을 고정했습니다.", day)
            else:
                all_complete = False
    return all_complete


def serialize_day(day: date, reference: date, row: sqlite3.Row | None) -> dict:
    return {
        "date": day.isoformat(),
        "label": f"{day:%m.%d} ({DAY_NAMES[day.weekday()]})",
        "state": "future" if day > reference else "locked" if day < reference else "today",
        "numbers": json.loads(row["numbers_json"]) if row else None,
    }


def load_week(
    team: str, mode: str, path: Path = DATABASE_PATH, *, include_permanent: bool = False
) -> dict:
    validate_selection(team, mode)
    if not isinstance(include_permanent, bool):
        raise ValueError("영구결번 포함 여부를 예 또는 아니오로 선택해 주세요.")
    key = storage_mode(mode, include_permanent)
    today = kst_today()
    with RosterClient() as client:
        reference = client.current_date()
        days = week_dates(reference)
        for day in days:
            if day > min(reference, today):
                continue
            row = stored_draw(path, day, team, key)
            if row is not None and draw_has_names(row):
                continue
            first_players = client.first_players(day, team)
            futures_players = client.futures_players(day, team) if mode == "all" else {}
            first = first_pool_for_selection(team, set(first_players), include_permanent)
            futures = set(futures_players)
            if row is None:
                numbers = draw_numbers(first, futures, mode)
                numbers = add_player_names(numbers, first_players, futures_players, team, include_permanent)
                save_draw(path, day, team, key, numbers, first, futures)
            else:
                numbers = add_player_names(
                    json.loads(row["numbers_json"]), first_players, futures_players, team, include_permanent
                )
                update_draw_details(path, day, team, key, numbers, first, futures)
    display_today = kst_today()
    return {
        "team": team,
        "mode": mode,
        "includePermanent": include_permanent,
        "referenceDate": reference.isoformat(),
        "weekStart": days[0].isoformat(),
        "weekEnd": days[-1].isoformat(),
        "days": [serialize_day(day, display_today, stored_draw(path, day, team, key)) for day in days],
    }


def redraw_today(
    team: str, mode: str, path: Path = DATABASE_PATH, *, include_permanent: bool = False
) -> dict:
    validate_selection(team, mode)
    if not isinstance(include_permanent, bool):
        raise ValueError("영구결번 포함 여부를 예 또는 아니오로 선택해 주세요.")
    key = storage_mode(mode, include_permanent)
    with RosterClient() as client:
        reference = client.current_date()
        if reference == kst_today():
            first_players = client.first_players(reference, team)
            futures_players = client.futures_players(reference, team) if mode == "all" else {}
    if reference != kst_today():
        raise ValueError("오늘 번호만 다시 뽑을 수 있습니다. KBO 기준 날짜를 확인해 주세요.")
    if reference not in week_dates(reference):
        raise ValueError("월요일에는 번호를 다시 뽑을 수 없습니다.")
    row = stored_draw(path, reference, team, key)
    if row is None:
        raise ValueError("오늘 번호를 먼저 불러와 주세요.")
    first = first_pool_for_selection(team, set(first_players), include_permanent)
    futures = set(futures_players)
    numbers = add_player_names(
        draw_numbers(first, futures, mode), first_players, futures_players, team, include_permanent
    )
    with database_connection(path) as connection:
        if reference != kst_today():
            raise ValueError("날짜가 바뀌어 지난 번호가 고정되었습니다. 페이지를 새로고침해 주세요.")
        connection.execute(
            """UPDATE draws SET numbers_json = ?, first_pool_json = ?, futures_pool_json = ?, updated_at = ?
               WHERE draw_date = ? AND team = ? AND mode = ?""",
            (
                json.dumps(numbers), json.dumps(sorted(first)), json.dumps(sorted(futures)),
                datetime.now(KST).isoformat(timespec="seconds"), reference.isoformat(), team, key,
            ),
        )
    return serialize_day(reference, reference, stored_draw(path, reference, team, key))
