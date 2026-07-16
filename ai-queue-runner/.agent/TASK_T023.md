[TASK]
ID: T023
Goal: Calendar view — a calendar tab in the main window showing which tasks ran/finished/are
scheduled on each date, and a way to create a task pre-scheduled for a picked date
(mapping to the existing not_before field).

[CONTEXT]
- Read AGENTS.md and .agent/{STATE,DECISIONS,HANDOFF}.md first.
- not_before already gates scheduling (§5.1); "날짜 지정 실행" needs no schema change.
- D013: DB datetimes are naive UTC; convert to/from local time ONLY in the UI layer.
- TaskService is the only read path the UI may use; extend it with read-only queries.

[SCOPE]
May create:
- src/ai_queue_runner/ui/calendar_view.py
May modify:
- src/ai_queue_runner/ui/main_window.py   (add the calendar tab/panel)
- src/ai_queue_runner/ui/task_dialog.py   (accept an optional preset not_before)
- src/ai_queue_runner/application/task_service.py (read-only query additions only)
- tests/unit/test_ui.py, tests/integration/test_application_services.py (extend)
- .agent/STATE.md, .agent/TASKS.md, .agent/HANDOFF.md
Must NOT modify: everything else.

[REQUIREMENTS]
1. TaskService gains a read-only query for calendar data, e.g.
   activity_between(start_utc, end_utc) returning per-task (id, title, status,
   created_at, finished_at, not_before) rows overlapping the range. The UI computes
   local-day boundaries and converts for display.
2. Calendar tab (QCalendarWidget or equivalent, Korean labels):
   - dates with activity are visually marked (created/finished on that date, or scheduled
     via not_before), distinguishable at a glance (e.g. bold/colored text format)
   - selecting a date shows that day's tasks grouped as 완료 / 실패·취소 / 예정 / 생성,
     with status and title; double-click focuses the task in the main task table
   - [이 날짜에 작업 추가] opens the task dialog with not_before preset to the selected
     date at 09:00 local time (editable in the dialog as usual)
3. Task dialog: optional preset not_before parameter; when set, its datetime field shows
   the local-time value pre-filled.
4. Calendar refreshes when tasks change (reuse the existing refresh signal/timer the task
   table uses).

[TESTS]
1. activity_between returns tasks by created/finished/not_before overlap, excludes others
2. UI offscreen: marked dates match seeded tasks; selecting a date lists the expected
   groups; the add-on-date flow passes the preset into the dialog (assert prefilled value)
3. preset not_before survives into the created task (UTC conversion correct: local 09:00
   -> stored naive UTC)

[VERIFY]
- ..\.venv\Scripts\python -m pytest -q
- ..\.venv\Scripts\python -m ruff check src tests

[ACCEPTANCE]
- Full suite green, ruff clean; ai-anchors rule followed

[HANDOFF]
Mark T023 [done] in .agent/TASKS.md, write .agent/HANDOFF.md for T023, then CONTINUE in
this same session with .agent/TASK_T024.md.
