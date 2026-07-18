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
| `add-task` | 작업 등록 | `--title --prompt --project` 필수, `--agent {claude,codex,any}`, `--priority 0-100`, `--estimated-tokens`, `--approval {auto,confirm}`, `--not-before`, `--max-retries`, `--repeat {daily,weekly,N}`, `--tag`, `--depends-on` |
| `list-tasks` | 목록 조회 | `--status {waiting,reserved,running,succeeded,failed,retry_wait,blocked,cancelled,archived}` |
| `show-task ID` | 상세 + 최근 5회 실행 | 반복 체인과 `split_parent_task_id`, `split_child_ids` 분할 관계 확인 |
| `queue-status` | 큐·에이전트·자동화 설정 요약 | 작업 수, 승인 대기 목록, 에이전트 상태, `global_auto_run` |
| `daily-summary` | 로컬 날짜의 프로젝트별 종료 활동 요약 | `--date YYYY-MM-DD`(생략 시 오늘), 완료·실패·차단·취소·실행·fallback·worktree·사용량 제한 |
| `plan-batch PATH` | 토큰 예산으로 작업을 분배하고 원자적으로 등록 | JSON 파일, `--dry-run` |
| `split-task ID PATH` | 대기 작업을 같은 JSON 계약의 하위 작업 배치로 원자적 교체 | JSON 파일, `--dry-run` |
| `cleanup-worktree ID` | 병합 완료된 종료 작업의 worktree 제거 | dirty/미병합 커밋이면 거부, 브랜치는 유지 |
| `update-task ID` | 대기(waiting) 작업 부분 수정 | 준 플래그만 덮어씀. `--approval auto`, `--repeat`, `--clear-repeat`, `--priority`, `--estimated-tokens`, `--clear-not-before`, `--tag`(전체 교체), `--depends-on`(전체 교체) |
| `retry-task ID` | 실패(failed)/차단(blocked) 작업 재대기 | 재시도 횟수·오류 초기화 |
| `cancel-task ID` | 대기·재시도대기·차단 작업 취소 | |
| `duplicate-task ID` | 작업 복제(새 waiting 작업) | |
| `move-top ID` | 대기 작업을 맨 위로(P100) | |
| `archive-task ID` | 성공(succeeded) 작업 보관 | |
| `delete-task ID` | 작업+실행 기록 영구 삭제 | reserved/running은 불가, 의존 작업 있으면 불가 |
| `set-agent AGENT STATE` | 에이전트 수동 상태 설정 | `claude|codex` × `available|cooldown|disabled`, cooldown은 `--until "YYYY-MM-DD HH:MM"` |

## 프로젝트별 일일 작업 요약

`daily-summary`는 지정한 날짜의 로컬 자정부터 다음 날 로컬 자정 직전까지를 UTC로
변환해 집계합니다. 완료·실패·취소는 `tasks.finished_at`, 차단은 상태 전이 시각인
`tasks.updated_at`, 실행 관련 항목은 `task_runs.finished_at`이 이 구간에 속할 때
포함됩니다. 따라서 전날 시작해 자정 뒤 끝난 실행은 결과가 나온 날짜에 귀속됩니다.

```powershell
... daily-summary --json
... daily-summary --date 2026-07-17 --json
```

JSON 스키마는 다음 구조로 고정됩니다. `task_counts`와 `agent_run_counts`,
`usage_limit_wait_occurred`는 값이 0/false여도 두 상태·에이전트 키를 항상 포함합니다.
`total_runtime_seconds`는 종료 실행의 `finished_at - started_at` 합계입니다.

```json
{
  "date": "2026-07-17",
  "period": {
    "start_utc": "2026-07-16T15:00:00Z",
    "end_utc": "2026-07-17T15:00:00Z"
  },
  "projects": [
    {
      "project_path": "C:\\projects\\sample",
      "task_counts": {
        "succeeded": 2,
        "failed": 1,
        "blocked": 0,
        "cancelled": 0
      },
      "total_runtime_seconds": 5400.0,
      "agent_run_counts": {"claude": 1, "codex": 2},
      "fallback_count": 1,
      "worktree_branches": ["aqr/T42"],
      "failed_tasks": [
        {"id": 42, "title": "회귀 테스트", "last_error_type": "timeout"}
      ]
    }
  ],
  "totals": {
    "project_count": 1,
    "task_counts": {
      "succeeded": 2,
      "failed": 1,
      "blocked": 0,
      "cancelled": 0
    },
    "total_runtime_seconds": 5400.0,
    "agent_run_counts": {"claude": 1, "codex": 2},
    "fallback_count": 1,
    "worktree_branches": ["aqr/T42"],
    "failed_task_count": 1,
    "usage_limit_wait_occurred": {"claude": false, "codex": true}
  }
}
```

