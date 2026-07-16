"""Defensive human-readable rendering for CLI JSONL logs."""

from __future__ import annotations

import json
import re
from typing import Any

_ARG_VALUE_LIMIT = 80
_RESULT_PREVIEW_LIMIT = 120
_NUMBERED_FILE_LINE = re.compile(r"^\s*\d+\s*(?:\t|→|:|\|)")


def render_pretty(text: str, agent: str | None = None) -> str:
    """Render recognized Claude or Codex JSONL events without exposing raw envelopes."""
    rendered_lines: list[str] = []
    normalized_agent = _normalize_agent(agent)
    for raw_line in text.splitlines(keepends=True):
        line, ending = _split_ending(raw_line)
        if not line.lstrip().startswith("{"):
            rendered_lines.append(raw_line)
            continue
        try:
            event = json.loads(line)
        except Exception:
            rendered_lines.append(raw_line)
            continue
        if not isinstance(event, dict):
            rendered_lines.append(raw_line)
            continue
        try:
            rendered = _render_event(event, normalized_agent)
        except Exception:
            rendered = ""
        if rendered:
            rendered_lines.append(f"{rendered}{ending}")
    return "".join(rendered_lines)


def _render_event(event: dict[str, Any], agent: str | None) -> str:
    event_type = event.get("type")
    if agent == "claude" or event_type in {
        "assistant",
        "rate_limit_event",
        "result",
        "system",
        "user",
    }:
        return _render_claude_event(event)
    if agent == "codex" or (
        isinstance(event_type, str)
        and (event_type.startswith("item.") or event_type in {"agent_message", "command_execution"})
    ):
        return _render_codex_event(event)
    return ""


def _render_claude_event(event: dict[str, Any]) -> str:
    event_type = event.get("type")
    if event_type == "system":
        if event.get("subtype") != "init":
            return ""
        model = _scalar_text(event.get("model"), "unknown")
        cwd = _scalar_text(event.get("cwd"), "?")
        return f"· session {model}, cwd={cwd}"
    if event_type == "assistant":
        return _render_claude_assistant(event)
    if event_type == "user":
        return _render_claude_tool_results(event)
    if event_type == "rate_limit_event":
        info = event.get("rate_limit_info")
        status = info.get("status") if isinstance(info, dict) else None
        if status == "allowed":
            return ""
        status_text = f" ({_scalar_text(status)})" if status else ""
        return f"⚠ rate limit{status_text}"
    if event_type == "result":
        turns = _scalar_text(event.get("num_turns"), "?")
        duration = _duration_seconds(event.get("duration_ms"))
        header = f"✔ result ({turns} turns, {duration}s)"
        result = event.get("result")
        return f"{header}\n{result}" if isinstance(result, str) and result else header
    return ""


def _render_claude_assistant(event: dict[str, Any]) -> str:
    message = event.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""
    rendered: list[str] = []
    for block in content:
        if not isinstance(block, dict):
            continue
        block_type = block.get("type")
        if block_type == "text" and isinstance(block.get("text"), str):
            rendered.append(block["text"])
        elif block_type == "tool_use":
            name = _scalar_text(block.get("name"), "tool")
            arguments = _compact_arguments(block.get("input"))
            rendered.append(f"→ {name}{f' {arguments}' if arguments else ''}")
    return "\n".join(rendered)


def _render_claude_tool_results(event: dict[str, Any]) -> str:
    message = event.get("message")
    if not isinstance(message, dict):
        return ""
    content = message.get("content")
    if not isinstance(content, list):
        return ""
    rendered: list[str] = []
    for block in content:
        if not isinstance(block, dict) or block.get("type") != "tool_result":
            continue
        result_text = _extract_text(block.get("content"))
        if not result_text or _is_file_dump(event, result_text):
            continue
        preview = _first_line_preview(result_text)
        if preview:
            rendered.append(f"← {preview}")
    return "\n".join(rendered)


