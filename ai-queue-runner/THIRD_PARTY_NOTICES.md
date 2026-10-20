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

## How this fits with AQR's Apache-2.0 license

AQR's own source code is licensed under Apache-2.0 (see the root [`LICENSE`](../LICENSE) and [`NOTICE`](../NOTICE)). Apache-2.0 is compatible with LGPLv3. AQR imports PySide6 as an unmodified, separately installed Python package (dynamic linking), so the LGPL applies to PySide6/Qt themselves and does not change the license of AQR's code. If you modify PySide6/Qt or ship it inside a binary build, follow the LGPL obligations above, such as keeping the Qt libraries replaceable and providing their license texts.