활동이 전혀 없는 날도 성공(종료 코드 0)이며 `projects`는 빈 배열, `totals`는 0과
false로 채워집니다. 현재 스키마에는 상태 전이 이력 테이블이 없으므로, 나중에 재시도·취소된
작업의 과거 실패/차단 상태는 복원하지 않고 조회 시점에 해당 날짜의 종료 상태인 작업만 셉니다.

## 토큰 기반 배치 계획

`plan-batch` 입력은 `budgets`와 하나 이상의 `tasks`를 가진 JSON 객체다. 각 작업은
`TaskDraft` 필드명(`project_path`, `agent_policy`, `approval_mode`, `dependency_ids` 등)을
사용하고 `estimated_tokens`는 반드시 양수여야 한다.

```json
{
  "budgets": {"claude": 120000, "codex": 80000},
  "tasks": [
    {
      "title": "설계 검토",
      "prompt": "설계와 구현의 불일치를 찾으세요.",
      "project_path": "C:/projects/sample",
      "agent_policy": "any",
      "estimated_tokens": 25000,
      "approval_mode": "auto"
    },
    {
      "title": "검토 결과 반영",
      "prompt": "설계 검토 결과를 코드에 반영하세요.",
      "project_path": "C:/projects/sample",
      "agent_policy": "codex",
      "estimated_tokens": 40000,
      "dependency_ids": [12],
      "depends_on_indices": [0]
    }
  ]
}
```

- 전용 작업은 지정된 에이전트 예산에서 먼저 차감한다.
- `any` 작업은 예상 토큰 내림차순으로 처리하고, 등록 후 예산 사용률이 낮은 쪽에 배치한다.
- 같은 사용률이면 Claude, 같은 크기면 JSON에서 먼저 나온 작업이 우선한다.
- 결과 작업은 `any`가 아니라 결정된 `claude`/`codex` 정책으로 저장된다.
- `--dry-run`은 DB를 변경하지 않는다. 실제 등록은 전체 배치를 한 번만 커밋한다.
- `dependency_ids`는 등록 전에 이미 DB에 존재하는 작업 ID 배열이다.
- `depends_on_indices`는 같은 JSON의 `tasks` 배열을 가리키는 선택적 0-기반 인덱스 배열이다.
  두 필드는 함께 사용할 수 있고, 저장할 때 모두 실제 `task_dependencies` 행으로 합쳐진다.
- `depends_on_indices`의 중복·음수·자기 참조·범위 밖 참조·순환은 작업을 하나도
  등록하기 전에 거부된다.

```powershell
... plan-batch workload.json --dry-run --json
... plan-batch workload.json --json
```

## 작업 자동 분할

분할 판단과 JSON 작성은 큐를 조작하는 Claude/Codex가 담당하며 AQR은 LLM을 호출하지
않는다. `split-task ID PATH`는 `plan-batch`와 동일한 JSON 계약과 토큰 분배 규칙을 사용해
원본 작업을 하위 작업 배치로 교체한다.

```powershell
# 상태·경로·예산·기존/형제 의존을 검증하되 DB는 변경하지 않음
... split-task 42 split-plan.json --dry-run --json

# 원본 취소, 하위 작업 등록, 관계 기록, 의존 승계를 한 번만 커밋
... split-task 42 split-plan.json --json
```

- 원본은 `waiting`, `retry_wait`, `blocked`일 때만 분할할 수 있다. `reserved`, `running`과
  성공·실패·취소된 종료 작업은 거부한다.
- 실행하면 원본은 `cancelled`가 되고 각 하위 작업의 `split_parent_task_id`와 원본의
  `split_child_ids`에 관계가 남는다. `list-tasks`와 `show-task`의 텍스트/JSON에서 확인한다.
- 원본을 직접 기다리던 모든 작업은 원본 의존 대신 생성된 하위 작업 전체를 의존한다.
  따라서 스케줄러는 모든 하위 작업이 `succeeded`가 된 뒤에만 그 작업을 실행한다. 하나라도
  미완료·실패·취소 상태이면 후속 작업은 계속 대기한다.
- 하위 작업이 기존 후속 작업을 다시 의존해 승계 후 순환이 생기는 계획도 거부한다.
- 원본 상태 CAS 취소, 하위 작업과 형제 의존 등록, 원본↔하위 관계 저장, 후속 작업 의존
  재배선은 한 DB 트랜잭션이다. 어느 단계든 실패하면 원본 상태를 포함해 모두 롤백한다.

JSON 결과의 `split`은 실제 변경 여부, `original_task`는 원본의 전체 조회 레코드,
`rewired_dependent_ids`는 의존을 승계한 후속 작업 ID, `tasks`는 입력 인덱스·생성 ID·결정된
에이전트·`dependency_ids`·`depends_on_indices`를 담는다. dry-run에서는 `split=false`이고
하위 작업 ID가 `null`이다.

