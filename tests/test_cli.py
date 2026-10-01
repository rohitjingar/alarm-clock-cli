"""The CLI end to end: `alarm set` / `alarm edit` are driven by typing answers to
their questions, exactly as a person would."""

import contextlib
import io
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from alarmclock.cli import EXIT_ERROR, EXIT_OK, EXIT_USAGE, main
from alarmclock.models import DAILY, WEEKDAYS, WEEKENDS
from alarmclock.store import AlarmStore

NOW = datetime(2026, 10, 1, 12, 0, 0)  # Thursday noon


class CliTestCase(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.path = Path(self._tmp.name) / "alarms.json"
        self.store = AlarmStore(self.path)

    def tearDown(self):
        self._tmp.cleanup()

    def cli(self, *argv, answers=(), now=NOW):
        out, err = io.StringIO(), io.StringIO()
        typed = io.StringIO("".join(a + "\n" for a in answers))
        code = main(list(argv), store=self.store, clock=lambda: now,
                    inp=typed, out=out, err=err)
        return code, out.getvalue(), err.getvalue()

    def set_alarm(self, *answers, now=NOW):
        code, out, err = self.cli("set", answers=answers, now=now)
        self.assertEqual(code, EXIT_OK, out + err)
        return out

    def only_alarm(self):
        (alarm,) = self.store.load()
        return alarm


class SetTest(CliTestCase):
    """`alarm set`: Time -> AM or PM -> Repeat -> Title, like a phone."""

    def test_only_once(self):
        out = self.set_alarm("7:30", "am", "1", "")
        a = self.only_alarm()
        self.assertEqual((a.time, a.days, a.label), ("07:30:00", [], ""))
        self.assertEqual(a.date, "2026-10-02")  # 7:30 AM already passed today
        self.assertIn("Alarm 1 set: 7:30 AM, once.", out)
        self.assertIn("It will ring Fri 7:30 AM (in 19 hours 30 minutes)", out)

    def test_only_once_later_today(self):
        self.set_alarm("1:00", "pm", "1", "")
        self.assertEqual(self.only_alarm().date, "2026-10-01")

    def test_every_day_with_title(self):
        out = self.set_alarm("7:30", "pm", "2", "Dinner")
        a = self.only_alarm()
        self.assertEqual((a.time, a.days, a.label, a.date), ("19:30:00", DAILY, "Dinner", None))
        self.assertIn('Alarm 1 "Dinner" set: 7:30 PM, every day.', out)

    def test_weekdays_and_weekends(self):
        self.set_alarm("6:45", "am", "3", "Wake up")
        self.set_alarm("9:00", "am", "4", "Lie in")
        weekdays, weekends = self.store.load()
        self.assertEqual(weekdays.days, WEEKDAYS)
        self.assertEqual(weekends.days, WEEKENDS)

    def test_choose_days_by_number_or_name(self):
        self.set_alarm("6", "pm", "5", "1 3 5", "Gym")
        self.set_alarm("10", "am", "5", "sat, sun", "")
        gym, weekend = self.store.load()
        self.assertEqual((gym.time, gym.days), ("18:00:00", [0, 2, 4]))
        self.assertEqual(gym.describe_repeat(), "every Mon, Wed, Fri")
        self.assertEqual(weekend.days, [5, 6])

    def test_am_pm_question_skipped_when_time_is_already_clear(self):
        out = self.set_alarm("19:05", "1", "")
        self.assertNotIn("AM or PM", out)
        self.set_alarm("7:30pm", "1", "")
        self.assertEqual([a.time for a in self.store.load()], ["19:05:00", "19:30:00"])

    def test_twelve_oclock(self):
        self.set_alarm("12:00", "am", "2", "")
        self.set_alarm("12:00", "pm", "2", "")
        self.assertEqual([a.time for a in self.store.load()], ["00:00:00", "12:00:00"])

    def test_wrong_answers_are_explained_and_asked_again(self):
        out = self.set_alarm(
            "",            # no time
            "25:00",       # not a time
            "7:30", "x",   # not AM/PM
            "pm",
            "9", "",       # not a menu number
            "5", "8", "",  # day out of range, no day
            "mon",
            "Tea",
        )
        for hint in ("Please type a time", "isn't a time on a clock", "Please type AM or PM",
                     "number from 1 to 5", "Days go from 1 (Mon) to 7 (Sun)",
                     "Pick at least one day"):
            self.assertIn(hint, out)
        a = self.only_alarm()
        self.assertEqual((a.time, a.days, a.label), ("19:30:00", [0], "Tea"))

    def test_quitting_halfway_saves_nothing(self):
        code, out, _ = self.cli("set", answers=["7:30", "am"])  # then Ctrl+D
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("Cancelled. Nothing was changed.", out)
        self.assertEqual(self.store.load(), [])


class EditTest(CliTestCase):
    def test_enter_keeps_every_value(self):
        self.set_alarm("7:30", "am", "5", "1 3 5", "Gym")
        code, out, _ = self.cli("edit", "1", answers=["", "", "", "", ""])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("Time [7:30]", out)
        self.assertIn("AM or PM [AM]", out)
        self.assertIn("Pick 1-5 [5]", out)
        self.assertIn("Which days [1 3 5]", out)
        self.assertIn("Title [Gym]", out)
        a = self.only_alarm()
        self.assertEqual((a.time, a.days, a.label), ("07:30:00", [0, 2, 4], "Gym"))

    def test_change_each_field(self):
        self.set_alarm("7:30", "am", "5", "1 3 5", "Gym")
        out = self.cli("edit", "1", answers=["8:15", "pm", "2", "-"])[1]
        a = self.only_alarm()
        self.assertEqual((a.time, a.days, a.label), ("20:15:00", DAILY, ""))
        self.assertIn("Alarm 1 updated: 8:15 PM, every day.", out)

    def test_editing_switches_alarm_back_on_and_reschedules(self):
        self.set_alarm("1:00", "pm", "1", "")
        self.cli("off", "1")
        later = datetime(2026, 10, 1, 14, 0)
        self.cli("edit", "1", answers=["", "", "", ""], now=later)
        a = self.only_alarm()
        self.assertTrue(a.enabled)
        self.assertEqual(a.date, "2026-10-02")

    def test_unknown_alarm(self):
        code, _, err = self.cli("edit", "9")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("no alarm number 9", err)


class ListOnOffDeleteTest(CliTestCase):
    def test_list_is_sorted_by_time_and_uses_am_pm(self):
        self.set_alarm("7:30", "pm", "2", "Dinner")
        self.set_alarm("6:00", "am", "3", "Wake up")
        out = self.cli("list")[1]
        self.assertLess(out.index("6:00 AM"), out.index("7:30 PM"))
        self.assertIn("every weekday", out)
        self.assertIn("Fri 6:00 AM", out)

    def test_empty_list_says_what_to_do(self):
        self.assertIn("Set one with: alarm set", self.cli("list")[1])

    def test_missed_status(self):
        self.set_alarm("1:00", "pm", "1", "")
        self.assertIn("missed", self.cli("list", now=datetime(2026, 10, 1, 14, 0))[1])

    def test_off_on_delete(self):
        self.set_alarm("7:00", "am", "2", "")
        self.assertIn("Alarm 1 is off", self.cli("off", "1")[1])
        self.assertFalse(self.only_alarm().enabled)
        self.assertIn("Alarm 1 is on", self.cli("on", "#1")[1])
        self.assertTrue(self.only_alarm().enabled)
        self.assertIn("Deleted alarm 1", self.cli("delete", "1")[1])
        self.assertEqual(self.store.load(), [])

    def test_turning_on_an_old_one_time_alarm_moves_it_to_the_next_day(self):
        self.set_alarm("1:00", "pm", "1", "")
        out = self.cli("on", "1", now=datetime(2026, 10, 1, 14, 0))[1]
        self.assertEqual(self.only_alarm().date, "2026-10-02")
        self.assertIn("Fri 1:00 PM", out)

    def test_unknown_id_changes_nothing(self):
        self.set_alarm("7:00", "am", "2", "")
        code, _, err = self.cli("delete", "1", "99")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("no alarm number 99", err)
        self.assertEqual(len(self.store.load()), 1)  # all-or-nothing

    def test_ids_must_be_numbers(self):
        code, _, err = self.cli("off", "two")
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("by their number", err)


class HelpAndErrorsTest(CliTestCase):
    def test_no_command_and_help_show_what_i_can_do(self):
        for argv in ([], ["help"]):
            code, out, _ = self.cli(*argv)
            self.assertEqual(code, EXIT_OK)
            self.assertIn("alarm set", out)
            self.assertIn("time, AM or PM, repeat, title", out)

    def test_unknown_or_incomplete_command_is_friendly(self):
        for argv, expected in ((["fly"], "I don't know the command 'fly'"),
                               (["off"], "Something is missing")):
            with self.subTest(argv=argv), \
                    contextlib.redirect_stderr(io.StringIO()) as err, \
                    self.assertRaises(SystemExit) as exit_:
                self.cli(*argv)
            self.assertEqual(exit_.exception.code, EXIT_USAGE)
            self.assertIn(expected, err.getvalue())
            self.assertIn("alarm set", err.getvalue())

    def test_ring_timeout_must_be_shorter_than_grace(self):
        code, _, err = self.cli("start", "--grace", "1", "--ring-timeout", "90")
        self.assertEqual(code, EXIT_USAGE)
        self.assertIn("shorter than --grace", err)

    def test_corrupt_store_is_a_clean_error(self):
        self.path.write_text("nope")
        code, _, err = self.cli("list")
        self.assertEqual(code, EXIT_ERROR)
        self.assertIn("unreadable", err)


if __name__ == "__main__":
    unittest.main()
