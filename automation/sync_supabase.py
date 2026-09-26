"""KBO 명단을 동기화하고 전날 및 토요일 20시 공통 번호를 고정한다."""

from __future__ import annotations

import json
import logging
import os
from datetime import date, datetime, timedelta
from typing import Any

import requests

from lotto import (
    KBODataError,
    KST,
    MODES,
    TEAMS,
    RosterClient,
    add_player_names,
    draw_numbers,
    first_pool_for_selection,
    kst_today,
    page_date,
    FUTURES_URL,
)


SUPABASE_URL = os.getenv("SUPABASE_URL", "https://ziejjlfyrrrmwrxjxzaz.supabase.co").rstrip("/")
SUPABASE_SECRET_KEY = os.getenv("SUPABASE_SECRET_KEY", "")


def api_headers(prefer: str) -> dict[str, str]:
    if not SUPABASE_SECRET_KEY:
        raise RuntimeError("SUPABASE_SECRET_KEY가 설정되지 않았습니다.")
    headers = {
        "apikey": SUPABASE_SECRET_KEY,
        "Content-Type": "application/json",
        "Prefer": prefer,
    }
    # 기존 service_role JWT도 사용할 수 있다. sb_secret_ 키는 Bearer 토큰으로 보내지 않는다.
    if SUPABASE_SECRET_KEY.startswith("eyJ"):
        headers["Authorization"] = f"Bearer {SUPABASE_SECRET_KEY}"
    return headers


def postgrest_upsert(table: str, rows: list[dict[str, Any]], conflict: str, *, overwrite: bool) -> None:
    if not rows:
        return
    resolution = "merge-duplicates" if overwrite else "ignore-duplicates"
    response = requests.post(
        f"{SUPABASE_URL}/rest/v1/{table}",
        params={"on_conflict": conflict},
        headers=api_headers(f"resolution={resolution},return=minimal"),
        data=json.dumps(rows, ensure_ascii=False).encode("utf-8"),
        timeout=30,
    )
    if not response.ok:
        raise RuntimeError(f"Supabase {table} 저장 실패({response.status_code}): {response.text[:500]}")


def sync_current_rosters(client: RosterClient) -> None:
    first_date = client.current_date()
    if client.future_soup is None:
        client.future_soup = client._get(FUTURES_URL)
    futures_date = page_date(client.future_soup)
    rows: list[dict[str, Any]] = []
    failures: list[str] = []

    for team in TEAMS:
        try:
            rows.append({
                "roster_date": first_date.isoformat(),
                "team": team,
                "first_players": client.first_players(first_date, team),
                "futures_players": client.futures_players(futures_date, team),
                "futures_roster_date": futures_date.isoformat(),
                "synced_at": datetime.now(KST).isoformat(timespec="seconds"),
            })
        except (requests.RequestException, KBODataError) as error:
            failures.append(f"{team}: {error}")
    postgrest_upsert("roster_snapshots", rows, "roster_date,team", overwrite=True)
    logging.info("현재 명단 %s개 팀 동기화", len(rows))
    if failures:
        raise RuntimeError("현재 명단 일부 수집 실패: " + "; ".join(failures))


def freeze_date(client: RosterClient, target: date) -> None:
    if target.weekday() == 0:  # 월요일은 로또 표시 대상에서 제외한다.
        logging.info("%s은 월요일이라 고정 결과를 만들지 않습니다.", target)
        return

    rows: list[dict[str, Any]] = []
    failures: list[str] = []
    for team in TEAMS:
        try:
            first_players = client.first_players(target, team)
        except (requests.RequestException, KBODataError) as error:
            failures.append(f"{team} 1군: {error}")
            continue
        try:
            futures_players = client.futures_players(target, team)
        except (requests.RequestException, KBODataError) as error:
            logging.warning("%s %s 퓨처스 명단 수집 실패: %s", target, team, error)
            futures_players = {}

        for include_permanent in (False, True):
            first_pool = first_pool_for_selection(team, set(first_players), include_permanent)
            for mode in MODES:
                try:
                    numbers = add_player_names(
                        draw_numbers(first_pool, set(futures_players), mode),
                        first_players,
                        futures_players,
                        team,
                        include_permanent,
                    )
                except ValueError as error:
                    failures.append(f"{team} {mode} {include_permanent}: {error}")
                    continue
                rows.append({
                    "draw_date": target.isoformat(),
                    "team": team,
                    "mode": mode,
                    "include_permanent": include_permanent,
                    "numbers": numbers,
                    "roster_date": target.isoformat(),
                })

    # 이미 저장된 조합은 덮어쓰지 않아 매일 번호가 고정된다.
    postgrest_upsert(
        "daily_results",
        rows,
        "draw_date,team,mode,include_permanent",
        overwrite=False,
    )
    logging.info("%s 고정 결과 후보 %s개 처리", target, len(rows))
    if failures:
        raise RuntimeError(f"{target} 결과 일부 생성 실패: " + "; ".join(failures))


def freeze_yesterday(client: RosterClient) -> None:
    freeze_date(client, kst_today() - timedelta(days=1))


def freeze_saturday_at_cutoff(client: RosterClient) -> None:
    now = datetime.now(KST)
    if now.weekday() != 5 or now.hour < 20:
        logging.info("토요일 20시 고정 시각이 아니라 당일 결과 생성을 건너뜁니다.")
        return
    freeze_date(client, now.date())


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    failures: list[str] = []
    with RosterClient(timeout=30) as client:
        tasks = (
            ("현재 명단", sync_current_rosters),
            ("전날 결과", freeze_yesterday),
            ("토요일 20시 결과", freeze_saturday_at_cutoff),
        )
        for label, task in tasks:
            try:
                task(client)
            except Exception as error:  # 두 작업 중 하나가 실패해도 나머지는 시도한다.
                logging.exception("%s 작업 실패", label)
                failures.append(f"{label}: {error}")
    if failures:
        raise RuntimeError(" / ".join(failures))


if __name__ == "__main__":
    main()
