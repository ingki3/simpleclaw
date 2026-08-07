from __future__ import annotations

import json

import pytest

from scripts.dev.validate_naver_sports_asset import (
    ProductionAssetValidationError,
    validate_production_asset,
)
from simpleclaw.skills import naver_sports


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []

    def get_json(self, url):
        self.urls.append(url)
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


def test_installer_materializes_version_controlled_wrapper(tmp_path):
    from scripts.install_naver_sports_skill import install

    skill_dir = install(tmp_path)

    assert (skill_dir / "SKILL.md").is_file()
    assert "--mode live|results|standings" in (
        skill_dir / "SKILL.md"
    ).read_text(encoding="utf-8")
    assert (skill_dir / "scripts" / "naver_sports.py").read_text(
        encoding="utf-8"
    ).startswith("#!/usr/bin/env python3\nfrom simpleclaw.skills.naver_sports import main")


def test_helper_parser_rejects_abbreviated_limit_flag():
    with pytest.raises(SystemExit):
        naver_sports.build_parser().parse_args(
            ["--limit", "3", "--lim", "10"]
        )


def sports_response(*games):
    return {
        "code": 200,
        "success": True,
        "result": {"games": list(games), "gameTotalCount": len(games)},
    }


def test_live_rechecks_endpoint_and_uses_second_response():
    first = {
        "gameId": "g1",
        "categoryId": "kbo",
        "categoryName": "KBO리그",
        "gameDate": "2026-07-28",
        "gameDateTime": "2026-07-28T18:30:00",
        "homeTeamName": "삼성",
        "awayTeamName": "KIA",
        "homeTeamScore": 1,
        "awayTeamScore": 0,
        "statusCode": "STARTED",
        "statusInfo": "1회말",
        "cancel": False,
        "suspended": False,
    }
    second = {**first, "homeTeamScore": 3, "statusInfo": "3회말"}
    client = FakeClient([sports_response(first), sports_response(second)])

    result = naver_sports.run(
        mode="live",
        category="국내야구",
        date="2026-07-28",
        limit=5,
        client=client,
    )

    assert result["ok"] is True
    assert result["side_effect"] is False
    assert len(client.urls) == 2
    assert client.urls[0] == client.urls[1]
    assert result["freshness"]["refreshed_twice"] is True
    assert result["items"][0]["event_state"] == "live"
    assert result["items"][0]["score"] == {"away": 0, "home": 3}
    assert result["items"][0]["status"] == "3회말"
    assert result["items"][0]["started_at"].endswith("+09:00")


def test_live_filters_non_started_cancelled_and_wrong_category():
    base = {
        "gameId": "g1",
        "categoryId": "kbo",
        "gameDate": "2026-07-28",
        "gameDateTime": "2026-07-28T18:30:00",
        "homeTeamName": "홈",
        "awayTeamName": "원정",
        "homeTeamScore": 1,
        "awayTeamScore": 0,
        "statusCode": "STARTED",
        "cancel": False,
        "suspended": False,
    }
    invalid = [
        {**base, "gameId": "ended", "statusCode": "RESULT"},
        {**base, "gameId": "cancelled", "cancel": True},
        {**base, "gameId": "broadcast", "categoryId": "kbaseballetc"},
    ]
    valid = {**base, "gameId": "valid"}
    response = sports_response(*invalid, valid)

    result = naver_sports.run(
        mode="live",
        category="kbo",
        date="2026-07-28",
        limit=10,
        client=FakeClient([response, response]),
    )

    assert [item["game_id"] for item in result["items"]] == ["valid"]


