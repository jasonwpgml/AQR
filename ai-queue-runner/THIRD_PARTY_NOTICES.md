# Third-party notices

AI Queue Runner depends on third-party software. This summary is operational guidance, not legal advice.

| Component | Use | License / notice |
|---|---|---|
| PySide6 / Qt 6 | Desktop UI | LGPLv3, GPL, or commercial terms depending on the modules and distribution model. See <https://doc.qt.io/qt-6/licensing.html>. |
| APScheduler | Queue polling clock | MIT License. See <https://github.com/agronholm/apscheduler>. |
| SQLAlchemy | Persistence | MIT License. See <https://www.sqlalchemy.org/>. |
| Pydantic | Configuration/input validation | MIT License. See <https://github.com/pydantic/pydantic>. |
| psutil | Process recovery/termination | BSD-3-Clause. See <https://github.com/giampaolo/psutil>. |
| PyInstaller | Windows build tool | GPLv2-or-later with a special exception for bundled applications. See <https://pyinstaller.org/en/stable/license.html>. |

The default Windows artifact is `onedir`, which keeps Qt libraries separate and replaceable. Before redistributing the application, review Qt's LGPL obligations, retain applicable license texts/notices, and confirm that only suitably licensed Qt modules are included.
