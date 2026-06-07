# ruff: noqa: F401,F403,F405
"""DreamingPipeline에서 분리한 단계별 service 함수.

이 모듈의 함수들은 ``DreamingPipeline`` 인스턴스 메서드로 바인딩된다.
기존 public surface와 사용자 데이터 schema를 유지하기 위해 동작 코드는 원본에서
보수적으로 이동만 하고, 의존성은 dreaming 모듈의 기존 전역을 재사용한다.
"""

from __future__ import annotations

from simpleclaw.memory.dreaming import *  # noqa: F403
from simpleclaw.memory import dreaming as _dreaming

AUTO_TRIGGER_MODE_DOWNWEIGHT = _dreaming.AUTO_TRIGGER_MODE_DOWNWEIGHT
AUTO_TRIGGER_MODE_EXCLUDE = _dreaming.AUTO_TRIGGER_MODE_EXCLUDE
_CLUSTER_MARKER_END = _dreaming._CLUSTER_MARKER_END
_CLUSTER_MARKER_START = _dreaming._CLUSTER_MARKER_START
_CLUSTER_SECTION_RE = _dreaming._CLUSTER_SECTION_RE
_VALID_AUTO_TRIGGER_MODES = _dreaming._VALID_AUTO_TRIGGER_MODES
_coerce_meta_items = _dreaming._coerce_meta_items
logger = _dreaming.logger
json = _dreaming.json
re = _dreaming.re
shutil = _dreaming.shutil
time = _dreaming.time
datetime = _dreaming.datetime
timedelta = _dreaming.timedelta

def assign_clusters_for_unprocessed(self) -> dict[int, list[ConversationMessage]]:
    """클러스터링되지 않은 메시지를 점진 할당하고 영향받은 클러스터별 멤버를 반환한다.

    과정:
    1. ``get_unclustered_with_embeddings()``로 후보 메시지를 얻는다.
    2. 각 메시지에 대해 ``IncrementalClusterer.find_nearest()`` 실행:
       - 임계값 이상이면 기존 클러스터에 부착(centroid·member_count incremental update).
       - 미만이면 신규 클러스터 생성(첫 멤버의 임베딩이 곧 centroid).
    3. ``messages.cluster_id``를 갱신하고, 영향받은 클러스터별로 그 회차에 새로 들어온 메시지 목록을 모은다.

    Returns:
        ``{cluster_id: [ConversationMessage, ...]}`` — 이번 회차에 갱신된 클러스터와 멤버.
        클러스터링이 비활성이거나 처리 대상이 없으면 빈 딕셔너리.
    """
    if not self._enable_clusters or self._clusterer is None:
        return {}

    unprocessed = self._store.get_unclustered_with_embeddings()
    if not unprocessed:
        return {}

    # 매 메시지마다 list_clusters를 다시 호출하지 않고 인메모리 캐시를 갱신한다.
    # 신규 클러스터를 만들면 캐시에도 추가하여 같은 회차의 후속 메시지가 그 클러스터에 부착될 수 있게 한다.
    clusters_cache: dict[int, ClusterRecord] = {
        c.id: c for c in self._store.list_clusters()
    }
    affected: dict[int, list[ConversationMessage]] = {}

    for mid, msg, embedding in unprocessed:
        try:
            assignment = self._clusterer.find_nearest(
                embedding, list(clusters_cache.values())
            )
        except ValueError as exc:
            # 0벡터 등 의미 없는 임베딩 — 스킵
            logger.warning("Skipping message %d: %s", mid, exc)
            continue

        if assignment.cluster_id is not None:
            # 기존 클러스터에 부착 — centroid는 누적 평균으로, member_count는 +1
            cluster = clusters_cache[assignment.cluster_id]
            new_centroid = self._clusterer.update_centroid(
                cluster.centroid, cluster.member_count, embedding
            )
            new_count = cluster.member_count + 1
            self._store.update_cluster(
                cluster.id,
                centroid=new_centroid,
                member_count=new_count,
            )
            # 캐시 동기화 — 같은 회차 후속 메시지가 본 centroid 기준으로 비교되도록
            clusters_cache[cluster.id] = ClusterRecord(
                id=cluster.id,
                label=cluster.label,
                centroid=new_centroid,
                summary=cluster.summary,
                member_count=new_count,
                updated_at=datetime.now(),
            )
            cid = cluster.id
        else:
            # 신규 클러스터 — 첫 멤버 임베딩을 centroid로
            cid = self._store.create_cluster(
                label="",  # 라벨은 LLM 요약 단계에서 채움
                centroid=embedding,
                summary="",
                member_count=1,
            )
            clusters_cache[cid] = ClusterRecord(
                id=cid,
                label="",
                centroid=embedding.copy(),
                summary="",
                member_count=1,
                updated_at=datetime.now(),
            )

        self._store.assign_cluster(mid, cid)
        affected.setdefault(cid, []).append(msg)

    return affected

