# 레시피 워크플로우

레시피는 YAML로 정의하는 재사용 가능한 자동화 워크플로우입니다. 스킬 조합과 지시사항을 하나로 패키징하여, 슬래시 명령어(`/recipe-name`)나 Cron 예약으로 실행할 수 있습니다.

## 레시피 포맷

### v2 (Instruction 기반, 권장)

```yaml
name: morning-briefing
description: "아침 브리핑 - 메일과 캘린더 요약"
trigger: "아침 브리핑, morning briefing"

parameters:
  - name: date
    description: "대상 날짜"
    required: false
    default: "오늘"

skills:
  - gmail-skill
  - google-calendar-skill

instructions: |
  {{ date }}의 아침 브리핑을 만들어줘.
  1. 읽지 않은 메일을 확인하고
  2. 오늘 일정을 확인하고
  3. 중요도별로 요약해줘

settings:
  timeout: 120
```

### v1 (Step 기반, 레거시)

```yaml
name: daily-report
description: "일일 리포트 생성"
parameters:
  - name: date
    required: true
steps:
  - type: command
    name: "데이터 수집"
    content: "echo Gathering data for ${date}"
  - type: prompt
    name: "리포트 생성"
    content: "Generate a report for ${date}."
```

## 실행 방법

### 슬래시 명령어

텔레그램에서 직접 입력:

```
/morning-briefing
/morning-briefing date=2026-04-24
/morning-briefing date="2026-04-24"
```

### 자연어 트리거

레시피의 `trigger`에 정의된 키워드를 LLM이 매칭:

```
"아침 브리핑 해줘" → morning-briefing 레시피 자동 실행
```

### Cron 예약 실행

```
"매일 아침 9시에 morning-briefing 실행해줘"
→ cron job (0 9 * * *), action_type: recipe, action_ref: morning-briefing
```

## 레시피 만들기

### 1. 디렉토리 생성

```bash
mkdir -p .agent/recipes/my-recipe
```

### 2. recipe.yaml 작성

```yaml
name: my-recipe
description: "나만의 레시피"
trigger: "내 레시피"

parameters:
  - name: target
    description: "대상"
    required: true

skills:
  - my-skill

instructions: |
  {{ target }}에 대해 작업을 수행해줘.
```

### 3. 즉시 사용

레시피 파일을 추가하면 다음 메시지부터 자동으로 발견됩니다. 재시작 불필요.

## 변수 치환

두 가지 문법을 지원합니다:

| 문법 | 용도 | 예시 |
|------|------|------|
| Jinja 스타일 | v2 instructions 내 | `{{ date }}`, `{{ today }}` |
| Shell 스타일 | v1 steps 내 | `${date}` |

### 내장 변수 (Built-in Variables)

v2 레시피 실행 시 아래 변수가 **자동으로 주입**됩니다. Cron, 슬래시 명령어 모두 동일하게 적용되며, `parameters`에 정의할 필요 없이 instructions에서 바로 사용 가능합니다.

| 변수 | 설명 | 예시 값 |
|------|------|---------|
| `{{ today }}` | 실행 시점 날짜 (ISO 형식) | `2026-04-27` |
| `{{ today_ko }}` | 실행 시점 날짜 (한국어) | `2026년 04월 27일` |
| `{{ weekday }}` | 요일 (영어) | `Sunday` |
| `{{ now }}` | 실행 시점 날짜+시간 | `2026-04-27 07:15` |

**사용 예시:**

```yaml
instructions: |
  오늘은 {{ today_ko }} ({{ weekday }}) 이야.
  1. "AI news {{ today }}" 로 검색해줘
  2. {{ today_ko }} 기준 뉴스만 포함해줘
```

> **NOTE:** 내장 변수와 사용자 변수가 동일한 이름이면 사용자 변수가 우선합니다.
> 예: `parameters`에 `today`를 정의하고 `/recipe today=2026-05-01`로 실행하면 내장 변수 대신 사용자 값이 적용됩니다.

### 사용자 정의 변수

`parameters`에 정의한 변수는 슬래시 명령어 실행 시 사용자가 값을 전달합니다:

```yaml
parameters:
  - name: topic
    description: "검색할 토픽"
    required: false
    default: ""
```

```
/ai-report topic=agent
```

## v2 vs v1 비교

| | v2 (Instructions) | v1 (Steps) |
|---|---|---|
| 실행 방식 | LLM이 스킬을 자율적으로 사용 | 고정된 순서로 명령 실행 |
| 유연성 | LLM이 상황에 따라 판단 | 정해진 순서만 실행 |
| 슬래시 명령 | 지원 (`/recipe-name`) | 미지원 |
| Cron 지원 | recipe 이름으로 등록 | 파일 경로로 등록 |
| 포맷 감지 | `instructions` 키 존재 | `steps` 키 존재 |

## 디렉토리 구조

```
.agent/recipes/          ← 로컬 레시피 (우선)
~/.agents/recipes/       ← 전역 레시피
```

## 설정

```yaml
recipes:
  local_dir: ".agent/recipes"
  global_dir: "~/.agents/recipes"
```

## 관련 파일

- `src/simpleclaw/recipes/models.py` — RecipeDefinition, RecipeSettings 모델
- `src/simpleclaw/recipes/loader.py` — YAML 파싱, v1/v2 자동 감지, 디스커버리
- `src/simpleclaw/recipes/executor.py` — v1 실행기, `render_instructions()` (v2)
