# AI Queue Runner

Claude Code와 Codex CLI에 맡길 작업을 미리 등록하고, 에이전트 상태·우선순위·선행 작업·대기 시간에 따라 안전하게 실행하는 Windows용 로컬 데스크톱 앱입니다.

작업과 실행 이력은 SQLite에 유지되며, 동일 작업의 중복 배정과 동일 프로젝트의 동시 실행을 DB 예약 단계에서 막습니다. 각 작업에는 생성 AI와 세션이 기록되고 관리 이력은 작업을 영구 삭제해도 남습니다. 명시적으로 opt-in하면 잠금 충돌 작업만 전용 Git worktree에서 병렬 실행할 수 있습니다. 전체 요구사항은 [`../AI_QUEUE_RUNNER_PLAN.md`](../AI_QUEUE_RUNNER_PLAN.md), 구현 구조는 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), UI 기준은 [`DESIGN.md`](DESIGN.md)에 있습니다.

## 현재 상태

- [x] Phase 0: 프로젝트·설정·로깅·테스트 기반
- [x] Phase 1: 도메인 상태 전이, SQLite/SQLAlchemy 영속화
- [x] Phase 2: 후보 필터링, 점수 계산, 원자적 예약, 프로젝트 잠금
- [x] Phase 3: 비동기 프로세스 실행, 취소·타임아웃·재시도·크래시 복구
- [x] Phase 4: Claude Code/Codex CLI 어댑터와 오류 분류
- [x] Phase 5: PySide6 작업 관리 UI, 승인, 로그, 트레이, 알림
- [x] Phase 6: 경로 정책, 백업·설정 내보내기·시작 프로그램, PyInstaller 빌드, 소크 테스트
- [x] Phase 7: 예상 토큰 기반 Claude/Codex 작업량 분배와 원자적 배치 등록

## 빠른 시작

GUI 없이 자동 승인 작업을 계속 실행하려면 별도 터미널에서 워커를 실행합니다.

~~~powershell
..\.venv\Scripts\python -m ai_queue_runner --config config.toml worker
~~~

워커가 실행 중이어도 GUI는 언제든 열 수 있습니다. GUI는 같은 SQLite DB와 로그를
5초마다 갱신해 작업 진행을 보여 주며, 상단에 워커 PID와 관찰 모드를 표시합니다.
대기·일시정지 중에는 10초, GUI 스케줄러 실행 중에는 1초 간격이며 새로고침 버튼은
언제든 즉시 최신 상태를 읽습니다.
GUI를 닫아도 워커가 실행 중인 작업은 중단되지 않습니다. 확인 승인 작업은 워커가
선점하지 않으므로 GUI에서 직접 실행·승인할 수 있습니다.

Python 3.12 이상 3.15 미만과 설치된 `claude`, `codex` CLI가 필요합니다.

```powershell
# 저장소 루트의 가상환경 사용
..\.venv\Scripts\python -m pip install -e '.[dev]'

# 예제 설정 복사 후 allowed_project_roots를 반드시 지정
Copy-Item config.example.toml config.toml

# 실행
..\.venv\Scripts\python -m ai_queue_runner --config config.toml
```

설정 파일 없이 실행하면 `%LOCALAPPDATA%\AIQueueRunner` 아래에 DB와 로그를 저장합니다. 이 경우 허용된 프로젝트 루트가 비어 있으므로 작업 실행은 안전하게 차단됩니다. 자동 실행이 필요하면 `config.toml`에서 다음 두 값을 명시적으로 설정하십시오.

```toml
[app]
global_auto_run = true
allowed_project_roots = ["C:/Users/me/projects"]
```

`global_auto_run = false`이면 모든 예약 작업에 승인 창이 표시됩니다. `true`여도 작업의 승인 모드가 `confirm`이면 승인해야 실행됩니다.

