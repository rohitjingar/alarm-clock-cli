import unittest
from datetime import datetime, time, timedelta

from alarmclock.fmt import format_delta
from alarmclock.models import Alarm
from alarmclock.timeparse import parse_duration, parse_repeat, parse_time

THU_NOON = datetime(2026, 10, 1, 12, 0)  # a Thursday


class ParseTimeTest(unittest.TestCase):
    def test_valid_formats(self):
        cases = {
            "07:30": time(7, 30), "7:30": time(7, 30), "19:05": time(19, 5),
            "7:30am": time(7, 30), "7:30 PM": time(19, 30), "7pm": time(19, 0),
            "12am": time(0, 0), "12pm": time(12, 0), "00:00": time(0, 0),
            "23:59:30": time(23, 59, 30), "7:30 am": time(7, 30), "7.30": time(7, 30),
            "6 p.m.": time(18, 0),
        }
        for text, expected in cases.items():
            with self.subTest(text=text):
                self.assertEqual(parse_time(text), expected)

    def test_invalid_formats(self):
        for text in ["", "7", "24:00", "12:60", "13pm", "0am", "7:5", "noon", "-1:00"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_time(text)


class ParseDurationTest(unittest.TestCase):
    def test_valid(self):
        self.assertEqual(parse_duration("25m"), timedelta(minutes=25))
        self.assertEqual(parse_duration("1h30m"), timedelta(hours=1, minutes=30))
        self.assertEqual(parse_duration("45s"), timedelta(seconds=45))
        self.assertEqual(parse_duration("10"), timedelta(minutes=10))
        self.assertEqual(parse_duration("10 minutes"), timedelta(minutes=10))
        self.assertEqual(parse_duration("1 hour and 30 minutes"), timedelta(hours=1, minutes=30))
        self.assertEqual(parse_duration("1 min 5 secs"), timedelta(seconds=65))

    def test_invalid(self):
        for text in ["", "0m", "abc", "1d", "m", "ten minutes", "10 parsecs", "5m 3"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_duration(text)


class ParseRepeatTest(unittest.TestCase):
    def test_presets_and_days(self):
        self.assertEqual(parse_repeat("once"), [])
        self.assertEqual(parse_repeat("daily"), list(range(7)))
        self.assertEqual(parse_repeat("Weekdays"), [0, 1, 2, 3, 4])
        self.assertEqual(parse_repeat("weekends"), [5, 6])
        self.assertEqual(parse_repeat("fri, mon,Wednesday"), [0, 2, 4])
        self.assertEqual(parse_repeat("every day"), list(range(7)))
        self.assertEqual(parse_repeat("every monday and friday"), [0, 4])
        self.assertEqual(parse_repeat("Mondays"), [0])

    def test_invalid(self):
        for text in ["", "every", "mo", "monkey", "fortnightly", "every other day"]:
            with self.subTest(text=text), self.assertRaises(ValueError):
                parse_repeat(text)


class NextTriggerTest(unittest.TestCase):
    def test_one_time_future_and_past(self):
        a = Alarm(id=1, time="13:00:00", date="2026-10-01")
        self.assertEqual(a.next_trigger(THU_NOON), datetime(2026, 10, 1, 13, 0))
        self.assertIsNone(a.next_trigger(datetime(2026, 10, 1, 13, 0)))  # strictly after

    def test_daily_later_today_vs_tomorrow(self):
        a = Alarm(id=1, time="13:00:00", days=list(range(7)))
        self.assertEqual(a.next_trigger(THU_NOON), datetime(2026, 10, 1, 13, 0))
        b = Alarm(id=2, time="11:00:00", days=list(range(7)))
        self.assertEqual(b.next_trigger(THU_NOON), datetime(2026, 10, 2, 11, 0))

    def test_weekdays_skip_weekend(self):
        a = Alarm(id=1, time="07:00:00", days=[0, 1, 2, 3, 4])
        fri_evening = datetime(2026, 10, 2, 20, 0)
        self.assertEqual(a.next_trigger(fri_evening), datetime(2026, 10, 5, 7, 0))  # Monday

    def test_same_weekday_next_week(self):
        a = Alarm(id=1, time="11:00:00", days=[3])  # Thursdays, already passed today
        self.assertEqual(a.next_trigger(THU_NOON), datetime(2026, 10, 8, 11, 0))

    def test_describe_repeat(self):
        self.assertEqual(Alarm(1, "07:00:00", days=[5, 6]).describe_repeat(), "every weekend")
        self.assertEqual(Alarm(1, "07:00:00", days=[0, 2]).describe_repeat(), "every Mon, Wed")
        self.assertEqual(Alarm(1, "07:00:00", date="2026-10-01").describe_repeat(), "once")

    def test_roundtrip_dict(self):
        a = Alarm(id=3, time="06:45:00", label="Gym", days=[1, 3], enabled=False)
        self.assertEqual(Alarm.from_dict(a.to_dict()), a)


class FormatDeltaTest(unittest.TestCase):
    def test_formats_and_rounds_up(self):
        self.assertEqual(format_delta(timedelta(seconds=44.2)), "45 seconds")
        self.assertEqual(format_delta(timedelta(minutes=24, seconds=59.4)), "25 minutes")
        self.assertEqual(format_delta(timedelta(minutes=4, seconds=10)), "4 minutes 10 seconds")
        self.assertEqual(format_delta(timedelta(minutes=1)), "1 minute")
        self.assertEqual(format_delta(timedelta(hours=9, minutes=3)), "9 hours 3 minutes")
        self.assertEqual(format_delta(timedelta(hours=1)), "1 hour")
        self.assertEqual(format_delta(timedelta(days=2, hours=3)), "2 days 3 hours")
        self.assertEqual(format_delta(timedelta(seconds=-5)), "0 seconds")


if __name__ == "__main__":
    unittest.main()
