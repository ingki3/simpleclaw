"""Fetch bounded live scores, completed results, and standings from Naver.

``live``와 ``results``를 provider의 구조화 상태 enum으로 분리한다. 전자는
``STARTED``만, 후자는 ``ENDED``/``RESULT``만 수용하며 취소·중단·연기·알 수 없는
상태를 확정 결과로 승격하지 않는다.
"""

from __future__ import annotations

import argparse
import copy
import json
import socket
import sys
from datetime import date as date_type
from datetime import datetime
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlparse
from urllib.request import (
    HTTPRedirectHandler,
    Request,
    build_opener,
)
from zoneinfo import ZoneInfo

KST = ZoneInfo("Asia/Seoul")
SPORTS_HOST = "api-gw.sports.naver.com"
ESPORTS_HOST = "esports-api.game.naver.com"
ALLOWED_HOSTS = frozenset({SPORTS_HOST, ESPORTS_HOST})
MAX_RESPONSE_BYTES = 2_000_000
MAX_OUTPUT_CHARS = 7_000
DEFAULT_TIMEOUT_SECONDS = 15
USER_AGENT = "SimpleClaw-NaverSports/1.0"

SPORTS_BASE = f"https://{SPORTS_HOST}"
ESPORTS_BASE = f"https://{ESPORTS_HOST}"


