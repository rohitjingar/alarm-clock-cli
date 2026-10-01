"""Decides which alarms are due. Pure logic: the caller supplies the time.

Instead of asking "is it 07:30 right now?" (which fires 60 times in that
minute, or never if the loop stalls), each tick asks "did an occurrence fall
in the window (last_tick, now]?". Every occurrence is therefore seen exactly
once, and a stalled loop (laptop asleep, slow ring prompt) catches up.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import Enum

from .models import Alarm


class EventKind(Enum):
    RING = "ring"
    MISSED = "missed"  # due, but later than the grace period allows


@dataclass
class Event:
    kind: EventKind
    alarm: Alarm
    scheduled: datetime  # when it should have rung
    snoozed: bool = False


class Scheduler:
    def __init__(self, start: datetime, grace: timedelta = timedelta(minutes=5)):
        self.last_tick = start
        self.grace = grace
        self._snoozed: dict[int, datetime] = {}

    def tick(self, alarms: list[Alarm], now: datetime) -> list[Event]:
        if now < self.last_tick:
            # Wall clock moved backwards (NTP/manual change): restart the window
            # rather than re-firing occurrences we already handled.
            self.last_tick = now
            return []

        window_start, self.last_tick = self.last_tick, now
        live_ids = {a.id for a in alarms if a.enabled}
        # Drop snoozes for alarms that were removed or disabled meanwhile.
        self._snoozed = {i: t for i, t in self._snoozed.items() if i in live_ids}

        events = []
        for alarm in alarms:
            if not alarm.enabled:
                continue
            due = []
            scheduled = alarm.next_trigger(window_start)
            if scheduled is not None and scheduled <= now:
                due.append((scheduled, False))
            snooze_until = self._snoozed.get(alarm.id)
            if snooze_until is not None and window_start < snooze_until <= now:
                due.append((snooze_until, True))
            if not due:
                continue
            # At most one event per alarm per tick; the most recent one wins.
            when, from_snooze = max(due)
            self._snoozed.pop(alarm.id, None)
            kind = EventKind.MISSED if now - when > self.grace else EventKind.RING
            events.append(Event(kind, alarm, when, snoozed=from_snooze))
        return sorted(events, key=lambda e: e.scheduled)

    def snooze(self, alarm_id: int, until: datetime) -> None:
        self._snoozed[alarm_id] = until

    def snoozed_until(self, alarm_id: int):
        return self._snoozed.get(alarm_id)
