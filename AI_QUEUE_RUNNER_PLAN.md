# AI 작업 대기열 스케줄러 개발 계획

> Claude가 프로젝트 총괄·설계·검토를 담당하고, Codex가 구현·테스트·수정을 담당하는 것을 전제로 한다.  
> 1차 목표는 **개인용 로컬 프로그램**이며, Claude CLI와 Codex CLI를 공식적으로 실행 가능한 범위 안에서 자동 호출한다.

---

## 0. 문서 해석 규칙

| 표기 | 의미 |
|---|---|
| `[REQ]` | 반드시 구현해야 하는 요구사항 |
| `[DECISION]` | 이미 확정된 설계 결정 |
| `[TODO]` | 구현이 필요한 항목 |
| `[VERIFY]` | 테스트 또는 검증이 필요한 항목 |
| `[LATER]` | 1차 버전에서는 제외 |
| `[RISK]` | 장애 가능성 또는 정책상 주의점 |

Claude와 Codex는 작업을 시작하기 전에 이 문서와 `AGENTS.md`를 읽는다.

---

# 1. 프로젝트 개요

## 1.1 프로젝트명

임시명: **AI Queue Runner**

## 1.2 해결하려는 문제

Claude 또는 Codex의 사용량이 소진되면 사용자는 제한이 해제될 때까지 기다려야 한다.  
기다리는 동안 해야 할 작업을 잊거나, 어떤 작업을 어느 AI에게 맡겨야 하는지 다시 정리해야 하는 문제가 생긴다.

이 프로그램은 사용자가 미리 작업을 등록해두면 다음 조건에 따라 실행할 작업을 자동 선택한다.

- Claude 전용 작업
- Codex 전용 작업
- Claude 또는 Codex 누구나 가능한 작업
- 작업별 우선순위
- AI별 사용 가능 시각
- 오래 기다린 작업 우대
- 실행 실패 시 재시도
- 프로젝트별 작업 디렉터리

## 1.3 1차 목표

`[REQ]`

1. 사용자가 작업을 등록할 수 있다.
2. 작업마다 담당 AI를 지정할 수 있다.
3. 우선순위를 지정할 수 있다.
4. Claude와 Codex의 사용 가능 상태 또는 사용 가능 시각을 기록할 수 있다.
5. 사용 가능한 AI가 생기면 적합한 작업을 자동 선택한다.
6. 선택한 작업을 해당 CLI로 실행한다.
7. 실행 결과와 로그를 저장한다.
8. 프로그램을 재시작해도 작업과 상태가 유지된다.
9. 동일 작업이 Claude와 Codex에 동시에 배정되지 않는다.
10. 실행 전에 선택적으로 사용자 승인을 요구할 수 있다.

## 1.4 1차 버전에서 제외

`[LATER]`

- ChatGPT 웹사이트 또는 Claude 웹사이트 브라우저 자동 조작
- 서비스 사용량을 비공식 API나 화면 스크래핑으로 자동 감지
- 여러 사용자를 위한 로그인 및 서버 기능
- 결제 기능
- 모바일 앱
- 클라우드 동기화
- 팀 협업 권한 관리
- AI가 다른 AI에게 직접 자유롭게 작업을 생성하는 완전 자율 모드

---

# 2. 핵심 사용 흐름

```text
사용자 작업 등록
    ↓
작업 대기열 저장
    ↓
Claude/Codex 상태 확인
    ↓
실행 가능한 AI 탐색
    ↓
에이전트별 후보 작업 필터링
    ↓
점수 계산 및 작업 선택
    ↓
DB에서 원자적으로 작업 선점
    ↓
CLI 실행
    ↓
로그 저장
    ↓
성공 / 실패 / 재시도 / 사용자 확인
```

예시:

```text
작업 A
- 제목: 아키텍처 재검토
- 담당: Claude 전용
- 우선순위: 80

작업 B
- 제목: 테스트 코드 작성
- 담당: Codex 전용
- 우선순위: 70

작업 C
- 제목: README 정리
- 담당: Any
- 우선순위: 90
```

Claude와 Codex가 모두 사용 가능한 경우:

```text
Claude → 작업 A
Codex  → 작업 C
작업 B → Codex의 다음 후보
```

단, 전용 작업의 우선순위가 충분히 높거나 장시간 대기한 경우 전용 작업을 먼저 실행할 수 있다.

---

# 3. 기술 스택

## 3.1 확정 기술

`[DECISION]`