def _render_codex_event(event: dict[str, Any]) -> str:
    event_type = event.get("type")
    if isinstance(event_type, str) and event_type.startswith("item."):
        if event_type not in {"item.completed", "item.failed"}:
            return ""
        item = event.get("item")
        return _render_codex_item(item) if isinstance(item, dict) else ""
    if event_type == "agent_message":
        return _extract_text(event.get("text") or event.get("message"))
    if event_type == "command_execution":
        return _render_codex_item(event)
    return ""


def _render_codex_item(item: dict[str, Any]) -> str:
    item_type = item.get("type")
    if item_type == "agent_message":
        return _extract_text(item.get("text") or item.get("message"))
    if item_type != "command_execution":
        return ""
    command = _compact_value(item.get("command"), _RESULT_PREVIEW_LIMIT)
    rendered = [f"→ command{f' {command}' if command else ''}"]
    output = _extract_text(item.get("aggregated_output") or item.get("output"))
    if output and not _looks_like_file_dump(output):
        preview = _first_line_preview(output)
        if preview:
            rendered.append(f"← {preview}")
    return "\n".join(rendered)


def _compact_arguments(value: Any) -> str:
    if isinstance(value, dict):
        return ", ".join(
            f"{key}={_compact_value(item, _ARG_VALUE_LIMIT)}"
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        )
    return _compact_value(value, _ARG_VALUE_LIMIT)


def _compact_value(value: Any, limit: int) -> str:
    if isinstance(value, dict):
        compact = ", ".join(
            f"{key}:{_compact_value(item, limit)}"
            for key, item in sorted(value.items(), key=lambda pair: str(pair[0]))
        )
    elif isinstance(value, list):
        compact = "[" + ", ".join(_compact_value(item, limit) for item in value) + "]"
    elif value is None:
        compact = "null"
    elif isinstance(value, bool):
        compact = str(value).lower()
    else:
        compact = str(value)
    one_line = " ".join(compact.split())
    return f"{one_line[: limit - 1]}…" if len(one_line) > limit else one_line


def _extract_text(value: Any) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, dict):
        for key in ("text", "content", "message"):
            extracted = _extract_text(value.get(key))
            if extracted:
                return extracted
        return ""
    if isinstance(value, list):
        parts = [_extract_text(item) for item in value]
        return "\n".join(part for part in parts if part)
    return ""


def _first_line_preview(text: str) -> str:
    lines = text.splitlines()
    if not lines:
        return ""
    first_line = lines[0]
    truncated = "\n" in text or "\r" in text or len(first_line) > _RESULT_PREVIEW_LIMIT
    preview = first_line[:_RESULT_PREVIEW_LIMIT]
    return f"{preview}…" if truncated else preview


def _is_file_dump(event: dict[str, Any], text: str) -> bool:
    tool_result = event.get("tool_use_result")
    if isinstance(tool_result, dict) and isinstance(tool_result.get("file"), dict):
        return True
    return _looks_like_file_dump(text)


def _looks_like_file_dump(text: str) -> bool:
    lines = [line for line in text.splitlines()[:4] if line.strip()]
    return len(lines) >= 2 and sum(bool(_NUMBERED_FILE_LINE.match(line)) for line in lines) >= 2


def _duration_seconds(value: Any) -> str:
    try:
        return f"{float(value) / 1000:.0f}"
    except (TypeError, ValueError, OverflowError):
        return "?"


def _scalar_text(value: Any, default: str = "") -> str:
    return str(value) if value is not None else default


def _normalize_agent(agent: str | None) -> str | None:
    if not agent:
        return None
    normalized = str(agent).casefold()
    if normalized.endswith("claude"):
        return "claude"
    if normalized.endswith("codex"):
        return "codex"
    return None


def _split_ending(line: str) -> tuple[str, str]:
    for ending in ("\r\n", "\n", "\r"):
        if line.endswith(ending):
            return line[: -len(ending)], ending
    return line, ""
