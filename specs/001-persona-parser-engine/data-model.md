# Data Model: 페르소나 설정 파싱 엔진 및 프롬프트 인젝터

## Entities

### Section

마크다운 파일 내 하나의 헤딩과 그 본문을 나타내는 단위.

| Attribute | Type | Description |
|-----------|------|-------------|
| level | int | 헤딩 레벨 (1=`#`, 2=`##`, 3=`###` 등) |
| title | str | 헤딩 텍스트 (마크다운 기호 제외) |
| content | str | 해당 섹션의 본문 텍스트 (다음 동일/상위 헤딩까지) |

### PersonaFile

개별 페르소나 마크다운 파일을 나타내는 개체.

| Attribute | Type | Description |
|-----------|------|-------------|
| file_type | enum | AGENT, USER, MEMORY 중 하나 |
| source_path | str | 파일의 절대 경로 |
| source_scope | enum | LOCAL, GLOBAL (어느 경로에서 로드되었는지) |
| sections | list[Section] | 파싱된 섹션 목록 (순서 보존) |
| raw_content | str | 원본 마크다운 텍스트 |

**Uniqueness**: file_type 당 하나의 PersonaFile만 존재 (로컬 우선 해소 후).

### PromptAssembly

여러 PersonaFile을 조합한 최종 System Prompt 결과물.

| Attribute | Type | Description |
|-----------|------|-------------|
| parts | list[PersonaFile] | 조립에 사용된 파일 목록 (순서: AGENT → USER → MEMORY) |
| assembled_text | str | 최종 조립된 System Prompt 문자열 |
| token_count | int | assembled_text의 토큰 수 |
| token_budget | int | config에서 읽은 토큰 예산 한도 |
| was_truncated | bool | 잘라냄이 적용되었는지 여부 |

## Relationships

```
PersonaFile 1──* Section      (하나의 파일은 0개 이상의 섹션을 가짐)
PromptAssembly 1──3 PersonaFile  (최대 3개 파일을 조합, 순서 고정)
```

## State Transitions

이 feature에는 상태 전이가 없다. PersonaFile은 기동 시 1회 생성되며 불변(immutable)으로 취급된다.

## Validation Rules

- `Section.level`은 1~6 범위의 정수여야 한다.
- `PersonaFile.file_type`은 반드시 AGENT, USER, MEMORY 중 하나여야 한다.
- `PromptAssembly.token_count`는 0 이상이어야 한다.
- `PromptAssembly.token_budget`는 양의 정수여야 한다.
- 잘라냄 적용 후 `token_count <= token_budget`이 보장되어야 한다.