```text
Language       Python 3.12+
UI             PySide6
Database       SQLite
ORM            SQLAlchemy 2.x
Scheduler      APScheduler
Process        asyncio + subprocess
Validation     Pydantic
Tests          pytest
Logging        Python logging
Packaging      PyInstaller
Config         TOML
```

## 3.2 PySide6를 선택하는 이유

- 개인용 데스크톱 앱에 적합
- 시스템 트레이 지원
- 장시간 실행하기 쉬움
- Streamlit보다 로컬 프로그램처럼 동작
- 재부팅 후 자동 실행 기능을 붙이기 쉬움
- 나중에 작업 로그와 상태를 여러 패널로 분리하기 쉬움

## 3.3 초기 개발 단순화 원칙

`[DECISION]`

- 비동기 실행은 CLI 프로세스 관리에만 제한적으로 사용한다.
- DB는 SQLite 하나만 사용한다.
- 플러그인 구조는 Claude와 Codex 어댑터 2개만 먼저 구현한다.
- 사용량 제한 해제 시각은 사용자가 직접 입력한다.
- 자동 감지는 나중에 어댑터별 공식 지원 여부를 확인한 뒤 추가한다.
- 첫 버전은 Windows를 우선 지원한다.

---

# 4. 기능 요구사항

## 4.1 작업 등록

`[REQ]`

작업 등록 필드:

```yaml
title: string
prompt: string
project_path: absolute path
agent_policy: claude | codex | any
priority: integer 0..100
status: waiting
approval_mode: auto | confirm
max_retries: integer
retry_delay_minutes: integer
created_at: datetime
not_before: datetime | null
tags: list[string]
```

선택 기능:

- 작업 설명
- 선행 작업
- 예상 소요 시간
- 특정 명령 실행 허용 여부
- 완료 후 실행할 검증 명령
- Git 브랜치명
- 작업 결과 요약 요청

## 4.2 AI 상태 관리

각 AI는 아래 상태 중 하나를 가진다.

```text
available
busy
cooldown
disabled
error
unknown
```

필드:

```yaml
agent: claude | codex
status: enum
available_at: datetime | null
current_task_id: integer | null
last_checked_at: datetime
last_error: string | null
```

사용자가 입력할 수 있어야 하는 값:

- 지금 사용 가능
- 특정 시각부터 사용 가능
- 수동 비활성화
- 현재 실행 중인 작업 강제 중단
- 상태 초기화

## 4.3 작업 상태

```text
waiting
reserved
running
succeeded
failed
retry_wait
blocked
cancelled
```

상태 전이:

```text
waiting → reserved → running → succeeded
                    ↘ failed
                    ↘ retry_wait → waiting
                    ↘ blocked
waiting → cancelled
reserved → waiting       # 실행 시작 전 복구
running → failed         # 비정상 종료
```

## 4.4 작업 의존성

`[REQ]`

작업이 다른 작업을 필요로 할 수 있다.

예:

```text
DB 스키마 작성
    ↓
Repository 구현
    ↓
UI 연결
```

선행 작업이 성공하지 않았다면 후속 작업은 후보에서 제외한다.

초기 버전에서는 DAG 전체 편집 UI까지 만들지 않고, 작업 등록 시 선행 작업을 선택하는 기능만 제공한다.

## 4.5 작업 수정 및 관리

`[REQ]`

- 작업 수정
- 작업 복제
- 작업 취소
- 우선순위 변경
- 담당 AI 변경
- 즉시 실행
- 맨 위로 이동
- 실패 작업 재실행
- 완료 작업 보관
- 로그 열기
- 프로젝트 폴더 열기

---

# 5. 스케줄링 규칙

## 5.1 기본 후보 조건

특정 AI `agent`가 작업을 선택할 때 다음 조건을 모두 만족해야 한다.

```text
task.status == waiting
task.not_before <= now 또는 null
task.agent_policy == agent 또는 any
모든 선행 작업이 succeeded
해당 프로젝트가 다른 충돌 작업에 의해 잠겨 있지 않음
재시도 횟수가 max_retries 이하
```

## 5.2 점수 계산

초기 점수 공식:

```text
score =
    priority
  + dedicated_agent_bonus
  + waiting_time_bonus
  + retry_penalty
  + dependency_bonus
```

권장값:

```text
priority                 0~100
dedicated_agent_bonus    +20
waiting_time_bonus       대기 1시간당 +1, 최대 +24
retry_penalty            재시도 1회당 -5
dependency_bonus         후속 작업을 많이 막고 있으면 +0~10
```

Python 의사 코드:

