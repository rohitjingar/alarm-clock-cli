"""Human-friendly formatting helpers."""

from __future__ import annotations

import math
from datetime import datetime, time, timedelta


def plural(n: int, word: str) -> str:
    return f"{n} {word}" + ("" if n == 1 else "s")


def format_delta(delta: timedelta) -> str:
    """'2 days 3 hours', '9 hours 3 minutes', '4 minutes 10 seconds', '45 seconds'.

    Rounds up: an alarm 24m59.4s away reads "25 minutes", matching what was asked for.
    """
    total = max(0, math.ceil(delta.total_seconds()))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        parts = [(days, "day"), (hours, "hour")]
    elif hours:
        parts = [(hours, "hour"), (minutes, "minute")]
    elif minutes:
        parts = [(minutes, "minute"), (seconds if minutes < 10 else 0, "second")]
    else:
        return plural(seconds, "second")
    return " ".join(plural(n, w) for n, w in parts if n)


def format_clock(t: time) -> str:
    """12-hour clock, like a phone: '7:30 AM', '12:05 PM', '12:00 AM', '1:19:39 PM'.

    Built by hand rather than with %p, whose text depends on the system locale.
    """
    seconds = f":{t.second:02d}" if t.second else ""
    return f"{t.hour % 12 or 12}:{t.minute:02d}{seconds} {'AM' if t.hour < 12 else 'PM'}"


def format_when(when: datetime, now: datetime) -> str:
    """'Fri 7:30 AM (in 9 hours 3 minutes)'."""
    return f"{when:%a} {format_clock(when.time())} (in {format_delta(when - now)})"
