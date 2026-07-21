"""Thin headless command handlers over the existing task application service."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ai_queue_runner.application.agent_service import AgentService, AgentView
from ai_queue_runner.application.daily_summary_formatter import format_daily_summary
from ai_queue_runner.application.task_service import TaskDraft, TaskService
from ai_queue_runner.application.task_template_service import (
    TaskTemplateDraft,
    TaskTemplateService,
)
from ai_queue_runner.application.workload_planning import (
    WorkloadBatch,
    WorkloadPlan,
    plan_workload,
)
from ai_queue_runner.application.worktree_service import WorktreeService
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
        actor = getattr(args, "actor", "user")
        actor_session_id = getattr(args, "actor_session", None)
        service = TaskService(
            session_factory,
            config,
            actor=actor,
            actor_session_id=actor_session_id,
        )
        templates = TaskTemplateService(
            session_factory,
            actor=actor,
            actor_session_id=actor_session_id,
        )
        agents = AgentService(session_factory)
        worktrees = WorktreeService(session_factory, config)
        try:
            return _dispatch(args, service, templates, agents, worktrees, config)
        except (KeyError, ValidationError, ValueError) as exc:
            raise CliUsageError(_one_line(str(exc)).strip("'")) from exc
    finally:
        engine.dispose()


def _dispatch(
    args: argparse.Namespace,
    service: TaskService,
    templates: TaskTemplateService,
    agents: AgentService,
    worktrees: WorktreeService,
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
                estimated_tokens=args.estimated_tokens,
                approval_mode=args.approval,
                auto_commit=args.auto_commit,
                draft_pr=args.draft_pr,
                max_retries=args.max_retries,
                recurrence_interval_days=_parse_recurrence_interval(args.repeat),
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
    if args.command == "save-template":
        template, overwritten = templates.save(
            args.name,
            TaskTemplateDraft(
                title=args.title,
                prompt=args.prompt,
                project_path=args.project,
                agent_policy=args.agent,
                priority=args.priority,
                estimated_tokens=args.estimated_tokens,
                approval_mode=args.approval,
                auto_commit=args.auto_commit,
                draft_pr=args.draft_pr,
                max_retries=args.max_retries,
                retry_delay_minutes=args.retry_delay,
                recurrence_interval_days=_parse_recurrence_interval(args.repeat),
                not_before=_parse_local_datetime(args.not_before),
                tags=args.tag,
                dependency_ids=args.depends_on,
            ),
        )
        record = _template_record(template)
        record["overwritten"] = overwritten
        action = "덮어씀" if overwritten else "저장"
        return _json_or_text(
            record,
            f"템플릿 {template.name!r} {action}: {template.title}",
            args.json,
        )
    if args.command == "list-templates":
        records = [_template_record(template) for template in templates.list()]
        lines = ["NAME  AGENT  PRIORITY  TITLE"]
        lines.extend(
            f"{record['name']}  {record['agent_policy']}  "
            f"P{record['priority']}  {record['title']}"
            for record in records
        )
        return _json_or_text(
            records,
            "템플릿이 없습니다." if not records else "\n".join(lines),
            args.json,
        )
    if args.command == "show-template":
        template = templates.get(args.name)
        if template is None:
            raise CliUsageError(f"템플릿 {args.name!r}을 찾을 수 없습니다.")
        record = _template_record(template)
        return _json_or_text(
            record,
            "\n".join(f"{key}: {_text_value(value)}" for key, value in record.items()),
            args.json,
        )
    if args.command == "delete-template":
        templates.delete(args.name)
        return _json_or_text(
            {"deleted": args.name.strip()},
            f"템플릿 {args.name.strip()!r}을 삭제했습니다.",
            args.json,
        )
    if args.command == "add-from-template":
        draft = templates.render(
            args.name,
            _parse_template_variables(args.var),
            title=args.title,
            prompt=args.prompt,
            project_path=args.project,
            agent_policy=args.agent,
            priority=args.priority,
            estimated_tokens=args.estimated_tokens,
            approval_mode=args.approval,
            auto_commit=args.auto_commit,
            draft_pr=args.draft_pr,
            max_retries=args.max_retries,
            retry_delay_minutes=args.retry_delay,
            recurrence_interval_days=(
                None if args.repeat is None else _parse_recurrence_interval(args.repeat)
            ),
            not_before=(
                None if args.not_before is None else _parse_local_datetime(args.not_before)
            ),
            tags=args.tag,
            dependency_ids=args.depends_on,
        )
        if args.dry_run:
            service.validate_batch([draft])
            task = None
        else:
            task = service.create(draft)
        payload = {
            "template": args.name.strip(),
            "enqueued": task is not None,
            "draft": _draft_record(draft),
            "task": None if task is None else _task_record(task),
        }
        action = "미리보기" if task is None else f"작업 #{task.id} 생성"
        return _json_or_text(
            payload,
            f"템플릿 {args.name.strip()!r} {action}: {draft.title}",
            args.json,
        )
    if args.command == "list-tasks":
        tasks = service.list(
            status=args.status,
            project_path=args.project,
            created_by=args.created_by,
            creator_session_id=args.creator_session,
            active=args.active,
        )
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
        record["recent_events"] = [
            _event_record(event) for event in service.list_events(task.id, limit=10)
        ]
        return _json_or_text(record, _task_details(record), args.json)
    if args.command == "review-task":
        result = service.review_task(
            args.task_id,
            verdict=args.verdict,
            score=args.score,
            notes=args.notes,
            reopen_prompt=args.reopen,
        )
        payload = {
            "task": _task_record(result.task),
            "review": {
                "id": result.review.id,
                "task_id": result.review.task_id,
                "verdict": result.review.verdict.value,
                "score": result.review.score,
                "notes": result.review.notes,
                "reviewed_at": _utc_iso(result.review.reviewed_at),
                "revision": result.review.revision,
            },
            "previous_review_count": result.previous_review_count,
            "rework_task": (
                None
                if result.rework_task is None
                else _task_record(result.rework_task)
            ),
        }
        rework_text = (
            ""
            if result.rework_task is None
            else f"; rework task #{result.rework_task.id} enqueued"
        )
        return _json_or_text(
            payload,
            f"Task #{args.task_id} review revision {result.review.revision}: "
            f"{result.review.verdict.value}"
            + (f" score={result.review.score}" if result.review.score else "")
            + rework_text,
            args.json,
        )
    if args.command == "pending-reviews":
        records = [
            {
                "id": task.id,
                "title": task.title,
                "project_path": task.project_path,
                "finished_at": _utc_iso(task.finished_at),
            }
            for task in service.pending_reviews(project_path=args.project)
        ]
        lines = ["ID  FINISHED_AT           PROJECT  TITLE"]
        lines.extend(
            f"{record['id']:<3} {(record['finished_at'] or '-'):<21} "
            f"{record['project_path']}  {record['title']}"
            for record in records
        )
        return _json_or_text(
            records,
            "평가 대기 작업이 없습니다." if not records else "\n".join(lines),
            args.json,
        )
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
    if args.command == "delete-tasks":
        result = service.delete_project_tasks(
            args.project,
            created_by=args.created_by,
            creator_session_id=args.creator_session,
            statuses=args.status,
        )
        payload = {
            "project_path": result.project_path,
            "matched_count": result.matched_count,
            "deleted_ids": list(result.deleted_ids),
            "skipped": [
                {"id": item.id, "title": item.title, "reason": item.reason}
                for item in result.skipped
            ],
        }
        lines = [
            f"프로젝트 작업 {result.matched_count}개 중 "
            f"{len(result.deleted_ids)}개를 삭제했습니다.",
        ]
        lines.extend(
            f"- 건너뜀 #{item.id} {item.title}: {item.reason}"
            for item in result.skipped
        )
        return _json_or_text(payload, "\n".join(lines), args.json)
    if args.command == "list-events":
        events = service.list_events(
            args.task_id,
            project_path=args.project,
            actor=args.event_actor,
            limit=args.limit,
        )
        records = [_event_record(event) for event in events]
        lines = ["EVENT  TASK  TIME                  ACTOR    ACTION     PROJECT  TITLE"]
        lines.extend(
            f"{record['id']:<6} #{record['task_id']:<4} "
            f"{(record['created_at'] or '-'):<21} {record['actor']:<8} "
            f"{record['action']:<10} {record['project_path']}  {record['task_title']}"
            for record in records
        )
        return _json_or_text(
            records,
            "관리 이벤트가 없습니다." if not records else "\n".join(lines),
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
    if args.command == "daily-summary":
        summary = service.daily_summary(_parse_local_date(args.date))
        payload = _daily_summary_record(summary)
        return _json_or_text(payload, format_daily_summary(summary), args.json)
    if args.command == "plan-batch":
        return _plan_batch(args, service)
    if args.command == "split-task":
        return _split_task(args, service)
    if args.command == "cleanup-worktree":
        cleaned = worktrees.cleanup(args.task_id)
        return _json_or_text(
            {
                "task_id": cleaned.task_id,
                "worktree_path": cleaned.path,
                "worktree_branch": cleaned.branch,
            },
            f"작업 #{cleaned.task_id} worktree 제거: {cleaned.path} "
            f"(branch {cleaned.branch} 유지)",
            args.json,
        )
    raise CliUsageError(f"지원하지 않는 명령입니다: {args.command}")


def _merged_draft(args: argparse.Namespace, service: TaskService) -> TaskDraft:
    """Overlay only the provided flags on the stored task so updates stay partial."""
    current = service.get(args.task_id)
    if current is None:
        raise CliUsageError(f"작업 #{args.task_id}을 찾을 수 없습니다.")
    if args.clear_not_before and args.not_before is not None:
        raise CliUsageError("--not-before와 --clear-not-before는 함께 쓸 수 없습니다.")
    if args.clear_repeat and args.repeat is not None:
        raise CliUsageError("--repeat와 --clear-repeat는 함께 쓸 수 없습니다.")
    if args.clear_not_before:
        not_before = None
    elif args.not_before is not None:
        not_before = _parse_local_datetime(args.not_before)
    else:
        not_before = current.not_before
    if args.clear_repeat:
        recurrence_interval_days = None
    elif args.repeat is not None:
        recurrence_interval_days = _parse_recurrence_interval(args.repeat)
    else:
        recurrence_interval_days = current.recurrence_interval_days
    return TaskDraft(
        title=current.title if args.title is None else args.title,
        prompt=current.prompt if args.prompt is None else args.prompt,
        project_path=current.project_path if args.project is None else args.project,
        created_by=current.created_by,
        creator_session_id=current.creator_session_id,
        agent_policy=current.agent_policy if args.agent is None else args.agent,
        priority=current.priority if args.priority is None else args.priority,
        estimated_tokens=(
            current.estimated_tokens
            if args.estimated_tokens is None
            else args.estimated_tokens
        ),
        approval_mode=current.approval_mode if args.approval is None else args.approval,
        auto_commit=(
            current.auto_commit if args.auto_commit is None else args.auto_commit
        ),
        draft_pr=current.draft_pr if args.draft_pr is None else args.draft_pr,
        max_retries=current.max_retries if args.max_retries is None else args.max_retries,
        retry_delay_minutes=(
            current.retry_delay_minutes if args.retry_delay is None else args.retry_delay
        ),
        recurrence_interval_days=recurrence_interval_days,
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
        "worktree_parallel_enabled": config.scheduler.worktree_parallel_enabled,
        "auto_commit_enabled": config.scheduler.auto_commit_enabled,
        "draft_pr_enabled": config.scheduler.draft_pr_enabled,
        "worktrees_dir": str(config.app.worktrees_dir),
        "allowed_project_roots": [str(root) for root in config.app.allowed_project_roots],
        "task_counts": dict(sorted(counts.items())),
        "archived_count": archived_count,
        "pending_confirmations": pending_confirmations,
        "agents": [_agent_record(view) for view in agents.list()],
    }
    lines = [
        f"global_auto_run: {str(config.app.global_auto_run).lower()}",
        "worktree_parallel_enabled: "
        f"{str(config.scheduler.worktree_parallel_enabled).lower()}",
        f"worktrees_dir: {config.app.worktrees_dir}",
        f"auto_commit_enabled: {str(config.scheduler.auto_commit_enabled).lower()}",
        f"draft_pr_enabled: {str(config.scheduler.draft_pr_enabled).lower()}",
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


def _plan_batch(args: argparse.Namespace, service: TaskService) -> str:
    batch = _load_workload_batch(args.plan_path)
    plan = plan_workload(batch)
    drafts = [assignment.draft for assignment in plan.assignments]
    if args.dry_run:
        service.validate_batch(drafts)
        created = []
    else:
        created = service.create_batch(drafts)
    records = _workload_task_records(plan, created)
    payload = {
        "enqueued": not args.dry_run,
        "budgets": plan.budgets.model_dump(),
        "assigned_tokens": {
            agent.value: tokens for agent, tokens in plan.assigned_tokens.items()
        },
        "remaining_tokens": {
            agent.value: tokens for agent, tokens in plan.remaining_tokens.items()
        },
        "tasks": records,
    }
    action = "계획" if args.dry_run else "원자적 등록"
    lines = [
        f"토큰 작업량 {action}: {len(records)}개",
        _workload_budget_text(plan),
    ]
    lines.extend(
        f"  {record['input_index']}: {record['assigned_agent']} "
        f"{record['estimated_tokens']} tokens {record['title']}"
        for record in records
    )
    return _json_or_text(payload, "\n".join(lines), args.json)


def _split_task(args: argparse.Namespace, service: TaskService) -> str:
    batch = _load_workload_batch(args.plan_path)
    plan = plan_workload(batch)
    result = service.split_task(
        args.task_id,
        [assignment.draft for assignment in plan.assignments],
        dry_run=args.dry_run,
    )
    records = _workload_task_records(plan, list(result.children))
    payload = {
        "split": not args.dry_run,
        "original_task": _task_record(result.original),
        "rewired_dependent_ids": list(result.rewired_dependent_ids),
        "budgets": plan.budgets.model_dump(),
        "assigned_tokens": {
            agent.value: tokens for agent, tokens in plan.assigned_tokens.items()
        },
        "remaining_tokens": {
            agent.value: tokens for agent, tokens in plan.remaining_tokens.items()
        },
        "tasks": records,
    }
    action = "validated" if args.dry_run else "split"
    lines = [
        f"Task #{args.task_id} {action} into {len(records)} child tasks",
        _workload_budget_text(plan),
    ]
    lines.extend(
        f"  {record['input_index']}: {record['assigned_agent']} "
        f"{record['estimated_tokens']} tokens {record['title']}"
        for record in records
    )
    return _json_or_text(payload, "\n".join(lines), args.json)


def _workload_task_records(
    plan: WorkloadPlan,
    created: list[object],
) -> list[dict[str, Any]]:
    records = []
    for index, assignment in enumerate(plan.assignments):
        task = None if not created else created[index]
        records.append(
            {
                "input_index": assignment.input_index,
                "id": None if task is None else task.id,
                "title": assignment.draft.title,
                "requested_agent_policy": assignment.requested_policy.value,
                "assigned_agent": assignment.assigned_agent.value,
                "estimated_tokens": assignment.draft.estimated_tokens,
                "dependency_ids": (
                    list(assignment.draft.dependency_ids)
                    if task is None
                    else list(task.dependency_ids)
                ),
                "depends_on_indices": list(assignment.draft.depends_on_indices),
            }
        )
    return records


def _workload_budget_text(plan: WorkloadPlan) -> str:
    return ", ".join(
        f"{agent.value.title()} {tokens}/{plan.budgets.for_agent(agent)}"
        for agent, tokens in plan.assigned_tokens.items()
    )


def _load_workload_batch(path: Path) -> WorkloadBatch:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CliUsageError(f"작업량 계획 파일을 읽을 수 없습니다: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CliUsageError(
            f"작업량 계획 파일이 올바른 JSON이 아닙니다: line {exc.lineno}"
        ) from exc
    return WorkloadBatch.model_validate(payload)


def _agent_record(view: AgentView) -> dict[str, Any]:
    return {
        "agent": _enum_value(view.agent),
        "status": _enum_value(view.status),
        "available_at": _utc_iso(view.available_at),
        "current_task_id": view.current_task_id,
        "last_error": view.last_error,
    }


def _template_record(template: object) -> dict[str, Any]:
    return {
        "name": template.name,
        "title": template.title,
        "prompt": template.prompt,
        "project_path": template.project_path,
        "created_by": _enum_value(template.created_by),
        "creator_session_id": template.creator_session_id,
        "agent_policy": _enum_value(template.agent_policy),
        "priority": template.priority,
        "estimated_tokens": template.estimated_tokens,
        "approval_mode": _enum_value(template.approval_mode),
        "auto_commit": bool(template.auto_commit),
        "draft_pr": bool(template.draft_pr),
        "max_retries": template.max_retries,
        "retry_delay_minutes": template.retry_delay_minutes,
        "recurrence_interval_days": template.recurrence_interval_days,
        "not_before": _utc_iso(template.not_before),
        "tags": list(template.tags),
        "dependency_ids": list(template.dependency_ids),
        "created_at": _utc_iso(template.created_at),
        "updated_at": _utc_iso(template.updated_at),
    }


def _draft_record(draft: TaskDraft) -> dict[str, Any]:
    return {
        "title": draft.title,
        "prompt": draft.prompt,
        "project_path": str(draft.project_path),
        "created_by": _enum_value(draft.created_by),
        "creator_session_id": draft.creator_session_id,
        "agent_policy": draft.agent_policy.value,
        "priority": draft.priority,
        "estimated_tokens": draft.estimated_tokens,
        "approval_mode": draft.approval_mode.value,
        "auto_commit": bool(draft.auto_commit),
        "draft_pr": bool(draft.draft_pr),
        "max_retries": draft.max_retries,
        "retry_delay_minutes": draft.retry_delay_minutes,
        "recurrence_interval_days": draft.recurrence_interval_days,
        "not_before": _utc_iso(draft.not_before),
        "tags": list(draft.tags),
        "dependency_ids": list(draft.dependency_ids),
    }


def _parse_template_variables(values: list[str]) -> dict[str, str]:
    variables: dict[str, str] = {}
    for value in values:
        if "=" not in value:
            raise CliUsageError("--var는 key=value 형식이어야 합니다.")
        key, variable_value = value.split("=", 1)
        if not key.isidentifier():
            raise CliUsageError(f"올바르지 않은 템플릿 변수명입니다: {key!r}")
        if key in variables:
            raise CliUsageError(f"템플릿 변수가 중복되었습니다: {key}")
        variables[key] = variable_value
    return variables


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


def _parse_local_date(value: str | None) -> date | None:
    if value is None:
        return None
    if len(value) != 10 or value[4] != "-" or value[7] != "-":
        raise CliUsageError("--date는 YYYY-MM-DD 형식이어야 합니다.")
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CliUsageError("--date는 YYYY-MM-DD 형식이어야 합니다.") from exc


def _parse_recurrence_interval(value: str | None) -> int | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized == "daily":
        return 1
    if normalized == "weekly":
        return 7
    if normalized.endswith("d"):
        normalized = normalized[:-1]
    try:
        interval = int(normalized)
    except ValueError as exc:
        raise CliUsageError("--repeat는 daily, weekly 또는 양의 일수 N이어야 합니다.") from exc
    if interval < 1:
        raise CliUsageError("--repeat 일수는 1 이상이어야 합니다.")
    return interval


def _recurrence_text(interval_days: int | None) -> str | None:
    if interval_days is None:
        return None
    if interval_days == 1:
        return "daily"
    if interval_days == 7:
        return "weekly"
    return f"every {interval_days} days"


def _task_record(task: object) -> dict[str, Any]:
    return {
        "id": int(task.id),
        "title": task.title,
        "prompt": task.prompt,
        "project_path": task.project_path,
        "created_by": _enum_value(task.created_by),
        "creator_session_id": task.creator_session_id,
        "agent_policy": _enum_value(task.agent_policy),
        "assigned_agent": _enum_value(task.assigned_agent),
        "fallback_agent": _enum_value(task.fallback_agent),
        "priority": int(task.priority),
        "estimated_tokens": int(task.estimated_tokens),
        "status": _enum_value(task.status),
        "approval_mode": _enum_value(task.approval_mode),
        "auto_commit": bool(task.auto_commit),
        "draft_pr": bool(task.draft_pr),
        "max_retries": int(task.max_retries),
        "retry_count": int(task.retry_count),
        "retry_delay_minutes": int(task.retry_delay_minutes),
        "recurrence": _recurrence_text(task.recurrence_interval_days),
        "recurrence_interval_days": task.recurrence_interval_days,
        "recurrence_source_task_id": task.recurrence_source_task_id,
        "not_before": _utc_iso(task.not_before),
        "reserved_at": _utc_iso(task.reserved_at),
        "started_at": _utc_iso(task.started_at),
        "finished_at": _utc_iso(task.finished_at),
        "created_at": _utc_iso(task.created_at),
        "updated_at": _utc_iso(task.updated_at),
        "last_error": task.last_error,
        "tags": list(task.tags),
        "dependency_ids": list(task.dependency_ids),
        "split_parent_task_id": task.split_parent_task_id,
        "split_child_ids": list(task.split_child_ids),
        "rework_of_task_id": task.rework_of_task_id,
        "rework_task_ids": list(task.rework_task_ids),
        "review_status": (
            "unreviewed" if task.review_verdict is None else task.review_verdict.value
        ),
        "review_score": task.review_score,
        "review_notes": task.review_notes,
        "reviewed_at": _utc_iso(task.reviewed_at),
        "review_count": task.review_count,
        "archived": bool(task.archived),
        "use_worktree": bool(task.use_worktree),
        "worktree_path": task.worktree_path,
        "worktree_branch": task.worktree_branch,
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
        "fallback_to_agent": _enum_value(run.fallback_to_agent),
        "worktree_path": run.worktree_path,
        "worktree_branch": run.worktree_branch,
        "auto_commit_hash": run.auto_commit_hash,
        "auto_commit_result": run.auto_commit_result,
        "draft_pr_url": run.draft_pr_url,
        "draft_pr_result": run.draft_pr_result,
    }


def _event_record(event: object) -> dict[str, Any]:
    return {
        "id": int(event.id),
        "task_id": int(event.task_id),
        "task_created_at": _utc_iso(event.task_created_at),
        "action": _enum_value(event.action),
        "actor": _enum_value(event.actor),
        "actor_session_id": event.actor_session_id,
        "project_path": event.project_path,
        "task_title": event.task_title,
        "details": dict(event.details),
        "created_at": _utc_iso(event.created_at),
    }


def _daily_summary_record(summary: object) -> dict[str, Any]:
    projects = [
        {
            "project_path": project.project_path,
            "task_counts": _daily_task_counts(project),
            "total_runtime_seconds": project.total_runtime_seconds,
            "agent_run_counts": {
                "claude": project.claude_run_count,
                "codex": project.codex_run_count,
                "gemini": project.gemini_run_count,
            },
            "fallback_count": project.fallback_count,
            "worktree_branches": list(project.worktree_branches),
            "failed_tasks": [
                {
                    "id": task.id,
                    "title": task.title,
                    "last_error_type": _enum_value(task.last_error_type),
                }
                for task in project.failed_tasks
            ],
        }
        for project in summary.projects
    ]
    totals = summary.totals
    return {
        "date": summary.local_date.isoformat(),
        "period": {
            "start_utc": _utc_iso(summary.start_utc),
            "end_utc": _utc_iso(summary.end_utc),
        },
        "projects": projects,
        "totals": {
            "project_count": totals.project_count,
            "task_counts": _daily_task_counts(totals),
            "total_runtime_seconds": totals.total_runtime_seconds,
            "agent_run_counts": {
                "claude": totals.claude_run_count,
                "codex": totals.codex_run_count,
                "gemini": totals.gemini_run_count,
            },
            "fallback_count": totals.fallback_count,
            "worktree_branches": list(totals.worktree_branches),
            "failed_task_count": totals.failed_task_count,
            "usage_limit_wait_occurred": {
                "claude": totals.claude_usage_limit_wait_occurred,
                "codex": totals.codex_usage_limit_wait_occurred,
                "gemini": totals.gemini_usage_limit_wait_occurred,
            },
        },
    }


def _daily_task_counts(value: object) -> dict[str, int]:
    return {
        "succeeded": value.succeeded_count,
        "failed": value.failed_count,
        "blocked": value.blocked_count,
        "cancelled": value.cancelled_count,
    }


def _task_table(records: list[dict[str, Any]]) -> str:
    if not records:
        return "작업이 없습니다."
    rows = [
        "ID  STATUS       AGENT   PRIORITY  REPEAT        CREATOR  PROJECT  TITLE"
    ]
    rows.extend(
        f"{record['id']:<3} {record['status']:<12} "
        f"{(record['assigned_agent'] or record['agent_policy']):<7} "
        f"{record['priority']:<8}  {(record['recurrence'] or '-'):<12}  "
        f"{record['created_by']:<7}  "
        f"{record['project_path']}  {record['title']}"
        for record in records
    )
    return "\n".join(rows)


def _review_text(record: dict[str, Any]) -> str:
    status = record["review_status"]
    score = record["review_score"]
    return status if score is None else f"{status}({score})"


def _split_relation_text(record: dict[str, Any]) -> str:
    relations = []
    if record["split_parent_task_id"] is not None:
        relations.append(f"parent:#{record['split_parent_task_id']}")
    if record["split_child_ids"]:
        child_ids = ",".join(f"#{child_id}" for child_id in record["split_child_ids"])
        relations.append(f"children:{child_ids}")
    return ";".join(relations) or "-"


def _task_details(record: dict[str, Any]) -> str:
    lines = [
        f"{key}: {_text_value(value)}"
        for key, value in record.items()
        if key not in {"recent_runs", "recent_events"}
    ]
    lines.append("recent_runs:")
    runs = record["recent_runs"]
    if not runs:
        lines.append("  (없음)")
    else:
        lines.extend(
            "  - "
            f"attempt={run['attempt']} agent={run['agent']} exit_code={run['exit_code']} "
            + (
                f"fallback={run['agent']}->{run['fallback_to_agent']} "
                if run["fallback_to_agent"]
                else ""
            )
            + (
                f"worktree={run['worktree_branch']}@{run['worktree_path']} "
                if run["worktree_path"]
                else ""
            )
            + (
                "auto_commit="
                f"{run['auto_commit_hash'] or run['auto_commit_result']} "
                if run["auto_commit_hash"] or run["auto_commit_result"]
                else ""
            )
            + (
                "draft_pr="
                f"{run['draft_pr_url'] or run['draft_pr_result']} "
                if run["draft_pr_url"] or run["draft_pr_result"]
                else ""
            )
            + f"result_summary={_text_value(run['result_summary'])}"
            for run in runs
        )
    lines.append("recent_events:")
    events = record["recent_events"]
    if not events:
        lines.append("  (없음)")
    else:
        lines.extend(
            f"  - {event['created_at']} {event['actor']} {event['action']} "
            f"{_text_value(event['details'])}"
            for event in events
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