```python
def calculate_score(task, agent, now):
    if task.agent_policy not in (agent, "any"):
        return None

    score = task.priority

    if task.agent_policy == agent:
        score += 20

    waiting_hours = max(0, (now - task.created_at).total_seconds() / 3600)
    score += min(waiting_hours, 24)

    score -= task.retry_count * 5

    score += min(task.blocked_dependents_count * 2, 10)

    return score
```

## 5.3 동점 처리

점수가 같을 때 다음 순서로 선택한다.

1. 전용 작업
2. 우선순위가 높은 작업
3. 더 오래 기다린 작업
4. 더 먼저 생성된 작업
5. ID가 작은 작업

## 5.4 Any 작업 배정 규칙

`[REQ]`

`any` 작업이 두 AI에서 동시에 선택되지 않도록 해야 한다.

작업 선택과 상태 변경은 반드시 하나의 DB 트랜잭션 안에서 처리한다.

```text
BEGIN TRANSACTION
후보 작업 조회
최고 점수 작업 선택
status = reserved
assigned_agent = 현재 AI
reserved_at = now
COMMIT
```

SQLite에서 동시성 충돌이 발생하면 짧게 재시도한다.

## 5.5 전용 작업 보호

`[DECISION]`

Any 작업이 모든 실행 슬롯을 점유하여 전용 작업이 계속 밀리는 것을 막기 위해 아래 규칙을 적용한다.

```text
1. 해당 AI 전용 대기 작업이 존재하면 전용 보너스 +20
2. 전용 작업 대기 시간이 12시간 이상이면 추가 +10
3. 우선순위 90 이상의 전용 작업은 Any보다 먼저 고려 가능
```

이를 하드코딩하지 말고 설정 파일에서 변경할 수 있게 한다.

## 5.6 프로젝트 잠금

`[REQ]`

같은 프로젝트 디렉터리에서 Claude와 Codex가 동시에 파일을 수정하면 충돌할 수 있다.

기본 정책:

```text
project_lock_mode = exclusive
```

같은 `project_path`에서는 한 번에 하나의 작업만 실행한다.

추후 옵션:

```text
exclusive   프로젝트 전체 잠금
git_branch  별도 브랜치 사용 시 병렬 실행 허용
none        잠금 없음
```

1차 버전은 `exclusive`만 완전 지원한다.

---

# 6. CLI 실행 어댑터

## 6.1 공통 인터페이스

```python
class AgentAdapter(Protocol):
    name: str

    def is_installed(self) -> bool: ...
    def build_command(self, task: Task) -> list[str]: ...
    async def run(self, task: Task) -> RunResult: ...
    def classify_error(self, output: str, exit_code: int) -> ErrorType: ...
```

## 6.2 Claude 어댑터

`[TODO]`

Claude CLI의 실제 명령 형식은 설치된 버전에서 확인한다.

어댑터 책임:

- 설치 여부 확인
- 프로젝트 경로에서 실행
- 프롬프트 전달
- stdout/stderr 실시간 저장
- 종료 코드 저장
- 사용량 제한 메시지 분류
- 인증 오류 분류
- 사용자 취소 처리

## 6.3 Codex 어댑터

`[TODO]`

Codex CLI의 실제 명령 형식은 설치된 버전에서 확인한다.

어댑터 책임은 Claude 어댑터와 동일하다.

## 6.4 프롬프트 래퍼

각 작업의 사용자 프롬프트 앞뒤에 공통 지침을 추가할 수 있다.

예:

```text
[QUEUE_RUN_CONTEXT]
- Work only inside: {project_path}
- Task ID: {task_id}
- Do not start unrelated work.
- Read AGENTS.md and project instructions first.
- Run relevant tests before completion.
- Return a concise completion summary.
- Do not modify the scheduler database.
[/QUEUE_RUN_CONTEXT]

[USER_TASK]
{prompt}
[/USER_TASK]
```

Claude와 Codex에 전달되는 래퍼는 별도 템플릿 파일로 관리한다.

---

# 7. 사용량 제한 처리

## 7.1 1차 방식

`[DECISION]`

사용자가 AI별 사용 가능 시각을 직접 입력한다.

예:

```text
Claude available_at = 2026-07-16 15:30
Codex available_at  = now
```

스케줄러는 `available_at <= now`가 되면 상태를 `available`로 변경한다.

## 7.2 CLI 실행 중 제한 감지

CLI 출력에서 사용량 제한으로 판단되는 패턴이 발견되면:

```text
작업 상태: running → waiting
AI 상태: busy → cooldown
사용자에게 알림
available_at 입력 요청
```

