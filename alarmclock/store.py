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


class AlarmStore:
    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path else default_path()

    def load(self) -> list[Alarm]:
        if not self.path.exists():
            return []
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
            return [Alarm.from_dict(a) for a in data["alarms"]]
        except (ValueError, KeyError, TypeError) as exc:
            raise StoreError(
                f"alarm file {self.path} is unreadable ({exc}); fix or delete it"
            ) from exc

    def save(self, alarms: list[Alarm]) -> None:
        """Write via temp file + rename so a crash can never leave a half-written file."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "version": SCHEMA_VERSION,
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
        `alarm add` and `alarm run` touch the file from different terminals."""
        alarms = self.load()
        mutate(alarms)
        self.save(alarms)
        return alarms

    @staticmethod
    def next_id(alarms: list[Alarm]) -> int:
        return max((a.id for a in alarms), default=0) + 1
