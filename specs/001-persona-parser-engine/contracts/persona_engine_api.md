# API Contract: 페르소나 엔진

이 모듈은 Python 라이브러리로 제공되며, 다른 SimpleClaw 모듈에서 import하여 사용한다.

## Public Interface

### `resolve_persona_files(local_dir, global_dir) -> list[PersonaFile]`

지정된 로컬 및 전역 디렉토리에서 페르소나 파일을 탐색하고, 로컬 우선 규칙을 적용하여 최종 파일 목록을 반환한다.

**Parameters**:
- `local_dir` (str | Path): 로컬 페르소나 디렉토리 경로 (예: `.agent/`)
- `global_dir` (str | Path): 전역 페르소나 디렉토리 경로 (예: `~/.agents/main/`)

**Returns**: `list[PersonaFile]` — 최대 3개 (AGENT, USER, MEMORY), 존재하는 파일만 포함

**Errors**: 디렉토리가 존재하지 않으면 빈 리스트 반환 (예외 발생 없음)

---

### `parse_markdown(file_path, file_type) -> PersonaFile`

단일 마크다운 파일을 파싱하여 PersonaFile 객체를 반환한다.

**Parameters**:
- `file_path` (str | Path): 마크다운 파일 절대 경로
- `file_type` (FileType): AGENT, USER, MEMORY 중 하나

**Returns**: `PersonaFile` — 파싱된 섹션 목록 포함

**Errors**: 파일이 존재하지 않거나 디코딩 실패 시 빈 섹션 리스트의 PersonaFile 반환 + 경고 로그

---

### `assemble_prompt(persona_files, token_budget) -> PromptAssembly`

PersonaFile 목록을 AGENT → USER → MEMORY 순서로 조립하고, 토큰 예산을 초과하면 MEMORY 뒷부분부터 잘라낸다.

**Parameters**:
- `persona_files` (list[PersonaFile]): resolve_persona_files의 반환값
- `token_budget` (int): 최대 허용 토큰 수 (config에서 주입)

**Returns**: `PromptAssembly` — 조립된 텍스트, 토큰 수, 잘라냄 여부 포함

**Errors**: persona_files가 비어 있으면 빈 문자열의 PromptAssembly 반환

---

### `load_persona_config(config_path) -> dict`

config.yaml에서 페르소나 엔진 관련 설정을 로드한다.

**Parameters**:
- `config_path` (str | Path): config.yaml 파일 경로

**Returns**: `dict` — 토큰 예산, 경로 설정 등 포함

**Expected config keys**:
```yaml
persona:
  token_budget: 4096
  local_dir: ".agent"
  global_dir: "~/.agents/main"
  files:
    - name: "AGENT.md"
      type: "agent"
    - name: "USER.md"
      type: "user"
    - name: "MEMORY.md"
      type: "memory"
```

**Errors**: 파일 미존재 시 기본값 dict 반환