## 반복 작업

`--repeat daily`, `--repeat weekly`, 또는 `--repeat N`(N일 간격)으로 등록한다. 작업이
`succeeded`가 된 시각을 기준으로 N일 뒤의 `not_before`를 가진 다음 회차가 한 번만
자동 생성된다. 실패·차단·취소된 작업은 다음 회차를 만들지 않는다.

```powershell
... add-task --title "매일 저장소 점검" --prompt "..." --project "C:\projects\sample" `
    --agent codex --approval auto --repeat daily --json
... update-task 7 --repeat 3 --json       # 3일 간격으로 변경
... update-task 7 --clear-repeat --json  # 현재 회차 성공 후 체인 중단
... show-task 7 --json
```

`list-tasks`의 `REPEAT` 열과 `show-task`의 `recurrence`에서 규칙을 확인한다. 자동 생성된
회차의 `recurrence_source_task_id`는 직전 회차 ID다. 아직 실행 전인 현재 회차를
`cancel-task`로 취소해도 체인이 중단된다.

## Git worktree 병렬 실행

기본값은 off다. 같은 Git 프로젝트의 작업 두 개를 서로 다른 에이전트로 동시에 실행하려면
설정에 다음을 명시한다.

```toml
[app]
worktrees_dir = "worktrees"

[scheduler]
worktree_parallel_enabled = true
```

- 잠금 충돌이 없는 첫 작업은 원 프로젝트에서 기존 방식으로 실행된다.
- 두 번째 잠금 충돌 작업만 `worktrees_dir/T<ID>`의 전용 checkout과 `aqr/T<ID>` 브랜치를 사용한다.
- 비Git 프로젝트 또는 생성 실패는 작업을 `waiting`으로 되돌려 exclusive 직렬 실행을 기다린다.
- `show-task ID --json`의 `worktree_path`/`worktree_branch`와
  `recent_runs[]`의 같은 필드로 실제 실행 위치를 확인한다.
- 성공·실패·취소 후에도 자동 머지나 자동 삭제를 하지 않는다. 재시도/재시작은 기존
  checkout을 재사용하므로 사용자가 내용을 검사할 수 있다.

병합이 끝난 뒤 worktree만 정리한다. 아래 명령은 미커밋 파일과 현재 프로젝트 HEAD에
포함되지 않은 브랜치 커밋을 모두 검사하며, 하나라도 있으면 종료 코드 2로 거부한다.

```powershell
... cleanup-worktree 7 --json
```

성공해도 `aqr/T7` 브랜치는 삭제하지 않는다. squash/rebase로 내용만 반영해 브랜치 커밋이
HEAD의 조상이 아닌 경우도 보수적으로 거부하므로, 필요하면 먼저 정상 merge하거나 Git을
직접 확인한다.

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
3. 작업이 `any`이면 사용 가능한 반대 AI를 다음 틱에서 우선 예약한다
4. 둘 다 제한 상태이면 리셋 시각이 지난 AI부터 자동 복귀해 작업을 다시 예약·실행한다

즉 **앱과 스케줄러만 켜져 있으면 토큰 리셋을 기다렸다가 알아서 재개한다.**
재실행은 같은 프롬프트로 새 CLI 세션을 시작하므로, 프롬프트는 "현재 저장소 상태를
확인하고 이어서 진행"하도록 멱등하게 쓰는 것이 안전하다.

## Any 작업 자동 fallback

`[scheduler] fallback_enabled = true`(기본값)이면 `any` 작업은 한 AI에서 재시도 가능한
일반 실패가 `fallback_failure_threshold`회 연속 발생한 뒤 반대 AI를 우선합니다. 기본
임계값은 1회입니다. 전용 `claude`/`codex` 작업에는 적용되지 않습니다.

- 사용량 제한은 임계값과 관계없이 즉시 반대 AI를 우선하며 `retry_count`를 소모하지 않습니다.
- 반대 AI가 사용 가능하면 이전 AI는 그 작업을 후보로 선택하지 않습니다.
- 반대 AI가 쿨다운·비활성·미설치이면 사용 가능한 기존 AI가 다시 실행할 수 있어 큐가 고정되지 않습니다.
- 같은 DB 상태에서는 항상 같은 후보 필터와 기존 점수/동점 규칙을 사용합니다.
- `show-task ID --json`의 `fallback_agent`는 현재 우선 AI이고,
  `recent_runs[].agent -> fallback_to_agent`는 최근 전환 이력입니다.
- 기존 동작으로 되돌리려면 `fallback_enabled = false`로 설정합니다.

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