같은 Git 프로젝트를 에이전트별로 격리해 병렬 실행하려면 `[scheduler]`의
`worktree_parallel_enabled = true`를 명시합니다(기본 `false`). 충돌 작업은
`worktrees_dir/T<ID>`와 `aqr/T<ID>` 브랜치를 사용하며 자동 머지하지 않습니다.

성공한 실행 결과를 자동 커밋하려면 `[scheduler] auto_commit_enabled = true`와 작업별
`--auto-commit`을 모두 켭니다. 실행 전부터 dirty인 저장소는 이전 변경을 섞지 않도록
커밋을 생략하며, Git 실패가 작업의 `succeeded` 상태를 되돌리지는 않습니다.

자동 커밋된 관리형 worktree 브랜치를 draft PR로 올리려면 추가로 `[scheduler]
draft_pr_enabled = true`와 작업별 `--draft-pr`을 켭니다. `gh` 설치·인증과 `origin` 원격이
필요하며, 임의의 현재 브랜치를 push하지 않도록 실제 `aqr/T<ID>` worktree 실행에서만
동작합니다. 생략·실패도 작업 성공 상태를 바꾸지 않고 실행 이력에 사유를 남깁니다.

## 사용 흐름

1. Claude/Codex 상태 패널에서 사용 가능 여부나 재사용 가능 시각을 설정합니다.
2. 작업 추가 창에서 절대 프로젝트 경로, 프롬프트, 담당 에이전트, 우선순위, 승인 방식, 재시도와 선행 작업을 입력합니다.
3. 스케줄러를 시작합니다. 에이전트별로 한 작업만 원자적으로 예약합니다.
4. 자동 실행이 꺼져 있거나 확인이 필요한 작업은 경로·에이전트·명령·프롬프트를 보고 승인합니다. 거부한 작업은 차단 상태로 남아 다시 묻지 않으며, 재시도 버튼으로 대기열에 되돌릴 수 있습니다. auto 모드 작업을 "즉시 실행"하면 승인 창 없이 바로 실행됩니다.
5. 작업을 선택해 즉시 실행, 맨 위로 이동, 복제, 취소, 재시도, 완료 보관, 로그/프로젝트 폴더 열기를 사용할 수 있습니다.
6. 하단 상세/로그에서 실행 결과를 확인합니다. 화면은 최근 2MB를 표시하고 전체 로그는 파일에 보존합니다.

프롬프트는 명령행 인자가 아니라 표준 입력으로 전달됩니다. Claude는 `permission-mode auto`, Codex는 `workspace-write` 샌드박스와 승인 비활성 모드에서 실행하며 위험한 우회 플래그는 사용하지 않습니다.

## AI/CLI 연동

큐 관리 명령뿐 아니라 worker 명령으로 GUI 없는 실행 엔진도 시작할 수 있습니다.
프로세스 잠금은 같은 DB에 GUI 스케줄러와 워커가 동시에 붙어 중복 스케줄링하는 것을
막습니다. 다만 GUI의 조회·작업 등록·로그 확인은 잠금과 무관하게 계속 사용할 수 있습니다.

GUI를 열지 않고도 같은 설정과 `TaskService` 검증을 거쳐 큐 전체를 관리할 수 있습니다. 조회(`list-tasks`, `show-task`, `list-events`, `queue-status`, `daily-summary`, `pending-reviews`)와 작업 제어(`add-task`, `update-task`, `retry-task`, `cancel-task`, `duplicate-task`, `move-top`, `archive-task`, `delete-task`, `delete-tasks`), 반복 작업 정의(`save-template`, `list-templates`, `show-template`, `add-from-template`, `delete-template`), 완료 품질 평가(`review-task`), 원자적 작업 분할(`split-task`), worktree 정리(`cleanup-worktree`), 에이전트 상태(`set-agent`), 토큰 배치 계획(`plan-batch`)까지 지원하며, AI 에이전트용 상세 규약은 [`docs/AI_GUIDE.md`](docs/AI_GUIDE.md)에 있습니다. `--config`를 생략하면 GUI와 동일하게 현재 폴더의 `config.toml`, 없으면 `%LOCALAPPDATA%\AIQueueRunner\config.toml`을 사용합니다.

