[TASK]
ID: T017
Goal: Human-readable log rendering — the log viewer currently shows raw stream-json/JSONL
from the CLIs, which users cannot read. Render it as clean text by default (raw view stays
available), and surface each run's final result as TaskRun.result_summary.

[CONTEXT]
- Read AGENTS.md and .agent/{STATE,DECISIONS,HANDOFF}.md first.
- The Claude adapter runs with `--output-format stream-json --verbose` (required for
  rate_limit_event classification — DO NOT change the CLI flags), so stdout is JSONL:
  events with "type" of system/assistant/user/result etc. The Codex adapter runs with
  `--json` (JSONL events too).
- A real captured Claude log exists at logs/tasks/1/attempt_1.stdout.log (relative to
  ai-queue-runner/). Read it and base test fixtures on REPRESENTATIVE, shortened lines
  from it (do not commit the whole file; strip any signatures/keys from fixtures).
- task_runs.result_summary column already exists and the UI run history shows it; it is
  currently never populated.

[SCOPE]
May create:
- src/ai_queue_runner/application/log_rendering.py   (pure functions, no Qt imports)
- tests/unit/test_log_rendering.py
May modify:
- src/ai_queue_runner/ui/log_viewer.py               (pretty/raw toggle, default pretty)
- src/ai_queue_runner/infrastructure/adapters/{base,claude,codex}.py
  (ONLY to add summarize_output; do not change build_command/classify_error behavior)
- src/ai_queue_runner/application/execution_service.py (ONLY to populate result_summary)
- tests/unit/test_adapters.py, tests/unit/test_ui.py,
  tests/integration/test_execution_service.py (extend, do not weaken existing asserts)
- .agent/STATE.md, .agent/TASKS.md, .agent/HANDOFF.md
Must NOT modify:
- domain/**, scheduler_service.py, recovery_service.py, process_runner.py, database.py,
  repositories.py, config/**, prompts/**, main.py, other ui files, docs/**, README.md,
  pyproject.toml

[REQUIREMENTS]
1. log_rendering.render_pretty(text: str, agent: str | None = None) -> str
   - Non-JSON lines pass through unchanged; a mixed file must never crash the renderer.
   - Claude stream-json events:
     - system/init -> one line: "· session <model>, cwd=<cwd>" (no tool lists)
     - assistant text blocks -> the text itself (verbatim)
     - assistant tool_use -> "→ <ToolName> <one-line compact key args>" (truncate long values)
     - user tool_result -> "← <first line or first ~120 chars>…"; skip file-content dumps
     - thinking blocks / signatures / usage / rate_limit_event(status=allowed) -> omitted
     - result -> "✔ result (<num_turns> turns, <duration_ms/1000:.0f>s)" + final result text
   - Codex --json events: render agent text messages and executed commands analogously;
     unknown event types -> omitted silently.
   - Output must contain NO raw JSON braces for known event types.
2. Log viewer: default shows pretty rendering; a toggle (e.g. "원본 보기" checkbox) switches
   to the raw file content. Keep the existing 2MB display cap; render AFTER capping.
   Label text in Korean to match the existing UI language.
3. AgentAdapter protocol gains OPTIONAL summarize_output(output: str) -> str | None
   (default None via base helper). Claude: final "result" event's result text. Codex: last
   agent message. Both truncated to 500 chars.
4. execution_service: after a run finishes (success OR failure), set
   run.result_summary = adapter.summarize_output(stdout_tail_or_text) when available.
   Read at most the last 64KB of the stdout file for this.
5. Renderer must be defensive: malformed JSON, missing keys, huge lines -> never raise.

[TESTS]
1. render_pretty on a fixture built from the real captured log: assistant text visible,
   tool_use rendered as arrows, no "{" from known events, init/result lines present
2. mixed JSONL + plain text passes plain text through
3. malformed JSON line does not raise and passes through
4. rate_limit_event(status=allowed) omitted
5. Claude summarize_output extracts the result text; Codex summarize_output extracts the
   last agent message; both None on empty/garbage input
6. execution flow test: successful FakeAdapter run stores result_summary when the fake
   adapter provides summarize_output
7. UI test (offscreen): toggle switches between pretty and raw content

[VERIFY]
Run from ai-queue-runner/ and paste real output into HANDOFF:
- ..\.venv\Scripts\python -m pytest -q
- ..\.venv\Scripts\python -m ruff check src tests

[ACCEPTANCE]
- Full suite green (existing 186 + new), ruff clean
- log_rendering.py imports no Qt
- CLI flags in adapters unchanged (git diff shows no build_command changes)
- ai-anchors rule followed

[HANDOFF]
When done: add "[done] T017 UX: human-readable log rendering + result summaries" to
.agent/TASKS.md; set .agent/STATE.md (owner: codex, status: done, real pytest result,
next: "user feedback"); overwrite .agent/HANDOFF.md in the standard format.
