"""Parsing of what the user types: times and days.

Forgiving about format ("7:30 am", "7.30pm", "mon wed fri"),
strict about ambiguity: like a phone, "7:30" needs an AM or PM. Every function
raises ValueError with a message written for a non-technical user.
"""

from __future__ import annotations

import re
from datetime import time

from .models import DAILY, WEEKDAYS, WEEKENDS

class AmbiguousTime(ValueError):
    """'7:30' could be AM or PM. Carries the text so an interactive caller can ask."""

    def __init__(self, text: str):
        self.text = text.strip()
        super().__init__(f"Is {self.text} AM or PM? Say {self.text}am or {self.text}pm.")


_TIME_RE = re.compile(
    r"^(?P<h>\d{1,2})(?:[:.](?P<m>\d{2})(?:[:.](?P<s>\d{2}))?)?\s*(?P<ampm>[ap]\.?m\.?)?$",
    re.IGNORECASE,
)


def parse_time(text: str) -> time:
    """Parse '7:30am', '7:30 pm', '7pm', '19:05', '07:30', '7.30pm', '23:59:30'.

    Hours 1-12 without AM/PM raise AmbiguousTime -- unless written with a leading
    zero ('07:30'), the 24-hour convention. Hours 0 and 13-23 are never ambiguous.
    """
    m = _TIME_RE.match(text.strip())
    if not m:
        raise ValueError(f"I don't understand the time {text!r}. Try 7:30, 7:30am or 19:05.")
    hour, minute, second = int(m["h"]), int(m["m"] or 0), int(m["s"] or 0)
    ampm = (m["ampm"] or "").lower()[:1]
    not_a_time = ValueError(
        f"{text!r} isn't a time on a clock. Try something like 7:30 or 19:05."
    )

    if not ampm and 1 <= hour <= 12 and not m["h"].startswith("0"):
        raise AmbiguousTime(text)  # refuse to guess, like a phone's AM/PM switch
    if ampm:
        if not 1 <= hour <= 12:
            raise not_a_time
        hour = hour % 12 + (12 if ampm == "p" else 0)
    if hour > 23 or minute > 59 or second > 59:
        raise not_a_time
    return time(hour, minute, second)


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