```powershell
# AI 도우미가 특정 프로젝트의 작업을 등록
..\.venv\Scripts\python -m ai_queue_runner --config config.toml `
  --actor codex --actor-session "codex-session-42" add-task `
  --title "회귀 테스트 추가" --prompt "변경된 예약 흐름의 회귀 테스트를 추가하세요." `
  --project "C:\projects\sample" --agent codex --priority 80 --approval confirm `
  --auto-commit --draft-pr

# 선택한 날짜의 오전 9시(로컬 시간) 이후 실행하도록 예약
..\.venv\Scripts\python -m ai_queue_runner --config config.toml add-task `
  --title "월요일 문서 점검" --prompt "README와 실제 명령을 대조하세요." `
  --project "C:\projects\sample" --not-before "2026-07-20 09:00" --tag docs

# 조회, 상세 확인, 취소
..\.venv\Scripts\python -m ai_queue_runner --config config.toml list-tasks --status waiting
..\.venv\Scripts\python -m ai_queue_runner --config config.toml show-task 1
..\.venv\Scripts\python -m ai_queue_runner --config config.toml cancel-task 1

# 기계 판독 가능한 출력(열거형은 문자열, 시각은 UTC ISO-8601)
..\.venv\Scripts\python -m ai_queue_runner --config config.toml --json list-tasks

# 프로젝트·생성 AI·세션으로 다시 찾고, 같은 범위를 영구 정리
..\.venv\Scripts\python -m ai_queue_runner --config config.toml --json list-tasks `
  --project "C:\projects\sample" --created-by codex `
  --creator-session "codex-session-42" --active
..\.venv\Scripts\python -m ai_queue_runner --config config.toml --json delete-tasks `
  --project "C:\projects\sample" --created-by codex `
  --creator-session "codex-session-42"
..\.venv\Scripts\python -m ai_queue_runner --config config.toml --json list-events `
  --project "C:\projects\sample"

# 오늘 또는 지정한 로컬 날짜의 프로젝트별 실행 결과 요약
..\.venv\Scripts\python -m ai_queue_runner --config config.toml daily-summary --json
..\.venv\Scripts\python -m ai_queue_runner --config config.toml daily-summary `
  --date 2026-07-17 --json

# 완료 작업을 나중에 배치 검토하고 실패 결과를 재작업으로 연결
..\.venv\Scripts\python -m ai_queue_runner --config config.toml pending-reviews --json
..\.venv\Scripts\python -m ai_queue_runner --config config.toml review-task 42 `
  --verdict fail --score 2 --notes "회귀 테스트 누락" `
  --reopen "누락된 회귀 테스트를 추가하고 전체 검증하세요." --json
```

`--not-before "YYYY-MM-DD HH:MM"`은 실행하는 PC의 로컬 시간으로 해석해 DB에는 UTC로 저장합니다. `--json`은 각 하위 명령 뒤에도 지정할 수 있습니다.
달력 탭에서는 날짜를 선택한 뒤 **이 날짜 요약** 버튼으로 같은 집계를 확인할 수 있습니다.

### 토큰 작업량 배치 계획

`plan-batch`는 JSON에 적힌 Claude/Codex 토큰 예산과 작업별 `estimated_tokens`를
사용해 `any` 작업을 결정적으로 분배합니다. 전용 작업을 먼저 반영한 뒤 큰 `any`
작업부터 예상 사용률이 낮은 에이전트에 배치하며, 동률이면 Claude를 선택합니다.
하나라도 예산·경로·의존성 검증에 실패하면 작업을 전혀 등록하지 않습니다.

