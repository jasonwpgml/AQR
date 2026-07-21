"""Time helpers.

AIDEV-NOTE: all datetimes in the DB are naive UTC (SQLite has no timezone type).
Convert to local time only at the UI boundary. Always use utcnow() from here,
never datetime.now(), so scheduler tests can monkeypatch a single function.
"""

from __future__ import annotations

from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)
