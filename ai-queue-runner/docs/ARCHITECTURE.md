# 아키텍처 도식

사람이 코드 전체를 읽지 않고 구조를 파악하기 위한 문서입니다.
테이블·상태·모듈이 바뀌면 같은 작업 안에서 이 문서의 도식도 갱신합니다(AGENTS.md 규칙).

> 도식은 Mermaid로 작성되어 GitHub/VS Code 미리보기에서 그림으로 렌더링됩니다.

## 1. 전체 구조 (계층)

```mermaid
flowchart TB
    subgraph UI["ui/ — PySide6 (Phase 5)"]
        MW[메인 창 / 트레이 / 승인 다이얼로그]
    end

    subgraph APP["application/ — 서비스 (Phase 2~3)"]
        SCHED[scheduler_service.py<br/>후보 필터 → 점수 → 원자적 선점]
        EXEC[execution_service.py<br/>실행·6가지 결과 처리<br/>성공·재시도·제한·타임아웃·취소]
        RECOV[recovery_service.py<br/>예약 타임아웃 해제 · 재시도 승격<br/>PID 크래시 복구는 T009b]
    end

    subgraph DOMAIN["domain/ — 모델·순수 규칙 (구현됨)"]
        ENUMS[enums.py<br/>상태·정책 정의]
        TRANS[transitions.py<br/>상태 전이 규칙]
        SCORE[scoring.py<br/>설정 기반 점수 계산]
        MODELS[models.py<br/>ORM 테이블 5개]
        CLOCK[clock.py<br/>UTC 시간]
    end

    subgraph INFRA["infrastructure/ — 외부 연동"]
        DB[(database.py<br/>SQLite + WAL)]
        REPO[repositories.py<br/>CRUD 창구]
        ADAPT[adapters/<br/>claude · codex CLI<br/>Phase 4]
    end

    CFG[config/<br/>TOML 설정<br/>점수 가중치 · 제한 패턴]

    MW --> SCHED
    SCHED --> EXEC --> ADAPT
    SCHED --> REPO
    SCHED --> SCORE
    EXEC --> REPO
    RECOV --> REPO
    REPO --> MODELS
    REPO --> TRANS
    MODELS --> DB
    CFG -.모든 계층이 참조.-> APP
```

의존 방향은 항상 **UI → application → domain/infrastructure** 한쪽입니다.
domain은 아무것도 import하지 않는 순수 로직 계층입니다.

## 2. 데이터 모델 (ERD)

```mermaid
erDiagram
    tasks {
        int id PK
        text title
        text prompt
        text project_path
        text agent_policy "claude | codex | any"
        text assigned_agent "선점한 AI"
        int priority "0..100"
        text status "8가지 상태"
        text approval_mode "auto | confirm"
        int max_retries
        int retry_count
        datetime not_before "이 시각 전에는 실행 금지"
        datetime created_at
        json tags_json
    }
    task_dependencies {
        int task_id PK, FK
        int depends_on_task_id PK, FK
    }
    agent_states {
        text agent PK "claude | codex"
        text status "available·cooldown 등"
        datetime available_at "사용 가능 시각(수동 입력)"
        int current_task_id FK
    }
    task_runs {
        int id PK
        int task_id FK
        text agent
        int attempt "몇 번째 시도"
        json command_json "실행한 명령"
        int pid
        int exit_code
        text stdout_path
        text stderr_path
        text error_type "usage_limit·auth 등"
    }
    settings {
        text key PK
        json value_json "schema_version 포함"
    }

    tasks ||--o{ task_dependencies : "선행 작업"
    tasks ||--o{ task_runs : "실행 기록"
    tasks |o--o| agent_states : "현재 실행 중"
```

## 3. 작업 상태 전이

`domain/transitions.py`가 이 그림 그대로를 코드로 강제합니다.
그림에 없는 전이는 `InvalidTransitionError`가 발생합니다.

```mermaid
stateDiagram-v2
    [*] --> waiting: 작업 등록
    waiting --> reserved: 스케줄러가 선점
    waiting --> cancelled: 사용자 취소
    reserved --> running: CLI 실행 시작
    reserved --> waiting: 실행 전 복구 / 타임아웃
    running --> succeeded: 정상 완료
    running --> failed: 실패 (재시도 소진)
    running --> retry_wait: 실패 (재시도 남음)
    running --> blocked: 사용자 개입 필요
    running --> waiting: 사용량 제한 감지 §7.2
    retry_wait --> waiting: retry_delay 경과
    retry_wait --> cancelled
    blocked --> waiting: 차단 해제
    blocked --> cancelled
    failed --> waiting: 사용자 재실행
    succeeded --> [*]
    cancelled --> [*]
```