`[RISK]`

오류 메시지는 CLI 버전에 따라 달라질 수 있다.  
문자열 패턴은 설정 파일에 보관하며 실제 출력에 맞춰 수정 가능해야 한다.

## 7.3 제한 감지 실패 시

- 일반 실패로 기록
- 로그를 사용자에게 표시
- 사용자가 직접 `cooldown`으로 변경 가능
- 작업을 재시도 대기열로 되돌릴 수 있음

---

# 8. 실행 안전장치

## 8.1 승인 모드

작업별 승인 방식:

```text
auto      조건 충족 시 자동 실행
confirm   알림 후 사용자가 승인해야 실행
```

앱 전체 안전 모드:

```text
global_auto_run = false
```

초기 기본값은 `false`로 한다.

## 8.2 실행 전 확인

`confirm` 작업은 아래 정보를 보여준다.

```text
작업 제목
담당 AI
프로젝트 경로
우선순위
실행 명령
프롬프트 미리보기
```

## 8.3 경로 제한

`[REQ]`

허용된 프로젝트 루트 목록을 설정한다.

예:

```toml
allowed_project_roots = [
  "C:/projects",
  "D:/workspace"
]
```

등록한 작업의 경로가 허용 범위 밖이면 실행하지 않는다.

## 8.4 명령어 보안

- 사용자 프롬프트를 셸 문자열에 직접 연결하지 않는다.
- `subprocess`에는 문자열이 아닌 인자 배열을 전달한다.
- 가능한 경우 `shell=False`를 사용한다.
- API 키나 인증 토큰을 DB 로그에 기록하지 않는다.
- 환경 변수 전체를 로그에 출력하지 않는다.

## 8.5 중단 및 복구

- 실행 중인 프로세스 중단 버튼
- 앱 비정상 종료 후 `running` 작업 탐지
- 재시작 시 해당 작업을 `failed` 또는 `waiting`으로 복구할지 사용자에게 선택
- 오래된 `reserved` 상태 자동 해제
- PID와 시작 시각 저장

---

# 9. 데이터 모델

## 9.1 tasks

```sql
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY,
    title TEXT NOT NULL,
    prompt TEXT NOT NULL,
    project_path TEXT NOT NULL,
    agent_policy TEXT NOT NULL,
    assigned_agent TEXT,
    priority INTEGER NOT NULL DEFAULT 50,
    status TEXT NOT NULL DEFAULT 'waiting',
    approval_mode TEXT NOT NULL DEFAULT 'confirm',
    max_retries INTEGER NOT NULL DEFAULT 2,
    retry_count INTEGER NOT NULL DEFAULT 0,
    retry_delay_minutes INTEGER NOT NULL DEFAULT 10,
    not_before DATETIME,
    reserved_at DATETIME,
    started_at DATETIME,
    finished_at DATETIME,
    created_at DATETIME NOT NULL,
    updated_at DATETIME NOT NULL,
    last_error TEXT,
    tags_json TEXT
);
```

## 9.2 task_dependencies

```sql
CREATE TABLE task_dependencies (
    task_id INTEGER NOT NULL,
    depends_on_task_id INTEGER NOT NULL,
    PRIMARY KEY (task_id, depends_on_task_id)
);
```

## 9.3 agent_states

```sql
CREATE TABLE agent_states (
    agent TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    available_at DATETIME,
    current_task_id INTEGER,
    last_checked_at DATETIME,
    last_error TEXT
);
```

## 9.4 task_runs

```sql
CREATE TABLE task_runs (
    id INTEGER PRIMARY KEY,
    task_id INTEGER NOT NULL,
    agent TEXT NOT NULL,
    attempt INTEGER NOT NULL,
    command_json TEXT NOT NULL,
    pid INTEGER,
    exit_code INTEGER,
    stdout_path TEXT,
    stderr_path TEXT,
    started_at DATETIME NOT NULL,
    finished_at DATETIME,
    result_summary TEXT,
    error_type TEXT
);
```

## 9.5 settings

```sql
CREATE TABLE settings (
    key TEXT PRIMARY KEY,
    value_json TEXT NOT NULL
);
```

---

# 10. UI 구성

## 10.1 메인 화면

