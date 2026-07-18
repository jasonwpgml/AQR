# AI 운영 가이드 (헤드리스 CLI)

이 문서는 **AI 에이전트가 GUI 없이 AI Queue Runner를 조작하는 표준 방법**이다.
모든 명령은 GUI와 같은 설정·검증(`TaskService`)을 거치며 DB에 즉시 반영된다.

## 전제

```powershell
# 기본 형식 (ai-queue-runner/ 기준, venv는 저장소 루트)
..\.venv\Scripts\python -m ai_queue_runner --config config.toml `
  --actor <user|claude|codex|system> --actor-session <ID> <subcommand> [옵션]
```

- `--config` 생략 시: 현재 폴더 `config.toml` → 없으면 `%LOCALAPPDATA%\AIQueueRunner\config.toml`.
- AI가 큐를 변경할 때는 전역 `--actor`와 가능하면 `--actor-session`을 하위 명령 앞에
  지정한다. 생략 시 `user`로 기록된다. 생성 작업의 `created_by`/`creator_session_id`와
  모든 관리 이벤트의 호출 주체를 다음 세션에서 다시 찾는 기준으로 사용한다.
- **모든 하위 명령은 `--json`을 지원한다.** AI는 항상 `--json`을 사용할 것.
  열거형은 문자열, 시각은 UTC ISO-8601(`...Z`)로 출력된다.
- `--not-before`/`--until` 입력은 **로컬 시간** `"YYYY-MM-DD HH:MM"` 형식이고 내부에는 UTC로 저장된다.
- 종료 코드: `0` 성공 / `2` 잘못된 요청(검증 실패 등, stderr에 사유) / `3` 예기치 않은 오류.

## 완전 자동 실행(사용자 승인 없음) 조건

네 가지가 모두 충족되어야 승인 창 없이 실행된다.

1. `config.toml`의 `[app] global_auto_run = true`
2. 작업의 승인 모드가 `auto` → **작업 추가 시 `--approval auto`를 명시할 것** (기본값은 `confirm`)
3. GUI 앱이 떠 있고 스케줄러가 시작된 상태
   - 실행 엔진은 GUI 프로세스 안에서만 동작한다. CLI는 큐(DB)만 조작한다.
   - 트레이로 최소화된 상태여도 스케줄러는 계속 돈다.
4. 실행할 에이전트 상태가 `available` → **새 DB는 `unknown`으로 시작하므로 최초 1회
   `set-agent codex available`(또는 claude)을 실행할 것.** `unknown`/`limited` 에이전트에는
   작업이 예약되지 않는다. 현재 상태는 `queue-status`로 확인.

`--approval confirm` 작업은 GUI 승인 창에서 사용자가 허용해야 실행된다.
승인 창에서 거부된 작업은 `blocked` 상태로 남는다 → `retry-task`로 재대기시킬 수 있다.

## 명령 요약

| 명령 | 용도 | 주요 옵션/인자 |
|---|---|---|
| `add-task` | 작업 등록 | `--title --prompt --project` 필수, `--agent {claude,codex,any}`, `--priority 0-100`, `--estimated-tokens`, `--approval {auto,confirm}`, `--auto-commit`, `--draft-pr`, `--not-before`, `--max-retries`, `--repeat {daily,weekly,N}`, `--tag`, `--depends-on` |
| `save-template NAME` | 단일 작업 초안 저장/덮어쓰기 | `--title --prompt` 필수, 나머지는 `add-task`와 같은 기본 필드. `--project`는 나중에 지정 가능 |
| `list-templates` / `show-template NAME` | 템플릿 목록/상세 조회 | 이름순 목록, 저장된 TaskDraft 기본값 확인 |
| `add-from-template NAME` | 변수 치환 후 작업 등록 | `--var key=value` 반복, TaskDraft 오버라이드, `--dry-run` |
| `delete-template NAME` | 템플릿 삭제 | 작업과 이미 등록된 작업에는 영향 없음 |
| `list-tasks` | 범위 목록 조회 | `--project PATH`, `--created-by`, `--creator-session`, `--active` 또는 `--status` |
| `show-task ID` | 상세 + 최근 실행/관리 이력 | 출처·반복·분할·재작업 관계, 평가, 자동 커밋 결과 확인 |
| `list-events` | 삭제 후에도 남는 관리 이력 조회 | `--task-id`, `--project`, `--actor`, `--limit` |
| `pending-reviews` | 성공했지만 미평가인 작업 조회 | `--project PATH`로 프로젝트 한정, 완료 시각 순 |
| `review-task ID` | 성공 작업 품질 평가 기록 | `--verdict {pass,fail}` 필수, `--score 1-5`, `--notes`, fail 전용 `--reopen PROMPT` |
| `queue-status` | 큐·에이전트·자동화 설정 요약 | 작업 수, 승인 대기 목록, 에이전트 상태, `global_auto_run`, `auto_commit_enabled`, `draft_pr_enabled` |
| `daily-summary` | 로컬 날짜의 프로젝트별 종료 활동 요약 | `--date YYYY-MM-DD`(생략 시 오늘), 완료·실패·차단·취소·실행·fallback·worktree·사용량 제한 |
| `plan-batch PATH` | 토큰 예산으로 작업을 분배하고 원자적으로 등록 | JSON 파일, `--dry-run` |
| `split-task ID PATH` | 대기 작업을 같은 JSON 계약의 하위 작업 배치로 원자적 교체 | JSON 파일, `--dry-run` |
| `cleanup-worktree ID` | 병합 완료된 종료 작업의 worktree 제거 | dirty/미병합 커밋이면 거부, 브랜치는 유지 |
| `update-task ID` | 대기(waiting) 작업 부분 수정 | 준 플래그만 덮어씀. `--auto-commit`/`--no-auto-commit`, `--draft-pr`/`--no-draft-pr`, `--approval auto`, `--repeat`, `--clear-repeat`, `--priority`, `--estimated-tokens`, `--clear-not-before`, `--tag`(전체 교체), `--depends-on`(전체 교체) |
| `retry-task ID` | 실패(failed)/차단(blocked) 작업 재대기 | 재시도 횟수·오류 초기화 |
| `cancel-task ID` | 대기·재시도대기·차단 작업 취소 | |
| `duplicate-task ID` | 작업 복제(새 waiting 작업) | |
| `move-top ID` | 대기 작업을 맨 위로(P100) | |
| `archive-task ID` | 성공(succeeded) 작업 보관 | |
| `delete-task ID` | 작업+실행 기록 영구 삭제 | reserved/running은 불가, 의존 작업 있으면 불가 |
| `delete-tasks` | 프로젝트 범위 영구 삭제 | `--project PATH` 필수, `--created-by`, `--creator-session`, `--status` 반복 가능; 건너뛴 사유 반환 |
| `set-agent AGENT STATE` | 에이전트 수동 상태 설정 | `claude|codex` × `available|cooldown|disabled`, cooldown은 `--until "YYYY-MM-DD HH:MM"` |

## 작업 출처와 프로젝트 범위 관리

`created_by`는 작업을 처음 만든 호출 주체이고 `creator_session_id`는 호출자가 제공한 불투명
세션 식별자다. GUI에서 만든 작업은 `user`, 기존 DB에서 이관한 작업은 추정하지 않고
`unknown`이다. 반복 후속 회차는 원본 출처를 유지하고, 템플릿으로 만든 작업·분할 하위
작업·재작업·복제는 해당 명령을 실행한 현재 actor에 귀속된다.

```powershell
# 필요하면 이전 세션 ID로 재발견
... --json list-tasks --project "C:\projects\sample" --created-by codex --active
... --json list-tasks --project "C:\projects\sample" --created-by codex `
    --creator-session "codex-20260718-a"

# 단일 작업 상세에는 recent_events가 포함됨
... --json show-task 42

# 작업이 이미 영구 삭제됐어도 이벤트는 조회 가능
... --json list-events --task-id 42
... --json list-events --project "C:\projects\sample" --actor codex
```

