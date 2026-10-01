"""The `alarm run` loop: poll the store, tick the scheduler, ring, react.

Clock, sleep, output and the ringer are injected so the whole loop runs in
tests against a fake clock without real waiting or real input.
"""

from __future__ import annotations

import sys
import time
from datetime import datetime, timedelta
from typing import Callable, Optional, TextIO

from .fmt import format_delta, format_when
from .models import Alarm
from .ringer import Action
from .scheduler import EventKind, Scheduler
from .store import AlarmStore, StoreError


class Runner:
    def __init__(
        self,
        store: AlarmStore,
        ringer,
        *,
        snooze: timedelta = timedelta(minutes=5),
        grace: timedelta = timedelta(minutes=5),
        max_unanswered: int = 3,
        clock: Callable[[], datetime] = datetime.now,
        sleep: Callable[[float], None] = time.sleep,
        out: Optional[TextIO] = None,
    ):
        self.store = store
        self.ringer = ringer
        self.snooze = snooze
        self.max_unanswered = max_unanswered
        self.clock = clock
        self.sleep = sleep
        self.out = out or sys.stdout
        self.scheduler = Scheduler(clock(), grace)
        self.alarms: list[Alarm] = []
        self._unanswered: dict[int, int] = {}
        self._store_warned = False

    def say(self, msg: str) -> None:
        self.out.write(msg + "\n")
        self.out.flush()

    def run(self, keep_running: Callable[[], bool] = lambda: True) -> None:
        self._reload()
        enabled = sum(a.enabled for a in self.alarms)
        self.say(f"Alarm clock running with {enabled} enabled alarm(s). Ctrl+C to stop.")
        self._announce_next()
        while keep_running():
            self.step()
            self.sleep(1.0)

    def step(self) -> None:
        now = self.clock()
        self._reload()  # picks up `alarm add/remove` from other terminals live
        events = self.scheduler.tick(self.alarms, now)
        for event in events:
            if event.kind is EventKind.MISSED:
                late = format_delta(now - event.scheduled)
                self.say(
                    f"Missed alarm #{event.alarm.id} {event.alarm.label!r} "
                    f"(due {event.scheduled:%a %H:%M}, {late} ago; beyond grace period)."
                )
                self._finish(event.alarm)
            else:
                self._ring(event.alarm, event.snoozed)
        if events:
            self._reload()
            self._announce_next()

    def _ring(self, alarm: Alarm, snoozed: bool) -> None:
        action = self.ringer.ring(alarm, snoozed)
        if action is Action.TIMEOUT:
            count = self._unanswered.get(alarm.id, 0) + 1
            self._unanswered[alarm.id] = count
            if count <= self.max_unanswered:
                self.say(f"No answer, auto-snoozing ({count}/{self.max_unanswered}).")
                action = Action.SNOOZE
            else:
                self.say(f"No answer after {self.max_unanswered} snoozes, giving up.")
                action = Action.DISMISS
        elif action is Action.SNOOZE:
            self._unanswered.pop(alarm.id, None)

        if action is Action.SNOOZE:
            until = self.clock() + self.snooze
            self.scheduler.snooze(alarm.id, until)
            self.say(f"Snoozed until {until:%H:%M:%S}.")
        else:
            self.say("Dismissed.")
            self._finish(alarm)

    def _finish(self, alarm: Alarm) -> None:
        """An occurrence is done. One-time alarms switch off; recurring ones stay."""
        self._unanswered.pop(alarm.id, None)
        if alarm.is_recurring:
            return

        def disable(alarms: list[Alarm]) -> None:
            for a in alarms:
                if a.id == alarm.id:
                    a.enabled = False

        try:
            self.store.update(disable)
        except StoreError as exc:
            self.say(f"warning: could not disable alarm #{alarm.id}: {exc}")

    def _reload(self) -> None:
        try:
            self.alarms = self.store.load()
            self._store_warned = False
        except StoreError as exc:
            # Keep ringing with the last good copy rather than crash mid-night.
            if not self._store_warned:
                self.say(f"warning: {exc}; using last known alarms")
                self._store_warned = True

    def _announce_next(self) -> None:
        now = self.clock()
        upcoming = []
        for a in self.alarms:
            if not a.enabled:
                continue
            for when in (a.next_trigger(now), self.scheduler.snoozed_until(a.id)):
                if when is not None and when > now:
                    upcoming.append((when, a))
        if not upcoming:
            self.say("No upcoming alarms. Add one from another terminal: alarm add 07:30")
            return
        when, alarm = min(upcoming, key=lambda x: x[0])
        label = f" {alarm.label!r}" if alarm.label else ""
        self.say(f"Next: #{alarm.id}{label} {format_when(when, now)}")