```text
┌────────────────────────────────────────────────────┐
│ Claude: Cooldown / 15:30 사용 가능                │
│ Codex : Available                                  │
├────────────────────────────────────────────────────┤
│ [작업 추가] [스케줄러 시작] [일시정지] [설정]     │
├────────────────────────────────────────────────────┤
│ 필터: 전체 / 대기 / 실행 / 실패 / 완료            │
│                                                    │
│ P  AI       상태       제목              프로젝트 │
│ 95 Claude   waiting    아키텍처 검토      Momento  │
│ 90 Any      waiting    README 정리        Momento  │
│ 70 Codex    running    테스트 작성        Galpi    │
├────────────────────────────────────────────────────┤
│ 선택 작업 상세 / 로그 / 실행 기록                 │
└────────────────────────────────────────────────────┘
```

## 10.2 작업 등록 창

필수 입력:

- 제목
- 프롬프트
- 프로젝트 경로
- Claude / Codex / Any
- 우선순위
- 자동 실행 / 승인 후 실행
- 선행 작업
- 재시도 횟수

## 10.3 시스템 트레이

`[REQ]`

- 스케줄러 실행/일시정지
- Claude 상태 변경
- Codex 상태 변경
- 다음 작업 표시
- 앱 열기
- 종료

알림 예:

```text
Codex 사용 가능
우선순위 90 작업 “README 정리”를 실행합니다.
```

---

# 11. 프로젝트 파일 구조

```text
ai-queue-runner/
├─ AGENTS.md
├─ README.md
├─ pyproject.toml
├─ config.example.toml
├─ src/
│  └─ ai_queue_runner/
│     ├─ __init__.py
│     ├─ main.py
│     ├─ domain/
│     │  ├─ models.py
│     │  ├─ enums.py
│     │  ├─ scoring.py
│     │  └─ transitions.py
│     ├─ application/
│     │  ├─ task_service.py
│     │  ├─ scheduler_service.py
│     │  ├─ execution_service.py
│     │  └─ recovery_service.py
│     ├─ infrastructure/
│     │  ├─ database.py
│     │  ├─ repositories.py
│     │  ├─ process_runner.py
│     │  ├─ logging_config.py
│     │  └─ adapters/
│     │     ├─ base.py
│     │     ├─ claude.py
│     │     └─ codex.py
│     ├─ ui/
│     │  ├─ main_window.py
│     │  ├─ task_dialog.py
│     │  ├─ agent_status_widget.py
│     │  ├─ task_table.py
│     │  ├─ log_viewer.py
│     │  └─ tray.py
│     ├─ config/
│     │  ├─ loader.py
│     │  └─ defaults.py
│     └─ prompts/
│        ├─ claude_wrapper.txt
│        └─ codex_wrapper.txt
├─ tests/
│  ├─ unit/
│  │  ├─ test_scoring.py
│  │  ├─ test_transitions.py
│  │  ├─ test_scheduler.py
│  │  └─ test_path_validation.py
│  ├─ integration/
│  │  ├─ test_task_reservation.py
│  │  ├─ test_recovery.py
│  │  └─ test_fake_adapter_execution.py
│  └─ fixtures/
├─ scripts/
│  ├─ run_dev.py
│  └─ build_windows.ps1
├─ logs/
└─ data/
```

---

# 12. Claude와 Codex의 역할 분담

## 12.1 Claude 역할

Claude는 **총괄 설계자와 리뷰어** 역할을 맡는다.

`[REQ]`

- 요구사항 해석
- 전체 구조 결정
- 작업을 작은 단위로 분해
- Codex에게 구현 작업 지시
- Codex 결과 검토
- 파일 간 일관성 확인
- 과도한 복잡성 방지
- 테스트 누락 확인
- 다음 작업 결정
- 문서 갱신
- 최종 승인

Claude가 직접 구현해도 되는 경우:

- 작은 설정 변경
- 문서 수정
- 설계 오류로 인해 여러 파일을 함께 조정해야 하는 경우
- Codex 작업 결과를 연결하기 위한 소규모 통합

## 12.2 Codex 역할

Codex는 **구현자와 테스트 실행자** 역할을 맡는다.

`[REQ]`

- Claude가 지정한 범위만 수정
- 기존 구조와 스타일 준수
- 구현 전 관련 파일 읽기
- 단위 테스트 추가
- 테스트 실행
- 실패 원인 수정
- 변경 파일 목록과 검증 결과 보고
- 요청하지 않은 대규모 리팩터링 금지

## 12.3 협업 원칙

```text
Claude: WHAT / WHY / BOUNDARY 결정
Codex : HOW / CODE / TEST 실행
Claude: REVIEW / INTEGRATION / NEXT 결정
```

한 번에 너무 큰 작업을 주지 않는다.

권장 작업 크기:

