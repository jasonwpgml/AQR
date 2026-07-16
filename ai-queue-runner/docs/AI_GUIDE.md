# AI 운영 가이드 (헤드리스 CLI)

이 문서는 **AI 에이전트가 GUI 없이 AI Queue Runner를 조작하는 표준 방법**이다.
모든 명령은 GUI와 같은 설정·검증(`TaskService`)을 거치며 DB에 즉시 반영된다.

## 전제

```powershell
# 기본 형식 (ai-queue-runner/ 기준, venv는 저장소 루트)
..\.venv\Scripts\python -m ai_queue_runner --config config.toml <subcommand> [옵션]
```

- `--config` 생략 시: 현재 폴더 `config.toml` → 없으면 `%LOCALAPPDATA%\AIQueueRunner\config.toml`.
- **모든 하위 명령은 `--json`을 지원한다.** AI는 항상 `--json`을 사용할 것.
  열거형은 문자열, 시각은 UTC ISO-8601(`...Z`)로 출력된다.
- `--not-before`/`--until` 입력은 **로컬 시간** `"YYYY-MM-DD HH:MM"` 형식이고 내부에는 UTC로 저장된다.
- 종료 코드: `0` 성공 / `2` 잘못된 요청(검증 실패 등, stderr에 사유) / `3` 예기치 않은 오류.

## 완전 자동 실행(사용자 승인 없음) 조건

세 가지가 모두 충족되어야 승인 창 없이 실행된다.

1. `config.toml`의 `[app] global_auto_run = true`
2. 작업의 승인 모드가 `auto` → **작업 추가 시 `--approval auto`를 명시할 것** (기본값은 `confirm`)
3. GUI 앱이 떠 있고 스케줄러가 시작된 상태
   - 실행 엔진은 GUI 프로세스 안에서만 동작한다. CLI는 큐(DB)만 조작한다.
   - 트레이로 최소화된 상태여도 스케줄러는 계속 돈다.

`--approval confirm` 작업은 GUI 승인 창에서 사용자가 허용해야 실행된다.
승인 창에서 거부된 작업은 `blocked` 상태로 남는다 → `retry-task`로 재대기시킬 수 있다.

## 명령 요약

| 명령 | 용도 | 주요 옵션/인자 |
|---|---|---|
| `add-task` | 작업 등록 | `--title --prompt --project` 필수, `--agent {claude,codex,any}`, `--priority 0-100`, `--approval {auto,confirm}`, `--not-before`, `--max-retries`, `--tag`, `--depends-on` |
| `list-tasks` | 목록 조회 | `--status {waiting,reserved,running,succeeded,failed,retry_wait,blocked,cancelled,archived}` |
| `show-task ID` | 상세 + 최근 5회 실행 | |
| `queue-status` | 큐·에이전트·자동화 설정 요약 | 작업 수, 승인 대기 목록, 에이전트 상태, `global_auto_run` |
| `update-task ID` | 대기(waiting) 작업 부분 수정 | 준 플래그만 덮어씀. `--approval auto`로 전환, `--priority`, `--clear-not-before`, `--tag`(전체 교체), `--depends-on`(전체 교체) |
| `retry-task ID` | 실패(failed)/차단(blocked) 작업 재대기 | 재시도 횟수·오류 초기화 |
| `cancel-task ID` | 대기·재시도대기·차단 작업 취소 | |
| `duplicate-task ID` | 작업 복제(새 waiting 작업) | |
| `move-top ID` | 대기 작업을 맨 위로(P100) | |
| `archive-task ID` | 성공(succeeded) 작업 보관 | |
| `delete-task ID` | 작업+실행 기록 영구 삭제 | reserved/running은 불가, 의존 작업 있으면 불가 |
| `set-agent AGENT STATE` | 에이전트 수동 상태 설정 | `claude|codex` × `available|cooldown|disabled`, cooldown은 `--until "YYYY-MM-DD HH:MM"` |

## 상태 머신 (어떤 명령이 통하는지)

```
waiting → reserved → running → succeeded | failed | retry_wait | blocked
reserved → waiting|blocked (거부)   retry_wait → waiting (자동 승격)
failed|blocked → waiting (retry-task)   waiting|retry_wait|blocked → cancelled (cancel-task)
```

- `update-task`·`move-top`은 **waiting에서만** 허용된다.
- `reserved`는 GUI 승인 대기 또는 실행 직전 상태다. CLI로 승인할 수 없다
  (자동 실행을 원하면 처음부터 `--approval auto`로 등록하거나 waiting일 때 `update-task --approval auto`).

## 사용량 제한(토큰 소진) 자동 복구

실행 중 CLI가 사용량 제한을 보고하면 런타임이 자동으로:

1. 작업을 `waiting`으로 되돌리고 (재시도 횟수 소모 없음)
2. 해당 에이전트를 `cooldown`으로 전환한다 — Claude는 응답의 정확한 리셋 시각, 실패 시 `default_cooldown_minutes`
3. 리셋 시각이 지나면 스케줄러 틱이 에이전트를 자동 복귀시키고 작업을 다시 예약·실행한다

즉 **앱과 스케줄러만 켜져 있으면 토큰 리셋을 기다렸다가 알아서 재개한다.**
재실행은 같은 프롬프트로 새 CLI 세션을 시작하므로, 프롬프트는 "현재 저장소 상태를
확인하고 이어서 진행"하도록 멱등하게 쓰는 것이 안전하다.

## 권장 워크플로 예시

```powershell
# 1. 현재 상황 파악
... --json queue-status

# 2. 자동 실행 작업 등록 (승인 불필요)
... add-task --title "회귀 테스트 추가" --prompt "..." `
    --project "C:\projects\sample" --agent codex --approval auto --priority 80 --json

# 3. 기존 confirm 작업을 자동으로 전환
... update-task 7 --approval auto --json

# 4. 실패 작업 원인 확인 후 재시도
... show-task 7 --json     # recent_runs[].result_summary / error_type 확인
... retry-task 7 --json
```

## 하지 말 것

- `reserved`/`running` 작업을 `delete-task`로 지우려 하지 말 것 (거부됨).
- `--json` 없이 텍스트 출력을 파싱하지 말 것 (표시용이며 형식이 바뀔 수 있음).
- `allowed_project_roots` 밖의 경로로 작업을 만들려 하지 말 것 (검증에서 거부됨).
  루트 목록은 `queue-status`로 확인한다.
