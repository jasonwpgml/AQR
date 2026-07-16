from ai_queue_runner.main import main


def test_main_runs_with_defaults(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert main([]) == 0
    assert "configuration OK" in capsys.readouterr().out


def test_main_reports_config_error(tmp_path, capsys):
    assert main(["--config", str(tmp_path / "missing.toml")]) == 2
    assert "config error" in capsys.readouterr().err