```text
1개 기능
또는
1개 계층
또는
3~6개 파일
또는
30~90분 안에 검증 가능한 단위
```

---

# 13. AI 협업 프로토콜

## 13.1 작업 상태 파일

프로젝트 루트에 다음 파일을 둔다.

```text
.agent/
├─ STATE.md
├─ TASKS.md
├─ DECISIONS.md
└─ HANDOFF.md
```

### STATE.md

현재 상태만 짧게 기록한다.

```yaml
phase: 2
current_goal: "scheduler core"
active_task: "atomic task reservation"
owner: codex
status: in_progress
blocked_by: null
last_verified:
  command: "pytest tests/unit/test_scheduler.py"
  result: "12 passed"
```

### TASKS.md

```text
[done] T001 프로젝트 초기화
[done] T002 도메인 모델
[in-progress] T003 스케줄러 점수 계산
[todo] T004 원자적 작업 선점
[todo] T005 FakeAdapter 실행
```

### DECISIONS.md

중요한 결정만 누적한다.

```text
D001 PySide6 사용
D002 SQLite + SQLAlchemy 사용
D003 프로젝트 단위 exclusive lock
D004 사용 가능 시각은 1차 버전에서 수동 입력
```

### HANDOFF.md

Claude와 Codex가 교대할 때 사용한다.

```text
FROM: codex
TO: claude
TASK: T004
STATUS: completed
CHANGED:
- src/.../scheduler_service.py
- tests/.../test_task_reservation.py
VERIFIED:
- pytest tests/integration/test_task_reservation.py
- 8 passed
NOTES:
- SQLite lock retry is currently fixed at 3 attempts.
RISKS:
- Windows process termination is not yet implemented.
```

## 13.2 충돌 방지

- 동시에 같은 파일을 수정하지 않는다.
- Codex 작업 중 Claude는 해당 파일을 직접 수정하지 않는다.
- Claude는 Codex 결과를 받은 뒤 통합한다.
- 작업 시작 전 `STATE.md`의 owner를 변경한다.
- 작업 완료 후 반드시 HANDOFF를 작성한다.
- Git diff가 예상 범위를 벗어나면 작업을 중단하고 검토한다.

## 13.3 Git 운영

권장 방식:

```text
main
└─ dev
   ├─ feat/domain-models
   ├─ feat/scheduler-core
   ├─ feat/cli-adapters
   └─ feat/desktop-ui
```

개인 프로젝트이므로 PR을 반드시 만들 필요는 없지만, 기능 단위 커밋은 유지한다.

커밋 예:

```text
feat: add task scoring rules
test: cover dedicated-agent priority
fix: reserve any-task atomically
docs: update scheduler decisions
```

---

# 14. 구현 단계

## Phase 0. 프로젝트 초기화

목표:

- Python 프로젝트 실행 가능
- 테스트 실행 가능
- 기본 폴더 구조 생성
- 설정 로딩 가능

작업:

- `pyproject.toml`
- 패키지 구조
- pytest
- ruff 또는 기본 lint
- config loader
- logging
- `AGENTS.md`
- `.agent/` 상태 파일

완료 기준:

```text
python -m ai_queue_runner 실행 성공
pytest 실행 성공
설정 파일 로딩 테스트 통과
```

---

## Phase 1. 도메인 모델과 DB

목표:

- 작업, AI 상태, 실행 기록 저장

작업:

- Enum 정의
- SQLAlchemy 모델
- Repository
- DB 초기화
- 마이그레이션 최소 구조
- CRUD 테스트

완료 기준:

```text
작업 생성/조회/수정/삭제 가능
앱 재시작 후 데이터 유지
잘못된 agent_policy 거부
priority 범위 검증
```

---

## Phase 2. 스케줄러 코어

목표:

- 적합한 작업을 정확히 선택

작업:

- 후보 필터
- 점수 계산
- 동점 처리
- 선행 작업 검사
- 프로젝트 잠금 검사
- 원자적 선점
- reserved timeout 복구

필수 테스트:

1. Claude 전용 작업은 Codex가 선택하지 않음
2. Codex 전용 작업은 Claude가 선택하지 않음
3. Any 작업은 둘 다 후보로 볼 수 있음
4. 동일 Any 작업이 중복 배정되지 않음
5. 전용 보너스 적용
6. 대기 시간 보너스 적용
7. 선행 작업 미완료 시 선택되지 않음
8. 같은 프로젝트 작업은 동시에 실행되지 않음
9. `not_before` 이전에는 실행되지 않음
10. 비활성화된 AI는 작업을 선택하지 않음

