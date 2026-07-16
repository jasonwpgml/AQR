[TASK]
ID: T022
Goal: In the task dialog, project-path selection must start from the allowed roots — a
dropdown of configured allowed_project_roots plus a subfolder browse constrained to them —
replacing the free-text path entry.

[CONTEXT]
- Read AGENTS.md and .agent/{STATE,DECISIONS,HANDOFF}.md first.
- Today task_dialog.py has a free QLineEdit (path_edit) + 찾아보기 that can pick any folder;
  invalid picks only fail later at TaskDraft/TaskService validation.
- The dialog must read roots from the LIVE shared AppConfig (the settings dialog mutates
  config.app.allowed_project_roots in place), re-reading them each time it opens.
- Service-layer validation (path_policy via TaskService) stays as-is — the UI narrows
  choices, the service still enforces.

[SCOPE]
May modify:
- src/ai_queue_runner/ui/task_dialog.py
- src/ai_queue_runner/ui/main_window.py (ONLY if needed to pass the shared AppConfig)
- tests/unit/test_ui.py (extend)
- .agent/STATE.md, .agent/TASKS.md, .agent/HANDOFF.md
Must NOT modify:
- everything else (domain/**, application/**, infrastructure/**, config/**, prompts/**,
  other ui files, main.py, docs/**, README.md, pyproject.toml)

[REQUIREMENTS]
1. Replace the free-text project path row with:
   - a non-editable combo listing config.app.allowed_project_roots (refreshed on dialog open)
   - a [하위 폴더 선택…] button: QFileDialog.getExistingDirectory starting at the selected
     root; accept the result ONLY if it equals or is under ANY allowed root (resolved-path
     comparison, same containment rule as path_policy); otherwise show a Korean warning and
     keep the previous value
   - a read-only display of the effective selected path (root itself when no subfolder chosen)
2. Selecting a different root resets the effective path to that root.
3. Empty allowed roots: disable the row and show "설정에서 허용 프로젝트 루트를 먼저 추가하세요";
   the dialog cannot be accepted in that state.
4. Edit/duplicate mode: pre-select the root containing the existing task's path and show
   that path; if the stored path is no longer under any allowed root, show it read-only
   with a warning and require re-selection before accepting.
5. The value submitted to TaskDraft remains a plain path string, as today.

[TESTS]
Extend tests/unit/test_ui.py (offscreen):
1. combo lists exactly the configured roots
2. subfolder inside a root (created under tmp) is accepted and shown
3. a folder outside all roots is rejected (warning path) and the previous value kept
4. empty roots disables the row and blocks accept
5. edit mode pre-selects the containing root and path
6. root switch resets the effective path

[VERIFY]
Run from ai-queue-runner/ and paste real output into HANDOFF:
- ..\.venv\Scripts\python -m pytest -q
- ..\.venv\Scripts\python -m ruff check src tests

[ACCEPTANCE]
- Full suite green, ruff clean
- ai-anchors rule followed

[HANDOFF]
When done: add "[done] T022 task dialog restricts project path to allowed roots" to
.agent/TASKS.md; set .agent/STATE.md (owner: codex, status: done, real pytest result,
next: "T023 calendar view"); overwrite .agent/HANDOFF.md in the standard format.
Do NOT kill any running desktop app; do not write to data/queue.db or LOCALAPPDATA data.