class SportsError(Exception):
    """Stable, user-safe error raised at the API boundary."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        warning: str | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.retryable = retryable
        self.warning = warning


class _SafeRedirectHandler(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        try:
            validate_url(newurl)
        except SportsError as exc:
            raise SportsError(
                "REDIRECT_BLOCKED",
                "허용되지 않은 호스트로의 리디렉션을 차단했습니다.",
                retryable=False,
            ) from exc
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class SportsClient:
    """Small stdlib HTTPS client with host, size, redirect, and schema boundaries."""

    def __init__(
        self,
        *,
        timeout: int = DEFAULT_TIMEOUT_SECONDS,
        max_bytes: int = MAX_RESPONSE_BYTES,
    ) -> None:
        self.timeout = timeout
        self.max_bytes = max_bytes
        self.urls: list[str] = []
        self._opener = build_opener(_SafeRedirectHandler())

    def get_json(self, url: str) -> Any:
        validate_url(url)
        self.urls.append(url)
        request = Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": USER_AGENT,
            },
            method="GET",
        )
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                final_url = response.geturl()
                validate_url(final_url)
                raw = response.read(self.max_bytes + 1)
        except SportsError:
            raise
        except HTTPError as exc:
            if exc.code == 429:
                raise SportsError(
                    "RATE_LIMITED",
                    "네이버 스포츠 API 요청 한도를 초과했습니다.",
                    retryable=True,
                ) from exc
            raise SportsError(
                "HTTP_ERROR",
                f"네이버 스포츠 API가 HTTP {exc.code}를 반환했습니다.",
                retryable=500 <= exc.code < 600,
            ) from exc
        except TimeoutError as exc:
            raise SportsError(
                "TIMEOUT",
                "네이버 스포츠 API 응답 시간이 초과되었습니다.",
                retryable=True,
            ) from exc
        except URLError as exc:
            reason = getattr(exc, "reason", None)
            if isinstance(reason, TimeoutError):
                code = "TIMEOUT"
                message = "네이버 스포츠 API 응답 시간이 초과되었습니다."
            elif isinstance(reason, socket.gaierror):
                code = "DNS_ERROR"
                message = "네이버 스포츠 API 호스트를 확인하지 못했습니다."
            else:
                code = "NETWORK_ERROR"
                message = "네이버 스포츠 API에 연결하지 못했습니다."
            raise SportsError(code, message, retryable=True) from exc
        except OSError as exc:
            raise SportsError(
                "NETWORK_ERROR",
                "네이버 스포츠 API 통신 중 오류가 발생했습니다.",
                retryable=True,
            ) from exc

        if len(raw) > self.max_bytes:
            raise SportsError(
                "RESPONSE_TOO_LARGE",
                "네이버 스포츠 API 응답이 허용 크기를 초과했습니다.",
                retryable=False,
            )
        try:
            return json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SportsError(
                "INVALID_JSON",
                "네이버 스포츠 API 응답을 JSON으로 해석하지 못했습니다.",
                retryable=True,
            ) from exc


def validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise SportsError(
            "URL_BLOCKED",
            "HTTPS 및 허용된 Naver Sports 호스트만 조회할 수 있습니다.",
            retryable=False,
        )
    if parsed.username or parsed.password:
        raise SportsError(
            "URL_BLOCKED",
            "인증 정보가 포함된 URL은 허용되지 않습니다.",
            retryable=False,
        )


_CATEGORY_ALIASES = {
    "kbo": "kbo",
    "국내야구": "kbo",
    "kbaseball": "kbo",
    "mlb": "mlb",
    "해외야구": "mlb",
    "wbaseball": "mlb",
    "npb": "npb",
    "kleague": "kleague",
    "k리그": "kleague",
    "국내축구": "kleague",
    "kfootball": "kleague",
    "kleague2": "kleague2",
    "epl": "epl",
    "해외축구": "epl",
    "wfootball": "epl",
    "primera": "primera",
    "laliga": "primera",
    "bundesliga": "bundesliga",
    "seria": "seria",
    "ligue1": "ligue1",
    "eredivisie": "eredivisie",
    "uefacl": "uefacl",
    "uefacup": "uefacup",
    "conference": "conference",
    "fifa": "fifa",
    "afc": "afc",
    "champs": "champs",
    "basketball": "basketball",
    "농구": "basketball",
    "kbl": "kbl",
    "nba": "nba",
    "wkbl": "wkbl",
    "ubasketball": "ubasketball",
    "volleyball": "volleyball",
    "배구": "volleyball",
    "kovo": "kovo",
    "wkovo": "wkovo",
    "uvolleyball": "uvolleyball",
    "general": "general",
    "일반": "general",
    "atp": "atp",
    "wta": "wta",
    "koha": "koha",
    "wkoha": "wkoha",
    "ufc": "ufc",
    "billiards": "billiards",
    "golf": "golf",
    "골프": "golf",
    "pga": "pga",
    "lpga": "lpga",
    "kpga": "kpga",
    "klpga": "klpga",
    "esports": "esports",
    "e스포츠": "esports",
    "lol": "esports",
    "lck": "esports",
}

_WORLD_FOOTBALL_IDS = frozenset(
    {
        "epl",
        "primera",
        "bundesliga",
        "seria",
        "ligue1",
        "eredivisie",
        "uefacl",
        "uefacup",
        "conference",
        "fifa",
        "afc",
        "champs",
    }
)
_BASKETBALL_IDS = frozenset({"kbl", "nba", "wkbl", "ubasketball"})
_VOLLEYBALL_IDS = frozenset({"kovo", "wkovo", "uvolleyball"})
_GENERAL_IDS = frozenset({"atp", "wta", "koha", "wkoha", "ufc", "billiards"})
_GOLF_IDS = frozenset({"pga", "lpga", "kpga", "klpga"})
_TEAM_STANDINGS = frozenset(
    {
        "kbo",
        "mlb",
        "kleague",
        "kleague2",
        "epl",
        "kbl",
        "nba",
        "wkbl",
        "kovo",
        "wkovo",
    }
)
_PLAYER_STANDINGS = _GOLF_IDS


def _canonical_category(value: str) -> str:
    canonical = _CATEGORY_ALIASES.get(str(value or "").strip().lower())
    if canonical is None:
        supported = "kbo, mlb, kleague, epl, basketball, volleyball, general, golf, esports"
        raise SportsError(
            "INVALID_ARGUMENT",
            f"지원하지 않는 category입니다. 지원 값: {supported}",
        )
    return canonical


def _normalize_date(value: str | None) -> str:
    if value in (None, "", "today"):
        return datetime.now(KST).date().isoformat()
    try:
        parsed = date_type.fromisoformat(str(value))
    except ValueError as exc:
        raise SportsError(
            "INVALID_ARGUMENT",
            "date는 today 또는 유효한 YYYY-MM-DD 형식이어야 합니다.",
        ) from exc
    if parsed.isoformat() != str(value):
        raise SportsError(
            "INVALID_ARGUMENT",
            "date는 today 또는 유효한 YYYY-MM-DD 형식이어야 합니다.",
        )
    return parsed.isoformat()


def _normalize_limit(value: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or not 1 <= value <= 20:
        raise SportsError(
            "INVALID_ARGUMENT",
            "limit은 1..20 범위의 정수여야 합니다.",
        )
    return value


def _base_payload(
    *,
    ok: bool,
    mode: str,
    category: str,
    selected_date: str,
    client: Any,
) -> dict[str, Any]:
    now = datetime.now(KST).isoformat(timespec="seconds")
    urls = list(dict.fromkeys(getattr(client, "urls", [])))
    return {
        "ok": ok,
        "source": {
            "provider": "Naver Sports structured API",
            "urls": urls,
        },
        "mode": mode,
        "category": category,
        "season": None,
        "date": selected_date,
        "fetched_at": now,
        "freshness": {
            "timezone": "Asia/Seoul",
            "as_of": now,
            "refreshed_twice": mode == "live",
        },
        "items": [],
        "warnings": [],
        "error": None,
    }


def _error_payload(
    exc: SportsError,
    *,
    mode: str,
    category: str,
    selected_date: str,
    client: Any,
) -> dict[str, Any]:
    payload = _base_payload(
        ok=False,
        mode=mode,
        category=category,
        selected_date=selected_date,
        client=client,
    )
    payload["freshness"]["refreshed_twice"] = False
    payload["error"] = {
        "code": exc.code,
        "message": exc.message[:500],
        "retryable": exc.retryable,
    }
    if exc.warning:
        payload["warnings"].append(exc.warning[:300])
    return payload


def _sports_result(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise SportsError(
            "SCHEMA_ERROR",
            "네이버 스포츠 응답 최상위가 객체가 아닙니다.",
            retryable=True,
        )
    if payload.get("code") != 200 or payload.get("success") is not True:
        raise SportsError(
            "UPSTREAM_ERROR",
            "네이버 스포츠 API가 실패 상태를 반환했습니다.",
            retryable=True,
        )
    result = payload.get("result")
    if not isinstance(result, dict):
        raise SportsError(
            "SCHEMA_ERROR",
            "네이버 스포츠 응답의 result 객체가 없습니다.",
            retryable=True,
        )
    return result


def _esports_content(payload: Any) -> list[dict[str, Any]]:
    if not isinstance(payload, dict) or payload.get("code") != 200:
        raise SportsError(
            "UPSTREAM_ERROR",
            "네이버 e스포츠 API가 실패 상태를 반환했습니다.",
            retryable=True,
        )
    content = payload.get("content")
    if not isinstance(content, list):
        raise SportsError(
            "SCHEMA_ERROR",
            "네이버 e스포츠 응답의 content 배열이 없습니다.",
            retryable=True,
        )
    if not all(isinstance(item, dict) for item in content):
        raise SportsError(
            "SCHEMA_ERROR",
            "네이버 e스포츠 content 엔티티 형식이 올바르지 않습니다.",
            retryable=True,
        )
    return content


def _schedule_params(
    category: str,
    selected_date: str,
    limit: int,
    *,
    status_code: str = "STARTED",
) -> dict[str, str]:
    """구조화 schedule endpoint 인자를 authoritative 상태별로 만든다."""
    params = {
        "fromDate": selected_date,
        "toDate": selected_date,
        "size": str(max(limit, 20)),
        "page": "1",
        "statusCode": status_code,
        "fields": "basic,schedule,manualRelayUrl",
    }
    if category == "kbo":
        params.update(
            {
                "upperCategoryId": "kbaseball",
                "categoryIds": "kbo",
                "fields": "basic,schedule,baseball,manualRelayUrl",
            }
        )
    elif category in {"mlb", "npb"}:
        params.update(
            {
                "upperCategoryId": "wbaseball",
                "categoryIds": category,
                "fields": "basic,schedule,baseball,manualRelayUrl",
            }
        )
    elif category in {"kleague", "kleague2"}:
        params.update(
            {
                "upperCategoryId": "kfootball",
                "categoryIds": category,
                "fields": "basic,schedule,football,manualRelayUrl",
            }
        )
    elif category in _WORLD_FOOTBALL_IDS:
        params.update(
            {
                "upperCategoryId": "wfootball",
                "categoryIds": category,
                "fields": "basic,schedule,football,manualRelayUrl",
            }
        )
    elif category == "basketball":
        params.update(
            {
                "superCategoryId": "basketball",
                "categoryIds": ",".join(sorted(_BASKETBALL_IDS)),
                "fields": "basic,schedule,basketball,manualRelayUrl",
            }
        )
    elif category in _BASKETBALL_IDS:
        params.update(
            {
                "superCategoryId": "basketball",
                "categoryIds": category,
                "fields": "basic,schedule,basketball,manualRelayUrl",
            }
        )
    elif category == "volleyball":
        params.update(
            {
                "superCategoryId": "volleyball",
                "categoryIds": ",".join(sorted(_VOLLEYBALL_IDS)),
                "fields": (
                    "basic,schedule,round,groupName,neutralGround,manualRelayUrl"
                ),
            }
        )
    elif category in _VOLLEYBALL_IDS:
        params.update(
            {
                "superCategoryId": "volleyball",
                "categoryIds": category,
                "fields": (
                    "basic,schedule,round,groupName,neutralGround,manualRelayUrl"
                ),
            }
        )
    elif category == "general":
        params.update(
            {
                "upperCategoryIds": "others,tennis,handball,ufc",
                "categoryIds": ",".join(sorted(_GENERAL_IDS)),
                "fields": (
                    "basic,schedule,participant,round,groupName,manualRelayUrl"
                ),
            }
        )
    elif category in _GENERAL_IDS:
        params.update(
            {
                "upperCategoryIds": "others,tennis,handball,ufc",
                "categoryIds": category,
                "fields": (
                    "basic,schedule,participant,round,groupName,manualRelayUrl"
                ),
            }
        )
    elif category == "golf":
        params.update(
            {
                "upperCategoryId": "golf",
                "categoryIds": ",".join(sorted(_GOLF_IDS)),
                "fields": "basic,schedule,golf,manualRelayUrl",
            }
        )
    elif category in _GOLF_IDS:
        params.update(
            {
                "upperCategoryId": "golf",
                "categoryIds": category,
                "fields": "basic,schedule,golf,manualRelayUrl",
            }
        )
    else:
        raise SportsError("INVALID_ARGUMENT", "live mode를 지원하지 않는 category입니다.")
    return params


def _allowed_ids(category: str) -> frozenset[str]:
    if category == "kbo":
        return frozenset({"kbo"})
    if category in {"mlb", "npb"}:
        return frozenset({category})
    if category in {"kleague", "kleague2"}:
        return frozenset({category})
    if category in _WORLD_FOOTBALL_IDS:
        return frozenset({category})
    if category == "basketball":
        return _BASKETBALL_IDS
    if category in _BASKETBALL_IDS:
        return frozenset({category})
    if category == "volleyball":
        return _VOLLEYBALL_IDS
    if category in _VOLLEYBALL_IDS:
        return frozenset({category})
    if category == "general":
        return _GENERAL_IDS
    if category in _GENERAL_IDS:
        return frozenset({category})
    if category == "golf":
        return _GOLF_IDS
    if category in _GOLF_IDS:
        return frozenset({category})
    return frozenset()


def _as_kst_iso(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, (int, float)):
        return datetime.fromtimestamp(value / 1000, tz=KST).isoformat(
            timespec="seconds"
        )
    text = str(value).strip()
    if not text:
        return None
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return text
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=KST)
    else:
        parsed = parsed.astimezone(KST)
    return parsed.isoformat(timespec="seconds")


def _is_live_game(game: dict[str, Any], selected_date: str, allowed: set[str]) -> bool:
    return (
        game.get("statusCode") == "STARTED"
        and game.get("gameDate") == selected_date
        and game.get("cancel") is not True
        and game.get("suspended") is not True
        and str(game.get("categoryId") or "") in allowed
        and bool(game.get("gameId"))
    )


def _is_live_golf(
    game: dict[str, Any],
    selected_date: str,
    allowed: set[str],
) -> bool:
    start = str(game.get("competitionStartDate") or game.get("gameDate") or "")
    end = str(game.get("competitionEndDate") or start)
    return (
        game.get("statusCode") == "STARTED"
        and bool(start)
        and start <= selected_date <= end
        and game.get("cancel") is not True
        and game.get("suspended") is not True
        and str(game.get("categoryId") or "") in allowed
        and bool(game.get("gameId"))
    )


def _normal_game(game: dict[str, Any], source_url: str) -> dict[str, Any] | None:
    home = str(game.get("homeTeamName") or "").strip()
    away = str(game.get("awayTeamName") or "").strip()
    if not home or not away:
        return None
    item = {
        "game_id": str(game["gameId"]),
        "category": str(game.get("categoryId") or ""),
        "category_name": game.get("categoryName"),
        "event_state": "live",
        "status": game.get("statusInfo") or "진행 중(세부 상황 미제공)",
        "status_code": "STARTED",
        "started_at": _as_kst_iso(game.get("gameDateTime")),
        "participants": {
            "away": {"name": away},
            "home": {"name": home},
        },
        "score": {
            "away": game.get("awayTeamScore"),
            "home": game.get("homeTeamScore"),
        },
        "source_url": source_url,
        "fetched_at": datetime.now(KST).isoformat(timespec="seconds"),
    }
    for source_key, output_key in (
        ("stadium", "stadium"),
        ("broadChannel", "broadcast"),
        ("roundName", "round"),
        ("groupName", "group"),
    ):
        if game.get(source_key) not in (None, ""):
            item[output_key] = game[source_key]
    return item


def _numeric_score(value: Any) -> int | float | None:
    """승패 계산에 사용할 명시적 숫자 score만 보수적으로 정규화한다."""
    if isinstance(value, bool) or value in (None, ""):
        return None
    if isinstance(value, int | float):
        return value
    text = str(value).strip()
    try:
        number = float(text)
    except ValueError:
        return None
    return int(number) if number.is_integer() else number


def _result_event_state(game: dict[str, Any]) -> str:
    """표시 문구가 아닌 schema flag/enum만으로 제외 상태를 분류한다."""
    if game.get("cancel") is True:
        return "cancelled"
    if game.get("suspended") is True:
        return "suspended"
    status_code = str(game.get("statusCode") or "")
    if status_code in {"POSTPONED", "DELAYED"}:
        return "postponed"
    if status_code in {"ENDED", "RESULT"}:
        return "final"
    return "unknown"


def _excluded_result_event(
    game: dict[str, Any],
    *,
    source_url: str,
    reason: str,
) -> dict[str, Any]:
    """확정 결과에서 제외한 provider event의 typed 상태와 provenance를 남긴다."""
    return {
        "game_id": str(game.get("gameId") or ""),
        "date": str(game.get("gameDate") or ""),
        "event_state": _result_event_state(game),
        "status_code": str(game.get("statusCode") or "UNKNOWN"),
        "reason": reason,
        "source_url": source_url,
    }


def _completed_game(
    game: dict[str, Any],
    source_url: str,
) -> dict[str, Any] | None:
    """ENDED/RESULT conventional game을 score/winner가 있는 final item으로 투영한다."""
    home = str(game.get("homeTeamName") or "").strip()
    away = str(game.get("awayTeamName") or "").strip()
    home_score = _numeric_score(game.get("homeTeamScore"))
    away_score = _numeric_score(game.get("awayTeamScore"))
    if not home or not away or home_score is None or away_score is None:
        return None
    if home_score > away_score:
        winner = {"side": "home", "name": home}
    elif away_score > home_score:
        winner = {"side": "away", "name": away}
    else:
        winner = {"side": "draw", "name": None}
    fetched_at = datetime.now(KST).isoformat(timespec="seconds")
    return {
        "game_id": str(game["gameId"]),
        "category": str(game.get("categoryId") or ""),
        "category_name": game.get("categoryName"),
        "event_state": "final",
        "status": game.get("statusInfo") or "final",
        "status_code": str(game.get("statusCode")),
        "date": str(game.get("gameDate") or ""),
        "started_at": _as_kst_iso(game.get("gameDateTime")),
        "participants": {
            "away": {"name": away},
            "home": {"name": home},
        },
        "score": {"away": away_score, "home": home_score},
        "winner": winner,
        "source_url": source_url,
        "fetched_at": fetched_at,
    }


def _participant_name(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, list):
        names = [
            name
            for item in value
            if (name := _participant_name(item))
        ]
        return " / ".join(names)
    if isinstance(value, dict):
        for key in ("name", "playerName", "participantName", "title"):
            if value.get(key):
                return str(value[key]).strip()
    return ""


def _tennis_game(game: dict[str, Any], source_url: str) -> dict[str, Any] | None:
    participant = game.get("participant")
    if not isinstance(participant, dict):
        participant = {}
    name_a = _participant_name(participant.get("positionA"))
    name_b = _participant_name(participant.get("positionB"))
    if not name_a or not name_b:
        return None
    item = {
        "game_id": str(game["gameId"]),
        "category": str(game.get("categoryId") or ""),
        "category_name": game.get("categoryName"),
        "event_state": "live",
        "status": game.get("statusInfo") or "진행 중(세부 상황 미제공)",
        "status_code": "STARTED",
        "started_at": _as_kst_iso(game.get("gameDateTime")),
        "participants": {
            "a": {"name": name_a},
            "b": {"name": name_b},
        },
        "score": {"a": game.get("scoreA"), "b": game.get("scoreB")},
        "source_url": source_url,
        "fetched_at": datetime.now(KST).isoformat(timespec="seconds"),
    }
    if game.get("currentSet") is not None:
        item["current_set"] = game["currentSet"]
    if game.get("roundName"):
        item["round"] = game["roundName"]
    return item


def _sports_live(
    category: str,
    selected_date: str,
    limit: int,
    client: Any,
) -> tuple[list[dict[str, Any]], str]:
    params = _schedule_params(category, selected_date, limit)
    url = f"{SPORTS_BASE}/schedule/games?{urlencode(params)}"
    first = _sports_result(client.get_json(url))
    if not isinstance(first.get("games"), list):
        raise SportsError(
            "SCHEMA_ERROR",
            "네이버 스포츠 응답의 games 배열이 없습니다.",
            retryable=True,
        )
    second = _sports_result(client.get_json(url))
    games = second.get("games")
    if not isinstance(games, list):
        raise SportsError(
            "SCHEMA_ERROR",
            "네이버 스포츠 재확인 응답의 games 배열이 없습니다.",
            retryable=True,
        )

    allowed = set(_allowed_ids(category))
    items: list[dict[str, Any]] = []
    for game in games:
        if not isinstance(game, dict) or not _is_live_game(
            game, selected_date, allowed
        ):
            continue
        if str(game.get("categoryId")) in {"atp", "wta"}:
            item = _tennis_game(game, url)
        else:
            item = _normal_game(game, url)
        if item is not None:
            items.append(item)
        if len(items) >= limit:
            break
    return items, url


def _sports_results(
    category: str,
    selected_date: str,
    limit: int,
    client: Any,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """ENDED/RESULT endpoint만 조회해 확정 결과와 제외 상태를 분리한다."""
    allowed = set(_allowed_ids(category))
    items: list[dict[str, Any]] = []
    excluded: list[dict[str, Any]] = []
    seen_games: set[str] = set()
    for requested_status in ("ENDED", "RESULT"):
        params = _schedule_params(
            category,
            selected_date,
            limit,
            status_code=requested_status,
        )
        url = f"{SPORTS_BASE}/schedule/games?{urlencode(params)}"
        result = _sports_result(client.get_json(url))
        games = result.get("games")
        if not isinstance(games, list):
            raise SportsError(
                "SCHEMA_ERROR",
                "네이버 스포츠 종료 결과 응답의 games 배열이 없습니다.",
                retryable=True,
            )
        for game in games:
            if not isinstance(game, dict):
                raise SportsError(
                    "SCHEMA_ERROR",
                    "네이버 스포츠 종료 결과 game 형식이 올바르지 않습니다.",
                    retryable=True,
                )
            game_id = str(game.get("gameId") or "")
            if (
                not game_id
                or game_id in seen_games
                or str(game.get("gameDate") or "") != selected_date
                or str(game.get("categoryId") or "") not in allowed
            ):
                continue
            seen_games.add(game_id)
            event_state = _result_event_state(game)
            if event_state != "final":
                excluded.append(
                    _excluded_result_event(
                        game,
                        source_url=url,
                        reason="not_a_completed_result",
                    )
                )
                continue
            item = _completed_game(game, url)
            if item is None:
                excluded.append(
                    _excluded_result_event(
                        game,
                        source_url=url,
                        reason="missing_participant_or_score",
                    )
                )
                continue
            items.append(item)
            if len(items) >= limit:
                return items, excluded
    return items, excluded


def _result_claim_map(
    items: list[dict[str, Any]],
    *,
    fetched_at: str,
) -> dict[str, dict[str, Any]]:
    """확정 결과에서 실제 관찰된 typed claim만 provenance와 함께 투영한다."""
    source_url = next(
        (
            str(item.get("source_url") or "")
            for item in items
            if str(item.get("source_url") or "")
        ),
        "",
    )
    if not items or not source_url or not fetched_at:
        return {}

    common = {
        "source_url": source_url,
        "provenance": "Naver Sports structured API",
        "observed_at": fetched_at,
        "fresh": True,
        "usable": True,
    }
    claims: dict[str, dict[str, Any]] = {
        "game_result": {"value": items, **common},
    }
    scores = [item["score"] for item in items if isinstance(item.get("score"), dict)]
    winners = [
        item["winner"] for item in items if isinstance(item.get("winner"), dict)
    ]
    if len(scores) == len(items):
        claims["score"] = {"value": scores, **common}
    if len(winners) == len(items):
        claims["winner"] = {"value": winners, **common}
    return claims


def _leader(entry: dict[str, Any]) -> dict[str, Any] | None:
    name = entry.get("name") or entry.get("playerName")
    if not name:
        return None
    projected = {
        "rank": entry.get("rank"),
        "name": str(name),
        "score": entry.get("score"),
        "strokes": entry.get("strokes"),
        "today_score": entry.get("todayScore"),
        "thru": entry.get("thruValue"),
        "tied": entry.get("tied"),
        "status": entry.get("status"),
    }
    if isinstance(entry.get("rounds"), list):
        projected["rounds"] = entry["rounds"][:8]
    return projected


def _golf_live(
    category: str,
    selected_date: str,
    limit: int,
    client: Any,
) -> list[dict[str, Any]]:
    params = _schedule_params(category, selected_date, limit)
    schedule_url = f"{SPORTS_BASE}/schedule/games?{urlencode(params)}"
    first = _sports_result(client.get_json(schedule_url))
    if not isinstance(first.get("games"), list):
        raise SportsError(
            "SCHEMA_ERROR",
            "네이버 골프 응답의 games 배열이 없습니다.",
            retryable=True,
        )
    second = _sports_result(client.get_json(schedule_url))
    games = second.get("games")
    if not isinstance(games, list):
        raise SportsError(
            "SCHEMA_ERROR",
            "네이버 골프 재확인 응답의 games 배열이 없습니다.",
            retryable=True,
        )

    allowed = set(_allowed_ids(category))
    items: list[dict[str, Any]] = []
    for game in games:
        if not isinstance(game, dict) or not _is_live_golf(
            game, selected_date, allowed
        ):
            continue
        game_id = str(game["gameId"])
        item = {
            "game_id": game_id,
            "category": str(game.get("categoryId") or ""),
            "title": game.get("title") or game.get("categoryName") or game_id,
            "event_state": "live",
            "status": game.get("competitionStatus")
            or game.get("statusInfo")
            or "진행 중",
            "status_code": "STARTED",
            "started_at": _as_kst_iso(game.get("gameDateTime")),
            "competition_start": game.get("competitionStartDate"),
            "competition_end": game.get("competitionEndDate"),
            "current_round": game.get("currentRound"),
            "source_url": schedule_url,
            "fetched_at": datetime.now(KST).isoformat(timespec="seconds"),
            "leaders": [],
        }
        if game.get("leaderBoardEnable") is True:
            leaderboard_url = (
                f"{SPORTS_BASE}/golf/games/{game_id}/leaderboard"
            )
            leaderboard_result = _sports_result(client.get_json(leaderboard_url))
            leaderboard = leaderboard_result.get("leaderboard")
            if not isinstance(leaderboard, dict):
                raise SportsError(
                    "SCHEMA_ERROR",
                    "네이버 골프 응답의 leaderboard 객체가 없습니다.",
                    retryable=True,
                )
            regular = leaderboard.get("regular")
            if not isinstance(regular, list):
                raise SportsError(
                    "SCHEMA_ERROR",
                    "네이버 골프 응답의 regular 배열이 없습니다.",
                    retryable=True,
                )
            item["source_url"] = leaderboard_url
            item["leaders"] = [
                projected
                for entry in regular
                if isinstance(entry, dict)
                and (projected := _leader(entry)) is not None
            ][:limit]
        items.append(item)
        if len(items) >= min(limit, 3):
            break
    return items


def _esports_live(
    selected_date: str,
    limit: int,
    client: Any,
) -> list[dict[str, Any]]:
    today = datetime.now(KST).date().isoformat()
    if selected_date == today:
        url = f"{ESPORTS_BASE}/service/v1/match/live"
    else:
        query = urlencode(
            {"day": selected_date, "gameCode": "lol", "relay": "true"}
        )
        url = f"{ESPORTS_BASE}/service/v1/schedule/day?{query}"
    _esports_content(client.get_json(url))
    content = _esports_content(client.get_json(url))

    items: list[dict[str, Any]] = []
    for match in content:
        started_at = _as_kst_iso(match.get("startDate"))
        started_date = started_at[:10] if started_at else ""
        if (
            match.get("matchStatus") != "STARTED"
            or match.get("gameCode", "lol") != "lol"
            or started_date != selected_date
        ):
            continue
        home = _participant_name(match.get("homeTeam"))
        away = _participant_name(match.get("awayTeam"))
        if not match.get("gameId") or not home or not away:
            continue
        items.append(
            {
                "game_id": str(match["gameId"]),
                "category": "esports",
                "league_id": match.get("leagueId"),
                "event_state": "live",
                "status": "STARTED",
                "status_code": "STARTED",
                "started_at": started_at,
                "participants": {
                    "away": {"name": away},
                    "home": {"name": home},
                },
                "score": {
                    "away": match.get("awayScore"),
                    "home": match.get("homeScore"),
                },
                "current_set": match.get("currentMatchSet"),
                "max_sets": match.get("maxMatchCount"),
                "source_url": url,
                "fetched_at": datetime.now(KST).isoformat(timespec="seconds"),
            }
        )
        if len(items) >= limit:
            break
    return items


def _ranking_category(category: str) -> str:
    defaults = {
        "basketball": "kbl",
        "volleyball": "kovo",
        "golf": "pga",
    }
    selected = defaults.get(category, category)
    if selected not in _TEAM_STANDINGS | _PLAYER_STANDINGS:
        raise SportsError(
            "INVALID_ARGUMENT",
            "standings mode를 지원하지 않는 category입니다.",
        )
    return selected


def _format_compact_date(value: Any) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    if len(text) == 8 and text.isdigit():
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    try:
        return date_type.fromisoformat(text).isoformat()
    except ValueError:
        return text


def _select_season(seasons: list[Any], requested: str | None) -> dict[str, Any]:
    valid = [
        season
        for season in seasons
        if isinstance(season, dict)
        and season.get("seasonCode") not in (None, "")
        and season.get("isEnable", "Y") == "Y"
    ]
    if not valid:
        raise SportsError(
            "SCHEMA_ERROR",
            "사용 가능한 시즌 정보가 없습니다.",
            retryable=False,
        )
    if requested:
        requested_text = str(requested)
        matches = [
            season
            for season in valid
            if requested_text
            in {
                str(season.get("seasonCode")),
                str(season.get("year")),
                str(season.get("title")),
            }
        ]
        if not matches:
            raise SportsError(
                "INVALID_ARGUMENT",
                f"요청한 season({requested_text})을 찾지 못했습니다.",
            )
        selected = matches[-1]
    else:
        selected = valid[-1]
    return {
        "code": str(selected["seasonCode"]),
        "title": str(selected.get("title") or selected["seasonCode"]),
        "start": _format_compact_date(selected.get("startDate")),
        "end": _format_compact_date(selected.get("endDate")),
    }


def _pick(entry: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in entry and entry[key] is not None:
            return entry[key]
    return None


def _project_team(
    entry: dict[str, Any],
    category: str,
) -> dict[str, Any] | None:
    team = _pick(entry, "teamName", "name")
    if not team and isinstance(entry.get("team"), dict):
        team = entry["team"].get("name")
    rank = _pick(entry, "ranking", "rank")
    if not team or rank is None:
        return None
    projected = {
        "rank": rank,
        "team": str(team),
        "played": _pick(entry, "gameCount", "matchesPlayed", "played"),
        "wins": _pick(entry, "winGameCount", "wins", "win"),
        "draws": _pick(entry, "drawnGameCount", "draws", "draw"),
        "losses": _pick(entry, "loseGameCount", "losses", "lose"),
    }
    optional = {
        "win_rate": ("wra", "winRate", "winPct"),
        "games_behind": ("gameBehind", "gamesBehind", "gameDifference"),
        "goal_difference": ("goalsDifference", "goalDifference"),
        "set_ratio": ("setRatio", "setPct", "setGainLoseRatio"),
        "score_ratio": (
            "scoreRatio",
            "scorePct",
            "scoreGainLoseRatio",
            "pointRatio",
        ),
    }
    if category in {
        "kleague",
        "kleague2",
        "epl",
        "kovo",
        "wkovo",
    }:
        optional["points"] = ("points", "point")
    for output_key, keys in optional.items():
        value = _pick(entry, *keys)
        if value is not None:
            projected[output_key] = value
    for output_key, source_key in (
        ("league", "league"),
        ("division", "division"),
        ("conference", "conf"),
    ):
        if entry.get(source_key):
            projected[output_key] = entry[source_key]
    return {key: value for key, value in projected.items() if value is not None}


def _project_player(entry: dict[str, Any]) -> dict[str, Any] | None:
    name = _pick(entry, "playerName", "name", "fullName")
    rank = entry.get("rank")
    if not name or rank is None:
        return None
    projected = {
        "rank": rank,
        "name": str(name),
        "earnings": entry.get("earningsAmount"),
        "currency": entry.get("earningsCurrency"),
        "scoring_avg": entry.get("scoringAvg"),
        "played": entry.get("played"),
    }
    return {key: value for key, value in projected.items() if value is not None}


def _sports_standings(
    category: str,
    requested_season: str | None,
    limit: int,
    client: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]], str]:
    selected_category = _ranking_category(category)
    seasons_url = (
        f"{SPORTS_BASE}/statistics/categories/{selected_category}/seasons"
    )
    seasons_result = _sports_result(client.get_json(seasons_url))
    seasons = seasons_result.get("seasons")
    if not isinstance(seasons, list):
        raise SportsError(
            "SCHEMA_ERROR",
            "네이버 스포츠 응답의 seasons 배열이 없습니다.",
            retryable=True,
        )
    season = _select_season(seasons, requested_season)
    if selected_category in _PLAYER_STANDINGS:
        query = urlencode(
            {"sortField": "earningsAmount", "sortDirection": "asc"}
        )
        stats_url = (
            f"{SPORTS_BASE}/statistics/categories/{selected_category}/seasons/"
            f"{season['code']}/players?{query}"
        )
        stats_result = _sports_result(client.get_json(stats_url))
        rows = stats_result.get("seasonPlayerStats")
        player_rows = True
    else:
        stats_url = (
            f"{SPORTS_BASE}/statistics/categories/{selected_category}/seasons/"
            f"{season['code']}/teams"
        )
        stats_result = _sports_result(client.get_json(stats_url))
        rows = stats_result.get("seasonTeamStats")
        player_rows = False
    if not isinstance(rows, list):
        raise SportsError(
            "SCHEMA_ERROR",
            "네이버 스포츠 순위 배열이 없습니다.",
            retryable=True,
        )
    if player_rows:
        items = [
            projected
            for entry in rows
            if isinstance(entry, dict)
            and (projected := _project_player(entry)) is not None
        ][:limit]
    else:
        items = [
            projected
            for entry in rows
            if isinstance(entry, dict)
            and (
                projected := _project_team(entry, selected_category)
            )
            is not None
        ][:limit]
    return season, items, selected_category


def _lck_standings(
    requested_season: str | None,
    limit: int,
    client: Any,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    current_year = datetime.now(KST).year
    candidates = (
        [str(requested_season)]
        if requested_season
        else [str(current_year), str(current_year - 1)]
    )
    last_error: SportsError | None = None
    for candidate in candidates:
        league_id = (
            candidate if str(candidate).startswith("lck_") else f"lck_{candidate}"
        )
        url = f"{ESPORTS_BASE}/service/v1/ranking/{league_id}/team"
        try:
            rows = _esports_content(client.get_json(url))
        except SportsError as exc:
            last_error = exc
            if requested_season:
                raise
            continue
        valid_rows = [
            row
            for row in rows
            if row.get("leagueId") == league_id
            and row.get("rank") is not None
            and _participant_name(row.get("team"))
        ]
        if not valid_rows:
            last_error = SportsError(
                "SCHEMA_ERROR",
                f"{league_id} 순위 응답의 leagueId가 일치하지 않습니다.",
                retryable=False,
            )
            if requested_season:
                raise last_error
            continue
        items = [
            {
                "rank": row.get("rank"),
                "team": _participant_name(row.get("team")),
                "wins": row.get("wins"),
                "losses": row.get("losses"),
                "draws": row.get("draws"),
                "score": row.get("score"),
                "win_rate": row.get("winRate"),
            }
            for row in valid_rows[:limit]
        ]
        return {
            "code": league_id,
            "title": league_id.upper(),
            "start": None,
            "end": None,
        }, items
    if last_error is not None:
        raise last_error
    raise SportsError(
        "SCHEMA_ERROR",
        "사용 가능한 LCK 순위를 찾지 못했습니다.",
        retryable=False,
    )


def run(
    *,
    mode: str = "live",
    category: str = "kbo",
    date: str | None = "today",
    season: str | None = None,
    limit: int = 10,
    client: Any | None = None,
) -> dict[str, Any]:
    client = client or SportsClient()
    safe_mode = str(mode or "").strip().lower()
    raw_category = str(category or "")
    safe_category = raw_category.strip().lower()
    safe_date = str(date or "today")
    try:
        if safe_mode not in {"live", "results", "standings"}:
            raise SportsError(
                "INVALID_ARGUMENT",
                "mode는 live, results 또는 standings여야 합니다.",
            )
        selected_category = _canonical_category(raw_category)
        selected_date = _normalize_date(date)
        selected_limit = _normalize_limit(limit)

        payload = _base_payload(
            ok=True,
            mode=safe_mode,
            category=selected_category,
            selected_date=selected_date,
            client=client,
        )
        if safe_mode == "live":
            if selected_category == "esports":
                items = _esports_live(selected_date, selected_limit, client)
            elif selected_category == "golf" or selected_category in _GOLF_IDS:
                items = _golf_live(
                    selected_category,
                    selected_date,
                    selected_limit,
                    client,
                )
            else:
                items, _ = _sports_live(
                    selected_category,
                    selected_date,
                    selected_limit,
                    client,
                )
            payload["items"] = items
            if not items:
                payload["message"] = (
                    "정상 조회했지만 엄격한 STARTED 기준의 라이브 경기가 없습니다."
                )
        elif safe_mode == "results":
            if selected_category == "esports" or (
                selected_category == "golf" or selected_category in _GOLF_IDS
            ):
                raise SportsError(
                    "INVALID_ARGUMENT",
                    "results mode는 구조화된 conventional schedule category만 지원합니다.",
                )
            items, excluded = _sports_results(
                selected_category,
                selected_date,
                selected_limit,
                client,
            )
            payload["items"] = items
            payload["excluded_events"] = excluded
            if excluded:
                payload["warnings"].append(
                    f"확정 결과가 아닌 이벤트 {len(excluded)}건을 제외했습니다."
                )
            if not items:
                payload["message"] = (
                    "정상 조회했지만 ENDED/RESULT 기준의 확정 경기 결과가 없습니다."
                )
        elif selected_category == "esports":
            selected_season, items = _lck_standings(
                season, selected_limit, client
            )
            payload["season"] = selected_season
            payload["items"] = items
        else:
            selected_season, items, ranking_category = _sports_standings(
                selected_category,
                season,
                selected_limit,
                client,
            )
            payload["category"] = ranking_category
            payload["season"] = selected_season
            payload["items"] = items
            if ranking_category != selected_category:
                payload["warnings"].append(
                    f"{selected_category} 기본 순위 리그로 {ranking_category}를 선택했습니다."
                )
            if not items:
                payload["message"] = (
                    "정상 조회했지만 선택한 시즌의 순위 데이터가 비어 있습니다."
                )

        now = datetime.now(KST).isoformat(timespec="seconds")
        payload["fetched_at"] = now
        payload["freshness"]["as_of"] = now
        payload["source"]["urls"] = list(
            dict.fromkeys(getattr(client, "urls", []))
        )
        if safe_mode == "results":
            payload["claim_map"] = _result_claim_map(
                payload["items"],
                fetched_at=now,
            )
        return payload
    except SportsError as exc:
        return _error_payload(
            exc,
            mode=safe_mode or "unknown",
            category=safe_category or "unknown",
            selected_date=safe_date,
            client=client,
        )
    except Exception:  # noqa: BLE001 - public CLI fail-closed error boundary
        return _error_payload(
            SportsError(
                "INTERNAL_ERROR",
                "스포츠 데이터를 처리하는 중 예기치 않은 오류가 발생했습니다.",
                retryable=False,
            ),
            mode=safe_mode or "unknown",
            category=safe_category or "unknown",
            selected_date=safe_date,
            client=client,
        )


def dumps_bounded(payload: dict[str, Any]) -> str:
    """Serialize one JSON document while enforcing the public 7,000-char cap."""

    data = copy.deepcopy(payload)

    def encode() -> str:
        return json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
        )

    encoded = encode()
    if len(encoded) <= MAX_OUTPUT_CHARS:
        return encoded

    warnings = data.setdefault("warnings", [])
    if isinstance(warnings, list):
        warnings.append("출력 크기 제한으로 일부 항목을 생략했습니다.")

    items = data.get("items")
    if isinstance(items, list):
        for item in items:
            if not isinstance(item, dict):
                continue
            leaders = item.get("leaders")
            if isinstance(leaders, list):
                while leaders and len(encode()) > MAX_OUTPUT_CHARS:
                    leaders.pop()
        while items and len(encode()) > MAX_OUTPUT_CHARS:
            items.pop()

    source = data.get("source")
    if isinstance(source, dict) and isinstance(source.get("urls"), list):
        source["urls"] = source["urls"][:2]
    encoded = encode()
    if len(encoded) <= MAX_OUTPUT_CHARS:
        return encoded

    compact = {
        "ok": False,
        "source": {"provider": "Naver Sports structured API", "urls": []},
        "mode": data.get("mode"),
        "category": data.get("category"),
        "season": data.get("season"),
        "date": data.get("date"),
        "fetched_at": data.get("fetched_at"),
        "freshness": data.get("freshness"),
        "items": [],
        "warnings": ["출력 크기 제한으로 결과를 생략했습니다."],
        "error": {
            "code": "OUTPUT_TOO_LARGE",
            "message": "정규화된 출력이 7,000자 제한을 초과했습니다.",
            "retryable": False,
        },
    }
    return json.dumps(
        compact,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Fetch bounded live scores, completed results, or standings from Naver."
        )
    )
    parser.add_argument(
        "--mode",
        choices=("live", "results", "standings"),
        default="live",
    )
    parser.add_argument("--category", default="kbo")
    parser.add_argument("--date", default="today")
    parser.add_argument("--season")
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument(
        "--json",
        action="store_true",
        help="Compatibility flag; stdout is always one JSON document.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
        payload = run(
            mode=args.mode,
            category=args.category,
            date=args.date,
            season=args.season,
            limit=args.limit,
        )
    except SystemExit as exc:
        if exc.code == 0:
            raise
        payload = _error_payload(
            SportsError(
                "INVALID_ARGUMENT",
                "CLI 인자를 해석하지 못했습니다. --help를 확인해 주세요.",
            ),
            mode="unknown",
            category="unknown",
            selected_date="unknown",
            client=SportsClient(),
        )
    sys.stdout.write(dumps_bounded(payload) + "\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