async def summarize_cluster(
    self,
    messages: list[ConversationMessage],
    existing_label: str = "",
    existing_summary: str = "",
) -> dict[str, str]:
    """단일 클러스터의 신규 메시지를 받아 갱신된 라벨·요약을 반환한다.

    LLM 라우터가 없거나 호출이 실패하면 단순 폴백을 사용한다.

    Args:
        messages: 이번 회차에 이 클러스터에 부착된 메시지들.
        existing_label: 기존 라벨 (없으면 빈 문자열).
        existing_summary: 기존 요약 (없으면 빈 문자열).

    Returns:
        ``{"label": str, "summary": str}`` — 갱신된 라벨과 요약 본문.
    """
    if not messages:
        return {"label": existing_label, "summary": existing_summary}

    if self._router:
        try:
            return await self._summarize_cluster_with_llm(
                messages, existing_label, existing_summary
            )
        except Exception:
            logger.exception("LLM cluster summarization failed, using fallback")

    return self._summarize_cluster_fallback(
        messages, existing_label, existing_summary
    )

async def _summarize_cluster_with_llm(
    self,
    messages: list[ConversationMessage],
    existing_label: str,
    existing_summary: str,
) -> dict[str, str]:
    """LLM에게 클러스터 메시지를 분석시켜 갱신된 라벨/요약을 받는다.

    BIZ-299:
    - 프롬프트는 ``cluster.yaml`` (BIZ-298 로더) 에서 로드 — 운영자 override 우선.
    - ``LLMRequest.max_tokens`` 에 ``dreaming.max_tokens.cluster`` 값을 적용.
    - 입력 ``[:6000]`` 하드 truncation 제거 — 누락 backlog 가 길어도 그대로 흘려보낸다.
    - 호출 메트릭은 ``self._per_file_metrics["cluster_<cid?>"]`` 에 누적. cluster 갯수가
      가변이라 key 충돌을 피하기 위해 누적 횟수 suffix 를 붙인다.
    """
    conv_lines: list[str] = []
    for msg in messages:
        role = msg.role.value.upper()
        ts = msg.timestamp.strftime("%Y-%m-%d %H:%M")
        conv_lines.append(f"[{ts} {role}] {msg.content}")
    new_block = "\n".join(conv_lines)

    # 같은 사이클 안에서 여러 cluster 가 호출될 수 있다 — metric key 충돌 방지를
    # 위해 ``cluster_0``, ``cluster_1`` 식으로 누적 인덱스를 붙인다.
    suffix = sum(1 for k in self._per_file_metrics if k.startswith("cluster_"))
    metric_key = f"cluster_{suffix}"
    raw = await self._call_dreaming_llm_for_key(
        prompt_name="cluster",
        prompt_vars={
            "existing_label": existing_label or "(없음)",
            "existing_summary": existing_summary or "(없음)",
            "new_messages": new_block,
        },
        max_tokens_key="cluster",
        metric_key=metric_key,
    )
    return self._parse_cluster_result(raw, existing_label, existing_summary)

