[TASK]
ID: T024
Goal: Headless CLI subcommands so an AI assistant (e.g. Claude Code) can manage the queue
from a terminal: add, list, show, and cancel tasks without starting the GUI.

[CONTEXT]
- Read AGENTS.md and .agent/{STATE,DECISIONS,HANDOFF}.md first (you may already have; this
  task runs in the same session as T023).
- main.py already has maintenance flags (--backup, --export-config, --startup) that run
  and exit before the GUI. Subcommands must reuse the SAME config resolution: explicit
  --config, else cwd config.toml, else app-data config.toml (the active_config_path logic).
- TaskService enforces path policy and validation; the CLI must go through it, never
  through repositories directly. TaskDraft is the validated input model.
- Concurrent use with a running GUI is expected: SQLite WAL + busy timeout already handle
  it. Do not hold sessions open longer than needed.

[SCOPE]
May modify:
- src/ai_queue_runner/main.py            (subcommand parsing + handlers)
- tests/unit/test_main.py, tests/integration/test_application_services.py (extend)
- README.md                              (add an "AI/CLI 연동" usage section)
- .agent/STATE.md, .agent/TASKS.md, .agent/HANDOFF.md
May create:
- src/ai_queue_runner/application/cli_service.py  (only if main.py would exceed ~350 lines;
  thin command handlers, no new business logic)
Must NOT modify: everything else (ui/**, adapters, scheduler, domain, config models).

[REQUIREMENTS]
1. Subcommands (argparse subparsers; global --config and --json still work):
   - add-task --title T --prompt P --project PATH [--agent claude|codex|any]
     [--priority 0-100] [--approval auto|confirm] [--not-before "YYYY-MM-DD HH:MM"]
     [--max-retries N] [--tag TAG ...] [--depends-on ID ...]
     -> prints the created task id + summary; exit 0
   - list-tasks [--status STATUS] -> table-ish text: id, status, agent, priority, title
   - show-task ID -> full task fields incl. last_error and recent runs' result_summary
   - cancel-task ID -> cancels via TaskService; clear error if not cancellable
   - --json on any subcommand switches output to machine-readable JSON (stable keys,
     enum values as strings, datetimes as ISO-8601 UTC)
2. --not-before is interpreted as LOCAL time and stored as naive UTC (D013); document
   this in --help text.
3. Validation/policy errors print a one-line Korean message to stderr, exit 2; unexpected
   errors exit 3. Never print a traceback for expected failures.
4. No GUI/Qt import may occur on the subcommand paths (keep imports lazy as main.py
   already does for run_desktop_app).
5. README: short section with copy-pasteable examples, including one showing an AI
   assistant scheduling a task for a specific date.

[TESTS]
1. add-task creates a task via real TaskService against a tmp config/db (path under an
   allowed root); rejected path -> exit 2 with message
2. list-tasks/--status filter and show-task output contain the expected fields;
   --json parses and round-trips key fields
3. cancel-task cancels waiting task; refuses succeeded one (exit 2)
4. --not-before local->UTC conversion verified
5. subcommand paths do not import PySide6 (assert sys.modules after running in a fresh
   subprocess, or monkeypatch-guard the import)

[VERIFY]
- ..\.venv\Scripts\python -m pytest -q
- ..\.venv\Scripts\python -m ruff check src tests

[ACCEPTANCE]
- Full suite green, ruff clean; ai-anchors rule followed

[HANDOFF]
Mark T024 [done] in .agent/TASKS.md; set .agent/STATE.md (owner: codex, status: done, real
pytest result, next: "review + rebuild"); overwrite .agent/HANDOFF.md covering BOTH T023
and T024 (CHANGED lists per task). Do NOT kill any running desktop app; do not write to
data/queue.db or LOCALAPPDATA data.
