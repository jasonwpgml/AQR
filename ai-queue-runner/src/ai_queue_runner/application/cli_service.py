"""Thin headless command handlers over the existing task application service."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from typing import Any

from pydantic import ValidationError

from ai_queue_runner.application.agent_service import AgentService, AgentView
from ai_queue_runner.application.task_service import TaskDraft, TaskService
from ai_queue_runner.config.defaults import AppConfig
from ai_queue_runner.domain.enums import TaskStatus
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
        session_factory = make_session_factory(engine)
        service = TaskService(session_factory, config)
        agents = AgentService(session_factory)
        try:
            return _dispatch(args, service, agents, config)
        except (KeyError, ValidationError, ValueError) as exc:
            raise CliUsageError(_one_line(str(exc)).strip("'")) from exc
    finally:
        engine.dispose()


def _dispatch(
    args: argparse.Namespace,
    service: TaskService,
    agents: AgentService,
    config: AppConfig,
) -> str:
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
    if args.command == "retry-task":
        task = service.retry(args.task_id)
        return _json_or_text(
            _task_record(task),
            f"작업 #{task.id} 재대기: {task.title}",
            args.json,
        )
    if args.command == "update-task":
        task = service.update(args.task_id, _merged_draft(args, service))
        return _json_or_text(
            _task_record(task),
            f"작업 #{task.id} 수정: [{task.status.value}] "
            f"{task.agent_policy.value} P{task.priority} {task.title}",
            args.json,
        )
    if args.command == "duplicate-task":
        task = service.duplicate(args.task_id)
        return _json_or_text(
            _task_record(task),
            f"작업 #{args.task_id} 복제 → 새 작업 #{task.id}: {task.title}",
            args.json,
        )
    if args.command == "move-top":
        task = service.move_to_top(args.task_id)
        return _json_or_text(
            _task_record(task),
            f"작업 #{task.id}을 대기열 맨 위로 이동 (P{task.priority})",
            args.json,
        )
    if args.command == "archive-task":
        task = service.archive(args.task_id)
        return _json_or_text(
            _task_record(task),
            f"작업 #{task.id} 보관: {task.title}",
            args.json,
        )
    if args.command == "delete-task":
        service.delete(args.task_id)
        return _json_or_text(
            {"deleted": args.task_id},
            f"작업 #{args.task_id}과 실행 기록을 삭제했습니다.",
            args.json,
        )
    if args.command == "set-agent":
        view = agents.set_availability(
            args.agent,
            args.state,
            _parse_local_datetime(args.until),
        )
        return _json_or_text(
            _agent_record(view),
            f"{view.agent.value}: {view.status.value}"
            + (f" (재사용 가능: {_utc_iso(view.available_at)})" if view.available_at else ""),
            args.json,
        )
    if args.command == "queue-status":
        return _queue_status(args, service, agents, config)
    raise CliUsageError(f"지원하지 않는 명령입니다: {args.command}")


def _merged_draft(args: argparse.Namespace, service: TaskService) -> TaskDraft:
    """Overlay only the provided flags on the stored task so updates stay partial."""
    current = service.get(args.task_id)
    if current is None:
        raise CliUsageError(f"작업 #{args.task_id}을 찾을 수 없습니다.")
    if args.clear_not_before and args.not_before is not None:
        raise CliUsageError("--not-before와 --clear-not-before는 함께 쓸 수 없습니다.")
    if args.clear_not_before:
        not_before = None
    elif args.not_before is not None:
        not_before = _parse_local_datetime(args.not_before)
    else:
        not_before = current.not_before
    return TaskDraft(
        title=current.title if args.title is None else args.title,
        prompt=current.prompt if args.prompt is None else args.prompt,
        project_path=current.project_path if args.project is None else args.project,
        agent_policy=current.agent_policy if args.agent is None else args.agent,
        priority=current.priority if args.priority is None else args.priority,
        approval_mode=current.approval_mode if args.approval is None else args.approval,
        max_retries=current.max_retries if args.max_retries is None else args.max_retries,
        retry_delay_minutes=(
            current.retry_delay_minutes if args.retry_delay is None else args.retry_delay
        ),
        not_before=not_before,
        tags=list(current.tags) if args.tag is None else args.tag,
        dependency_ids=(
            list(current.dependency_ids) if args.depends_on is None else args.depends_on
        ),
    )


def _queue_status(
    args: argparse.Namespace,
    service: TaskService,
    agents: AgentService,
    config: AppConfig,
) -> str:
    tasks = service.list()
    counts = Counter(_enum_value(task.status) for task in tasks)
    archived_count = len(service.list(status="archived"))
    pending_confirmations = [
        {"id": task.id, "title": task.title, "approval_mode": _enum_value(task.approval_mode)}
        for task in tasks
        if task.status is TaskStatus.RESERVED
    ]
    payload = {
        "global_auto_run": config.app.global_auto_run,
        "allowed_project_roots": [str(root) for root in config.app.allowed_project_roots],
        "task_counts": dict(sorted(counts.items())),
        "archived_count": archived_count,
        "pending_confirmations": pending_confirmations,
        "agents": [_agent_record(view) for view in agents.list()],
    }
    lines = [
        f"global_auto_run: {str(config.app.global_auto_run).lower()}",
        "allowed_project_roots: "
        + (", ".join(payload["allowed_project_roots"]) or "(비어 있음 — 실행 차단됨)"),
        "task_counts: "
        + (
            " ".join(f"{status}={count}" for status, count in payload["task_counts"].items())
            or "(작업 없음)"
        )
        + f" archived={archived_count}",
        "agents:",
    ]
    lines.extend(
        f"  {record['agent']}: {record['status']}"
        + (f" (재사용 가능: {record['available_at']})" if record["available_at"] else "")
        + (
            f" (작업 #{record['current_task_id']} 진행 중)"
            if record["current_task_id"] is not None
            else ""
        )
        for record in payload["agents"]
    )
    if pending_confirmations:
        lines.append("승인 대기(reserved):")
        lines.extend(
            f"  #{entry['id']} {entry['title']} ({entry['approval_mode']})"
            for entry in pending_confirmations
        )
    return _json_or_text(payload, "\n".join(lines), args.json)


def _agent_record(view: AgentView) -> dict[str, Any]:
    return {
        "agent": _enum_value(view.agent),
        "status": _enum_value(view.status),
        "available_at": _utc_iso(view.available_at),
        "current_task_id": view.current_task_id,
        "last_error": view.last_error,
    }


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
