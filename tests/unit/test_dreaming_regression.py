"""BIZ-75 — Dreaming 품질 회귀 테스트.

부모: BIZ-66 §3 / BIZ-72(A) + BIZ-73(B) 위에 얹는 회귀 가드.

본 파일은 dreaming 파이프라인이 향후 변경되어도 다음 네 가지 invariant이
깨지지 않음을 픽스처 기반으로 보장한다:

1. **managed-외 영역 100% 보존** — 사용자 정체성·캘린더·디렉토리 규약 같은 마커
   외부 콘텐츠는 dreaming 사이클 전후 byte-for-byte 동일.
2. **단일 관측 confidence 가드** — 한 회차에 1번 관측된 인사이트가 ``confidence ≥ 0.7``
   로 승격되지 않음(BIZ-66 §2의 "단발 관측 과일반화" 회귀 방지).
3. **보호 섹션 destructive overwrite 시 fail-closed** — 마커 누락/malformed 등으로
   안전한 쓰기가 불가능한 경우, 4종 파일 어느 것도 변경되지 않고 사이클 abort.
4. **기존 인사이트 보존** — 이번 회차에 reinforcement(같은 topic 재관측) 가 없는
   기존 인사이트도 임의로 사라지지 않는다(decay/archive 는 BIZ-78에서 별도 처리).

또한 모든 시나리오는 ``enable_clusters`` (Phase 3 클러스터링) 토글 양쪽에서
동일하게 통과해야 한다 — BIZ-28 활성화 후에도 본 가드가 무너지지 않도록.

설계 원칙:
- 모든 입력은 ``tests/fixtures/dreaming/`` 의 실제 파일에서 로드한다(시각적으로 검수 가능).
- LLM 응답은 결정론적으로 mock — 외부 호출 없음, 매 실행 동일 결과.
- 픽스처는 *복사본*을 ``tmp_path`` 로 옮긴 뒤 사용 — 원본 픽스처가 절대 변경되지 않게 한다.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from simpleclaw.memory.clustering import IncrementalClusterer
from simpleclaw.memory.conversation_store import ConversationStore
from simpleclaw.memory.dreaming import DreamingPipeline
from simpleclaw.memory.insights import InsightStore
from simpleclaw.memory.models import ConversationMessage, MessageRole


# ──────────────────────────────────────────────────────────
# 픽스처 로딩 헬퍼
# ──────────────────────────────────────────────────────────

_FIXTURES_DIR = Path(__file__).parent.parent / "fixtures" / "dreaming"
_FIXTURE_FILES = ("MEMORY.md", "USER.md", "AGENT.md", "SOUL.md")
_CONVERSATION_FILE = "conversation.jsonl"
_EXISTING_INSIGHTS_FILE = "existing_insights.jsonl"


def _copy_fixtures(dest: Path) -> dict[str, Path]:
    """``tests/fixtures/dreaming/`` 의 4종 markdown 파일을 ``dest`` 로 복사하고 경로를 반환.

    원본 픽스처를 보호하기 위해 항상 사본을 만든다 — 테스트가 잘못 동작해도
    리포지토리의 픽스처는 절대 변경되지 않는다.
    """
    out: dict[str, Path] = {}
    for name in _FIXTURE_FILES:
        src = _FIXTURES_DIR / name
        dst = dest / name
        shutil.copy2(src, dst)
        out[name] = dst
    return out


def _load_conversation() -> list[ConversationMessage]:
    """fixture conversation 을 ``ConversationMessage`` 리스트로 로드."""
    path = _FIXTURES_DIR / _CONVERSATION_FILE
    msgs: list[ConversationMessage] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        d = json.loads(line)
        role = MessageRole(d["role"])
        msgs.append(ConversationMessage(role=role, content=d["content"]))
    return msgs


def _seed_conversation_store(
    store: ConversationStore,
    *,
    with_embeddings: bool = False,
) -> list[int]:
    """fixture conversation 을 store 에 적재한다. 반환값은 message rowid 리스트.

    ``with_embeddings=True`` 면 같은 클러스터로 묶이도록 비슷한 임베딩을 부여
    (Phase 3 클러스터 모드 회귀 검증용).
    """
    ids: list[int] = []
    for idx, msg in enumerate(_load_conversation()):
        mid = store.add_message(msg)
        ids.append(mid)
        if with_embeddings:
            # 6개 메시지를 모두 한 클러스터(첫 축)로 부착되게 — cluster 모드에서도
            # journal 모드와 같은 본문 분포 위에서 가드가 작동함을 보이기 위함.
            store.add_embedding(mid, [1.0, 0.05 * idx, 0.0])
    return ids


def _outside_text(file_path: Path, *split_markers: str) -> str:
    """파일 본문에서 모든 managed marker *이전*의 영역과 *이후*의 영역을 합친 문자열을 반환.

    "outside" 정의 = managed 마커 (시작/끝 모두) 와 그 본문 사이를 모두 잘라낸 나머지.
    이 문자열이 dreaming 사이클 전후 동일하면 외부 영역 보존이 증명된다.
    """
    text = file_path.read_text(encoding="utf-8")
    # 단순화 — 알려진 managed 섹션 이름들의 "<!-- managed:dreaming:NAME -->" 부터
    # 짝이 되는 "<!-- /managed:dreaming:NAME -->" 까지를 모두 제거.
    import re

    pattern = re.compile(
        r"<!--\s*managed:dreaming:([A-Za-z0-9_-]+)\s*-->"
        r".*?"
        r"<!--\s*/managed:dreaming:\1\s*-->",
        re.DOTALL,
    )
    return pattern.sub("", text)


# ──────────────────────────────────────────────────────────
# Mock LLM 라우터 — 결정론적 응답
# ──────────────────────────────────────────────────────────

def _mock_router(payload: dict) -> MagicMock:
    """주어진 dict 를 JSON 직렬화하여 항상 같은 응답을 돌려주는 mock LLM router."""
    response = MagicMock()
    response.text = json.dumps(payload, ensure_ascii=False)
    router = MagicMock()
    router.send = AsyncMock(return_value=response)
    return router


def _single_observation_payload() -> dict:
    """이번 회차에 단발(1회) 관측되는 7개 인사이트를 LLM 이 추출했다고 가정.

    BIZ-66 §1의 USER.md 4-28 인사이트 패턴(한 번의 뉴스/가격 조회를 \"지속적 관심\"
    으로 과일반화) 을 그대로 시뮬레이션한다 — 모두 evidence_count=1 이므로 어느
    하나도 confidence ≥ 0.7 로 승격되어선 안 된다.
    """
    meta = [
        {"topic": "정치뉴스", "text": "정치 뉴스와 시사 안보 이슈에 지속적인 관심"},
        {"topic": "맥북에어가격", "text": "맥북에어 15인치 구매 고려"},
        {"topic": "BIZ-72 리뷰", "text": "PR 리뷰 요청에 적극적"},
    ]
    user_insights_text = "\n".join(f"- {m['text']}" for m in meta)
    return {
        "memory": "## 2026-05-03\n- 단발 관측 시뮬레이션 회차",
        "user_insights": user_insights_text,
        "user_insights_meta": meta,
        "soul_updates": "",
        "agent_updates": "",
    }


def _new_topic_payload() -> dict:
    """기존 인사이트와 겹치지 않는 단일 신규 topic 회차.

    test #4(기존 인사이트 보존) 에서 사용 — 이번 회차의 신규 topic 1개가
    sidecar 에 추가되어도 기존 3개 topic 이 모두 살아있어야 한다.
    """
    return {
        "memory": "## 2026-05-03\n- 신규 topic only",
        "user_insights": "- 새로운 단발 관심사",
        "user_insights_meta": [
            {"topic": "신규주제", "text": "새로운 단발 관심사"},
        ],
        "soul_updates": "",
        "agent_updates": "",
    }


# ──────────────────────────────────────────────────────────
# 공용 픽스처 + 헬퍼
# ──────────────────────────────────────────────────────────

@pytest.fixture
def workspace(tmp_path):
    """4종 markdown 파일 + ConversationStore + DreamingPipeline 를 묶은 workspace.

    ``enable_clusters`` 는 기본 False(레거시 append 모드). 클러스터 모드 검증은
    파라미터화로 분리 — ``cluster_workspace`` fixture 사용.
    """
    files = _copy_fixtures(tmp_path)
    store = ConversationStore(tmp_path / "conv.db")
    pipeline = DreamingPipeline(
        store,
        files["MEMORY.md"],
        user_file=files["USER.md"],
        soul_file=files["SOUL.md"],
        agent_file=files["AGENT.md"],
        # insights_file 미지정 → user_file 옆 ``insights.jsonl`` 로 자동 결정.
        insight_promotion_threshold=3,
        # BIZ-79: 본 회귀 가드는 sidecar 직접 적재 경로를 검증한다. 새 dry_run 기본값을
        # 회귀 의미와 분리하기 위해 명시적으로 옵트아웃 — dry_run 자체는 별도 모듈에서 검증.
        # dry_run 비활성 — suggestions_file 미지정으로 레거시 모드 사용.
    )
    return {
        "tmp_path": tmp_path,
        "store": store,
        "pipeline": pipeline,
        "files": files,
        "insights_path": tmp_path / "insights.jsonl",
        "enable_clusters": False,
    }


@pytest.fixture
def cluster_workspace(tmp_path):
    """Phase 3 클러스터 모드(``enable_clusters=True``) workspace.

    동일 픽스처를 사용하되, 파이프라인은 ``IncrementalClusterer`` 를 받고
    cluster 섹션(``managed:dreaming:clusters``) 을 사용한다 (MEMORY.md 픽스처에는
    journal/clusters 두 marker 가 모두 들어 있어 토글 양쪽에서 같은 파일을 쓸 수 있다).
    """
    files = _copy_fixtures(tmp_path)
    store = ConversationStore(tmp_path / "conv.db")
    clusterer = IncrementalClusterer(threshold=0.75)
    pipeline = DreamingPipeline(
        store,
        files["MEMORY.md"],
        user_file=files["USER.md"],
        soul_file=files["SOUL.md"],
        agent_file=files["AGENT.md"],
        clusterer=clusterer,
        enable_clusters=True,
        insight_promotion_threshold=3,
        # BIZ-79: cluster 모드도 동일하게 sidecar 직접 적재 경로 가드 — dry_run 옵트아웃.
        # dry_run 비활성 — suggestions_file 미지정으로 레거시 모드 사용.
    )
    return {
        "tmp_path": tmp_path,
        "store": store,
        "pipeline": pipeline,
        "files": files,
        "insights_path": tmp_path / "insights.jsonl",
        "enable_clusters": True,
    }


def _snapshot(files: dict[str, Path]) -> dict[str, str]:
    """4종 파일 현재 상태를 dict 로 스냅샷 (파일명 → 본문)."""
    return {name: p.read_text(encoding="utf-8") for name, p in files.items()}


# ──────────────────────────────────────────────────────────
# 회귀 가드 #1: managed 외 영역 100% 보존
# ──────────────────────────────────────────────────────────

class TestGuard1_OutsidePreservation:
    """managed 마커 외부 영역은 dreaming 사이클 전후 byte-for-byte 동일해야 한다.

    BIZ-66 §1의 AGENT.md 30→2줄 사고를 회귀 시나리오로 고정. 본 테스트는 USER.md
    의 정체성/캘린더, AGENT.md 의 integrations/디렉토리 규약, MEMORY.md 의 정적
    사실을 모두 outside 로 두고, dreaming 이 cluster 모드 여부와 무관하게 그
    바이트들을 변경하지 않음을 보인다.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ws_fixture", ["workspace", "cluster_workspace"])
    async def test_outside_bytes_unchanged_after_dreaming(
        self, ws_fixture, request
    ):
        ws = request.getfixturevalue(ws_fixture)

        # 사이클 전: 4종 파일 outside 영역(마커 사이를 모두 제거한 텍스트) 캡처
        before = {
            name: _outside_text(p) for name, p in ws["files"].items()
        }

        ws["pipeline"]._router = _mock_router(_single_observation_payload())
        _seed_conversation_store(
            ws["store"], with_embeddings=ws["enable_clusters"]
        )

        await ws["pipeline"].run()

        # 사이클 후: outside 영역 다시 캡처
        after = {
            name: _outside_text(p) for name, p in ws["files"].items()
        }

        # 핵심 invariant — 단 1바이트도 변경되어선 안 된다.
        for name in _FIXTURE_FILES:
            assert after[name] == before[name], (
                f"{name} 의 managed 외부 영역이 변경됨 — outside preservation 위반"
            )

        # 그리고 정체성 키워드들이 outside 에 그대로 살아있어야 한다(positive 가드).
        assert "Calendar: ingki3@gmail.com" in after["USER.md"]
        assert "Google Calendar:" in after["AGENT.md"]
        assert "사칭하지 않는다" in after["AGENT.md"]
        assert "SimpleClaw 메인 사용자" in after["MEMORY.md"]
        assert "따뜻하지만" in after["SOUL.md"]


