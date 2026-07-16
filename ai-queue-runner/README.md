# AI Queue Runner

Claude Code와 Codex CLI에 맡길 작업을 미리 등록하고, 에이전트 상태·우선순위·선행 작업·대기 시간에 따라 안전하게 실행하는 Windows용 로컬 데스크톱 앱입니다.

작업과 실행 이력은 SQLite에 유지되며, 동일 작업의 중복 배정과 동일 프로젝트의 동시 실행을 DB 예약 단계에서 막습니다. 전체 요구사항은 [`../AI_QUEUE_RUNNER_PLAN.md`](../AI_QUEUE_RUNNER_PLAN.md), 구현 구조는 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md), UI 기준은 [`DESIGN.md`](DESIGN.md)에 있습니다.

## 현재 상태

- [x] Phase 0: 프로젝트·설정·로깅·테스트 기반
- [x] Phase 1: 도메인 상태 전이, SQLite/SQLAlchemy 영속화
- [x] Phase 2: 후보 필터링, 점수 계산, 원자적 예약, 프로젝트 잠금
- [x] Phase 3: 비동기 프로세스 실행, 취소·타임아웃·재시도·크래시 복구
- [x] Phase 4: Claude Code/Codex CLI 어댑터와 오류 분류
- [x] Phase 5: PySide6 작업 관리 UI, 승인, 로그, 트레이, 알림
- [x] Phase 6: 경로 정책, 백업·설정 내보내기·시작 프로그램, PyInstaller 빌드, 소크 테스트

## 빠른 시작

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

## 사용 흐름

1. Claude/Codex 상태 패널에서 사용 가능 여부나 재사용 가능 시각을 설정합니다.
2. 작업 추가 창에서 절대 프로젝트 경로, 프롬프트, 담당 에이전트, 우선순위, 승인 방식, 재시도와 선행 작업을 입력합니다.
3. 스케줄러를 시작합니다. 에이전트별로 한 작업만 원자적으로 예약합니다.
4. 자동 실행이 꺼져 있거나 확인이 필요한 작업은 경로·에이전트·명령·프롬프트를 보고 승인합니다.
5. 작업을 선택해 즉시 실행, 맨 위로 이동, 복제, 취소, 재시도, 완료 보관, 로그/프로젝트 폴더 열기를 사용할 수 있습니다.
6. 하단 상세/로그에서 실행 결과를 확인합니다. 화면은 최근 2MB를 표시하고 전체 로그는 파일에 보존합니다.

프롬프트는 명령행 인자가 아니라 표준 입력으로 전달됩니다. Claude는 `permission-mode auto`, Codex는 `workspace-write` 샌드박스와 승인 비활성 모드에서 실행하며 위험한 우회 플래그는 사용하지 않습니다.

## AI/CLI 연동

GUI를 열지 않고도 같은 설정과 `TaskService` 검증을 거쳐 작업을 추가·조회·취소할 수 있습니다. `--config`를 생략하면 GUI와 동일하게 현재 폴더의 `config.toml`, 없으면 `%LOCALAPPDATA%\AIQueueRunner\config.toml`을 사용합니다.

```powershell
# AI 도우미가 특정 프로젝트의 작업을 등록
..\.venv\Scripts\python -m ai_queue_runner --config config.toml add-task `
  --title "회귀 테스트 추가" --prompt "변경된 예약 흐름의 회귀 테스트를 추가하세요." `
  --project "C:\projects\sample" --agent codex --priority 80 --approval confirm

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
```

`--not-before "YYYY-MM-DD HH:MM"`은 실행하는 PC의 로컬 시간으로 해석해 DB에는 UTC로 저장합니다. `--json`은 각 하위 명령 뒤에도 지정할 수 있습니다.

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
