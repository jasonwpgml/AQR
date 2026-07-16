# AI Queue Runner

Claude/Codex CLI에 맡길 작업을 미리 등록해두면, 각 AI의 사용 가능 상태·우선순위·대기
시간을 따져 자동으로 실행해 주는 개인용 로컬 스케줄러입니다.

전체 요구사항과 아키텍처는 [`../AI_QUEUE_RUNNER_PLAN.md`](../AI_QUEUE_RUNNER_PLAN.md),
구조 도식(계층·ERD·상태 전이)은 [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md),
에이전트 협업 규칙은 [`AGENTS.md`](AGENTS.md)를 참고하세요.

## 개발 환경

```powershell
# 저장소 루트의 .venv 사용
..\.venv\Scripts\python -m pip install -e .[dev]

# 테스트 / 린트 / 실행
..\.venv\Scripts\python -m pytest -q
..\.venv\Scripts\python -m ruff check src tests
..\.venv\Scripts\python -m ai_queue_runner
```

## 설정

`config.example.toml`을 `config.toml`로 복사한 뒤 수정합니다.
상대 경로(`db_path`, `logs_dir`)는 `config.toml`이 있는 디렉터리를 기준으로 해석됩니다.

## 진행 상태

- [x] Phase 0 — 프로젝트 초기화 (config loader, logging, pytest, ruff)
- [x] Phase 1 — 도메인 모델과 DB (enum, 상태 전이, ORM 5개 테이블, repository)
- [x] Phase 2 — 스케줄러 코어 (후보 필터·점수·원자적 선점·예약 타임아웃 복구)
- [ ] Phase 3 — 실행 엔진과 FakeAdapter
- [ ] Phase 4 — Claude/Codex CLI 어댑터
- [ ] Phase 5 — 데스크톱 UI (PySide6)
- [ ] Phase 6 — 안정화 및 배포