async def _call_dreaming_llm_for_key(
    self,
    *,
    prompt_name: str,
    prompt_vars: dict,
    max_tokens_key: str,
    metric_key: str,
) -> str:
    """``_call_dreaming_llm`` 의 cluster-친화 변형 — metric key 를 외부에서 지정.

    cluster 호출은 한 사이클에 여러 번 일어나므로 ``prompt_name`` 단일 키로는
    덮어쓰기가 발생한다. 이 헬퍼는 메트릭 키를 caller 가 결정한다.
    """
    from simpleclaw.llm.models import LLMRequest

    spec = load_dreaming_prompt(prompt_name)
    user_prompt = spec.format(**prompt_vars)

    max_tokens = self._max_tokens.get(max_tokens_key)
    request = LLMRequest(
        system_prompt=spec.system_prompt,
        user_message=user_prompt,
        backend_name=self._dreaming_model,
        max_tokens=max_tokens,
    )
    started = time.monotonic()
    try:
        response = await self._router.send(request)
    except Exception as exc:
        elapsed_ms = int((time.monotonic() - started) * 1000)
        self._per_file_metrics[metric_key] = {
            "duration_ms": elapsed_ms,
            "max_tokens": max_tokens,
            "error": f"{type(exc).__name__}: {exc}",
        }
        raise
    elapsed_ms = int((time.monotonic() - started) * 1000)
    usage = response.usage if isinstance(response.usage, dict) else {}
    metric: dict = {
        "duration_ms": elapsed_ms,
        "max_tokens": max_tokens,
    }
    if "input_tokens" in usage:
        metric["input_tokens"] = usage.get("input_tokens")
    if "output_tokens" in usage:
        metric["output_tokens"] = usage.get("output_tokens")
    self._per_file_metrics[metric_key] = metric
    return response.text.strip()

def _parse_cluster_result(
    self,
    raw: str,
    existing_label: str,
    existing_summary: str,
) -> dict[str, str]:
    """LLM 응답 JSON에서 label/summary를 추출한다.

    파싱 실패 시 기존 값을 유지하고 raw 텍스트 앞 200자를 summary로 폴백한다.
    """
    if "```" in raw:
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
        raw = raw.strip()

    try:
        data = json.loads(raw)
        label = (data.get("label") or existing_label or "").strip()
        summary = (data.get("summary") or existing_summary or "").strip()
        return {"label": label, "summary": summary}
    except json.JSONDecodeError:
        logger.warning("Failed to parse cluster JSON: %s", raw[:200])
        return {
            "label": existing_label,
            "summary": existing_summary or raw[:200],
        }

def _summarize_cluster_fallback(
    self,
    messages: list[ConversationMessage],
    existing_label: str,
    existing_summary: str,
) -> dict[str, str]:
    """LLM 없이 단순 텍스트 기반 클러스터 요약(메시지 첫 줄을 bullet로 나열).

    라벨은 기존값을 우선하며, 비어있으면 첫 메시지의 앞 8글자를 사용한다.
    """
    if existing_label:
        label = existing_label
    else:
        first_text = messages[0].content if messages else ""
        label = first_text[:8].strip() or "untagged"

    bullet_lines: list[str] = []
    if existing_summary.strip():
        bullet_lines.append(existing_summary.strip())
    for msg in messages:
        snippet = msg.content.replace("\n", " ").strip()[:80]
        if snippet:
            bullet_lines.append(f"- {snippet}")
    summary = "\n".join(bullet_lines)
    return {"label": label, "summary": summary}

