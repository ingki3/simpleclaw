# 서브에이전트 응답 프로토콜

`SubAgentSpawner`로 실행되는 서브프로세스 서브에이전트는 **표준 JSON 스키마**에 맞춰 stdout으로 결과를 반환해야 한다. 이 문서는 서브에이전트 작성자를 위한 출력 규약과 spawner 측 검증 동작을 정의한다.

## 표준 응답 스키마

```json
{
  "status": "success" | "error",
  "data":   { ... }   // optional, success일 때 결과 페이로드
  "error":  "..."     // status="error"일 때 필수, 문자열 또는 SubAgentErrorDetail
  "meta":   { ... }   // optional, version/agent_id/trace_id 등 자유 메타데이터
}
```

| 필드 | 타입 | 필수 여부 | 설명 |
|---|---|---|---|
| `status` | `"success"` \| `"error"` | 필수 | 작업 결과 분기. 다른 값은 거부된다. |
| `data` | object \| null | 선택 | 작업 결과 페이로드. 객체(JSON object)여야 하며 배열·스칼라는 거부된다. |
| `error` | string \| object \| null | `status="error"`일 때 필수 | 단순 메시지 또는 `{code, message, details}` 구조. |
| `meta` | object \| null | 선택 | 서브에이전트가 자유롭게 첨부하는 메타데이터. spawner는 그대로 전달한다. |

### 일관성 규칙

- `status="error"`인데 `error`가 비어 있으면 검증 실패 — 디버깅 정보가 사라지는 것을 막는다.
- `status="success"`인데 `error`가 채워져 있으면 검증 실패 — 의미가 모호하다.
- 알 수 없는 최상위 필드는 무시(`extra="ignore"`) — 향후 호환성을 위한 보수적 정책.

### `SubAgentErrorDetail` 구조

```json
{
  "code": "E_TIMEOUT",         // 선택, 분류 코드
  "message": "took too long",  // 필수, 사람이 읽는 메시지
  "details": { "elapsed": 30 } // 선택, 진단용 임의 데이터
}
```

`spawner`는 구조화 에러를 받으면 `SubAgentResult.error` 문자열로 평탄화하여 호출자에게 전달한다(`[E_TIMEOUT] took too long` 형태).

## Exit code 규칙

| 상황 | exit_code | spawner의 처리 |
|---|---|---|
| 정상 종료 + 표준 JSON | `0` | JSON을 검증하여 `SubAgentResult`로 반환 |
| 정상 종료 + 표준 JSON에 `status="error"` | `0` | 논리 에러로 그대로 보존 (exit_code=0) |
| 비정상 종료(예외 등) + 표준 JSON | `≠0` | JSON 검증 시도, 통과하면 그 정보를 살린다 |
| 비정상 종료 + 비유효 JSON | `≠0` | stderr 첫 500자를 `error`로 폴백 |
| 정상 종료 + 빈 stdout | `0` | `status="success"`, `data={}` 로 처리 |

## 검증 실패 폴백 표

비표준 응답은 예외를 던지지 않고 안전한 `status="error"` 결과로 정규화된다. 진단 정보는 `SubAgentResult.meta["validation_failure"]`에 포함된다.

| 분류 (`reason`) | 트리거 | `error` 메시지 |
|---|---|---|
| `empty_output` | stdout이 공백/비어있음 | `Sub-agent produced no stdout output` |
| `invalid_json` | JSON 파싱 실패 | `Invalid JSON output: …` |
| `schema_violation` | 최상위가 객체가 아님 / 필수 필드 누락 / 잘못된 status / 타입 위반 | `Schema violation: …` |

`meta["validation_failure"].raw`에는 stdout 첫 500자가 보존되어 디버깅 시 활용할 수 있다.

## Python 서브에이전트 예시

```python
import json
import sys

def main() -> int:
    try:
        result = do_work()
        print(json.dumps({
            "status": "success",
            "data": result,
            "meta": {
                "agent_id": os.environ.get("AGENT_ID", ""),
                "version": "1.0",
            },
        }))
        return 0
    except UserInputError as exc:
        print(json.dumps({
            "status": "error",
            "error": {
                "code": "E_INPUT",
                "message": str(exc),
                "details": {"field": exc.field},
            },
        }))
        return 0  # 논리 에러는 exit_code=0으로 두는 것을 권장
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1

if __name__ == "__main__":
    sys.exit(main())
```

## 환경 변수 계약 (spawner → 서브에이전트)

서브에이전트 프로세스에는 다음 환경 변수가 자동 주입된다.

| 변수 | 의미 |
|---|---|
| `AGENT_ID` | spawner가 부여한 8자 ID (예: `a1b2c3d4`) |
| `AGENT_SCOPE` | 권한 범위 JSON (`{"allowed_paths": [...], "network": bool}`) |
| `AGENT_WORKSPACE` | 격리된 워크스페이스 디렉토리 절대 경로 |
| `SIMPLECLAW_TRACE_ID` | 분산 트레이싱 ID — 로그에 함께 기록할 것 |

## 호환성 노트

- `SubAgentResult`는 dataclass 그대로 유지된다. `meta` 필드가 추가되었으나 기존 호출자는 영향 없음(`Optional[dict]`, 기본값 `None`).
- 기존 서브에이전트가 `data`/`error` 외 알 수 없는 필드를 보내더라도 무시되어 호환성이 유지된다.
- 응답 파싱이 더 엄격해졌으므로, 비표준 출력을 보내던 서브에이전트는 이제 `status="error"` 결과로 정규화된다 — 사일런트 통과되던 잘못된 출력이 명시적 실패로 드러난다.

## 참고

- 스키마 정의: `src/simpleclaw/agents/protocol.py` (`SubAgentResponse`, `SubAgentErrorDetail`)
- 검증 진입점: `validate_response(stdout_text)` → `SubAgentResponse | ValidationFailure`
- spawner 통합: `src/simpleclaw/agents/spawner.py::SubAgentSpawner._execute`
- 테스트: `tests/unit/test_agent_protocol.py`, `tests/unit/test_agent_spawner.py`