- `created_by`는 `unknown|user|claude|codex|system`이다. v10 이전 작업은 추정하지 않고
  `unknown`으로 마이그레이션한다.
- 반복 후속 회차는 최초 작업의 생성자·세션을 승계하고 생성 이벤트의 actor는 `system`이다.
- 템플릿으로 만든 작업, 분할 하위 작업, 복제·재작업은 호출한 AI의 actor/세션을 사용한다.
- 관리 이벤트는 생성·수정·취소·재시도·맨 위 이동·보관·평가·분할·삭제를 기록한다.
  이벤트에는 당시 프로젝트와 제목도 저장되어 작업 행이 없어도 사람이 확인할 수 있다.

프로젝트 전체 또는 한 AI/세션이 만든 작업을 정리할 때는 `delete-task` 반복 대신 범위가
명시된 `delete-tasks`를 사용한다. 영구 삭제 자체에 사용자 확인은 요구하지 않는다.

```powershell
... --actor claude --actor-session "claude-cleanup-1" --json delete-tasks `
    --project "C:\projects\sample" --created-by claude
```

대상 안의 의존 작업은 가능한 순서로 먼저 삭제한다. `reserved`/`running`, 남아 있는
worktree, 범위 밖 작업이 참조하는 의존·분할·재작업 원본은 삭제하지 않고 `skipped`에
ID·제목·사유를 반환한다. 나머지 삭제는 그대로 커밋된다.

## 반복 작업 템플릿

템플릿은 DB의 `task_templates`에 단일 작업 초안을 저장합니다. 같은 이름으로 다시
`save-template`을 실행하면 기존 행을 덮어쓰며 텍스트 출력은 `덮어씀`, JSON 출력은
`"overwritten": true`로 이를 알립니다. 저장 시 프로젝트를 생략할 수 있지만 실제 등록이나
드라이런에는 템플릿 또는 `--project` 오버라이드로 유효한 프로젝트 경로가 있어야 합니다.

```powershell
# 자주 쓰는 구현+검증 작업 정의 저장
... save-template feature `
    --title "{feature} 구현" `
    --prompt "{feature} 구현 후 {checks} 통과" `
    --project "C:\projects\sample" --agent codex --priority 80 `
    --estimated-tokens 20000 --approval auto --auto-commit --json

