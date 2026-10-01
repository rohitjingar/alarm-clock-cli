import unittest
from datetime import datetime, timedelta

from alarmclock.models import Alarm
from alarmclock.scheduler import EventKind, Scheduler

T0 = datetime(2026, 10, 1, 6, 59, 0)  # Thursday


def daily(id_=1, at="07:00:00", **kw):
    return Alarm(id=id_, time=at, days=list(range(7)), **kw)


def ticks(sched, alarms, start, seconds):
    """Tick once per second for `seconds`, collecting every event."""
    events = []
    for s in range(1, seconds + 1):
        events += sched.tick(alarms, start + timedelta(seconds=s))
    return events


class SchedulerTest(unittest.TestCase):
    def test_fires_exactly_once_per_occurrence(self):
        # The naive "is it 07:00 now?" check would fire 60 times in that minute.
        sched = Scheduler(T0)
        events = ticks(sched, [daily()], T0, 180)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0].kind, EventKind.RING)
        self.assertEqual(events[0].scheduled, datetime(2026, 10, 1, 7, 0))

    def test_catches_up_after_a_stalled_loop(self):
        sched = Scheduler(T0)
        events = sched.tick([daily()], T0 + timedelta(minutes=3))  # one late tick
        self.assertEqual([e.kind for e in events], [EventKind.RING])

    def test_beyond_grace_is_reported_missed_not_rung(self):
        sched = Scheduler(T0, grace=timedelta(minutes=5))
        events = sched.tick([daily()], T0 + timedelta(hours=2))  # laptop slept
        self.assertEqual([e.kind for e in events], [EventKind.MISSED])

    def test_alarm_already_past_at_startup_does_not_fire(self):
        start = datetime(2026, 10, 1, 7, 0, 30)
        sched = Scheduler(start)
        self.assertEqual(ticks(sched, [daily()], start, 60), [])

    def test_clock_going_backwards_does_not_refire(self):
        sched = Scheduler(T0)
        self.assertEqual(len(ticks(sched, [daily()], T0, 120)), 1)  # rings at 07:00
        back = T0 + timedelta(seconds=30)  # clock jumps back to 06:59:30
        self.assertEqual(sched.tick([daily()], back), [])
        # ...and forward again across 07:00 -- it is a genuine re-crossing, so it
        # rings again; but crucially nothing fired *during* the backwards jump.
        self.assertEqual(len(ticks(sched, [daily()], back, 60)), 1)

    def test_disabled_alarm_never_fires(self):
        sched = Scheduler(T0)
        self.assertEqual(ticks(sched, [daily(enabled=False)], T0, 120), [])

    def test_snooze_fires_again(self):
        sched = Scheduler(T0)
        alarms = [Alarm(id=1, time="07:00:00", date="2026-10-01")]  # one-time
        first = ticks(sched, alarms, T0, 60)
        self.assertEqual(len(first), 1)
        now = T0 + timedelta(seconds=60)
        sched.snooze(1, now + timedelta(minutes=5))
        later = ticks(sched, alarms, now, 6 * 60)
        self.assertEqual(len(later), 1)
        self.assertTrue(later[0].snoozed)

    def test_snooze_dropped_when_alarm_disabled(self):
        sched = Scheduler(T0)
        sched.snooze(1, T0 + timedelta(seconds=30))
        self.assertEqual(ticks(sched, [daily(enabled=False)], T0, 60), [])
        self.assertIsNone(sched.snoozed_until(1))

    def test_multiple_alarms_ordered_by_time(self):
        sched = Scheduler(T0)
        alarms = [daily(1, "07:00:30"), daily(2, "07:00:10")]
        events = sched.tick(alarms, T0 + timedelta(minutes=2))
        self.assertEqual([e.alarm.id for e in events], [2, 1])


if __name__ == "__main__":
    unittest.main()