# ──────────────────────────────────────────────────────────
# 회귀 가드 #2: 단일 관측 confidence 캡
# ──────────────────────────────────────────────────────────

class TestGuard2_SingleObservationCap:
    """한 회차에 1번 관측된 인사이트는 ``confidence ≥ 0.7`` 로 승격되지 않는다.

    BIZ-66 §2의 \"단발 정치 뉴스 1회 → 지속적 관심\" 과일반화 패턴 회귀 방지.
    promotion_threshold 가 1로 잘못 설정돼도 단발 1회는 confidence 0.4 캡이 보장돼야
    한다(``compute_confidence`` 의 핵심 가드).
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ws_fixture", ["workspace", "cluster_workspace"])
    async def test_no_insight_promoted_after_single_observation(
        self, ws_fixture, request
    ):
        ws = request.getfixturevalue(ws_fixture)
        ws["pipeline"]._router = _mock_router(_single_observation_payload())
        _seed_conversation_store(
            ws["store"], with_embeddings=ws["enable_clusters"]
        )

        await ws["pipeline"].run()

        sidecar = InsightStore(ws["insights_path"])
        loaded = sidecar.load()
        # payload 의 3개 topic 이 모두 1회 관측 상태로 sidecar 에 적재돼야 한다.
        assert len(loaded) == 3, (
            f"sidecar 에 예상 3개 topic 대신 {len(loaded)} 개 적재됨"
        )

        for key, meta in loaded.items():
            assert meta.evidence_count == 1, (
                f"{key} evidence_count 가 1 이 아님: {meta.evidence_count}"
            )
            assert meta.confidence < 0.7, (
                f"{key} 가 단발 관측인데 승격됨 (confidence={meta.confidence})"
            )
            assert meta.confidence == pytest.approx(0.4), (
                f"{key} confidence 가 0.4 캡을 벗어남 ({meta.confidence})"
            )

    @pytest.mark.asyncio
    async def test_low_threshold_still_caps_single_observation(self, tmp_path):
        """promotion_threshold=1 로 설정해도 단발 관측은 여전히 0.4 로 캡된다.

        \"임계치를 1로 두면 1회 관측이 즉시 승격된다\"는 잘못된 가정이 코드에
        스며드는 것을 방지하는 회귀 가드. ``compute_confidence`` 가 evidence_count==1
        분기에서 무조건 0.4 를 반환해야 통과한다.
        """
        files = _copy_fixtures(tmp_path)
        store = ConversationStore(tmp_path / "conv.db")
        pipeline = DreamingPipeline(
            store,
            files["MEMORY.md"],
            user_file=files["USER.md"],
            soul_file=files["SOUL.md"],
            agent_file=files["AGENT.md"],
            insight_promotion_threshold=1,  # 가장 공격적 임계치
        )
        pipeline._router = _mock_router(_single_observation_payload())
        _seed_conversation_store(store)

        await pipeline.run()

        sidecar = InsightStore(tmp_path / "insights.jsonl")
        for key, meta in sidecar.load().items():
            assert meta.evidence_count == 1
            assert meta.confidence == pytest.approx(0.4), (
                f"{key} threshold=1 환경에서도 단발 cap 유지돼야 함"
            )


# ──────────────────────────────────────────────────────────
# 회귀 가드 #3: 보호 섹션 destructive overwrite 시 fail-closed
# ──────────────────────────────────────────────────────────

class TestGuard3_FailClosedOnDestructiveOverwrite:
    """managed 마커가 누락/malformed 인 상황에서 dreaming 은 4종 파일 모두 보존한다.

    \"한 파일 손상이 다른 파일까지 손상시키는\" 부분 변경 위험을 차단한다.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ws_fixture", ["workspace", "cluster_workspace"])
    async def test_missing_marker_aborts_entire_cycle(
        self, ws_fixture, request
    ):
        """USER.md 의 marker 만 통째로 제거 → 4종 파일 모두 변경 없음."""
        ws = request.getfixturevalue(ws_fixture)
        # USER.md 에서 marker 두 줄을 모두 제거(outside 만 남김).
        user_path = ws["files"]["USER.md"]
        sanitized = (
            user_path.read_text(encoding="utf-8")
            .replace("<!-- managed:dreaming:insights -->\n", "")
            .replace("<!-- /managed:dreaming:insights -->\n", "")
        )
        user_path.write_text(sanitized, encoding="utf-8")

        before = _snapshot(ws["files"])
        ws["pipeline"]._router = _mock_router(_single_observation_payload())
        _seed_conversation_store(
            ws["store"], with_embeddings=ws["enable_clusters"]
        )

        result = await ws["pipeline"].run()

        # 사이클이 abort 되어 None 반환.
        assert result is None
        # 4종 파일 모두 byte-for-byte 동일.
        after = _snapshot(ws["files"])
        assert after == before
        # LLM 이 던진 본문 키워드가 어느 파일에도 새지 않음.
        for content in after.values():
            assert "지속적인 관심" not in content
            assert "맥북에어 15인치 구매" not in content

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ws_fixture", ["workspace", "cluster_workspace"])
    async def test_malformed_marker_aborts_entire_cycle(
        self, ws_fixture, request
    ):
        """SOUL.md 의 closing marker 만 남기고 opening 을 제거 → fail-closed."""
        ws = request.getfixturevalue(ws_fixture)
        soul_path = ws["files"]["SOUL.md"]
        broken = soul_path.read_text(encoding="utf-8").replace(
            "<!-- managed:dreaming:dreaming-updates -->\n", ""
        )
        soul_path.write_text(broken, encoding="utf-8")

        before = _snapshot(ws["files"])
        ws["pipeline"]._router = _mock_router(_single_observation_payload())
        _seed_conversation_store(
            ws["store"], with_embeddings=ws["enable_clusters"]
        )

        result = await ws["pipeline"].run()
        assert result is None
        assert _snapshot(ws["files"]) == before