@pytest.mark.parametrize("status_code", ("ENDED", "RESULT"))
def test_results_returns_typed_score_winner_and_provenance(status_code):
    game = {
        "gameId": f"final-{status_code.lower()}",
        "categoryId": "kbo",
        "categoryName": "KBO리그",
        "gameDate": "2026-08-02",
        "gameDateTime": "2026-08-02T18:30:00",
        "homeTeamName": "한화",
        "awayTeamName": "두산",
        "homeTeamScore": 5,
        "awayTeamScore": 3,
        "statusCode": status_code,
        "statusInfo": "경기 종료",
        "cancel": False,
        "suspended": False,
    }
    empty = sports_response()
    responses = (
        [sports_response(game), empty]
        if status_code == "ENDED"
        else [empty, sports_response(game)]
    )

    result = naver_sports.run(
        mode="results",
        category="kbo",
        date="2026-08-02",
        limit=10,
        client=FakeClient(responses),
    )

    assert result["ok"] is True
    assert result["mode"] == "results"
    assert result["items"] == [
        {
            "game_id": f"final-{status_code.lower()}",
            "category": "kbo",
            "category_name": "KBO리그",
            "event_state": "final",
            "status": "경기 종료",
            "status_code": status_code,
            "date": "2026-08-02",
            "started_at": "2026-08-02T18:30:00+09:00",
            "participants": {
                "away": {"name": "두산"},
                "home": {"name": "한화"},
            },
            "score": {"away": 3, "home": 5},
            "winner": {"side": "home", "name": "한화"},
            "source_url": result["source"]["urls"][0 if status_code == "ENDED" else 1],
            "fetched_at": result["items"][0]["fetched_at"],
        }
    ]
    assert result["items"][0]["fetched_at"].endswith("+09:00")
    assert set(result["claim_map"]) == {"game_result", "score", "winner"}
    score_record = result["claim_map"]["score"]["records"][0]
    winner_record = result["claim_map"]["winner"]["records"][0]
    assert score_record["value"] == {"away": 3, "home": 5}
    assert winner_record["value"] == {"side": "home", "name": "한화"}
    assert score_record["source_index"] == 0
    assert result["claim_map"]["score"]["sources"] == [
        result["items"][0]["source_url"]
    ]
    assert result["claim_map"]["score"]["fresh"] is True
    assert any("statusCode=ENDED" in url for url in result["source"]["urls"])
    assert any("statusCode=RESULT" in url for url in result["source"]["urls"])


def test_results_claim_map_preserves_mixed_endpoint_provenance_and_deduplicates():
    base = {
        "categoryId": "kbo",
        "categoryName": "KBO리그",
        "gameDate": "2026-08-02",
        "gameDateTime": "2026-08-02T18:30:00",
        "statusInfo": "경기 종료",
        "cancel": False,
        "suspended": False,
    }
    ended_game = {
        **base,
        "gameId": "ended-game",
        "homeTeamName": "한화",
        "awayTeamName": "두산",
        "homeTeamScore": 2,
        "awayTeamScore": 1,
        "statusCode": "ENDED",
    }
    duplicate = {
        **ended_game,
        "homeTeamScore": 99,
        "awayTeamScore": 98,
        "statusCode": "RESULT",
    }
    result_game = {
        **base,
        "gameId": "result-game",
        "homeTeamName": "LG",
        "awayTeamName": "KT",
        "homeTeamScore": 4,
        "awayTeamScore": 3,
        "statusCode": "RESULT",
    }

    result = naver_sports.run(
        mode="results",
        category="kbo",
        date="2026-08-02",
        limit=10,
        client=FakeClient(
            [sports_response(ended_game), sports_response(duplicate, result_game)]
        ),
    )

    assert [item["game_id"] for item in result["items"]] == [
        "ended-game",
        "result-game",
    ]
    assert [record["value"] for record in result["claim_map"]["score"]["records"]] == [
        {"away": 1, "home": 2},
        {"away": 3, "home": 4},
    ]
    score_claim = result["claim_map"]["score"]
    winner_claim = result["claim_map"]["winner"]
    score_sources = [
        score_claim["sources"][record["source_index"]]
        for record in score_claim["records"]
    ]
    winner_sources = [
        winner_claim["sources"][record["source_index"]]
        for record in winner_claim["records"]
    ]
    assert "statusCode=ENDED" in score_sources[0]
    assert "statusCode=RESULT" in score_sources[1]
    assert winner_sources == score_sources