완료 기준:

```text
스케줄러 단위/통합 테스트 전체 통과
동시 선점 테스트 반복 실행 성공
```

---

## Phase 3. 실행 엔진과 FakeAdapter

목표:

- 실제 AI CLI 없이 전체 흐름 검증

작업:

- ProcessRunner
- FakeAdapter
- stdout/stderr 저장
- 실행 취소
- timeout
- 성공/실패 상태 전이
- 재시도
- 비정상 종료 복구

완료 기준:

```text
FakeAdapter 성공 작업 완료
FakeAdapter 실패 작업 재시도
앱 종료 후 running 작업 복구
로그 파일 생성
```

---

## Phase 4. Claude/Codex CLI 어댑터

목표:

- 실제 CLI 실행

작업:

- 설치 확인
- 버전 확인
- 명령 생성
- cwd 지정
- 프롬프트 전달
- 출력 스트리밍
- 종료 코드 분류
- 인증 오류 분류
- 사용량 제한 오류 분류

`[VERIFY]`

실제 설치된 CLI 버전의 도움말을 확인한 뒤 명령 형식을 확정한다.

완료 기준:

```text
테스트 프로젝트에서 Claude 작업 1개 성공
테스트 프로젝트에서 Codex 작업 1개 성공
로그와 종료 상태가 DB에 저장
실행 취소 가능
```

---

## Phase 5. 데스크톱 UI

목표:

- 터미널을 열지 않고 작업 관리 가능

작업:

- 메인 창
- 작업 목록
- 작업 등록/수정
- AI 상태 패널
- 사용 가능 시각 입력
- 실행 승인 창
- 로그 뷰어
- 시스템 트레이
- 알림

완료 기준:

```text
UI에서 작업 생성
상태 변경
작업 실행
로그 확인
앱 재시작 후 데이터 유지
```

---

## Phase 6. 안정화

목표:

- 일상적으로 사용할 수 있는 상태

작업:

- 예외 메시지 개선
- Windows 프로세스 종료 테스트
- 잘못된 프로젝트 경로 처리
- DB 백업
- 설정 내보내기
- PyInstaller 빌드
- 시작 프로그램 등록 옵션
- 장시간 실행 테스트

완료 기준:

```text
8시간 이상 실행
최소 20개 작업 큐 처리
앱 재시작 복구
중복 실행 없음
치명적 데이터 손실 없음
```

---

# 15. 테스트 시나리오

## 시나리오 A: Claude 제한, Codex 사용 가능

```text
Claude: cooldown until 15:00
Codex: available

T1 Claude 전용, P90
T2 Codex 전용, P70
T3 Any, P80
```

기대 결과:

```text
Codex는 T3 또는 T2 중 점수 높은 작업 선택
Claude는 아무 작업도 실행하지 않음
T1은 대기
```

## 시나리오 B: 두 AI 모두 사용 가능

```text
T1 Claude 전용, P80
T2 Codex 전용, P80
T3 Any, P100
```

기대 결과:

```text
두 AI가 같은 T3을 동시에 선택하지 않음
프로젝트 잠금까지 고려해 최적 배정
```

## 시나리오 C: 같은 프로젝트 충돌

```text
T1 Claude 전용, project=A
T2 Codex 전용, project=A
```

기대 결과:

```text
한 작업만 running
다른 작업은 waiting
```

## 시나리오 D: 실패 후 재시도

```text
max_retries = 2
retry_delay = 10분
```

기대 결과:

```text
1차 실패 → retry_wait
10분 후 waiting
2차 실패 → retry_wait
3차 실패 → failed
```

## 시나리오 E: 앱 강제 종료

```text
작업 running 중 앱 종료
```

기대 결과:

```text
재시작 시 미완료 작업 감지
PID가 없으면 복구 선택 제공
로그 보존
```

---

# 16. 완료 정의

MVP는 아래 조건을 모두 만족할 때 완료로 본다.

`[VERIFY]`

- [ ] Claude/Codex/Any 작업 등록 가능
- [ ] 우선순위 지정 가능
- [ ] AI별 사용 가능 시각 입력 가능
- [ ] 적합한 작업 자동 선택
- [ ] 동일 작업 중복 실행 방지
- [ ] 동일 프로젝트 동시 수정 방지
- [ ] CLI 실행 및 로그 저장
- [ ] 자동/승인 실행 모드
- [ ] 실패 재시도
- [ ] 앱 재시작 후 복구
- [ ] 시스템 트레이 동작
- [ ] Windows 실행 파일 생성
- [ ] 핵심 테스트 통과
- [ ] README 사용법 작성

