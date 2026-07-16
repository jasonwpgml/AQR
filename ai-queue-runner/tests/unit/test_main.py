from ai_queue_runner.main import main


def test_main_composes_desktop_app(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(
        "ai_queue_runner.main.run_desktop_app",
        lambda config, **kwargs: calls.append((config, kwargs)) or 0,
    )

    assert main(["--no-tray"]) == 0
    assert len(calls) == 1
    assert calls[0][1] == {"smoke_test": False, "create_tray": False}


def test_main_prints_version_without_loading_config(capsys):
    assert main(["--version"]) == 0
    assert "AI Queue Runner 0.1.0" in capsys.readouterr().out


def test_main_reports_config_error(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "missing.toml")]) == 2
    assert "config error" in capsys.readouterr().err


def test_main_loads_app_data_config_when_no_cwd_config(tmp_path, monkeypatch):
    """Settings saved by the GUI dialog to the app-data config apply on next start."""
    monkeypatch.chdir(tmp_path)  # no config.toml in cwd
    app_data = tmp_path / "appdata"
    app_data.mkdir()
    (app_data / "config.toml").write_text(
        '[app]\nallowed_project_roots = ["C:/projects"]\n', encoding="utf-8"
    )
    monkeypatch.setattr("ai_queue_runner.main.default_app_data_dir", lambda: app_data)
    seen = []
    monkeypatch.setattr(
        "ai_queue_runner.main.run_desktop_app",
        lambda config, **kwargs: seen.append(config) or 0,
    )

    assert main(["--no-tray"]) == 0
    assert [root.as_posix() for root in seen[0].app.allowed_project_roots] == ["C:/projects"]


def test_main_exports_effective_config_without_starting_ui(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "ai_queue_runner.main.run_desktop_app",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("UI started")),
    )
    destination = tmp_path / "exports" / "config.toml"

    assert main(["--export-config", str(destination)]) == 0
    assert destination.is_file()
    assert "configuration exported" in capsys.readouterr().out


def test_main_registers_development_startup_command(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    calls = []
    monkeypatch.setattr(
        "ai_queue_runner.main.set_startup_enabled",
        lambda *args, **kwargs: calls.append((args, kwargs)),
    )

    assert main(["--startup", "enable"]) == 0
    assert calls[0][0][0] is True
    assert calls[0][1] == {"module": "ai_queue_runner"}
    assert calls[0][0][2].is_file()
    assert "Windows startup enabled" in capsys.readouterr().out