def test_results_preserves_cancelled_suspended_postponed_as_excluded_states():
    base = {
        "categoryId": "kbo",
        "gameDate": "2026-08-02",
        "homeTeamName": "홈",
        "awayTeamName": "원정",
        "homeTeamScore": 1,
        "awayTeamScore": 0,
        "statusCode": "ENDED",
        "cancel": False,
        "suspended": False,
    }
    games = (
        {**base, "gameId": "cancelled", "cancel": True},
        {**base, "gameId": "suspended", "suspended": True},
        {**base, "gameId": "postponed", "statusCode": "POSTPONED"},
    )

    result = naver_sports.run(
        mode="results",
        category="kbo",
        date="2026-08-02",
        client=FakeClient([sports_response(*games), sports_response()]),
    )

    assert result["ok"] is True
    assert result["items"] == []
    assert {item["event_state"] for item in result["excluded_events"]} == {
        "cancelled",
        "suspended",
        "postponed",
    }
    assert result["warnings"] == ["확정 결과가 아닌 이벤트 3건을 제외했습니다."]


def test_results_valid_empty_is_not_upstream_failure():
    result = naver_sports.run(
        mode="results",
        category="kbo",
        date="2026-08-02",
        client=FakeClient([sports_response(), sports_response()]),
    )

    assert result["ok"] is True
    assert result["items"] == []
    assert result["error"] is None
    assert "ENDED/RESULT" in result["message"]


def test_results_schema_failure_is_typed_error_without_score():
    result = naver_sports.run(
        mode="results",
        category="kbo",
        date="2026-08-02",
        client=FakeClient([{"code": 200, "success": True, "result": {}}]),
    )

    assert result["ok"] is False
    assert result["items"] == []
    assert result["error"] == {
        "code": "SCHEMA_ERROR",
        "message": "네이버 스포츠 종료 결과 응답의 games 배열이 없습니다.",
        "retryable": True,
    }


def test_general_tennis_uses_participant_and_score_fields():
    tennis = {
        "gameId": "tennis-1",
        "categoryId": "atp",
        "categoryName": "ATP",
        "gameDate": "2026-07-28",
        "gameDateTime": "2026-07-28T20:00:00",
        "statusCode": "STARTED",
        "statusInfo": "2세트",
        "cancel": False,
        "suspended": False,
        "participant": {
            "positionA": [{"playerName": "선수 A"}],
            "positionB": [
                {"playerName": "선수 B"},
                {"playerName": "선수 C"},
            ],
        },
        "scoreA": 1,
        "scoreB": 0,
        "currentSet": 2,
        "roundName": "16강",
    }
    response = sports_response(tennis)

    result = naver_sports.run(
        mode="live",
        category="general",
        date="2026-07-28",
        limit=5,
        client=FakeClient([response, response]),
    )

    item = result["items"][0]
    assert item["participants"]["a"]["name"] == "선수 A"
    assert item["participants"]["b"]["name"] == "선수 B / 선수 C"
    assert item["score"] == {"a": 1, "b": 0}
    assert item["current_set"] == 2


def test_golf_leaderboard_is_projected_and_bounded():
    tournament = {
        "gameId": "golf-1",
        "categoryId": "pga",
        "categoryName": "PGA",
        "gameDate": "2026-07-27",
        "gameDateTime": "2026-07-27T08:00:00",
        "statusCode": "STARTED",
        "title": "테스트 오픈",
        "competitionStartDate": "2026-07-27",
        "competitionEndDate": "2026-07-31",
        "leaderBoardEnable": True,
        "currentRound": 2,
        "competitionStatus": "진행중",
        "cancel": False,
        "suspended": False,
    }
    schedule = sports_response(tournament)
    regular = [
        {
            "rank": index + 1,
            "name": f"선수 {index}",
            "score": -index,
            "strokes": 70 + index,
            "todayScore": -1,
            "thruValue": 18,
            "tied": False,
            "status": "PLAYING",
            "rounds": [70, 69],
            "participants": [{"secret": "must-not-leak"}] * 100,
        }
        for index in range(30)
    ]
    leaderboard = {
        "code": 200,
        "success": True,
        "result": {
            "leaderboard": {"regular": regular},
            "participants": [{"raw": "x" * 1000}] * 1000,
        },
    }
    client = FakeClient([schedule, schedule, leaderboard])

    result = naver_sports.run(
        mode="live",
        category="golf",
        date="2026-07-28",
        limit=4,
        client=client,
    )
    encoded = naver_sports.dumps_bounded(result)

    assert len(result["items"]) == 1
    assert len(result["items"][0]["leaders"]) == 4
    assert "participants" not in result["items"][0]["leaders"][0]
    assert "must-not-leak" not in encoded
    assert len(encoded) <= 7000


