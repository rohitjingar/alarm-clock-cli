"""Human-friendly formatting helpers."""

from __future__ import annotations

import math
from datetime import datetime, timedelta


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


def format_when(when: datetime, now: datetime) -> str:
    """'Fri 07:30 (in 9 hours 3 minutes)'."""
    clock = when.strftime("%H:%M:%S" if when.second else "%H:%M")
    return f"{when.strftime('%a')} {clock} (in {format_delta(when - now)})"