# 조회
... list-templates --json
... show-template feature --json

# DB에 작업을 만들지 않고 치환·경로·의존성까지 검증
... add-from-template feature --var feature=검색 --var checks=pytest/ruff `
    --priority 90 --dry-run --json

# 실제 waiting 작업 등록
... add-from-template feature --var feature=검색 --var checks=pytest/ruff --json

# 삭제(이미 생성된 작업에는 영향 없음)
... delete-template feature --json
```

자리표시자는 title/prompt의 단순한 `{변수명}`만 지원합니다. 변수명은 Python 식별자
형식이며 속성 접근, 인덱스, 형식 지정(`:...`), 변환(`!r`)은 허용하지 않습니다.
`--var`는 첫 `=`에서만 나누므로 값에는 `=`가 들어갈 수 있습니다. 필요한 변수가 하나라도
없거나 치환 값/오버라이드 결과에 `{변수명}`이 남으면 누락 또는 미해결 변수명을 정렬해
나열하고 등록을 거부합니다. 사용하지 않은 추가 변수는 무시합니다.

`--title`, `--prompt`, `--project`, `--agent`, `--priority`, `--estimated-tokens`,
`--approval`, `--auto-commit`/`--no-auto-commit`, `--draft-pr`/`--no-draft-pr`, `--not-before`, `--max-retries`, `--retry-delay`, `--repeat`, `--tag`,
`--depends-on`은 저장값보다 우선합니다. title/prompt를 직접 오버라이드하면 해당 템플릿
필드의 변수는 요구하지 않습니다. `--tag`와 `--depends-on`은 각각 전체 목록을 교체합니다.
`--dry-run`도 실제 등록과 같은 `TaskService` 검증을 수행하지만 `tasks` 행을 만들지 않습니다.

## 완료 결과 배치 검토

AQR은 LLM을 호출하거나 자동 채점하지 않습니다. 큐를 조작하는 AI 또는 사용자가 결과를
판단한 뒤, AQR은 평가와 후속 작업 관계만 저장합니다. 여러 작업을 연속 실행한 다음 아래
흐름으로 한꺼번에 검토합니다.

```powershell
# 1. 전체 또는 프로젝트별 미평가 성공 작업을 완료 시각 순으로 조회
... pending-reviews --json
... pending-reviews --project "C:\projects\sample" --json

# 2. 실행 로그·결과를 확인하고 평가
... show-task 42 --json
... review-task 42 --verdict pass --score 5 --notes "검증 완료" --json

# 3. 실패 평가와 후속 프롬프트를 한 트랜잭션으로 등록
... review-task 43 --verdict fail --score 2 --notes "회귀 테스트 누락" `
    --reopen "누락된 회귀 테스트를 추가하고 전체 테스트를 다시 실행하세요." --json
