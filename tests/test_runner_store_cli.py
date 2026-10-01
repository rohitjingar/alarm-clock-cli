import io
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from alarmclock.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main
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
        self.assertEqual(AlarmStore.next_id([]), 1)
        self.assertEqual(AlarmStore.next_id([Alarm(4, "07:00:00"), Alarm(2, "07:00:00")]), 5)


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
        self.assertIn("giving up", runner.out.getvalue())

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


class TerminalRingerTest(unittest.TestCase):
    """Uses a real pipe so the select()-based prompt is exercised for real."""

    def ring_with_input(self, text, timeout=2.0):
        r, w = os.pipe()
        with os.fdopen(r) as inp, os.fdopen(w, "w") as wr:
            wr.write(text)
            wr.flush()
            if text == "":
                wr.close()
            out = io.StringIO()
            ringer = TerminalRinger(5, timeout=timeout, bell_interval=0.05, out=out, inp=inp)
            return ringer.ring(Alarm(1, "07:00:00", "Wake")), out.getvalue()

    @unittest.skipIf(os.name == "nt", "select() on pipes is POSIX-only")
    def test_answers(self):
        self.assertEqual(self.ring_with_input("d\n")[0], Action.DISMISS)
        self.assertEqual(self.ring_with_input("\n")[0], Action.SNOOZE)
        action, out = self.ring_with_input("huh\ns\n")
        self.assertEqual(action, Action.SNOOZE)
        self.assertIn("type 's' to snooze", out)
        self.assertIn("ALARM #1", out)

    @unittest.skipIf(os.name == "nt", "select() on pipes is POSIX-only")
    def test_timeout_when_no_answer(self):
        r, w = os.pipe()
        with os.fdopen(r) as inp, os.fdopen(w, "w"):
            ringer = TerminalRinger(5, timeout=0.2, bell_interval=0.05, out=io.StringIO(), inp=inp)
            self.assertEqual(ringer.ring(Alarm(1, "07:00:00")), Action.TIMEOUT)

    @unittest.skipIf(os.name == "nt", "select() on pipes is POSIX-only")
    def test_closed_stdin_times_out_instead_of_spinning(self):
        self.assertEqual(self.ring_with_input("", timeout=0.2)[0], Action.TIMEOUT)


class CliTest(TempStoreMixin, unittest.TestCase):
    def cli(self, *argv, now=NOW):
        out, err = io.StringIO(), io.StringIO()
        code = main(list(argv), store=self.store, clock=lambda: now, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def test_add_one_time_later_today_and_tomorrow(self):
        self.cli("add", "13:00")
        self.cli("add", "11:00")
        a, b = self.store.load()
        self.assertEqual(a.date, "2026-10-01")
        self.assertEqual(b.date, "2026-10-02")

    def test_add_relative(self):
        code, out, _ = self.cli("add", "--in", "1h30m", "-l", "Tea")
        self.assertEqual(code, EXIT_OK)
        a = self.store.load()[0]
        self.assertEqual((a.time, a.date, a.label), ("13:30:00", "2026-10-01", "Tea"))
        self.assertIn("in 1h 30m", out)

    def test_add_recurring_and_list(self):
        self.cli("add", "7:30am", "-r", "weekdays", "-l", "Wake up")
        code, out, _ = self.cli("list")
        self.assertEqual(code, EXIT_OK)
        self.assertIn("weekdays", out)
        self.assertIn("Fri 07:30", out)
        self.assertIn("Wake up", out)

    def test_add_usage_errors(self):
        for argv in (["add"], ["add", "07:00", "--in", "5m"], ["add", "--in", "5m", "-r", "daily"],
                     ["add", "25:00"], ["add", "07:00", "-r", "someday"]):
            with self.subTest(argv=argv):
                code, _, err = self.cli(*argv)
                self.assertEqual(code, EXIT_USAGE)
                self.assertIn("alarm: error:", err)
        self.assertEqual(self.store.load(), [])

    def test_remove_enable_disable(self):
        self.cli("add", "07:00", "-r", "daily")
        self.cli("add", "08:00", "-r", "daily")
        self.assertEqual(self.cli("disable", "1")[0], EXIT_OK)
        self.assertFalse(self.store.load()[0].enabled)
        self.assertEqual(self.cli("enable", "1")[0], EXIT_OK)
        self.assertTrue(self.store.load()[0].enabled)
        self.assertEqual(self.cli("rm", "1", "2")[0], EXIT_OK)
        self.assertEqual(self.store.load(), [])

    def test_unknown_id_changes_nothing(self):
        self.cli("add", "07:00", "-r", "daily")
        code, _, err = self.cli("rm", "1", "99")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("99", err)
        self.assertEqual(len(self.store.load()), 1)  # all-or-nothing

    def test_reenabling_expired_one_time_alarm_reschedules_it(self):
        self.cli("add", "13:00")
        later = datetime(2026, 10, 1, 14, 0)
        self.cli("disable", "1", now=later)
        _, out, _ = self.cli("enable", "1", now=later)
        self.assertEqual(self.store.load()[0].date, "2026-10-02")
        self.assertIn("Fri 13:00", out)

    def test_expired_status_in_list(self):
        self.cli("add", "13:00")
        _, out, _ = self.cli("list", now=datetime(2026, 10, 1, 14, 0))
        self.assertIn("expired", out)

    def test_corrupt_store_is_a_clean_error(self):
        self.path.write_text("nope")
        code, _, err = self.cli("list")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("unreadable", err)


if __name__ == "__main__":
    unittest.main()
