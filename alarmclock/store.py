"""JSON persistence for alarms, with atomic writes."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Callable, Optional

from .models import Alarm

ENV_VAR = "ALARMCLOCK_FILE"
SCHEMA_VERSION = 1


class StoreError(Exception):
    """The alarm file exists but cannot be used. Never silently overwritten."""


def default_path() -> Path:
    return Path(os.environ.get(ENV_VAR) or Path.home() / ".alarmclock.json")


def _max_id(alarms: list[Alarm]) -> int:
    return max((a.id for a in alarms), default=0)


class AlarmStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else default_path()
        self._next_id = 1

    def load(self) -> list[Alarm]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            alarms = [Alarm.from_dict(a) for a in data["alarms"]]
            self._next_id = max(int(data.get("next_id", 1)), _max_id(alarms) + 1)
            return alarms
        except (ValueError, KeyError, TypeError) as exc:
            raise StoreError(
                f"alarm file {self.path} is unreadable ({exc}); fix or delete it"
            ) from exc

    def save(self, alarms: list[Alarm]) -> None:
        """Write via temp file + rename so a crash can never leave a half-written file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._next_id = max(self._next_id, _max_id(alarms) + 1)
        payload = {
            "version": SCHEMA_VERSION,
            "next_id": self._next_id,
            "alarms": [a.to_dict() for a in sorted(alarms, key=lambda a: a.id)],
        }
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, prefix=".alarmclock-")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
                fh.write("\n")
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def update(self, mutate: Callable[[list[Alarm]], None]) -> list[Alarm]:
        """Reload-modify-write, keeping the window for lost updates small when
        `alarm set` and `alarm start` touch the file from different terminals."""
        alarms = self.load()
        mutate(alarms)
        self.save(alarms)
        return alarms

    def next_id(self, alarms: list[Alarm]) -> int:
        """Numbers are never reused: after `alarm delete 3`, "alarm 3" must not quietly
        become a different alarm (or inherit the old one's snooze in `alarm start`)."""
        return max(self._next_id, _max_id(alarms) + 1)
