FROM: codex
TO: claude
TASK: T028
STATUS: completed

CHANGED:
- ../AI_QUEUE_RUNNER_PLAN.md
- .agent/STATE.md
- .agent/TASKS.md
- .agent/HANDOFF.md

VERIFIED:
- `git diff --stat` -> Markdown-only changes in ../AI_QUEUE_RUNNER_PLAN.md, .agent/STATE.md, .agent/TASKS.md, .agent/HANDOFF.md
- pytest/ruff not run; T028 changed Markdown only.

NOTES:
- Changed plan section 6.2 Claude adapter marker from `[TODO]` to `[DONE]` and made the CLI command-format sentence completed.
- Changed plan section 6.3 Codex adapter marker from `[TODO]` to `[DONE]` and made the CLI command-format sentence completed.
- Updated plan section 3.3 to reflect that usage-limit reset times can still be entered manually and are now auto-parsed when available from CLI output.
- Removed "사용량 복구 예상 시각 자동 파싱" from plan section 20 future extensions because T019 implemented it.
- Left section 17's initial Claude instruction text unchanged because it is historical task guidance, not current implementation status.

RISKS:
- Other future-looking statements were left untouched where they describe original phase criteria, review process, or design principles rather than clearly obsolete implementation state.