# ──────────────────────────────────────────────────────────
# 회귀 가드 #4: 기존 인사이트가 reinforcement 없이 사라지지 않음
# ──────────────────────────────────────────────────────────

class TestGuard4_ExistingInsightsSurviveWithoutReinforcement:
    """이번 회차에 다시 언급되지 않은 기존 인사이트도 sidecar 에 그대로 남아야 한다.

    BIZ-66 §2 후속 — 사용자가 한참 안 다룬 주제(SimpleClaw 빌드 등) 도 회상에서
    \"방금 언급되지 않았다\" 는 이유로 폐기되어선 안 된다. 명시적 decay/archive 정책은
    BIZ-78(C) 에서 도입되며, 그 전까지는 \"reinforcement 없으면 그대로 보존\" 이 기본.
    """

    def _seed_existing_sidecar(self, dest: Path) -> int:
        """fixture 의 기존 insights.jsonl 을 ``dest`` 로 복사. 적재된 항목 수 반환."""
        src = _FIXTURES_DIR / _EXISTING_INSIGHTS_FILE
        shutil.copy2(src, dest)
        # JSONL 줄 수가 곧 항목 수(빈 줄 무시).
        return sum(1 for line in src.read_text("utf-8").splitlines() if line.strip())

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ws_fixture", ["workspace", "cluster_workspace"])
    async def test_existing_insights_remain_after_unrelated_run(
        self, ws_fixture, request
    ):
        ws = request.getfixturevalue(ws_fixture)
        seeded_count = self._seed_existing_sidecar(ws["insights_path"])
        assert seeded_count == 3  # 픽스처 sanity check

        # 사이클 전 sidecar 스냅샷 — 같은 객체 식별이 아니라 dict equality 로 비교.
        sidecar = InsightStore(ws["insights_path"])
        before = {
            key: (m.topic, m.text, m.evidence_count, m.confidence)
            for key, m in sidecar.load().items()
        }

        # 이번 회차는 *새 topic 1개* 만 추출 — 기존 3개 topic 과 정규형 비교 시 겹치지 않음.
        ws["pipeline"]._router = _mock_router(_new_topic_payload())
        _seed_conversation_store(
            ws["store"], with_embeddings=ws["enable_clusters"]
        )

        await ws["pipeline"].run()

        after_loaded = sidecar.load()
        # 신규 topic 이 추가되었는지 확인.
        assert "신규주제" in after_loaded
        assert after_loaded["신규주제"].evidence_count == 1

        # 핵심 invariant: 기존 3개 topic 모두 그대로 남아 있어야 한다.
        for key, snapshot in before.items():
            assert key in after_loaded, (
                f"기존 인사이트 '{key}' 가 reinforcement 없이 사라짐"
            )
            cur = after_loaded[key]
            cur_tuple = (cur.topic, cur.text, cur.evidence_count, cur.confidence)
            assert cur_tuple == snapshot, (
                f"기존 인사이트 '{key}' 의 데이터가 변형됨: {snapshot} → {cur_tuple}"
            )

    @pytest.mark.asyncio
    async def test_existing_insight_is_reinforced_when_observed_again(
        self, workspace
    ):
        """positive 가드 — 같은 topic 이 다시 관측되면 evidence_count 가 누적된다.

        \"보존\" 이 \"freeze\" 로 해석되어 reinforcement 가 무시되는 회귀를 방지.
        Guard #4 의 의미가 \"절대 변경 금지\" 가 아니라 \"임의 폐기 금지\" 임을 명시.
        """
        ws = workspace
        # 기존 sidecar 적재.
        self._seed_existing_sidecar(ws["insights_path"])

        # 이번 회차에 \"한국어 우선\" topic 을 다시 관측.
        ws["pipeline"]._router = _mock_router({
            "memory": "## 2026-05-03\n- ko reinforcement",
            "user_insights": "- 한국어로 응답을 선호",
            "user_insights_meta": [
                {"topic": "한국어 우선", "text": "한국어 응답 강하게 선호"},
            ],
            "soul_updates": "",
            "agent_updates": "",
        })
        _seed_conversation_store(ws["store"])

        await ws["pipeline"].run()

        loaded = InsightStore(ws["insights_path"]).load()
        ko = loaded.get("한국어우선")
        assert ko is not None
        # 픽스처에서 evidence_count=4 → 5 로 증가해야 한다.
        assert ko.evidence_count == 5
        # text 는 최신 관측으로 갱신.
        assert ko.text == "한국어 응답 강하게 선호"


