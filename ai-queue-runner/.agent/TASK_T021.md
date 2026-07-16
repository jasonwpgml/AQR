[TASK]
ID: T021
Goal: Three UX/robustness fixes: (1) explicit app-exit with a running-task warning,
(2) task deletion from the task list, (3) resolve agent CLI executables robustly so the
packaged exe finds `claude` even when its directory is not on the persistent PATH — plus
make retry_wait visibly different from plain waiting.

[CONTEXT]
- Read AGENTS.md and .agent/{STATE,DECISIONS,HANDOFF}.md first.
- ROOT CAUSE (verified on this machine): claude.exe lives in %USERPROFILE%\.local\bin which
  is NOT in the persistent user/machine PATH (only in interactive shells). A GUI exe
  launched from Explorer therefore gets FileNotFoundError at spawn. codex.exe IS on the
  user PATH. AgentSection already has an `executable` override field.
- Real user-visible symptom: task failed with raw "FileNotFoundError: [WinError 2] ..." in
  last_error, then sat in retry_wait ("대기"처럼 보임) for retry_delay_minutes with no
  explanation.
- Tray already has a 종료 action; the main window itself has no explicit exit, and closing
  the window minimizes to tray when available.

[SCOPE]
May modify:
- src/ai_queue_runner/infrastructure/adapters/{base,claude,codex}.py
- src/ai_queue_runner/application/task_service.py      (add delete)
- src/ai_queue_runner/application/execution_service.py (ONLY spawn-failure message wording)
- src/ai_queue_runner/ui/{main_window,task_table,tray,agent_status_widget}.py
- tests/unit/{test_adapters,test_ui}.py, tests/integration/test_application_services.py
- .agent/STATE.md, .agent/TASKS.md, .agent/HANDOFF.md
Must NOT modify:
- domain/**, scheduler_service.py, recovery_service.py, process_runner.py, config/**,
  prompts/**, docs/**, README.md, pyproject.toml, main.py

[REQUIREMENTS]
1. Executable resolution (the bug fix):
   - In the shared adapter base, resolve the executable at adapter construction:
     explicit settings.executable -> use as-is (expand ~ and env vars);
     otherwise shutil.which(name); otherwise probe well-known install locations
     (claude: %USERPROFILE%\.local\bin\claude.exe; codex:
     %LOCALAPPDATA%\Programs\OpenAI\Codex\bin\codex.exe); otherwise keep the bare name.
   - build_command must emit the RESOLVED absolute path when one was found.
   - is_installed() reflects the resolution result.
   - Agent status panel: when an enabled agent's CLI is not resolvable, show a clear
     Korean warning (e.g. "claude CLI를 찾을 수 없습니다 — 설정에서 executable 경로를
     지정하세요") instead of allowing 사용 가능.
   - Spawn-failure last_error becomes actionable Korean text including the attempted
     executable, not a bare FileNotFoundError repr.
2. App exit:
   - Main window gets a 종료 button/menu action; tray 종료 reuses the same path.
   - If any task is running (or reserved with a live process), show a warning first:
     "정말 종료하시겠습니까? 진행 중인 작업이 모두 중지됩니다." Confirm -> cancel running
     executions (existing cancellation path), then quit. Decline -> nothing happens.
   - When nothing is running, exit immediately without the warning.
3. Task deletion:
   - Task table context menu + a 삭제 button for the selected task.
   - Terminal-status tasks (succeeded/failed/cancelled) and waiting/retry_wait/blocked
     tasks are deletable after a confirm box noting run history is deleted too (CASCADE).
   - running/reserved tasks are NOT deletable (button disabled) — must cancel first.
   - TaskService.delete(task_id) enforces the same rule at the service layer, and blocks
     deletion when other tasks depend on it (clear error listing dependent ids).
4. retry_wait visibility: in the task table, show retry_wait as "재시도 대기(HH:MM)" using
   not_before (local time), and make last_error reachable (tooltip or detail pane).

[TESTS]
1. adapter resolution: explicit executable wins; which() hit resolves; known-location probe
   resolves (monkeypatched paths); unresolved -> is_installed False and bare name kept
2. build_command contains the resolved absolute path
3. TaskService.delete: deletes terminal task + its runs; rejects running/reserved; rejects
   when dependents exist; allows after dependents removed
4. UI offscreen: delete button disabled for running task, enabled for succeeded; exit with
   no running tasks quits without dialog (assert no dialog); with a (faked) running task
   the confirm path is exercised
5. retry_wait row renders the 재시도 대기 label with time

[VERIFY]
Run from ai-queue-runner/ and paste real output into HANDOFF:
- ..\.venv\Scripts\python -m pytest -q
- ..\.venv\Scripts\python -m ruff check src tests

[ACCEPTANCE]
- Full suite green, ruff clean
- No shell=True / os.kill introduced; CLI flags otherwise unchanged
- ai-anchors rule followed

[HANDOFF]
When done: add "[done] T021 exit warning, task deletion, CLI path resolution, retry_wait visibility"
to .agent/TASKS.md; set .agent/STATE.md (owner: codex, status: done, real pytest result,
next: "user feedback"); overwrite .agent/HANDOFF.md in the standard format.
Do NOT kill any running desktop app; do not write to data/queue.db.