```json
{
  "budgets": {"claude": 100000, "codex": 80000},
  "tasks": [
    {
      "title": "아키텍처 검토",
      "prompt": "현재 구조의 위험을 검토하세요.",
      "project_path": "C:/projects/sample",
      "agent_policy": "claude",
      "estimated_tokens": 30000,
      "approval_mode": "auto"
    },
    {
      "title": "회귀 테스트",
      "prompt": "회귀 테스트를 추가하세요.",
      "project_path": "C:/projects/sample",
      "agent_policy": "any",
      "estimated_tokens": 20000,
      "approval_mode": "auto"
    }
  ]
}
```

```powershell
# 배치 등록 없이 분배 결과만 확인
..\.venv\Scripts\python -m ai_queue_runner --config config.toml plan-batch workload.json --dry-run --json

# 같은 계획을 한 트랜잭션으로 등록
..\.venv\Scripts\python -m ai_queue_runner --config config.toml plan-batch workload.json --json
```

일반 작업은 예상 토큰을 생략하거나 `0`으로 둘 수 있지만, `plan-batch`와 `split-task`의
모든 작업은 양수 추정치가 필요합니다. `dependency_ids`는 기존 DB 작업을,
`depends_on_indices`는 같은 JSON 배치의 0-기반 형제 작업을 참조합니다. 자기 참조·범위 밖
참조·순환은 등록 전에 거부됩니다.

```powershell
# 큰 작업을 검증만 하거나 원자적으로 하위 작업 배치로 교체
..\.venv\Scripts\python -m ai_queue_runner --config config.toml split-task 42 split-plan.json --dry-run --json
..\.venv\Scripts\python -m ai_queue_runner --config config.toml split-task 42 split-plan.json --json
```

분할된 원본은 취소되고 원본을 기다리던 작업은 모든 하위 작업의 성공을 기다리도록
의존성이 승계됩니다. 원본/하위 ID는 `list-tasks`와 `show-task`에서 확인할 수 있습니다.

## 유지보수 명령

아래 명령은 GUI를 시작하지 않고 작업을 완료한 뒤 종료합니다.

```powershell
# WAL 모드에서도 일관된 SQLite 온라인 백업 + integrity_check
..\.venv\Scripts\python -m ai_queue_runner --config config.toml --backup backups\queue.db

# 검증·경로 해석이 끝난 유효 설정을 TOML로 내보내기
..\.venv\Scripts\python -m ai_queue_runner --config config.toml --export-config exported.toml

# 현재 사용자(HKCU Run) 시작 프로그램 등록/해제
..\.venv\Scripts\python -m ai_queue_runner --config config.toml --startup enable
..\.venv\Scripts\python -m ai_queue_runner --startup disable
```

## 검증과 Windows 빌드

```powershell
..\.venv\Scripts\python -m pytest -q
..\.venv\Scripts\python -m ruff check src tests

# PyInstaller onedir 빌드 후 실행 파일 버전 스모크까지 수행
.\scripts\build_windows.ps1

# 기본 8시간, 매 반복마다 20개 작업·재시작·중복 실행 방지 검증
.\scripts\run_soak.ps1
```

빌드 결과는 `dist\AIQueueRunner\AIQueueRunner.exe`입니다. Qt/PySide 배포 전에는 [`THIRD_PARTY_NOTICES.md`](THIRD_PARTY_NOTICES.md)의 LGPL 안내와 해당 라이선스 의무를 확인하십시오.

## 검증 범위와 제한

- Codex와 Claude Code의 실 CLI 성공 경로를 모두 비대화형·임시 세션 모드로 검증했습니다.
- Claude 성공 출력에도 `rate_limit_event(status=allowed)`가 포함되는 실제 동작을 확인해 정상 이벤트와 제한 이벤트를 구분합니다.
- 패키지 GUI, Qt Windows 플랫폼 플러그인, 설정 내보내기와 SQLite 백업을 스모크 검증했습니다.
- 20개 작업·중간 런타임 재시작 소크 시나리오는 자동 테스트와 가속 스크립트로 통과했습니다. 실제 8시간 검증은 `scripts/run_soak.ps1`을 장시간 실행해야 합니다.
