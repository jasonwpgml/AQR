---
name: codex-delegate
description: AQR에서 구현 작업을 Codex CLI에 위임하는 절차. 기능 단위 구현을 위임할 때, 또는 사용자가 "코덱스한테 시켜"라고 할 때 사용. 소규모 수정(문서·수십 줄 픽스)은 위임하지 말고 직접 처리.
---

# Codex 위임 절차

작업 위치: `C:\Users\Admin\Desktop\AQR\ai-queue-runner` (지시문은 `.agent/TASK_T###.md`)

## 1. 위임 판단
- 자체 완결적 기능 단위 → 위임. 문서 수정·수십 줄 이하 픽스·긴급 버그 → Claude 직접.

## 2. 지시문 작성
- `.agent/TASK_T###.md`에 플랜 §18 템플릿([TASK]/[CONTEXT]/[SCOPE]/[REQUIREMENTS]/[TESTS]/[ACCEPTANCE]/[HANDOFF])으로 작성.
- 항상 포함: "커밋은 하지 말 것 (Claude가 커밋)", 전체 pytest + ruff 실행 요구.

## 3. 실행 (Bash 도구, 명령이 `codex exec`로 시작해야 허용 규칙에 걸림)
```
codex exec -C "C:/Users/Admin/Desktop/AQR/ai-queue-runner" -s danger-full-access [모델옵션] \
  -o "<scratchpad>/t###_last.txt" - < ".agent/TASK_T###.md" > "<scratchpad>/t###_run.log" 2>&1
```
- 백그라운드(run_in_background)로 실행하고 완료 알림을 기다린다. stdin은 반드시 `- < file` (인자로만 주면 멈춤). 파이프 앞에 다른 명령을 붙이면 허용 규칙에 안 걸림.
- Codex가 편집 중인 트리에서 소크 등 장기 pytest 루프를 병행하지 말 것 — 편집 중간 상태를 잡아 가짜 실패가 난다. 소크는 커밋 완료 후 시작.
- 모델: 중요·복잡 작업은 옵션 생략(기본 gpt-5.6-sol xhigh). 덜 중요하면 `-m gpt-5.5 -c model_reasoning_effort=medium`.

## 4. 완료 후 (구현 우선 정책)
1. `t###_last.txt`(핸드오프 보고)만 읽는다. 필요 시 run.log tail.
2. `..\.venv\Scripts\python -m pytest -q` + `ruff check src tests`만 실행 (diff 정독 생략).
3. 통과 → 완료된 TASK_T###.md 삭제 후 dev에 기능 단위 커밋(메시지에 사용 모델 명시). 실패 → 실패 내용으로 후속 지시문 작성해 재위임.
4. 정밀 검토는 커밋 여러 개 쌓인 뒤 /code-review로 배치 수행.