def test_esports_live_normalizes_epoch_and_team_score():
    match = {
        "gameId": "lol-1",
        "leagueId": "lck_2026",
        "gameCode": "lol",
        "startDate": 1785232800000,
        "homeScore": 1,
        "awayScore": 2,
        "matchStatus": "STARTED",
        "currentMatchSet": 4,
        "maxMatchCount": 5,
        "homeTeam": {"name": "T1"},
        "awayTeam": {"name": "젠지"},
    }
    payload = {"code": 200, "content": [match]}
    result = naver_sports.run(
        mode="live",
        category="lol",
        date="2026-07-28",
        limit=5,
        client=FakeClient([payload, payload]),
    )

    item = result["items"][0]
    assert item["participants"]["home"]["name"] == "T1"
    assert item["score"] == {"away": 2, "home": 1}
    assert item["league_id"] == "lck_2026"
    assert item["started_at"].endswith("+09:00")


def test_team_standings_select_last_enabled_season_and_project_fields():
    seasons = {
        "code": 200,
        "success": True,
        "result": {
            "seasons": [
                {
                    "seasonCode": "2025",
                    "title": "2025",
                    "startDate": "20250301",
                    "endDate": "20251001",
                    "isEnable": "Y",
                },
                {
                    "seasonCode": "2026",
                    "title": "2026",
                    "startDate": "20260301",
                    "endDate": "20261001",
                    "isEnable": "Y",
                },
            ]
        },
    }
    teams = {
        "code": 200,
        "success": True,
        "result": {
            "seasonTeamStats": [
                {
                    "ranking": 1,
                    "teamName": "삼성",
                    "gameCount": 90,
                    "winGameCount": 55,
                    "drawnGameCount": 2,
                    "loseGameCount": 33,
                    "wra": 0.625,
                    "gameBehind": 0,
                    "ignoredHugeField": "x" * 10000,
                }
            ]
        },
    }
    result = naver_sports.run(
        mode="standings",
        category="kbo",
        season="auto",
        limit=10,
        client=FakeClient([seasons, teams]),
    )

    assert result["season"] == {
        "code": "2026",
        "title": "2026",
        "start": "2026-03-01",
        "end": "2026-10-01",
    }
    assert result["items"] == [
        {
            "rank": 1,
            "team": "삼성",
            "played": 90,
            "wins": 55,
            "draws": 2,
            "losses": 33,
            "win_rate": 0.625,
            "games_behind": 0,
        }
    ]
    assert "answer" not in result
    assert len(naver_sports.dumps_bounded(result)) <= 7000


