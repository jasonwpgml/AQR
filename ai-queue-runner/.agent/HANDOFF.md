FROM: codex
TO: claude
TASK: T020
STATUS: completed

CHANGED:
- src/ai_queue_runner/application/prompt_template_service.py
- src/ai_queue_runner/ui/task_dialog.py
- src/ai_queue_runner/main.py
- src/ai_queue_runner/ui/main_window.py
- tests/integration/test_prompt_templates.py
- tests/unit/test_ui.py
- .agent/STATE.md
- .agent/TASKS.md
- .agent/HANDOFF.md

VERIFIED:
- `..\.venv\Scripts\python -m pytest -q` -> 213 passed in 8.26s
- `..\.venv\Scripts\python -m ruff check src tests` -> All checks passed!
- `..\.venv\Scripts\python -m pytest -q tests\unit\test_ui.py tests\integration\test_prompt_templates.py` -> 23 passed in 2.13s
- `git diff --check` -> clean (line-ending conversion warnings only)
- Scope audit -> only T020-permitted files changed; `data/queue.db` untouched
- AIDEV anchor audit -> relevant scope searched before and after editing; no stale or new anchors

NOTES:
- PromptTemplateService stores one sorted JSON list under the existing `prompt_templates` settings key and owns all mutation commits.
- Template names and prompts are trimmed; names are capped at 60 characters and new templates at 200 total, while overwrites remain allowed at the cap.
- The task dialog copies selected template text into its editor after confirmation, supports save/overwrite and confirmed deletion, and refreshes the dropdown after mutations.
- Templates remain independent from task rows; integration coverage proves later template edits do not change an existing task.
- No schema or infrastructure change was made; schema version remains v1.

RISKS:
- None known.
