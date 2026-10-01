"""Parsing of user-supplied times, durations and repeat rules.

All functions raise ValueError with a message fit to show the user.
"""

from __future__ import annotations

import re
from datetime import time, timedelta

from .models import DAILY, DAY_NAMES, WEEKDAYS, WEEKENDS

_TIME_RE = re.compile(
    r"^(?P<h>\d{1,2})(?::(?P<m>\d{2})(?::(?P<s>\d{2}))?)?\s*(?P<ampm>am|pm)?$",
    re.IGNORECASE,
)
_DURATION_RE = re.compile(r"^(?:(?P<h>\d+)h)?(?:(?P<m>\d+)m)?(?:(?P<s>\d+)s)?$")


def parse_time(text: str) -> time:
    """Parse '07:30', '7:30am', '7pm', '19:05', '23:59:30'."""
    m = _TIME_RE.match(text.strip())
    if not m:
        raise ValueError(f"invalid time {text!r} (try 07:30, 7:30am or 19:05)")
    hour = int(m["h"])
    minute = int(m["m"] or 0)
    second = int(m["s"] or 0)
    ampm = (m["ampm"] or "").lower()

    if not ampm and m["m"] is None:
        # A bare "7" is ambiguous (7am? 7pm? 7 minutes?) -- refuse rather than guess.
        raise ValueError(f"invalid time {text!r}: use HH:MM or add am/pm")
    if ampm:
        if not 1 <= hour <= 12:
            raise ValueError(f"invalid time {text!r}: hour must be 1-12 with am/pm")
        hour = hour % 12 + (12 if ampm == "pm" else 0)
    if hour > 23 or minute > 59 or second > 59:
        raise ValueError(f"invalid time {text!r}: out of range")
    return time(hour, minute, second)


def parse_duration(text: str) -> timedelta:
    """Parse '25m', '1h30m', '90s', '2h'. A bare number means minutes."""
    raw = text.strip().lower()
    if raw.isdigit():
        raw += "m"
    m = _DURATION_RE.match(raw)
    if not raw or not m:
        raise ValueError(f"invalid duration {text!r} (try 25m, 1h30m or 45s)")
    delta = timedelta(
        hours=int(m["h"] or 0), minutes=int(m["m"] or 0), seconds=int(m["s"] or 0)
    )
    if delta <= timedelta(0):
        raise ValueError(f"invalid duration {text!r}: must be greater than zero")
    return delta


_FULL_DAY_NAMES = ["monday", "tuesday", "wednesday", "thursday", "friday",
                   "saturday", "sunday"]
_DAY_LOOKUP = {
    **{name.lower(): i for i, name in enumerate(DAY_NAMES)},
    **{name: i for i, name in enumerate(_FULL_DAY_NAMES)},
}
_PRESETS = {"once": [], "daily": DAILY, "weekdays": WEEKDAYS, "weekends": WEEKENDS}


def parse_repeat(text: str) -> list[int]:
    """Parse 'once', 'daily', 'weekdays', 'weekends' or 'mon,wed,fri'."""
    raw = text.strip().lower()
    if raw in _PRESETS:
        return list(_PRESETS[raw])
    days = set()
    for part in raw.split(","):
        key = part.strip()
        if key not in _DAY_LOOKUP:
            raise ValueError(
                f"invalid repeat {text!r} "
                "(use once, daily, weekdays, weekends or e.g. mon,wed,fri)"
            )
        days.add(_DAY_LOOKUP[key])
    return sorted(days)
