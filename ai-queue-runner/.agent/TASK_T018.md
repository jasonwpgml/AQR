[TASK]
ID: T018
Goal: Settings dialog to manage allowed_project_roots from the UI — add/remove roots with a
folder picker, persist to the active config.toml, and apply immediately without restart.

[CONTEXT]
- Read AGENTS.md and .agent/{STATE,DECISIONS,HANDOFF}.md first.
- Today users must hand-edit config.toml. Path enforcement is
  infrastructure/path_policy.validate_project_path(), called by TaskService with
  self._config — services keep a reference to the ONE AppConfig instance, so mutating
  config.app.allowed_project_roots in place applies live. Do not create a second AppConfig.
- config/exporter.export_config(config, path) already serializes the validated effective
  config to TOML and round-trips through config/loader.load_config (README documents that
  exported paths are the resolved effective ones — acceptable).
- main.py currently loads config but does not remember WHICH file it came from; the dialog
  needs that path to save.

[SCOPE]
May create:
- src/ai_queue_runner/ui/settings_dialog.py
May modify:
- src/ai_queue_runner/ui/main_window.py   (ONLY to add a 설정 action/button opening the dialog)
- src/ai_queue_runner/main.py             (ONLY to compute and thread the active config path)
- tests/unit/test_ui.py                   (extend)
- .agent/STATE.md, .agent/TASKS.md, .agent/HANDOFF.md
Must NOT modify:
- domain/**, application/**, infrastructure/**, config/**, prompts/**, other ui files,
  docs/**, README.md, pyproject.toml, config.example.toml, other tests

[REQUIREMENTS]
1. Active config path in main.py: args.config if given; else Path.cwd()/"config.toml" if it
   exists; else default_app_data_dir()/"config.toml". Pass it through run_desktop_app into
   MainWindow (constructor parameter), then to the dialog.
2. SettingsDialog (Korean labels, consistent with existing UI):
   - list widget showing current config.app.allowed_project_roots
   - [폴더 추가]: QFileDialog.getExistingDirectory; reject non-absolute or non-directory
     paths; no duplicates (compare resolved paths)
   - [선택 제거]
   - a visible warning label when the list is empty: "허용 루트가 없으면 모든 작업 실행이 차단됩니다"
   - [저장]: mutate config.app.allowed_project_roots IN PLACE on the shared AppConfig, then
     export_config(config, active_config_path); show the saved file path in the dialog or a
     status message. [취소] discards edits.
3. Saving must not touch any other config values beyond what export_config already writes.
4. No Qt imports outside ui/ (keep logic in the dialog; reuse export_config as-is).

[TESTS]
Extend tests/unit/test_ui.py (offscreen Qt, follow existing patterns):
1. dialog lists existing roots from config
2. add (inject path programmatically, don't open the native picker) + save -> in-memory
   config updated AND exported file contains the new root
3. exported file round-trips: load_config(saved_path) yields the same allowed roots
4. remove + save -> root gone from config and file
5. empty list shows the warning label
6. cancel leaves config and file untouched
7. after save, TaskService accepts a project path under the newly added root (create via
   the real TaskService against a tmp DB — proves live application without restart)

[VERIFY]
Run from ai-queue-runner/ and paste real output into HANDOFF:
- ..\.venv\Scripts\python -m pytest -q
- ..\.venv\Scripts\python -m ruff check src tests

[ACCEPTANCE]
- Full suite green (existing 196 + new), ruff clean
- git diff on main_window.py/main.py limited to the described threading + menu entry
- ai-anchors rule followed

[HANDOFF]
When done: add "[done] T018 UX: allowed-roots settings dialog with live apply" to
.agent/TASKS.md; set .agent/STATE.md (owner: codex, status: done, real pytest result,
next: "T019 automatic usage-limit reset detection"); overwrite .agent/HANDOFF.md
in the standard format. Do not kill the running desktop app; do not write to data/queue.db.
