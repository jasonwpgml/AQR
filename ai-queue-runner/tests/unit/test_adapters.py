from __future__ import annotations

import subprocess
from datetime import UTC, timedelta
from pathlib import Path

import pytest

from ai_queue_runner.config.defaults import AgentSection
from ai_queue_runner.domain.clock import utcnow
from ai_queue_runner.domain.enums import ErrorType
from ai_queue_runner.domain.models import Task
from ai_queue_runner.infrastructure.adapters.claude import ClaudeAdapter
from ai_queue_runner.infrastructure.adapters.codex import CodexAdapter


def make_task(**overrides) -> Task:
    values = {
        "id": 42,
        "title": "Adapter test",
        "prompt": "Fix it; echo $TOKEN && rm -rf .",
        "project_path": "C:/projects/demo",
        "agent_policy": "any",
    }
    values.update(overrides)
    return Task(**values)


def test_claude_command_matches_inspected_cli_contract(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    task = make_task()

    command = ClaudeAdapter().build_command(task)

    assert command[:7] == [
        "claude",
        "--print",
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        "auto",
    ]
    assert command[7] == "--no-session-persistence"
    assert len(command) == 8
    stdin_text = ClaudeAdapter().build_stdin(task)
    assert "Work only inside: C:/projects/demo" in stdin_text
    assert "Task ID: 42" in stdin_text
    assert task.prompt in stdin_text


def test_codex_command_matches_inspected_cli_contract(monkeypatch, tmp_path):
    monkeypatch.setattr("shutil.which", lambda _name: None)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    task = make_task()

    command = CodexAdapter().build_command(task)

    assert command[:13] == [
        "codex",
        "--ask-for-approval",
        "never",
        "exec",
        "--sandbox",
        "workspace-write",
        "--cd",
        "C:/projects/demo",
        "--skip-git-repo-check",
        "--ephemeral",
        "--color",
        "never",
        "--json",
    ]
    assert len(command) == 14
    assert command[-1] == "-"
    assert task.prompt in CodexAdapter().build_stdin(task)


@pytest.mark.parametrize("adapter_type", [ClaudeAdapter, CodexAdapter])
def test_prompt_metacharacters_remain_inside_one_argument(adapter_type):
    adapter = adapter_type()
    command = adapter.build_command(make_task())

    assert "echo $TOKEN && rm -rf ." in adapter.build_stdin(make_task())
    assert all(part != "&&" for part in command)


@pytest.mark.parametrize("adapter_type", [ClaudeAdapter, CodexAdapter])
def test_limit_patterns_are_config_driven(adapter_type):
    adapter = adapter_type(AgentSection(limit_patterns=["custom quota exhausted"]))

    assert (
        adapter.classify_error("CUSTOM QUOTA EXHAUSTED", 1)
        is ErrorType.USAGE_LIMIT
    )
    assert adapter.classify_error("rate limit", 1) is ErrorType.UNKNOWN


@pytest.mark.parametrize(
    ("adapter", "output"),
    [
        (ClaudeAdapter(), "Not logged in. Run /login"),
        (CodexAdapter(), "401 Unauthorized: missing OPENAI_API_KEY"),
    ],
)
def test_authentication_errors_are_classified(adapter, output):
    assert adapter.classify_error(output, 1) is ErrorType.AUTH


def test_claude_real_session_limit_output_is_classified():
    output = '{"type":"rate_limit_event","error":"rate_limit"}\nSession limit reached'

    assert ClaudeAdapter().classify_error(output, 1) is ErrorType.USAGE_LIMIT


def test_claude_allowed_rate_limit_event_is_not_an_error():
    output = (
        '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed"}}\n'
        '{"type":"result","subtype":"success","is_error":false}'
    )

    assert ClaudeAdapter().classify_error(output, 0) is ErrorType.UNKNOWN


def test_claude_extracts_exact_reset_from_rejected_rate_limit_event():
    reset_at = (utcnow() + timedelta(hours=2)).replace(microsecond=0)
    reset_timestamp = int(reset_at.replace(tzinfo=UTC).timestamp())
    output = (
        '{"type":"rate_limit_event","rate_limit_info":'
        f'{{"status":"rejected","resetsAt":{reset_timestamp}}}}}'
    )

    assert ClaudeAdapter().extract_limit_reset(output) == reset_at


def test_claude_ignores_allowed_or_malformed_reset_events():
    reset_timestamp = int((utcnow() + timedelta(hours=2)).replace(tzinfo=UTC).timestamp())
    allowed = (
        '{"type":"rate_limit_event","rate_limit_info":'
        f'{{"status":"allowed","resetsAt":{reset_timestamp}}}}}'
    )

    assert ClaudeAdapter().extract_limit_reset(allowed) is None
    assert ClaudeAdapter().extract_limit_reset("{malformed json") is None


def test_codex_extracts_relative_reset_with_tolerance():
    earliest = utcnow() + timedelta(minutes=150)

    reset_at = CodexAdapter().extract_limit_reset("Try again in 2 hours 30 minutes.")

    latest = utcnow() + timedelta(minutes=150)
    assert reset_at is not None
    assert earliest <= reset_at <= latest


def test_codex_rejects_garbage_and_past_absolute_reset():
    adapter = CodexAdapter()

    assert adapter.extract_limit_reset("quota message without a reset time") is None
    assert adapter.extract_limit_reset("resets at 2020-01-01T00:00:00Z") is None
    assert adapter.extract_limit_reset("try again in 8 days") is None


def test_claude_summary_extracts_final_result_and_truncates():
    output = "\n".join(
        [
            '{"type":"assistant","message":{"content":[]}}',
            '{"type":"result","result":"earlier"}',
            '{"type":"result","result":"' + ("x" * 600) + '"}',
        ]
    )

    summary = ClaudeAdapter().summarize_output(output)

    assert summary == "x" * 500
    assert ClaudeAdapter().summarize_output("") is None
    assert ClaudeAdapter().summarize_output("not json") is None


def test_codex_summary_extracts_last_agent_message_and_truncates():
    output = "\n".join(
        [
            '{"type":"item.completed","item":{"type":"agent_message","text":"first"}}',
            '{"type":"item.completed","item":{"type":"command_execution"}}',
            '{"type":"agent_message","message":"' + ("y" * 600) + '"}',
        ]
    )

    summary = CodexAdapter().summarize_output(output)

    assert summary == "y" * 500
    assert CodexAdapter().summarize_output("") is None
    assert CodexAdapter().summarize_output("not json") is None


@pytest.mark.parametrize("adapter_type", [ClaudeAdapter, CodexAdapter])
def test_success_text_discussing_limits_is_not_an_error(adapter_type):
    output = '{"type":"assistant","message":"Explain rate limit and authentication failed"}'

    assert adapter_type().classify_error(output, 0) is ErrorType.UNKNOWN


@pytest.mark.parametrize("adapter_type", [ClaudeAdapter, CodexAdapter])
def test_negative_exit_is_classified_as_crash(adapter_type):
    assert adapter_type().classify_error("", -9) is ErrorType.CRASH


def test_installation_check_uses_path_lookup(monkeypatch, tmp_path):
    monkeypatch.setattr(
        "shutil.which",
        lambda executable: str(tmp_path / f"{executable}.exe"),
    )

    claude = ClaudeAdapter()
    codex = CodexAdapter()

    assert claude.is_installed()
    assert codex.is_installed()
    assert claude.build_command(make_task())[0] == str((tmp_path / "claude.exe").resolve())
    assert codex.build_command(make_task())[0] == str((tmp_path / "codex.exe").resolve())


def test_explicit_executable_wins_and_expands_environment(monkeypatch, tmp_path):
    executable = tmp_path / "custom-codex.exe"
    executable.touch()
    monkeypatch.setenv("AQR_TEST_EXECUTABLE", str(executable))
    monkeypatch.setattr(
        "shutil.which",
        lambda _name: pytest.fail("PATH lookup must not override an explicit executable"),
    )

    adapter = CodexAdapter(AgentSection(executable="$AQR_TEST_EXECUTABLE"))

    assert adapter.is_installed()
    assert adapter.build_command(make_task())[0] == str(executable.resolve())


@pytest.mark.parametrize(
    ("adapter_type", "environment", "relative_path"),
    [
        (ClaudeAdapter, "USERPROFILE", Path(".local/bin/claude.exe")),
        (
            CodexAdapter,
            "LOCALAPPDATA",
            Path("Programs/OpenAI/Codex/bin/codex.exe"),
        ),
    ],
)
def test_known_install_location_is_probed(
    adapter_type,
    environment,
    relative_path,
    monkeypatch,
    tmp_path,
):
    executable = tmp_path / relative_path
    executable.parent.mkdir(parents=True)
    executable.touch()
    monkeypatch.setenv(environment, str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda _name: None)

    adapter = adapter_type()

    assert adapter.is_installed()
    assert adapter.build_command(make_task())[0] == str(executable.resolve())


@pytest.mark.parametrize(
    ("adapter_type", "environment"),
    [(ClaudeAdapter, "USERPROFILE"), (CodexAdapter, "LOCALAPPDATA")],
)
def test_unresolved_executable_keeps_bare_name(
    adapter_type,
    environment,
    monkeypatch,
    tmp_path,
):
    monkeypatch.setenv(environment, str(tmp_path))
    monkeypatch.setattr("shutil.which", lambda _name: None)

    adapter = adapter_type()

    assert adapter.is_installed() is False
    assert adapter.build_command(make_task())[0] == adapter.name.value


@pytest.mark.parametrize("adapter_type", [ClaudeAdapter, CodexAdapter])
def test_disabled_adapter_is_not_available(adapter_type, monkeypatch):
    monkeypatch.setattr("shutil.which", lambda _executable: "C:/tools/agent.exe")

    assert adapter_type(AgentSection(enabled=False)).is_installed() is False


def test_version_check_is_bounded_and_shell_free(monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, stdout="tool 1.2.3\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)

    adapter = ClaudeAdapter()

    assert adapter.get_version() == "tool 1.2.3"
    assert calls == [
        (
            [adapter.build_command(make_task())[0], "--version"],
            {
                "capture_output": True,
                "check": False,
                "shell": False,
                "text": True,
                "timeout": 5,
            },
        )
    ]


def test_version_check_handles_missing_executable(monkeypatch):
    def fail(*_args, **_kwargs):
        raise FileNotFoundError

    monkeypatch.setattr(subprocess, "run", fail)

    assert CodexAdapter().get_version() is None


def test_version_check_rejects_nonzero_exit(monkeypatch):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda command, **_kwargs: subprocess.CompletedProcess(
            command,
            1,
            stdout="not a usable version",
            stderr="failed",
        ),
    )

    assert ClaudeAdapter().get_version() is None


def test_configured_executable_is_used():
    adapter = CodexAdapter(AgentSection(executable="C:/tools/codex-custom.exe"))

    assert adapter.build_command(make_task())[0] == "C:/tools/codex-custom.exe"


def test_prompt_templates_are_packaged():
    package_root = Path(__file__).parents[2] / "src" / "ai_queue_runner" / "prompts"

    assert (package_root / "claude_wrapper.txt").is_file()
    assert (package_root / "codex_wrapper.txt").is_file()
