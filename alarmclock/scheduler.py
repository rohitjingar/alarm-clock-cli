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
        self._first_tick = True

    def tick(self, alarms: list[Alarm], now: datetime) -> list[Event]:
        if now < self.last_tick:
            # Wall clock moved backwards (NTP/manual change): restart the window
            # rather than re-firing occurrences we already handled.
            self.last_tick = now
            return []

        window_start, self.last_tick = self.last_tick, now
        catch_up, self._first_tick = self._first_tick, False
        live_ids = {a.id for a in alarms if a.enabled}
        # Drop snoozes for alarms that were removed or disabled meanwhile.
        self._snoozed = {i: t for i, t in self._snoozed.items() if i in live_ids}

        events = []
        for alarm in alarms:
            if not alarm.enabled:
                continue
            due = []
            since = window_start
            if catch_up and not alarm.is_recurring:
                # A one-time alarm that is still enabled was never dismissed, so if it
                # fell due while `alarm start` wasn't running it never rang. Ring it (within
                # grace) or report it missed -- never let it expire silently.
                # Recurring alarms aren't caught up: we can't tell whether that
                # occurrence was already dismissed before a restart.
                since = datetime.min
            scheduled = self._latest_occurrence(alarm, since, now)
            if scheduled is not None:
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

    @staticmethod
    def _latest_occurrence(alarm: Alarm, since: datetime, now: datetime):
        """Most recent occurrence in (since, now], or None.

        After a long sleep a daily alarm has several; only the latest matters.
        Mon 08:00 -> Wed 07:02 must ring Wednesday's 07:00 (2 min late), not
        report Tuesday's as missed and stay silent.
        """
        latest = None
        when = alarm.next_trigger(since)
        while when is not None and when <= now:
            latest = when
            when = alarm.next_trigger(when)
        return latest

    def snooze(self, alarm_id: int, until: datetime) -> None:
        self._snoozed[alarm_id] = until

    def snoozed_until(self, alarm_id: int):
        return self._snoozed.get(alarm_id)
