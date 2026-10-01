"""Human-friendly formatting helpers."""

from __future__ import annotations

import math
from datetime import datetime, timedelta


def format_delta(delta: timedelta) -> str:
    """'2d 3h', '9h 3m', '4m 10s', '45s'.

    Rounds up: an alarm 24m59.4s away reads "25m", matching what was asked for.
    """
    total = max(0, math.ceil(delta.total_seconds()))
    days, rem = divmod(total, 86400)
    hours, rem = divmod(rem, 3600)
    minutes, seconds = divmod(rem, 60)
    if days:
        return f"{days}d {hours}h"
    if hours:
        return f"{hours}h {minutes}m"
    if minutes:
        return f"{minutes}m {seconds}s" if minutes < 10 and seconds else f"{minutes}m"
    return f"{seconds}s"


def format_when(when: datetime, now: datetime) -> str:
    """'Fri 07:30 (in 9h 3m)'."""
    clock = when.strftime("%H:%M:%S" if when.second else "%H:%M")
    return f"{when.strftime('%a')} {clock} (in {format_delta(when - now)})"