def upsert_memory_section(
    self, cluster_id: int, label: str, summary: str
) -> None:
    """MEMORY.md의 ``<!-- cluster:N -->`` 섹션을 갱신하거나 신규 추가한다.

    BIZ-72: cluster 섹션은 ``<!-- managed:dreaming:clusters -->`` 컨테이너
    안쪽에서만 살아있다. 컨테이너가 없거나 잘못된 경우 ``ProtectedSectionError``를
    던져 호출자가 fail-closed 처리하게 한다. 컨테이너 외부의 사용자 콘텐츠
    (예: 정체성 메모, 수기 메모)는 절대 변경되지 않는다.

    규칙:
    - 컨테이너 내부에서 동일 ``cluster_id`` 마커가 있으면 본문만 교체.
    - 컨테이너 내부에 마커가 없으면 컨테이너 끝부분에 새 섹션 append.
    """
    if not self._memory_file.is_file():
        raise ProtectedSectionMissing(
            f"managed 파일이 존재하지 않음: {self._memory_file} "
            f"(section={self._cluster_section})"
        )

    existing = self._memory_file.read_text(encoding="utf-8")
    # 컨테이너 본문(즉, dreaming이 자유롭게 cluster 마커를 두를 수 있는 영역)을 읽어온다.
    container_body = get_section_body(existing, self._cluster_section)
    # 끝부분 빈 줄을 정규화 — 항상 단일 trailing newline 기준으로 작업해 새 섹션 append시
    # 인접 빈 줄이 끝없이 늘어나는 것을 방지.
    normalized_body = container_body.strip("\n")

    section_body = self._format_cluster_section_body(cluster_id, label, summary)
    start_marker = _CLUSTER_MARKER_START.format(cid=cluster_id)
    end_marker = _CLUSTER_MARKER_END.format(cid=cluster_id)
    new_block = f"{start_marker}\n{section_body}\n{end_marker}"

    section_re = re.compile(
        rf"{re.escape(start_marker)}\n?.*?\n?{re.escape(end_marker)}",
        re.DOTALL,
    )
    if section_re.search(normalized_body):
        updated_body = section_re.sub(new_block, normalized_body, count=1)
    else:
        # 신규 cluster — 컨테이너 끝에 빈 줄 한 칸 띄우고 append
        if normalized_body:
            updated_body = normalized_body + "\n\n" + new_block
        else:
            updated_body = new_block

    new_text = replace_section_body(
        existing, self._cluster_section, updated_body
    )
    if new_text != existing:
        self._memory_file.write_text(new_text, encoding="utf-8")
        logger.info(
            "Upserted cluster %d in memory file (managed section '%s')",
            cluster_id,
            self._cluster_section,
        )

def _format_cluster_section_body(
    cluster_id: int, label: str, summary: str
) -> str:
    """클러스터 섹션 본문을 사람이 읽기 좋은 마크다운으로 포맷한다."""
    header_label = label.strip() or f"cluster {cluster_id}"
    body = summary.strip() or "(no summary yet)"
    return f"## {header_label} (cluster {cluster_id})\n\n{body}"

async def _run_cluster_pipeline(self) -> str:
    """Phase 3 그래프형 드리밍 — 영향받은 클러스터를 LLM 요약으로 갱신하고 MEMORY.md를 upsert.

    Returns:
        이번 회차에 갱신된 클러스터 요약을 줄로 합친 텍스트(MemoryEntry용).
        영향받은 클러스터가 없으면 빈 문자열.
    """
    affected = self.assign_clusters_for_unprocessed()
    if not affected:
        return ""

    summaries: list[str] = []
    for cid, msgs in affected.items():
        cluster = self._store.get_cluster(cid)
        existing_label = cluster.label if cluster else ""
        existing_summary = cluster.summary if cluster else ""
        updated = await self.summarize_cluster(
            msgs, existing_label, existing_summary
        )
        new_label = updated.get("label", existing_label)
        new_summary = updated.get("summary", existing_summary)
        self._store.update_cluster(cid, label=new_label, summary=new_summary)
        updated_cluster = self._store.get_cluster(cid)
        if updated_cluster is not None:
            self._safe_sync_memory_items(
                "cluster_summary",
                sync_cluster_summary_to_memory_item,
                self._store,
                updated_cluster,
            )
        self.upsert_memory_section(cid, new_label, new_summary)
        summaries.append(f"[cluster {cid} · {new_label}]\n{new_summary}")
    return "\n\n".join(summaries)

