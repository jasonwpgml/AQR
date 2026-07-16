[TASK]
ID: T020
Goal: Prompt templates — let the user save a written prompt under a name and reuse it from
the task dialog. Stored in the existing settings table (no schema migration).

[CONTEXT]
- Read AGENTS.md and .agent/{STATE,DECISIONS,HANDOFF}.md first.
- SettingsRepository (infrastructure/repositories.py) already stores arbitrary JSON under a
  key; schema stays at v1 (avoids the init_db migration wall). Store all templates under one
  key, e.g. "prompt_templates": a JSON list of {"name": str, "prompt": str} dicts.
- The task dialog (ui/task_dialog.py) has the prompt editor; templates are picked/saved there.

[SCOPE]
May create:
- src/ai_queue_runner/application/prompt_template_service.py
- tests/integration/test_prompt_templates.py
May modify:
- src/ai_queue_runner/ui/task_dialog.py (template combo + save button only)
- src/ai_queue_runner/main.py and ui/main_window.py (ONLY to construct/thread the service
  into the dialog)
- tests/unit/test_ui.py (extend)
- .agent/STATE.md, .agent/TASKS.md, .agent/HANDOFF.md
Must NOT modify:
- domain/**, other application services, infrastructure/**, config/**, prompts/**,
  other ui files, docs/**, README.md, pyproject.toml

[REQUIREMENTS]
1. PromptTemplateService(session_factory) with:
   - list() -> list[TemplateView(name, prompt)] sorted by name
   - save(name, prompt) — trimmed, both non-empty; overwrites same name
   - rename(old, new) — new must not collide
   - delete(name)
   All persist via SettingsRepository under one key; service owns commits (matches other
   application services).
2. Task dialog (Korean labels):
   - template dropdown listing saved names; picking one REPLACES the prompt editor content,
     after a confirm box when the editor has non-empty text that differs
   - [템플릿으로 저장] button: asks a name (QInputDialog), pre-filled with the selected
     template's name if any; saves current editor content
   - a small [삭제] affordance for the selected template (confirm box)
   - dropdown refreshes after save/delete
3. Name length cap 60 chars; template count cap 200 (reject beyond with a clear message).
4. Templates are independent of tasks — editing a template never changes existing tasks.

[TESTS]
1. service roundtrip: save/list/overwrite/rename/delete against a tmp DB; survives engine
   reopen (persistence)
2. validation: empty name/prompt rejected; rename collision rejected; caps enforced
3. UI offscreen: save button persists editor content; picking a template fills the editor;
   delete removes it from the dropdown
4. creating a task after inserting a template works end-to-end (TaskService unaffected)

[VERIFY]
Run from ai-queue-runner/ and paste real output into HANDOFF:
- ..\.venv\Scripts\python -m pytest -q
- ..\.venv\Scripts\python -m ruff check src tests

[ACCEPTANCE]
- Full suite green, ruff clean
- No schema change (init_db still stamps schema_version 1)
- ai-anchors rule followed

[HANDOFF]
When done: add "[done] T020 prompt templates (settings-backed, task dialog integration)" to
.agent/TASKS.md; set .agent/STATE.md (owner: codex, status: done, real pytest result,
next: "user feedback"); overwrite .agent/HANDOFF.md in the standard format.
Do not kill the running desktop app; do not write to data/queue.db.
