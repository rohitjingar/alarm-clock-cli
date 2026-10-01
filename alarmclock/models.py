"""Alarm model and the single source of truth for "when does this ring next?"."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta
from typing import Optional

from .fmt import format_clock

DAY_NAMES = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
WEEKDAYS = [0, 1, 2, 3, 4]
WEEKENDS = [5, 6]
DAILY = list(range(7))


@dataclass
class AlarmDraft:
    """The fields a person sets, phone-style, before it becomes a saved Alarm."""

    clock: time
    days: list[int]  # empty = only once
    label: str = ""


@dataclass
class Alarm:
    """An alarm at a local wall-clock time.

    ``days`` empty  -> one-time alarm on ``date``.
    ``days`` set    -> recurring on those weekdays (0 = Monday).
    """

    id: int
    time: str  # "HH:MM:SS", local wall-clock time
    label: str = ""
    days: list[int] = field(default_factory=list)
    date: Optional[str] = None  # ISO date, one-time alarms only
    enabled: bool = True

    @property
    def is_recurring(self) -> bool:
        return bool(self.days)

    @property
    def clock_time(self) -> time:
        return time.fromisoformat(self.time)

    def next_trigger(self, after: datetime) -> Optional[datetime]:
        """First ring time strictly after ``after``; None if it never rings again.

        Ignores ``enabled`` on purpose: callers decide what disabled means.
        """
        t = self.clock_time
        if not self.is_recurring:
            if self.date is None:
                return None
            candidate = datetime.combine(date.fromisoformat(self.date), t)
            return candidate if candidate > after else None

        # A recurring alarm always rings within the next 8 calendar days
        # (today may already be past the time; same weekday next week is +7).
        for offset in range(8):
            day = after.date() + timedelta(days=offset)
            if day.weekday() in self.days:
                candidate = datetime.combine(day, t)
                if candidate > after:
                    return candidate
        return None  # unreachable for non-empty days

    def describe_repeat(self) -> str:
        days = sorted(set(self.days))
        if not days:
            return "once"
        if days == DAILY:
            return "every day"
        if days == WEEKDAYS:
            return "every weekday"
        if days == WEEKENDS:
            return "every weekend"
        return "every " + ", ".join(DAY_NAMES[d] for d in days)

    def display_time(self) -> str:
        return format_clock(self.clock_time)

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "Alarm":
        """Build and validate. Raises ValueError/TypeError/KeyError on bad data, so a
        hand-edited file fails when loaded instead of crashing `alarm start` later."""
        alarm = cls(
            id=int(data["id"]),
            time=str(data["time"]),
            label=str(data.get("label", "")),
            days=[int(d) for d in data.get("days", [])],
            date=data.get("date"),
            enabled=bool(data.get("enabled", True)),
        )
        alarm.clock_time  # noqa: B018 -- raises ValueError if the time is invalid
        if any(not 0 <= d <= 6 for d in alarm.days):
            raise ValueError(f"alarm {alarm.id}: days must be 0-6")
        if not alarm.days:
            if alarm.date is None:
                raise ValueError(f"alarm {alarm.id}: a one-time alarm needs a date")
            date.fromisoformat(alarm.date)
        return alarm
