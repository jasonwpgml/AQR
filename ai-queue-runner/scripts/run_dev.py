"""Run the app from source without installing the package."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from ai_queue_runner.main import main  # noqa: E402

raise SystemExit(main())
