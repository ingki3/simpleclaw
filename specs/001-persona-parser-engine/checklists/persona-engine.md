# Requirements Quality Checklist: 페르소나 설정 파싱 엔진 및 프롬프트 인젝터

**Purpose**: Spec 및 Plan의 요구사항 품질을 검증하여 구현 전 리워크 리스크를 최소화
**Created**: 2026-04-17
**Feature**: [spec.md](../spec.md)
**Depth**: Standard | **Audience**: Reviewer | **Focus**: Completeness, Consistency, Edge Cases

## Requirement Completeness

- [ ] CHK001 - Are all three persona file types (AGENT, USER, MEMORY) explicitly enumerated with their expected content structure? [Completeness, Spec §FR-001]
- [ ] CHK002 - Are the exact markdown heading levels that trigger section splitting specified (e.g., all levels 1-6 or only specific levels)? [Completeness, Spec §FR-002]
- [ ] CHK003 - Is the default token budget value documented when config.yaml is absent or the key is missing? [Gap, Spec §FR-007]
- [ ] CHK004 - Are logging requirements specified for file discovery, parsing warnings, and truncation events? [Gap]
- [ ] CHK005 - Is the separator format between AGENT/USER/MEMORY blocks in the assembled prompt defined? [Gap, Spec §FR-004]

## Requirement Clarity

- [ ] CHK006 - Is "구조화된 데이터" quantified with a specific data structure or schema reference? [Clarity, Spec §FR-002]
- [ ] CHK007 - Is "경고를 남긴다" (leave a warning) clarified with the target output channel (stderr, log file, return value)? [Clarity, Spec §US1-AS2]
- [ ] CHK008 - Is "뒷부분(MEMORY 영역)부터 잘라낸다" specified with granularity (section-level, line-level, or token-level truncation)? [Clarity, Spec §FR-007]
- [ ] CHK009 - Is the "1초 이내" performance target defined with measurement conditions (cold start, warm, file sizes)? [Clarity, Spec §SC-001]

## Requirement Consistency

- [ ] CHK010 - Are persona file path conventions consistent between spec (`.agent/`, `~/.agents/main/`) and plan (`config.yaml` persona.local_dir/global_dir`)? [Consistency, Spec §FR-003 vs Plan §Contract]
- [ ] CHK011 - Is the file type enum naming consistent across spec entities (AGENT/USER/MEMORY) and config.yaml (agent/user/memory lowercase)? [Consistency]
- [ ] CHK012 - Are error handling behaviors consistent between US1-AS3 (빈 구조체 반환) and contract (빈 섹션 리스트 + 경고 로그)? [Consistency]

## Acceptance Criteria Quality

- [ ] CHK013 - Can SC-002 ("파일 1~3개가 누락된 모든 조합") be objectively enumerated as test cases? [Measurability, Spec §SC-002]
- [ ] CHK014 - Is SC-003 ("올바른 순서") defined with a verifiable assertion format (e.g., string ordering, section markers)? [Measurability, Spec §SC-003]
- [ ] CHK015 - Is SC-004 ("예산 한도를 넘지 않아야") defined with exact boundary behavior (equal-to-budget allowed or strictly less)? [Measurability, Spec §SC-004]

## Scenario Coverage

- [ ] CHK016 - Are requirements defined for when config.yaml itself is missing or malformed? [Coverage, Exception Flow, Gap]
- [ ] CHK017 - Are requirements specified for when a persona file contains only frontmatter/YAML header but no markdown body? [Coverage, Edge Case, Gap]
- [ ] CHK018 - Are requirements defined for handling duplicate headings within a single persona file? [Coverage, Edge Case, Gap]
- [ ] CHK019 - Are requirements specified for symbolic links or permission-denied scenarios on persona file paths? [Coverage, Edge Case, Gap]

## Dependencies & Assumptions

- [ ] CHK020 - Is the assumption "LLM API 인터페이스는 별도 구현" validated with a defined interface stub or contract? [Assumption, Spec §Assumptions]
- [ ] CHK021 - Is the tiktoken model encoding selection strategy documented for non-OpenAI models (Claude, Gemini)? [Dependency, Plan §Research]
- [ ] CHK022 - Is the markdown-it-py version constraint or minimum feature set documented? [Dependency, Gap]

## Notes

- Focus areas: Completeness (missing specs), Clarity (vague terms), Consistency (cross-doc alignment)
- Depth: Standard — covers primary and edge case requirements quality
- 22 items total, 18 with traceability references (82% ≥ 80% threshold)
