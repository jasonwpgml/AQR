"""Per-user Windows startup registration."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

_RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
_VALUE_NAME = "AIQueueRunner"


def set_startup_enabled(
    enabled: bool,
    executable: Path | str,
    config_path: Path | str,
    *,
    module: str | None = None,
) -> None:
    if sys.platform != "win32":
        raise OSError("startup registration is only supported on Windows")
    import winreg

    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, _RUN_KEY, access=winreg.KEY_SET_VALUE) as key:
        if enabled:
            winreg.SetValueEx(
                key,
                _VALUE_NAME,
                0,
                winreg.REG_SZ,
                build_startup_command(executable, config_path, module=module),
            )
        else:
            try:
                winreg.DeleteValue(key, _VALUE_NAME)
            except FileNotFoundError:
                pass


def build_startup_command(
    executable: Path | str,
    config_path: Path | str,
    *,
    module: str | None = None,
) -> str:
    executable_path = Path(executable).resolve()
    resolved_config = Path(config_path).resolve()
    arguments = [str(executable_path)]
    if module is not None:
        arguments.extend(("-m", module))
    arguments.extend(("--config", str(resolved_config)))
    return subprocess.list2cmdline(arguments)