## 4. 스케줄링 흐름

```mermaid
flowchart TD
    TICK[주기적 틱<br/>poll_interval_seconds] --> AVAIL{사용 가능한<br/>AI 있나?}
    AVAIL -- 없음 --> TICK
    AVAIL -- 있음 --> FILTER[후보 필터<br/>waiting · not_before 경과<br/>정책 일치 · 선행 작업 완료<br/>프로젝트 잠금 없음]
    FILTER --> SCORE["점수 계산<br/>priority + 전용보너스(+20)<br/>+ 대기시간(시간당 +1, 최대 24)<br/>− 재시도(회당 5) + 의존보너스"]
    SCORE --> RESERVE[["단일 트랜잭션으로 선점<br/>status=reserved<br/>assigned_agent 기록"]]
    RESERVE --> APPROVE{approval_mode?}
    APPROVE -- auto --> RUN[CLI 실행]
    APPROVE -- confirm --> WAIT_USER[사용자 승인 대기] --> RUN
    RUN --> RESULT{결과}
    RESULT -- 성공 --> DONE[succeeded]
    RESULT -- 실패·재시도 남음 --> RETRY[retry_wait] --> TICK
    RESULT -- 사용량 제한 --> COOL[AI를 cooldown으로<br/>작업은 waiting으로] --> TICK
```

핵심 안전 규칙: **선점(reserve)은 하나의 DB 트랜잭션** 안에서 이루어집니다.
구현은 CAS(compare-and-swap) UPDATE 방식 — `WHERE status='waiting'` 조건이 붙은
UPDATE는 경쟁 시 한쪽만 성공하므로 같은 `any` 작업이 두 AI에 배정될 수 없고,
진 쪽은 자동으로 재선택합니다. 선점 시 `agent_states.current_task_id`도 함께
기록되어 한 AI가 두 작업을 동시에 쥘 수 없습니다. 타임아웃을 넘긴 예약은
`recovery_service`가 waiting으로 되돌립니다.

## 5. 파일 지도

| 경로 | 역할 | 상태 |
|---|---|---|
| `domain/enums.py` | 모든 상태·정책 enum | ✅ Phase 1 |
| `domain/transitions.py` | 상태 전이 규칙 (위 3번 그림) | ✅ Phase 1 |
| `domain/models.py` | DB 테이블 5개 (위 2번 그림) | ✅ Phase 1 |
| `domain/clock.py` | UTC 시간 헬퍼 (테스트에서 시간 조작용) | ✅ Phase 1 |
| `domain/scoring.py` | 우선순위·대기·재시도·의존성 기반 점수 계산 | ✅ Phase 2 |
| `infrastructure/database.py` | 엔진 생성, FK/WAL PRAGMA, 스키마 버전 | ✅ Phase 1 |
| `infrastructure/process_runner.py` | 서브프로세스 실행 (로그 스트리밍·타임아웃·취소) | ✅ Phase 3 |
| `infrastructure/repositories.py` | CRUD 창구 — 상태 변경은 `set_status()`만 사용 | ✅ Phase 1 |
| `config/defaults.py` | 모든 튜닝값 (점수 가중치, 제한 패턴) | ✅ Phase 0 |
| `config/loader.py` | TOML 로드 + 검증 | ✅ Phase 0 |
| `infrastructure/logging_config.py` | 콘솔 + 회전 파일 로그 | ✅ Phase 0 |
| `application/scheduler_service.py` | 후보 필터·점수·동점 처리 + CAS 원자적 선점 | ✅ Phase 2 |
| `application/recovery_service.py` | 예약 타임아웃 해제 + retry_wait→waiting 승격 | ✅ Phase 2~3 |
| `application/execution_service.py` | 예약된 작업 실행, 성공/실패/재시도/제한/취소 처리 | ✅ Phase 3 |
| `infrastructure/adapters/base.py` | AgentAdapter 프로토콜 (명령 생성 + 오류 분류) | ✅ Phase 3 |
| `infrastructure/adapters/` | Claude/Codex CLI 어댑터 | ⬜ Phase 4 |
| `ui/` | PySide6 창·트레이 | ⬜ Phase 5 |
