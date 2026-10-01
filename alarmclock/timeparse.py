"""Parsing of what the user types: times, durations and repeat rules.

Forgiving about format ("7:30 am", "10 minutes", "every monday and friday"),
strict about ambiguity ("7" -- morning or evening?). Every function raises
ValueError with a message written for a non-technical user.
"""

from __future__ import annotations

import re
from datetime import time, timedelta

from .models import DAILY, WEEKDAYS, WEEKENDS

_TIME_RE = re.compile(
    r"^(?P<h>\d{1,2})(?:[:.](?P<m>\d{2})(?:[:.](?P<s>\d{2}))?)?\s*(?P<ampm>[ap]\.?m\.?)?$",
    re.IGNORECASE,
)


def parse_time(text: str) -> time:
    """Parse '07:30', '7:30am', '7:30 pm', '7pm', '19:05', '7.30', '23:59:30'."""
    m = _TIME_RE.match(text.strip())
    if not m:
        raise ValueError(f"I don't understand the time {text!r}. Try 7:30, 7:30am or 19:05.")
    hour, minute, second = int(m["h"]), int(m["m"] or 0), int(m["s"] or 0)
    ampm = (m["ampm"] or "").lower()[:1]
    not_a_time = ValueError(
        f"{text!r} isn't a time on a clock. Try something like 7:30 or 19:05."
    )

    if not ampm and m["m"] is None:
        # A bare "7" is ambiguous -- refuse rather than guess.
        if 1 <= hour <= 12:
            raise ValueError(f"Is {text!r} morning or evening? Say {hour}am or {hour}pm.")
        raise ValueError(f"Did you mean {hour:02d}:00? Please write it like that.")
    if ampm:
        if not 1 <= hour <= 12:
            raise not_a_time
        hour = hour % 12 + (12 if ampm == "p" else 0)
    if hour > 23 or minute > 59 or second > 59:
        raise not_a_time
    return time(hour, minute, second)


_UNIT_SECONDS = {}
for _names, _secs in (
    (("s", "sec", "secs", "second", "seconds"), 1),
    (("m", "min", "mins", "minute", "minutes"), 60),
    (("h", "hr", "hrs", "hour", "hours"), 3600),
):
    _UNIT_SECONDS.update(dict.fromkeys(_names, _secs))


def parse_duration(text: str) -> timedelta:
    """Parse '25m', '10 minutes', '1h30m', '1 hour and 30 minutes', '45s'.
    A bare number means minutes."""
    raw = text.strip().lower()
    if raw.isdigit():
        raw += "m"
    tokens = [t for t in re.findall(r"\d+|[a-z]+|\S", raw) if t not in ("and", ",")]
    bad = ValueError(
        f"I don't understand how long {text!r} is. Try 10 minutes, 1 hour or 30 seconds."
    )
    if not tokens or len(tokens) % 2:
        raise bad
    total = 0
    for number, unit in zip(tokens[::2], tokens[1::2]):
        if not number.isdigit() or unit not in _UNIT_SECONDS:
            raise bad
        total += int(number) * _UNIT_SECONDS[unit]
    if total <= 0:
        raise ValueError("That's no time at all! Pick something more than zero.")
    return timedelta(seconds=total)


_FULL_DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday",
                   "saturday", "sunday"]
_DAY_WORDS = {"tues": 1, "thur": 3, "thurs": 3}
for _i, _full in enumerate(_FULL_DAY_NAMES):
    _DAY_WORDS.update(dict.fromkeys((_full, _full + "s", _full[:3]), _i))
_GROUP_WORDS = {
    **dict.fromkeys(("day", "days", "daily", "everyday"), DAILY),
    **dict.fromkeys(("weekday", "weekdays"), WEEKDAYS),
    **dict.fromkeys(("weekend", "weekends"), WEEKENDS),
}


def parse_repeat(text: str) -> list[int]:
    """Parse 'once', 'daily', 'every day', 'weekdays', 'every weekend',
    'mon,wed,fri', 'every monday and friday'. Returns weekday numbers (0 = Monday)."""
    words = [w for w in re.split(r"[\s,]+", text.strip().lower()) if w and w != "and"]
    if words[:1] == ["every"]:
        words = words[1:]
    if words == ["once"]:
        return []
    bad = ValueError(
        f"I don't understand how often {text!r} is. "
        "Try: every day, every weekday, every weekend, or every monday friday."
    )
    if not words:
        raise bad
    days: set[int] = set()
    for word in words:
        if word in _GROUP_WORDS:
            days.update(_GROUP_WORDS[word])
        elif word in _DAY_WORDS:
            days.add(_DAY_WORDS[word])
        else:
            raise bad
    return sorted(days)
