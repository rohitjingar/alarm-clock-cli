import io
import json
import os
import tempfile
import threading
import time
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from alarmclock.models import Alarm
from alarmclock.ringer import Action, TerminalRinger
from alarmclock.runner import Runner
from alarmclock.store import AlarmStore, StoreError

NOW = datetime(2026, 10, 1, 12, 0, 0)  # Thursday


class TempStoreMixin:
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "alarms.json"
        self.store = AlarmStore(self.path)

    def tearDown(self):
        self._tmp.cleanup()


class StoreTest(TempStoreMixin, unittest.TestCase):
    def test_missing_file_is_empty(self):
        self.assertEqual(self.store.load(), [])

    def test_roundtrip(self):
        alarms = [Alarm(2, "07:00:00", "b", [0]), Alarm(1, "08:00:00", "a", date="2026-10-02")]
        self.store.save(alarms)
        self.assertEqual([a.id for a in self.store.load()], [1, 2])

    def test_corrupt_file_raises_and_is_not_overwritten(self):
        self.path.write_text("{not json")
        with self.assertRaises(StoreError):
            self.store.load()
        self.assertEqual(self.path.read_text(), "{not json")

    def test_atomic_write_leaves_no_temp_files(self):
        self.store.save([Alarm(1, "07:00:00", days=[0])])
        self.assertEqual(os.listdir(self._tmp.name), ["alarms.json"])

    def test_next_id(self):
        self.assertEqual(self.store.next_id([]), 1)
        self.assertEqual(self.store.next_id([Alarm(4, "07:00:00"), Alarm(2, "07:00:00")]), 5)

    def test_ids_are_never_reused_after_delete(self):
        # Regression (senior review): deleting the newest alarm used to hand its
        # number to the next new alarm.
        self.store.save([Alarm(1, "07:00:00", days=[0]), Alarm(2, "08:00:00", days=[0])])
        self.store.update(lambda alarms: alarms.pop())  # delete #2
        fresh = AlarmStore(self.path)  # a later, separate `alarm set`
        self.assertEqual(fresh.next_id(fresh.load()), 3)

    def test_invalid_fields_are_rejected_at_load(self):
        # Regression (senior review): a bad time passed load() and crashed `alarm start`.
        bad_alarms = [
            {"id": 1, "time": "25:00:00", "days": [0]},
            {"id": 1, "time": "07:00:00", "days": [9]},
            {"id": 1, "time": "07:00:00", "days": []},
            {"id": 1, "time": "07:00:00", "date": "2026-13-01"},
        ]
        for bad in bad_alarms:
            with self.subTest(bad=bad):
                self.path.write_text(json.dumps({"version": 1, "alarms": [bad]}))
                with self.assertRaises(StoreError):
                    self.store.load()


class FakeClock:
    def __init__(self, start):
        self.now = start

    def __call__(self):
        return self.now

    def sleep(self, seconds):
        self.now += timedelta(seconds=seconds)


class ScriptedRinger:
    """Answers each ring with the next scripted action, recording what rang."""

    def __init__(self, clock, actions, answer_after=timedelta(seconds=3)):
        self.clock, self.actions, self.answer_after = clock, list(actions), answer_after
        self.rang = []

    def ring(self, alarm, snoozed=False):
        self.rang.append((alarm.id, self.clock(), snoozed))
        self.clock.now += self.answer_after  # the user takes a moment to respond
        return self.actions.pop(0)