def test_explicit_unknown_standings_season_remains_invalid_argument():
    seasons = {
        "code": 200,
        "success": True,
        "result": {
            "seasons": [
                {
                    "seasonCode": "2026",
                    "title": "2026 KBO",
                    "isEnable": "Y",
                }
            ]
        },
    }
    client = FakeClient([seasons])

    result = naver_sports.run(
        mode="standings",
        category="kbo",
        season="unknown-season",
        client=client,
    )

    assert result["ok"] is False
    assert result["side_effect"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert len(client.urls) == 1


def test_exact_production_cli_season_auto_selects_active_season(
    monkeypatch, capsys
):
    seasons = {
        "code": 200,
        "success": True,
        "result": {
            "seasons": [
                {
                    "seasonCode": "2025",
                    "title": "2025 KBO",
                    "isEnable": "Y",
                },
                {
                    "seasonCode": "2026",
                    "title": "2026 KBO",
                    "isEnable": "Y",
                },
            ]
        },
    }
    teams = {
        "code": 200,
        "success": True,
        "result": {
            "seasonTeamStats": [
                {"ranking": 1, "teamName": "LG", "winGameCount": 60},
                {"ranking": 2, "teamName": "한화", "winGameCount": 58},
                {"ranking": 3, "teamName": "롯데", "winGameCount": 55},
            ]
        },
    }
    client = FakeClient([seasons, teams])
    monkeypatch.setattr(naver_sports, "SportsClient", lambda: client)

    exit_code = naver_sports.main(
        [
            "--mode",
            "standings",
            "--category",
            "kbo",
            "--date",
            "today",
            "--season",
            "auto",
            "--limit",
            "10",
            "--json",
        ]
    )
    payload = json.loads(capsys.readouterr().out)

    assert exit_code == 0
    assert payload["ok"] is True
    assert payload["side_effect"] is False
    assert payload["season"]["code"] == "2026"
    assert len(payload["items"]) == 3
    assert "answer" not in payload


def test_production_asset_gate_fails_when_helper_rejects_season_auto(
    tmp_path,
    monkeypatch,
):
    from scripts.install_naver_sports_skill import install

    skill_dir = install(tmp_path / "global")
    original = naver_sports._select_season

    def reject_auto(seasons, requested):
        if str(requested or "").strip().casefold() == "auto":
            raise naver_sports.SportsError(
                "INVALID_ARGUMENT",
                "mutation fixture rejects the documented sentinel",
            )
        return original(seasons, requested)

    monkeypatch.setattr(naver_sports, "_select_season", reject_auto)

    with pytest.raises(
        ProductionAssetValidationError,
        match="helper_not_ok",
    ) as captured:
        validate_production_asset(skill_dir)
    assert "mutation fixture" not in str(captured.value)


def test_production_asset_gate_enforces_requested_top_three(tmp_path):
    from scripts.install_naver_sports_skill import install

    skill_dir = install(tmp_path / "global")
    argv = (
        "--mode",
        "standings",
        "--category",
        "kbo",
        "--date",
        "today",
        "--season",
        "auto",
        "--limit",
        "3",
        "--json",
    )

    result = validate_production_asset(
        skill_dir,
        argv=argv,
        expected_result_limit=3,
    )

    assert result.evidence.requested_limit == 3
    assert result.evidence.item_count == 3
    assert len(result.payload["items"]) == 3
    assert "answer" not in result.payload
    assert all(item["rank"] <= 3 for item in result.payload["items"])


def test_production_asset_gate_rejects_bound_limit_drift_before_execution(tmp_path):
    from scripts.install_naver_sports_skill import install

    skill_dir = install(tmp_path / "global")
    argv = (
        "--mode",
        "standings",
        "--category",
        "kbo",
        "--date",
        "today",
        "--limit",
        "10",
        "--json",
    )

    with pytest.raises(ProductionAssetValidationError) as captured:
        validate_production_asset(
            skill_dir,
            argv=argv,
            expected_result_limit=3,
        )

    assert captured.value.code == "result_limit_drift"


def test_golf_player_standings_projection():
    seasons = {
        "code": 200,
        "success": True,
        "result": {
            "seasons": [
                {
                    "seasonCode": "2026",
                    "title": "2026",
                    "startDate": "20260115",
                    "endDate": "20261206",
                    "isEnable": "Y",
                }
            ]
        },
    }
    players = {
        "code": 200,
        "success": True,
        "result": {
            "seasonPlayerStats": [
                {
                    "rank": 1,
                    "playerName": "스코티 셰플러",
                    "earningsAmount": 1000,
                    "earningsCurrency": "$",
                    "scoringAvg": 68.5,
                    "played": 10,
                }
            ]
        },
    }
    result = naver_sports.run(
        mode="standings",
        category="pga",
        limit=10,
        client=FakeClient([seasons, players]),
    )

    assert result["items"][0] == {
        "rank": 1,
        "name": "스코티 셰플러",
        "earnings": 1000,
        "currency": "$",
        "scoring_avg": 68.5,
        "played": 10,
    }


def test_lck_standings_validates_league_and_projects_team():
    rankings = {
        "code": 200,
        "content": [
            {
                "leagueId": "lck_2026",
                "rank": 1,
                "wins": 15,
                "losses": 3,
                "draws": 0,
                "score": 21,
                "winRate": 0.83,
                "team": {"name": "한화생명e스포츠"},
            }
        ],
    }
    result = naver_sports.run(
        mode="standings",
        category="esports",
        season="2026",
        limit=10,
        client=FakeClient([rankings]),
    )

    assert result["season"]["code"] == "lck_2026"
    assert result["items"][0] == {
        "rank": 1,
        "team": "한화생명e스포츠",
        "wins": 15,
        "losses": 3,
        "draws": 0,
        "score": 21,
        "win_rate": 0.83,
    }


def test_lck_standings_auto_uses_current_season_not_lck_auto():
    current_year = naver_sports.datetime.now(naver_sports.KST).year
    league_id = f"lck_{current_year}"
    rankings = {
        "code": 200,
        "content": [
            {
                "leagueId": league_id,
                "rank": 1,
                "wins": 15,
                "losses": 3,
                "team": {"name": "한화생명e스포츠"},
            }
        ],
    }
    client = FakeClient([rankings])

    result = naver_sports.run(
        mode="standings",
        category="lck",
        season="auto",
        client=client,
    )

    assert result["ok"] is True
    assert result["season"]["code"] == league_id
    assert all("lck_auto" not in url for url in client.urls)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"mode": "live", "category": "unknown"}, "category"),
        (
            {"mode": "live", "category": "kbo", "date": "2026-99-99"},
            "YYYY-MM-DD",
        ),
        ({"mode": "live", "category": "kbo", "limit": 0}, "1..20"),
        ({"mode": "live", "category": "kbo", "limit": 21}, "1..20"),
    ],
)
def test_invalid_input_returns_bounded_json_without_fetch(kwargs, message):
    client = FakeClient([])
    result = naver_sports.run(client=client, **kwargs)
    encoded = naver_sports.dumps_bounded(result)

    assert result["ok"] is False
    assert result["error"]["code"] == "INVALID_ARGUMENT"
    assert message in result["error"]["message"]
    assert result["items"] == []
    assert client.urls == []
    assert "Traceback" not in encoded
    assert len(encoded) <= 7000


