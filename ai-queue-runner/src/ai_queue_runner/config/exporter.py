"""Deterministic TOML export for the validated effective configuration."""

from __future__ import annotations

import json
from pathlib import Path

from ai_queue_runner.config.defaults import AppConfig


def export_config(config: AppConfig, destination: Path | str) -> Path:
    path = Path(destination)
    path.parent.mkdir(parents=True, exist_ok=True)
    sections = config.model_dump(mode="python")
    lines: list[str] = []

    _append_section(lines, "app", sections["app"])
    _append_section(lines, "scheduler", sections["scheduler"])
    for agent_name in sorted(sections["agents"]):
        _append_section(lines, f"agents.{agent_name}", sections["agents"][agent_name])

    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")
    return path


def _append_section(lines: list[str], name: str, values: dict[str, object]) -> None:
    if lines:
        lines.append("")
    lines.append(f"[{name}]")
    lines.extend(
        f"{key} = {_toml_value(value)}"
        for key, value in values.items()
        if value is not None
    )


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Path):
        return json.dumps(value.as_posix(), ensure_ascii=False)
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=False)
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item) for item in value) + "]"
    raise TypeError(f"unsupported TOML value: {type(value).__name__}")
