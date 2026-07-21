from __future__ import annotations

import json

from ai_queue_runner.application.log_rendering import render_pretty


def _line(event: dict) -> str:
    return json.dumps(event, ensure_ascii=False)


def test_render_pretty_uses_representative_captured_claude_events():
    fixture = "\n".join(
        [
            _line(
                {
                    "type": "system",
                    "subtype": "init",
                    "model": "claude-fable-5",
                    "cwd": "C:/work/ai-queue-runner",
                    "tools": ["Read", "Bash"],
                }
            ),
            _line(
                {
                    "type": "rate_limit_event",
                    "rate_limit_info": {"status": "allowed"},
                }
            ),
            _line(
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "private reasoning"},
                            {
                                "type": "text",
                                "text": "I'll start by reading AGENTS.md.",
                            },
                            {
                                "type": "tool_use",
                                "name": "Read",
                                "input": {"file_path": "C:/work/AGENTS.md"},
                            },
                        ]
                    },
                }
            ),
            _line(
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "content": "1\t# AGENTS.md\n2\tInstructions for agents",
                            }
                        ]
                    },
                    "tool_use_result": {
                        "type": "text",
                        "file": {
                            "filePath": "C:/work/AGENTS.md",
                            "content": "shortened fixture",
                        },
                    },
                }
            ),
            _line(
                {
                    "type": "result",
                    "subtype": "success",
                    "num_turns": 8,
                    "duration_ms": 58099,
                    "result": "검증을 완료했습니다.",
                }
            ),
        ]
    )

    rendered = render_pretty(fixture, agent="claude")

    assert "· session claude-fable-5, cwd=C:/work/ai-queue-runner" in rendered
    assert "I'll start by reading AGENTS.md." in rendered
    assert "→ Read file_path=C:/work/AGENTS.md" in rendered
    assert "✔ result (8 turns, 58s)" in rendered
    assert "검증을 완료했습니다." in rendered
    assert "private reasoning" not in rendered
    assert "Instructions for agents" not in rendered
    assert "{" not in rendered


def test_render_pretty_preserves_plain_text_in_mixed_log():
    text = 'plain before\n{"type":"assistant","message":{"content":[]}}\nplain after\n'

    assert render_pretty(text, agent="claude") == "plain before\nplain after\n"


def test_render_pretty_preserves_malformed_json_without_raising():
    malformed = '{"type":"assistant", this is not valid JSON}\n'

    assert render_pretty(malformed, agent="claude") == malformed


def test_render_pretty_omits_allowed_rate_limit_event():
    event = _line(
        {"type": "rate_limit_event", "rate_limit_info": {"status": "allowed"}}
    )

    assert render_pretty(event, agent="claude") == ""


def test_render_pretty_renders_codex_messages_and_commands():
    fixture = "\n".join(
        [
            _line(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "item_1",
                        "type": "command_execution",
                        "command": "pytest -q",
                        "aggregated_output": "3 passed\n",
                    },
                }
            ),
            _line(
                {
                    "type": "item.completed",
                    "item": {
                        "id": "item_2",
                        "type": "agent_message",
                        "text": "All checks pass.",
                    },
                }
            ),
            _line({"type": "turn.completed", "usage": {"input_tokens": 10}}),
        ]
    )

    rendered = render_pretty(fixture, agent="codex")

    assert "→ command pytest -q" in rendered
    assert "← 3 passed…" in rendered
    assert "All checks pass." in rendered
    assert "{" not in rendered


def test_render_pretty_handles_huge_plain_line():
    text = "x" * (2 * 1024 * 1024)

    assert render_pretty(text) == text