def test_url_allowlist_blocks_http_and_foreign_hosts():
    with pytest.raises(naver_sports.SportsError) as insecure:
        naver_sports.validate_url("http://api-gw.sports.naver.com/test")
    assert insecure.value.code == "URL_BLOCKED"

    with pytest.raises(naver_sports.SportsError) as foreign:
        naver_sports.validate_url("https://example.com/redirect")
    assert foreign.value.code == "URL_BLOCKED"


@pytest.mark.parametrize(
    ("error", "code", "retryable"),
    [
        (
            pytest.param(
                Exception("placeholder"),
                "INTERNAL_ERROR",
                False,
                id="unexpected",
            )
        ),
    ],
)
def test_unexpected_errors_are_stable_and_do_not_leak_traceback(
    error, code, retryable
):
    result = naver_sports.run(
        mode="live",
        category="kbo",
        date="2026-07-28",
        client=FakeClient([error]),
    )
    encoded = naver_sports.dumps_bounded(result)

    assert result["ok"] is False
    assert result["error"]["code"] == code
    assert result["error"]["retryable"] is retryable
    assert "Traceback" not in encoded
    assert len(encoded) <= 7000


def test_all_outputs_keep_public_schema_keys():
    result = naver_sports.run(
        mode="live",
        category="kbo",
        date="2026-07-28",
        client=FakeClient([sports_response(), sports_response()]),
    )

    assert {
        "ok",
        "side_effect",
        "source",
        "mode",
        "category",
        "season",
        "date",
        "fetched_at",
        "freshness",
        "items",
        "warnings",
        "error",
    }.issubset(result)
    assert result["ok"] is True
    assert result["side_effect"] is False
    assert result["items"] == []
    assert result["message"]


def test_error_and_compact_outputs_keep_explicit_no_effect_contract():
    error = naver_sports.run(
        mode="live",
        category="unknown",
        client=FakeClient([]),
    )

    assert error["ok"] is False
    assert error["side_effect"] is False

    oversized = {**error, "unpruned": "x" * 10_000}
    compact = json.loads(naver_sports.dumps_bounded(oversized))

    assert compact["ok"] is False
    assert compact["side_effect"] is False
    assert compact["error"]["code"] == "OUTPUT_TOO_LARGE"


def test_cli_prints_exactly_one_json_document(capsys):
    exit_code = naver_sports.main(
        [
            "--mode",
            "live",
            "--category",
            "unknown",
            "--json",
        ]
    )
    stdout = capsys.readouterr().out

    assert exit_code == 0
    parsed = json.loads(stdout)
    assert parsed["ok"] is False
    assert stdout.count("\n") == 1
