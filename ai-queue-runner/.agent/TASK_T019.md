[TASK]
ID: T019
Goal: Automatic usage-limit reset detection — when a run hits a usage limit, set the agent's
available_at automatically instead of leaving it null for manual entry. Claude: exact, from
the stream-json rate_limit_event `resetsAt` unix timestamp. Codex: best effort from output,
falling back to a configurable default cooldown.

[CONTEXT]
- Read AGENTS.md and .agent/{STATE,DECISIONS,HANDOFF}.md first.
- Real captured Claude output contains:
  {"type":"rate_limit_event","rate_limit_info":{"status":"allowed","resetsAt":1784192400,
  "rateLimitType":"five_hour",...}} — on a limit rejection the same event shape carries the
  reset time. status "allowed" events MUST keep being ignored (existing classifier behavior).
- Current behavior (execution_service usage-limit path): agent -> COOLDOWN with
  available_at=None; the user must type the reset time (plan §7.1). Keep manual entry as an
  override; this task only pre-fills it automatically.
- DB datetimes are naive UTC (D013); resetsAt is unix seconds -> convert with
  datetime.fromtimestamp(ts, UTC).replace(tzinfo=None).
- Agents become reservable again when status is AVAILABLE; verify how cooldown agents
  return to available when available_at passes (QueueRuntime/agent_service). If no such
  promotion exists, add it in the scheduler tick path; if it exists, add a regression test.

[SCOPE]
May modify:
- src/ai_queue_runner/infrastructure/adapters/{base,claude,codex}.py
  (add extract_limit_reset(output: str) -> datetime | None; do not change build_command)
- src/ai_queue_runner/application/execution_service.py (usage-limit path only)
- src/ai_queue_runner/application/runtime.py and/or agent_service.py
  (ONLY the cooldown->available promotion when available_at <= now, if missing)
- src/ai_queue_runner/config/defaults.py and config.example.toml
  (ONLY add default_cooldown_minutes to AgentSection, default 300, ge=1)
- tests/unit/test_adapters.py, tests/integration/test_execution_service.py,
  tests/integration/test_runtime.py (extend)
- .agent/STATE.md, .agent/TASKS.md, .agent/HANDOFF.md
Must NOT modify:
- domain/**, scheduler_service.py, recovery_service.py, process_runner.py, ui/**, main.py,
  prompts/**, docs/**, README.md, pyproject.toml

[REQUIREMENTS]
1. ClaudeAdapter.extract_limit_reset: scan JSONL for rate_limit_event whose
   rate_limit_info.status != "allowed" and return its resetsAt as naive-UTC datetime;
   None when absent/malformed. Ignore events without resetsAt.
2. CodexAdapter.extract_limit_reset: best effort — case-insensitive search for common
   reset phrasings in plain output (e.g. "try again at <time>", "resets at <time>",
   "try again in Xh Ym / X hours / Y minutes"); return parsed naive-UTC datetime or None.
   Keep the regexes few and conservative; a wrong time is worse than falling back.
3. execution_service usage-limit path: available_at =
   adapter.extract_limit_reset(last 64KB of stdout+stderr)
   or utcnow() + default_cooldown_minutes. Never leave available_at None on auto-detection;
   task.last_error should mention the chosen available_at so the user sees why.
4. A cooldown agent whose available_at has passed must become reservable again
   automatically (promotion to AVAILABLE in the scheduler tick). Add only if missing.
5. If the reset time is in the past or > 7 days ahead, treat as unparseable (fallback).

[TESTS]
1. Claude: fixture with rejected rate_limit_event -> exact datetime returned;
   allowed-only fixture -> None; malformed JSON -> None
2. Codex: "try again in 2 hours 30 minutes" -> now+150min (tolerance); garbage -> None;
   past timestamp -> None
3. Execution integration: usage-limit run with reset event -> agent COOLDOWN with exact
   available_at; without parseable reset -> now+default_cooldown_minutes (tolerance)
4. Runtime/scheduler: cooldown agent with past available_at gets promoted and can reserve;
   future available_at stays cooldown
5. Existing "allowed event ignored" behavior still green

[VERIFY]
Run from ai-queue-runner/ and paste real output into HANDOFF:
- ..\.venv\Scripts\python -m pytest -q
- ..\.venv\Scripts\python -m ruff check src tests

[ACCEPTANCE]
- Full suite green, ruff clean
- build_command diffs: none
- ai-anchors rule followed

[HANDOFF]
When done: add "[done] T019 auto usage-limit reset detection (Claude exact, Codex best-effort)"
to .agent/TASKS.md; set .agent/STATE.md (owner: codex, status: done, real pytest result,
next: "T020 prompt templates"); overwrite .agent/HANDOFF.md in the standard format.
Do not kill the running desktop app; do not write to data/queue.db.
