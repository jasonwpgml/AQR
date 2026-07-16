"""Thin headless command handlers over the existing task application service."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from ai_queue_runner.application.task_service import TaskDraft, TaskService
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.infrastructure.database import (
    create_db_engine,
    init_db,
    make_session_factory,
)


class CliUsageError(ValueError):
    """Expected invalid command input or disallowed task operation."""


def run_cli_command(args: argparse.Namespace, config: AppConfig) -> str:
    engine = create_db_engine(config.app.db_path)
    try:
        init_db(engine)
        service = TaskService(make_session_factory(engine), config)
        try:
            return _dispatch(args, service)
        except (KeyError, ValidationError, ValueError) as exc:
            raise CliUsageError(_one_line(str(exc)).strip("'")) from exc
    finally:
        engine.dispose()


def _dispatch(args: argparse.Namespace, service: TaskService) -> str:
    if args.command == "add-task":
        task = service.create(
            TaskDraft(
                title=args.title,
                prompt=args.prompt,
                project_path=args.project,
                agent_policy=args.agent,
                priority=args.priority,
                approval_mode=args.approval,
                max_retries=args.max_retries,
                not_before=_parse_local_datetime(args.not_before),
                tags=args.tag,
                dependency_ids=args.depends_on,
            )
        )
        return _json_or_text(
            _task_record(task),
            f"작업 #{task.id} 생성: [{task.status.value}] "
            f"{task.agent_policy.value} P{task.priority} {task.title}",
            args.json,
        )
    if args.command == "list-tasks":
        tasks = service.list(status=args.status)
        records = [_task_record(task) for task in tasks]
        return _json_or_text(records, _task_table(records), args.json)
    if args.command == "show-task":
        task = service.get(args.task_id)
        if task is None:
            raise CliUsageError(f"작업 #{args.task_id}을 찾을 수 없습니다.")
        record = _task_record(task)
        record["recent_runs"] = [
            _run_record(run) for run in reversed(service.list_runs(task.id)[-5:])
        ]
        return _json_or_text(record, _task_details(record), args.json)
    if args.command == "cancel-task":
        task = service.cancel(args.task_id)
        return _json_or_text(
            _task_record(task),
            f"작업 #{task.id} 취소: {task.title}",
            args.json,
        )
    raise CliUsageError(f"지원하지 않는 명령입니다: {args.command}")


def _parse_local_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        local = datetime.strptime(value, "%Y-%m-%d %H:%M").astimezone()
    except ValueError as exc:
        raise CliUsageError(
            "--not-before는 YYYY-MM-DD HH:MM 형식이어야 합니다."
        ) from exc
    return local.astimezone(UTC).replace(tzinfo=None)


def _task_record(task: object) -> dict[str, Any]:
    return {
        "id": int(task.id),
        "title": task.title,
        "prompt": task.prompt,
        "project_path": task.project_path,
        "agent_policy": _enum_value(task.agent_policy),
        "assigned_agent": _enum_value(task.assigned_agent),
        "priority": int(task.priority),
        "status": _enum_value(task.status),
        "approval_mode": _enum_value(task.approval_mode),
        "max_retries": int(task.max_retries),
        "retry_count": int(task.retry_count),
        "retry_delay_minutes": int(task.retry_delay_minutes),
        "not_before": _utc_iso(task.not_before),
        "reserved_at": _utc_iso(task.reserved_at),
        "started_at": _utc_iso(task.started_at),
        "finished_at": _utc_iso(task.finished_at),
        "created_at": _utc_iso(task.created_at),
        "updated_at": _utc_iso(task.updated_at),
        "last_error": task.last_error,
        "tags": list(task.tags),
        "dependency_ids": list(task.dependency_ids),
        "archived": bool(task.archived),
    }


def _run_record(run: object) -> dict[str, Any]:
    return {
        "id": int(run.id),
        "task_id": int(run.task_id),
        "agent": _enum_value(run.agent),
        "attempt": int(run.attempt),
        "command": list(run.command),
        "pid": run.pid,
        "exit_code": run.exit_code,
        "stdout_path": run.stdout_path,
        "stderr_path": run.stderr_path,
        "started_at": _utc_iso(run.started_at),
        "finished_at": _utc_iso(run.finished_at),
        "result_summary": run.result_summary,
        "error_type": _enum_value(run.error_type),
    }


def _task_table(records: list[dict[str, Any]]) -> str:
    if not records:
        return "작업이 없습니다."
    rows = ["ID  STATUS       AGENT   PRIORITY  TITLE"]
    rows.extend(
        f"{record['id']:<3} {record['status']:<12} "
        f"{(record['assigned_agent'] or record['agent_policy']):<7} "
        f"{record['priority']:<8}  {record['title']}"
        for record in records
    )
    return "\n".join(rows)


def _task_details(record: dict[str, Any]) -> str:
    lines = [
        f"{key}: {_text_value(value)}"
        for key, value in record.items()
        if key != "recent_runs"
    ]
    lines.append("recent_runs:")
    runs = record["recent_runs"]
    if not runs:
        lines.append("  (없음)")
    else:
        lines.extend(
            "  - "
            f"attempt={run['attempt']} agent={run['agent']} exit_code={run['exit_code']} "
            f"result_summary={_text_value(run['result_summary'])}"
            for run in runs
        )
    return "\n".join(lines)


def _json_or_text(payload: object, text: str, as_json: bool) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True) if as_json else text


def _utc_iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    aware = value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)
    return aware.isoformat().replace("+00:00", "Z")


def _enum_value(value: object) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))


def _text_value(value: object) -> str:
    if value is None:
        return "-"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "-"
    return str(value)


def _one_line(value: str) -> str:
    return " ".join(value.splitlines())
