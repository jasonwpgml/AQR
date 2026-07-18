"""Shared text formatting for daily project activity summaries."""

from __future__ import annotations


def format_daily_summary(summary: object) -> str:
    totals = summary.totals
    lines = [
        f"일일 작업 요약: {summary.local_date.isoformat()} (로컬)",
        _summary_counts_text("전체", totals),
        "사용량 제한 대기 발생: "
        f"claude={_yes_no(totals.claude_usage_limit_wait_occurred)}, "
        f"codex={_yes_no(totals.codex_usage_limit_wait_occurred)}",
    ]
    if not summary.projects:
        lines.append("프로젝트 활동이 없습니다.")
        return "\n".join(lines)
    for project in summary.projects:
        lines.extend(("", project.project_path, _summary_counts_text("  결과", project)))
        branches = ", ".join(project.worktree_branches) or "-"
        lines.append(f"  worktree 브랜치: {branches}")
        if project.failed_tasks:
            lines.append("  실패 작업:")
            lines.extend(
                f"    #{task.id} {task.title} "
                f"(오류: {_enum_text(task.last_error_type) or '-'})"
                for task in project.failed_tasks
            )
    return "\n".join(lines)


def _summary_counts_text(label: str, value: object) -> str:
    return (
        f"{label}: 완료={value.succeeded_count} 실패={value.failed_count} "
        f"차단={value.blocked_count} 취소={value.cancelled_count} · "
        f"실행 시간={_duration_text(value.total_runtime_seconds)} · "
        f"실행 claude={value.claude_run_count} codex={value.codex_run_count} · "
        f"fallback={value.fallback_count}"
    )


def _duration_text(seconds: float) -> str:
    rounded = max(0, round(seconds))
    hours, remainder = divmod(rounded, 3600)
    minutes, seconds = divmod(remainder, 60)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d}"


def _yes_no(value: bool) -> str:
    return "yes" if value else "no"


def _enum_text(value: object) -> str | None:
    if value is None:
        return None
    return str(getattr(value, "value", value))
