# Research: 페르소나 설정 파싱 엔진 및 프롬프트 인젝터

## 1. 마크다운 파싱 라이브러리 선택

**Decision**: `markdown-it-py`

**Rationale**: CommonMark 준수, 순수 Python 구현, AST(Abstract Syntax Tree) 접근이 가능하여 헤딩 기준 섹션 분리가 용이하다. `mistune`도 후보였으나, markdown-it-py가 플러그인 확장성과 CommonMark 적합성에서 우위.

**Alternatives considered**:
- `mistune`: 빠르지만 CommonMark 완전 준수가 아님
- `python-markdown`: 무겁고 확장 패턴이 복잡
- 정규식 직접 파싱: 유지보수성 저하, 엣지 케이스 취약

## 2. 토큰 카운팅 방식

**Decision**: `tiktoken` 라이브러리 사용, 모델별 인코딩 자동 선택

**Rationale**: OpenAI의 tiktoken은 GPT 계열 토크나이저를 정확하게 구현하며, Claude 등 다른 모델에 대해서도 근사치로 활용 가능하다. 정확한 토큰 수를 알아야 예산 잘라냄이 올바르게 동작한다.

**Alternatives considered**:
- 문자 수 기반 근사 (chars / 4): 부정확, 다국어에서 오차 큼
- `transformers` 라이브러리 토크나이저: 무거운 의존성, Constitution II 위반 우려
- 모델별 API 토큰 카운트 엔드포인트: 네트워크 의존, 오프라인 불가

## 3. 파일 경로 탐색 전략

**Decision**: 로컬(`.agent/`) → 전역(`~/.agents/main/`) 순서 탐색, 로컬 우선

**Rationale**: PRD 4.1절의 "전역 상태(Global State)" 규정과 Constitution VII(Extensibility via Isolation)에 부합. 로컬 오버라이드를 통해 프로젝트별 커스터마이징을 지원하면서도 전역 기본값을 유지.

**Alternatives considered**:
- 전역만 사용: 멀티 프로젝트 환경에서 페르소나 격리 불가
- 병합(merge) 전략: 복잡성 증가, 섹션 충돌 해결 규칙 필요
- 환경 변수로 경로 지정: Configuration-Driven이나, 기본 규칙 없이는 사용성 저하

## 4. 프롬프트 조립 순서 및 잘라냄 전략

**Decision**: AGENT → USER → MEMORY 순서 조립, 토큰 초과 시 MEMORY 뒷부분부터 역순 제거

**Rationale**: AGENT(역할 정의)와 USER(사용자 정보)는 에이전트의 핵심 정체성이므로 항상 보존해야 한다. MEMORY는 맥락 보조 정보이므로 잘라냄의 우선 대상으로 적절하다.

**Alternatives considered**:
- 균등 분할: 각 파일에 1/3씩 예산 배분 — AGENT가 짧을 때 예산 낭비
- 우선순위 가중치: 파일별 비율을 config로 설정 — 초기에는 과도한 복잡성
- 요약 후 삽입: LLM 호출 필요, 순환 의존 발생

## 5. 설정 파일 형식

**Decision**: `config.yaml` (PyYAML로 로드)

**Rationale**: Constitution III에 따라 설정 파일 기반 유연성을 확보. YAML은 사람이 읽기 쉽고, PRD 4.3절에서 `config.yaml`을 명시적으로 지정하고 있다.

**Alternatives considered**:
- TOML: Python 3.11+ 내장이나 PRD 명시 형식과 불일치
- JSON: 주석 불가, 사용자 편집성 저하
- `.env` 단독: 중첩 구조 표현 불가
