from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_RULES = (REPO_ROOT / "AGENTS.md").read_text(encoding="utf-8")


def test_single_writer_and_clean_handoff_contract() -> None:
    assert "Single-writer invariant" in AGENT_RULES
    assert "한 이슈·브랜치·worktree에는 정확히 한 명의 active writer" in AGENT_RULES
    assert "uncommitted partial 변경은 이어받지 않는다" in AGENT_RULES
    assert "clean commit/PR" in AGENT_RULES
    assert "patch artifact" in AGENT_RULES
    assert "clean `origin/dev` 기반의 격리 worktree에서 재시작" in AGENT_RULES


def test_operator_and_dev_stage_ownership_contract() -> None:
    assert "Stage A 의 기본 책임자는 Operator/Planning" in AGENT_RULES
    assert "개발 이슈는 계획 단계부터 `Dev Agent` 에 할당" in AGENT_RULES
    assert "명시 승인하지 않은 한 Stage B 에 진입" in AGENT_RULES
    assert "Operator/Hermes 의 Stage B 진입은 위 명시 승인 조건" in AGENT_RULES


def test_plain_comment_trigger_and_active_run_comment_ban() -> None:
    assert "plain member comment도" in AGENT_RULES
    assert "`kind=comment` run을 enqueue" in AGENT_RULES
    assert "`queued|running` direct/comment run" in AGENT_RULES
    assert "ack/status/stand-down 코멘트 금지" in AGENT_RULES


def test_active_run_requires_read_only_stand_down() -> None:
    assert "`queued|running` 상태의 `direct|comment` run" in AGENT_RULES
    assert "즉시 read-only stand-down" in AGENT_RULES
    assert "comment/rerun/parallel implementation을 하지 않는다" in AGENT_RULES
    assert "`issue get` / `issue runs` / `run-messages` / GitHub read-only 조회" in AGENT_RULES
    assert "active run issue에 모니터링 상태 코멘트를 남기지 않는다" in AGENT_RULES


def test_back_merge_requires_tree_diff_and_operator_approval() -> None:
    assert "`main → dev` back-merge gate" in AGENT_RULES
    assert "commit count나 ancestry만으로" in AGENT_RULES
    assert "git rev-parse origin/dev^{tree}" in AGENT_RULES
    assert "git rev-parse origin/main^{tree}" in AGENT_RULES
    assert "git diff --name-status origin/dev..origin/main" in AGENT_RULES
    assert "tree SHA가 같거나 실제 diff가 없으면 sync PR을 만들지 않는다" in AGENT_RULES
    assert "운영자가 승인한 경우에만" in AGENT_RULES


def test_nontrivial_changes_require_review_without_dev_self_merge() -> None:
    required_kinds = "코드, dependency, runtime, security, migration, CI-policy 변경"
    assert required_kinds in AGENT_RULES
    assert "항상 `in_review` 를 거쳐야 하며 Dev Agent 가 self-merge 하지 않는다" in AGENT_RULES
    assert "명시 승인한 docs/metadata-only 변경으로 제한" in AGENT_RULES


def test_canonical_branch_flow_and_merge_modes_remain_explicit() -> None:
    assert "feature/biz-NNN-<slug>" in AGENT_RULES
    assert "dev  ──(PR, Merge commit)──>  main" in AGENT_RULES
    assert "| `feature/*` → `dev` | **Squash and merge**" in AGENT_RULES
    assert "| `dev` → `main` | **Create a merge commit**" in AGENT_RULES