class RunnerTest(TempStoreMixin, unittest.TestCase):
    def make_runner(self, actions, start=datetime(2026, 10, 1, 6, 59, 50), **kw):
        clock = FakeClock(start)
        ringer = ScriptedRinger(clock, actions)
        runner = Runner(self.store, ringer, clock=clock, sleep=clock.sleep,
                        out=io.StringIO(), snooze=timedelta(minutes=5), **kw)
        return runner, ringer, clock

    def run_for(self, runner, seconds):
        remaining = [seconds]

        def keep_running():
            remaining[0] -= 1
            return remaining[0] >= 0

        runner.run(keep_running)

    def test_one_time_alarm_snooze_then_dismiss_then_disabled(self):
        self.store.save([Alarm(1, "07:00:00", "Wake", date="2026-10-01")])
        runner, ringer, _ = self.make_runner([Action.SNOOZE, Action.DISMISS])
        self.run_for(runner, 15 * 60)
        self.assertEqual(len(ringer.rang), 2)
        self.assertEqual(ringer.rang[0][1], datetime(2026, 10, 1, 7, 0, 0))
        self.assertTrue(ringer.rang[1][2], "second ring should be the snooze")
        # Snoozed 5 min from when the user pressed snooze (07:00:03).
        self.assertEqual(ringer.rang[1][1], datetime(2026, 10, 1, 7, 5, 3))
        self.assertFalse(self.store.load()[0].enabled)

    def test_recurring_alarm_stays_enabled_after_dismiss(self):
        self.store.save([Alarm(1, "07:00:00", days=list(range(7)))])
        runner, ringer, _ = self.make_runner([Action.DISMISS])
        self.run_for(runner, 120)
        self.assertEqual(len(ringer.rang), 1)
        self.assertTrue(self.store.load()[0].enabled)

    def test_unanswered_auto_snoozes_then_gives_up(self):
        self.store.save([Alarm(1, "07:00:00", date="2026-10-01")])
        runner, ringer, _ = self.make_runner([Action.TIMEOUT] * 3, max_unanswered=2)
        self.run_for(runner, 30 * 60)
        self.assertEqual(len(ringer.rang), 3)  # original + 2 auto-snoozes
        self.assertFalse(self.store.load()[0].enabled)
        self.assertIn("No answer after 2 tries", runner.out.getvalue())

    def test_alarm_that_expired_while_not_running_is_reported_and_disabled(self):
        self.store.save([Alarm(1, "06:00:00", "Early", date="2026-10-01")])
        runner, ringer, _ = self.make_runner([])
        self.run_for(runner, 3)
        self.assertEqual(ringer.rang, [])
        self.assertIn("Missed alarm #1", runner.out.getvalue())
        self.assertFalse(self.store.load()[0].enabled)

    def test_picks_up_alarm_added_while_running(self):
        runner, ringer, clock = self.make_runner([Action.DISMISS])
        self.run_for(runner, 5)
        self.store.save([Alarm(1, "07:00:30", date="2026-10-01")])  # `alarm add` elsewhere
        self.run_for(runner, 60)
        self.assertEqual(len(ringer.rang), 1)

    def test_corrupt_store_mid_run_keeps_last_good_alarms(self):
        self.store.save([Alarm(1, "07:00:00", days=list(range(7)))])
        runner, ringer, _ = self.make_runner([Action.DISMISS])
        self.run_for(runner, 2)
        self.path.write_text("garbage")
        self.run_for(runner, 60)
        self.assertEqual(len(ringer.rang), 1)
        self.assertIn("using last known alarms", runner.out.getvalue())


class CountingSound:
    """Stands in for real audio: counts plays, and stops when told to."""

    name = "test sound"

    def __init__(self):
        self.plays = 0

    def play(self, stop):
        self.plays += 1
        stop.wait(0.02)


class TerminalRingerTest(unittest.TestCase):
    """Uses a real pipe so the select()-based prompt is exercised for real."""

    def setUp(self):
        self.sound = CountingSound()

    def ring_with_input(self, text, timeout=2.0, typed_before=""):
        """Ring, with `typed_before` already queued and `text` typed once it rings."""
        r, w = os.pipe()
        with os.fdopen(r) as inp, os.fdopen(w, "w") as wr:
            wr.write(typed_before)
            wr.flush()

            def user_types():
                time.sleep(0.1)  # a human answers after the alarm starts ringing
                wr.write(text)
                wr.flush()
                if text == "":
                    wr.close()

            threading.Thread(target=user_types, daemon=True).start()
            out = io.StringIO()
            ringer = TerminalRinger(5, timeout=timeout, sound=self.sound, out=out, inp=inp)
            return ringer.ring(Alarm(1, "07:00:00", "Wake")), out.getvalue()

    @unittest.skipIf(os.name == "nt", "select() on pipes is POSIX-only")
    def test_answers(self):
        self.assertEqual(self.ring_with_input("d\n")[0], Action.DISMISS)
        self.assertEqual(self.ring_with_input("\n")[0], Action.SNOOZE)
        action, out = self.ring_with_input("huh\ns\n")
        self.assertEqual(action, Action.SNOOZE)
        self.assertIn("Press ENTER to snooze, or type stop", out)
        self.assertIn("Alarm #1", out)
        self.assertIn("WAKE", out)
        self.assertEqual(self.ring_with_input("stop\n")[0], Action.DISMISS)
        self.assertGreater(self.sound.plays, 0, "the alarm sound should play while ringing")

    @unittest.skipIf(os.name == "nt", "select() on pipes is POSIX-only")
    def test_timeout_when_no_answer(self):
        r, w = os.pipe()
        with os.fdopen(r) as inp, os.fdopen(w, "w"):
            ringer = TerminalRinger(5, timeout=0.2, sound=self.sound, out=io.StringIO(), inp=inp)
            self.assertEqual(ringer.ring(Alarm(1, "07:00:00")), Action.TIMEOUT)

    @unittest.skipIf(os.name == "nt", "select() on pipes is POSIX-only")
    def test_closed_stdin_times_out_instead_of_spinning(self):
        self.assertEqual(self.ring_with_input("", timeout=0.3)[0], Action.TIMEOUT)

    @unittest.skipIf(os.name == "nt", "select() on pipes is POSIX-only")
    def test_keys_typed_before_the_ring_are_ignored(self):
        # Regression: a 'd' typed while snoozed used to dismiss the next ring instantly.
        action, _ = self.ring_with_input("s\n", typed_before="d\n")
        self.assertEqual(action, Action.SNOOZE)


if __name__ == "__main__":
    unittest.main()