# ──────────────────────────────────────────────────────────
# 회귀 가드 #5 (BIZ-104): doc 코멘트 안 마커 토큰을 포함한 파일에서도 dreaming 정상 진행
# ──────────────────────────────────────────────────────────

class TestGuard5_DocCommentMarkerTolerance:
    """파일 상단 doc 주석 안에 마커 사용 예시가 *문자 그대로* 들어 있어도 dreaming 이
    정상 진행돼야 한다.

    BIZ-104 회귀 시나리오:
        ``.agent/MEMORY.md`` 의 운영자 starter 템플릿이 doc 주석 안에 마커 예시
        (``<!-- managed:dreaming:journal -->`` 등) 를 그대로 적었던 사고가 있었다.
        그 결과 ``find_managed_sections`` 가 같은 이름의 섹션을 두 번 잡아
        ``ProtectedSectionMalformed`` 을 던지고 사이클 전체가 ``preflight_failed`` 로
        skip 되었다.

    1차 안전망(템플릿 escape) 외에 2차 안전망 — 운영자가 doc 에 또 실수로 마커
    토큰을 적어도 dreaming 이 막히지 않는다 — 을 본 가드로 고정한다.
    """

    def _inject_doc_marker_block(self, file_path: Path, sections: list[str]) -> None:
        """파일 최상단에 마커 토큰을 *문자 그대로* 포함한 doc 주석 블록을 주입한다.

        실 사고와 동일한 형태(outer ``<!-- ... -->`` 안에 inner ``<!-- managed:..-->`` 가
        등장) 를 만든다. 보호 영역(마커 외부) 보존 검증을 위해 기존 본문은
        그대로 보존된다.
        """
        original = file_path.read_text(encoding="utf-8")
        # 헤더 다음 줄에 doc 코멘트를 끼워넣는다 — 운영자가 starter template 에
        # 적는 위치와 동일.
        lines = original.splitlines(keepends=True)
        header_end = 0
        for idx, line in enumerate(lines):
            if line.startswith("# "):
                header_end = idx + 1
                break

        doc_block = ["<!--\n", "이 파일의 두 영역(BIZ-104 회귀 시나리오):\n"]
        for n in sections:
            doc_block.append(
                f"- <!-- managed:dreaming:{n} --> ~ "
                f"<!-- /managed:dreaming:{n} -->: dreaming 갱신 영역\n"
            )
        doc_block.append("-->\n\n")

        new_lines = lines[:header_end] + ["\n"] + doc_block + lines[header_end:]
        file_path.write_text("".join(new_lines), encoding="utf-8")

    @pytest.mark.asyncio
    @pytest.mark.parametrize("ws_fixture", ["workspace", "cluster_workspace"])
    async def test_dreaming_completes_with_doc_comment_markers(
        self, ws_fixture, request
    ):
        ws = request.getfixturevalue(ws_fixture)

        # 4종 파일 모두에 doc 주석 블록을 주입 — 한 파일이라도 preflight 가
        # malformed 로 잡아내면 사이클 전체가 abort 되므로, 한 번에 모두 검증한다.
        self._inject_doc_marker_block(
            ws["files"]["MEMORY.md"], ["journal", "clusters"]
        )
        self._inject_doc_marker_block(ws["files"]["USER.md"], ["insights"])
        self._inject_doc_marker_block(
            ws["files"]["AGENT.md"], ["dreaming-updates"]
        )
        self._inject_doc_marker_block(
            ws["files"]["SOUL.md"], ["dreaming-updates"]
        )

        # 사이클 전 outside (doc 주석 포함) 스냅샷 — outside 영역의 byte-for-byte
        # 보존도 함께 검증한다.
        before_outside = {
            name: _outside_text(p) for name, p in ws["files"].items()
        }

        ws["pipeline"]._router = _mock_router(_single_observation_payload())
        _seed_conversation_store(
            ws["store"], with_embeddings=ws["enable_clusters"]
        )

        result = await ws["pipeline"].run()

        # 핵심 invariant — preflight 가 통과해 사이클이 완료됐다.
        # ``DreamingPipeline.run`` 은 정상 완료 시 ``MemoryEntry`` 를 돌려준다.
        # ``None`` 은 abort/skip 의미이므로 회귀 발생.
        assert result is not None, (
            "doc 주석 안 마커 토큰 때문에 사이클이 abort 됨 — BIZ-104 회귀"
        )

        # outside 영역(주입한 doc 주석 포함) 은 byte-for-byte 보존돼야 한다.
        after_outside = {
            name: _outside_text(p) for name, p in ws["files"].items()
        }
        for name in _FIXTURE_FILES:
            assert after_outside[name] == before_outside[name], (
                f"{name} outside 가 변경됨 — BIZ-104 가드 위반"
            )

        # managed 영역 안에는 새 본문이 잘 적용돼야 한다(LLM mock 가 던진 키워드
        # 가 USER.md insights 영역에 들어갔는지로 진행 여부를 양성 검증).
        user_text = ws["files"]["USER.md"].read_text(encoding="utf-8")
        assert "지속적인 관심" in user_text or "맥북에어 15인치 구매" in user_text, (
            "사이클이 진행됐는데 새 인사이트가 USER.md 에 반영되지 않음"
        )