```

평가는 `succeeded` 작업에만 기록할 수 있습니다. 재평가는 기존 행을 수정하지 않고 새
이력 행을 추가하며, 가장 최근 행이 `list-tasks`/`show-task`의 현재 평가가 됩니다.
`review_count`와 `review-task` 결과의 `revision`, `previous_review_count`로 재평가 여부를
확인할 수 있습니다. 첫 평가가 생기면 해당 작업은 `pending-reviews`에서 제외됩니다.

`--reopen`은 `fail`과만 사용할 수 있습니다. 생성되는 waiting 작업은 원본의 프로젝트,
AI 정책, 우선순위, 토큰 추정, 승인·재시도 설정, 태그를 승계하고 옵션 문자열을 새
프롬프트로 사용합니다. 제목은 `Rework: <원본 제목>`이며 `rework_of_task_id`로 원본과
연결됩니다. 평가 저장과 후속 작업 등록은 같은 커밋이므로 어느 한쪽이 실패하면 둘 다
남지 않습니다.

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

## 성공 후 자동 Git 커밋

자동 커밋은 기본값이 off이며 다음 두 opt-in이 모두 있어야 동작한다.

```toml
[scheduler]
auto_commit_enabled = true
```

```powershell
# 새 작업
... add-task --title "회귀 테스트 추가" --prompt "..." `
    --project "C:\projects\sample" --agent codex --approval auto `
    --auto-commit --json

# 기존 waiting 작업 토글
... update-task 7 --auto-commit --json
... update-task 7 --no-auto-commit --json
```

- AQR은 실제 실행 cwd에서 CLI를 시작하기 전에 `git status --porcelain`을 캡처한다.
  일반 프로젝트와 재사용 worktree 모두 같은 검사를 받는다.
- 비Git 디렉터리, 실행 전부터 dirty인 저장소, 성공 후 변경이 없는 저장소는 작업 실패가
  아니라 커밋 생략이다. 특히 사전 dirty 저장소는 이전 작업을 섞지 않기 위해 거부한다.
- clean 상태에서 시작해 성공 후 변경이 있으면 `git add -A`와
  `aqr: <작업 제목> (task #<ID>)` 형식의 커밋을 실행한다. 본문에는 실행 ID와 AI가 들어간다.
- worktree 실행은 해당 `aqr/T<ID>` 브랜치에 커밋한다. push·PR은 아래의 별도 opt-in에서만 수행한다.
- Git 명령이나 커밋 훅이 실패해도 작업은 `succeeded`를 유지한다. 실패 사유만 실행 기록에
  남으며, `show-task ID --json`의 `recent_runs[].auto_commit_hash` 또는
  `recent_runs[].auto_commit_result`로 확인한다.

## 자동 draft PR 생성

draft PR은 기본값이 off이며 자동 커밋 opt-in까지 포함해 다음 조건이 모두 필요하다.

```toml
[scheduler]
worktree_parallel_enabled = true
auto_commit_enabled = true
draft_pr_enabled = true
```

```powershell
# 새 작업 또는 템플릿 저장
... add-task --title "회귀 테스트 추가" --prompt "..." `
    --project "C:\projects\sample" --auto-commit --draft-pr --json
... save-template pr-ready --title "{feature} 구현" --prompt "..." `
    --auto-commit --draft-pr --json

# waiting 작업 토글. draft_pr을 끈 뒤에만 auto_commit을 끌 수 있다.
... update-task 7 --auto-commit --draft-pr --json
... update-task 7 --no-draft-pr --no-auto-commit --json
```

전제조건은 `origin` 원격, 설치된 GitHub CLI(`gh`), 그리고 `gh auth login`으로 완료된
인증이다. AQR은 인증 정보를 저장하지 않는다. 작업별 `draft_pr=true`인데
`auto_commit=false`인 등록·수정·템플릿은 검증 오류로 거부한다.

push 대상 안전성 때문에 실제 실행이 관리형 `worktrees_dir/T<ID>` checkout의
`aqr/T<ID>` 브랜치인 경우에만 발행한다. 잠금 충돌이 없어 원 프로젝트에서 실행된 작업은
현재 브랜치가 무엇이든 자동 push하지 않고 생략 사유를 기록한다. 이 정책상
`worktree_parallel_enabled=true`만으로 모든 작업의 PR 생성을 보장하지는 않는다.

성공한 실행에서 자동 커밋이 실제 생성되면 AQR은 다음 순서로 처리한다.

1. `origin`, 현재 관리형 브랜치, `gh` 설치와 인증을 확인한다.
2. 같은 head 브랜치의 기존 open/closed/merged PR이 있으면 새로 만들지 않고 URL을 기록한다.
3. 없다면 `git push -u origin aqr/T<ID>` 후 제목을 작업 제목으로 하여
   `gh pr create --draft`를 실행한다. 본문에는 task ID, 실행 ID, agent, commit hash가 들어간다.

원격 없음, gh 부재·미인증, 커밋 미생성, 비worktree 실행은 `skipped:`로, push·조회·생성
오류는 `failed:`로 `recent_runs[].draft_pr_result`에 남는다. URL은
`recent_runs[].draft_pr_url`에 저장된다. 어느 경우에도 이미 확정된 `succeeded` 상태를
되돌리지 않는다.

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