---

# 17. Claude에게 줄 최초 지시문

아래 내용을 Claude에게 전달한다.

```text
이 저장소의 총괄 개발 에이전트 역할을 맡아라.

먼저 AI_QUEUE_RUNNER_PLAN.md 전체를 읽고,
프로젝트 루트의 AGENTS.md 및 .agent 상태 파일을 생성하라.

너의 역할:
1. 요구사항과 아키텍처를 유지한다.
2. 작업을 검증 가능한 작은 단위로 분해한다.
3. Codex에게 구현 범위, 수정 가능 파일, 완료 기준, 테스트 명령을 명확히 전달한다.
4. Codex 결과를 diff와 테스트 결과를 기준으로 검토한다.
5. 과도한 추상화와 범위 확장을 막는다.
6. 각 단계가 끝날 때 STATE.md, TASKS.md, DECISIONS.md를 갱신한다.

첫 작업:
- Phase 0의 구현 계획을 세운다.
- 프로젝트 파일 구조를 만든다.
- pyproject.toml, 기본 패키지, config loader, logging, pytest 환경을 구현한다.
- Codex에 맡길 작업과 Claude가 직접 할 작업을 나눈다.
- 모든 변경 후 테스트를 실행한다.

중요:
- 아직 실제 Claude/Codex CLI 명령 형식을 추측해서 구현하지 마라.
- Phase 4에서 설치된 CLI의 help/version 출력을 확인한 뒤 어댑터를 확정한다.
- 한 번에 전체 프로젝트를 만들지 말고 Phase 단위로 진행한다.
```

---

# 18. Claude가 Codex에게 줄 작업 지시 템플릿

```text
[TASK]
ID: T###
Goal: 구현 목표 한 줄

[CONTEXT]
- 관련 설계 결정
- 필요한 기존 구조
- 선행 작업 결과

[SCOPE]
수정 가능:
- path/file_a.py
- path/file_b.py
- tests/test_x.py

수정 금지:
- UI 전체
- CLI 어댑터
- DB 스키마

[REQUIREMENTS]
1. ...
2. ...
3. ...

[TESTS]
반드시 실행:
- pytest ...
- python -m ...

[ACCEPTANCE]
- ...
- ...
- ...

[HANDOFF]
완료 후 아래를 보고:
- 변경 파일
- 구현 내용
- 실행한 테스트
- 테스트 결과
- 남은 위험
- 추후 작업
```

---

# 19. Codex 작업 결과 검토 체크리스트

Claude는 매 작업 후 아래를 확인한다.

```text
[ ] 요청한 범위만 수정했는가
[ ] 요구사항을 모두 만족하는가
[ ] 테스트가 실제로 추가되었는가
[ ] 테스트 결과가 기록되었는가
[ ] 실패 경로가 처리되었는가
[ ] 상태 전이가 올바른가
[ ] 동시성 문제가 없는가
[ ] 로그에 민감 정보가 남지 않는가
[ ] 불필요한 추상화가 추가되지 않았는가
[ ] 문서와 코드가 일치하는가
```

---

# 20. 향후 확장

`[LATER]`

- Gemini CLI 어댑터
- OpenAI API / Anthropic API 비용 기반 라우팅
- AI별 예상 토큰 사용량
- 작업 자동 분할
- AI가 실패한 작업을 다른 AI로 넘기는 fallback
- Git worktree 기반 병렬 작업
- 원격 서버 실행
- 웹 대시보드
- 프로젝트별 일일 작업 요약
- 사용량 복구 예상 시각 자동 파싱
- 결과 품질 평가
- 작업 템플릿
- 반복 작업
- 작업 완료 후 자동 commit
- 사용자가 승인한 경우 draft PR 생성

---

# 21. 최종 원칙

```text
1. 개인용이므로 단순하게 시작한다.
2. 자동화보다 중복 실행 방지가 우선이다.
3. 전용 작업 조건을 절대 위반하지 않는다.
4. Any 작업은 남는 AI 자원을 효율적으로 사용한다.
5. 같은 프로젝트를 동시에 수정하지 않는다.
6. AI 사용량 상태는 처음에는 수동 입력한다.
7. 실제 CLI 형식은 설치 환경에서 검증한다.
8. 모든 자동 실행은 로그와 복구 수단을 남긴다.
9. Claude는 설계와 통합, Codex는 구현과 테스트를 담당한다.
10. 각 단계는 테스트 가능한 작은 단위로 진행한다.
```
