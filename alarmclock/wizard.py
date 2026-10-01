"""Phone-style alarm setup: one question per field.

    Time  ->  AM or PM  ->  Repeat (only once / every day / weekdays / weekends /
    choose days)  ->  Title

The same questions serve `alarm set` (a blank form) and `alarm edit N` (current
values shown in [brackets]; ENTER keeps them). It reads plain lines, so it
behaves the same whether a person types or a test pipes the answers in.
Ctrl+C or Ctrl+D cancels without saving anything.
"""

from __future__ import annotations

import re
from datetime import time
from typing import Callable, Optional, TextIO, TypeVar

from .models import DAILY, DAY_NAMES, WEEKDAYS, WEEKENDS, AlarmDraft
from .timeparse import AmbiguousTime, parse_repeat, parse_time

T = TypeVar("T")

REPEAT_OPTIONS = [
    ("Only once", []),
    ("Every day", DAILY),
    ("Weekdays (Mon to Fri)", WEEKDAYS),
    ("Weekends (Sat and Sun)", WEEKENDS),
    ("Choose days", None),
]
CHOOSE_DAYS = len(REPEAT_OPTIONS)


class Cancelled(Exception):
    """The person quit the questions. Nothing is saved."""


class Prompter:
    def __init__(self, inp: TextIO, out: TextIO):
        self.inp = inp
        self.out = out

    def say(self, text: str = "") -> None:
        self.out.write(text + "\n")

    def ask(self, question: str, convert: Callable[[str], T]) -> T:
        """Ask until `convert` accepts the answer. Its ValueError message is shown
        as a hint and the same question is asked again -- never a crash."""
        while True:
            self.out.write(question)
            self.out.flush()
            try:
                line = self.inp.readline()
            except KeyboardInterrupt:
                raise Cancelled from None
            if not line:  # Ctrl+D / end of input
                raise Cancelled
            try:
                return convert(line.strip())
            except ValueError as exc:
                self.say(f"     Oops! {exc}")


def ask_alarm(p: Prompter, current: Optional[AlarmDraft] = None) -> AlarmDraft:
    clock = _ask_time(p, current.clock if current else None)
    days = _ask_repeat(p, current.days if current else None)
    label = _ask_title(p, current.label if current else None)
    return AlarmDraft(clock, days, label)


def _question(label: str, current: Optional[str], hint: str) -> str:
    return f"  {label} [{current}]: " if current else f"  {label} ({hint}): "


def _ask_time(p: Prompter, current: Optional[time]) -> time:
    shown = None
    if current is not None:
        shown = f"{current.hour % 12 or 12}:{current.minute:02d}"
        if current.second:
            shown += f":{current.second:02d}"

    def convert(answer: str):
        text = answer or shown
        if not text:
            raise ValueError("Please type a time, like 7:30.")
        try:
            return parse_time(text), None
        except AmbiguousTime as exc:
            return None, exc.text  # AM or PM is its own question, like on a phone

    clock, needs_am_pm = p.ask(_question("Time", shown, "like 7:30"), convert)
    if clock is not None:  # "19:30" or "7:30pm" already says which half of the day
        return clock
    current_half = None if current is None else ("AM" if current.hour < 12 else "PM")
    return _ask_am_pm(p, needs_am_pm, current_half)


def _ask_am_pm(p: Prompter, time_text: str, current: Optional[str]) -> time:
    def convert(answer: str) -> time:
        half = (answer or current or "").lower().replace(".", "")
        if half in ("a", "am"):
            return parse_time(time_text + "am")
        if half in ("p", "pm"):
            return parse_time(time_text + "pm")
        raise ValueError("Please type AM or PM.")

    return p.ask(_question("AM or PM", current, "am/pm"), convert)


def _ask_repeat(p: Prompter, current: Optional[list[int]]) -> list[int]:
    current_choice = None
    if current is not None:
        current_choice = next(
            (i for i, (_, days) in enumerate(REPEAT_OPTIONS, 1) if days == sorted(current)),
            CHOOSE_DAYS,
        )
    p.say("  Repeat:")
    for i, (name, _) in enumerate(REPEAT_OPTIONS, 1):
        p.say(f"     {i}) {name}")

    def convert(answer: str) -> int:
        choice = answer or (str(current_choice) if current_choice else "")
        if choice.isdigit() and 1 <= int(choice) <= len(REPEAT_OPTIONS):
            return int(choice)
        raise ValueError(f"Please type a number from 1 to {len(REPEAT_OPTIONS)}.")

    shown = str(current_choice) if current_choice else None
    choice = p.ask(_question(f"Pick 1-{len(REPEAT_OPTIONS)}", shown, "type a number"), convert)
    if choice == CHOOSE_DAYS:
        return _ask_days(p, current if current_choice == CHOOSE_DAYS else None)
    return list(REPEAT_OPTIONS[choice - 1][1])


def _ask_days(p: Prompter, current: Optional[list[int]]) -> list[int]:
    p.say("     " + "   ".join(f"{i}) {name}" for i, name in enumerate(DAY_NAMES, 1)))
    shown = " ".join(str(d + 1) for d in current) if current else None

    def convert(answer: str) -> list[int]:
        text = answer or shown or ""
        tokens = [t for t in re.split(r"[\s,]+", text) if t]
        if not tokens:
            raise ValueError("Pick at least one day, like: 1 3 5")
        if all(t.isdigit() for t in tokens):
            if not all(1 <= int(t) <= 7 for t in tokens):
                raise ValueError("Days go from 1 (Mon) to 7 (Sun).")
            return sorted({int(t) - 1 for t in tokens})
        days = parse_repeat(text)  # names work too: mon wed fri
        if not days:
            raise ValueError("Pick at least one day, like: 1 3 5")
        return days

    return p.ask(_question("Which days", shown, "like 1 3 5"), convert)


def _ask_title(p: Prompter, current: Optional[str]) -> str:
    if current:
        question = f"  Title [{current}] (ENTER keeps it, - removes it): "
    else:
        question = "  Title (optional, press ENTER to skip): "

    def convert(answer: str) -> str:
        if answer == "-":
            return ""
        return answer or current or ""

    return p.ask(question, convert)
