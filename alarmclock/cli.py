"""Command-line interface, in plain English:

    alarm set 7:30 wake up every weekday
    alarm in 10 minutes tea
    alarm list  |  alarm off 2  |  alarm on 2  |  alarm delete 2
    alarm start

The earlier flag style (add -l LABEL -r REPEAT, add --in, rm, enable, disable,
run) still works as hidden aliases, so nothing that used it breaks.
"""

from __future__ import annotations

import argparse
import re
import sys
from datetime import datetime, timedelta
from typing import Callable, Optional, Sequence

from . import __version__
from .fmt import format_when
from .models import Alarm
from .ringer import TerminalRinger
from .runner import Runner
from .store import AlarmStore, StoreError
from .timeparse import parse_duration, parse_repeat, parse_time

EXIT_OK, EXIT_ERROR, EXIT_USAGE = 0, 1, 2

CHEAT_SHEET = """\
⏰ alarm: a simple alarm clock

  alarm set 7:30                         set an alarm for 7:30
  alarm set 7:30 wake up every weekday   give it a name, repeat it Monday to Friday
  alarm set 6pm dinner every day         also: every weekend, every monday friday
  alarm in 10 minutes tea                ring once, 10 minutes from now
  alarm list                             see all your alarms
  alarm off 2   /   alarm on 2           pause / un-pause alarm number 2
  alarm delete 2                         delete alarm number 2
  alarm start                            start the clock (leave it open so alarms can ring!)

When an alarm rings: press ENTER to snooze, or type stop and press ENTER.
"""

# A trailing word that means "repeat", even without "every" in front of it.
_REPEAT_SHORTHANDS = {"daily", "everyday", "weekdays", "weekends"}
_AMPM = re.compile(r"^[ap]\.?m\.?$", re.IGNORECASE)


class UsageError(Exception):
    pass


class FriendlyParser(argparse.ArgumentParser):
    """argparse, but its errors point at the cheat sheet instead of a usage dump."""

    def error(self, message):
        unknown = re.search(r"invalid choice: '([^']*)'", message)
        if unknown:
            problem = f"I don't know the command {unknown[1]!r}."
        elif "required" in message:
            problem = "Something is missing from that command."
        else:
            problem = f"I didn't understand that ({message})."
        self.exit(EXIT_USAGE, f"Oops! {problem} Here's what I can do:\n\n{CHEAT_SHEET}")


def build_parser() -> argparse.ArgumentParser:
    hidden = argparse.SUPPRESS
    p = FriendlyParser(prog="alarm", usage="alarm COMMAND ...", description=CHEAT_SHEET,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND", title="commands")

    set_ = sub.add_parser("set", aliases=["add"], help="set an alarm: alarm set 7:30 wake up")
    set_.add_argument("words", nargs="*", metavar="TIME [NAME] [every ...]")
    set_.add_argument("-l", "--label", help=hidden)
    set_.add_argument("-r", "--repeat", help=hidden)
    set_.add_argument("--in", dest="in_", help=hidden)

    in_ = sub.add_parser("in", help="ring once after a while: alarm in 10 minutes tea")
    in_.add_argument("words", nargs="+", metavar="HOW-LONG [NAME]")
    in_.add_argument("-l", "--label", help=hidden)

    sub.add_parser("list", aliases=["ls", "show"], help="see all your alarms")
    for name, aliases, help_ in (
        ("off", ["disable", "pause"], "pause an alarm: alarm off 2"),
        ("on", ["enable", "unpause"], "un-pause an alarm: alarm on 2"),
        ("delete", ["remove", "rm"], "delete an alarm: alarm delete 2"),
    ):
        cmd = sub.add_parser(name, aliases=aliases, help=help_)
        cmd.add_argument("ids", nargs="+", metavar="NUMBER")

    start = sub.add_parser("start", aliases=["run"],
                           help="start the clock so alarms can ring (leave it open)")
    start.add_argument("--snooze", type=int, default=5, metavar="MINUTES",
                       help="how long a snooze lasts (default 5)")
    start.add_argument("--grace", type=int, default=5, metavar="MINUTES",
                       help="still ring alarms up to this late, e.g. after the computer "
                            "slept (default 5)")
    start.add_argument("--ring-timeout", type=int, default=60, metavar="SECONDS",
                       help="if nobody answers for this long, snooze (default 60)")

    sub.add_parser("help", help="show examples")
    return p


def _split_repeat(words: list[str]) -> tuple[list[str], Optional[str]]:
    """['wake', 'up', 'every', 'weekday'] -> (['wake', 'up'], 'weekday')."""
    lowered = [w.lower() for w in words]
    if "every" in lowered:
        i = lowered.index("every")
        rule = " ".join(words[i + 1:])
        if not rule:
            raise UsageError("Every what? Try: every day, every weekday or every monday.")
        return words[:i], rule
    if lowered and lowered[-1] in _REPEAT_SHORTHANDS:
        return words[:-1], words[-1]
    return words, None


def _take_time(words: list[str]):
    """Time from the front of the words; '7:30 am' may be split over two words."""
    if len(words) >= 2 and _AMPM.match(words[1]):
        return parse_time(words[0] + words[1]), words[2:]
    return parse_time(words[0]), words[1:]


def _take_duration(words: list[str]):
    """Longest prefix that reads as a duration: '10 minutes tea' -> (10 min, ['tea'])."""
    for n in range(len(words), 0, -1):
        try:
            return parse_duration(" ".join(words[:n])), words[n:]
        except ValueError:
            continue
    parse_duration(words[0])  # raises the friendly error for the first word
    raise AssertionError("unreachable")


def _next_date_for(clock, now: datetime):
    """One-time alarm date: today if that time is still ahead, else tomorrow."""
    today = datetime.combine(now.date(), clock)
    return (today if today > now else today + timedelta(days=1)).date()


def cmd_set(args, store: AlarmStore, now: datetime, out) -> None:
    flag_in = getattr(args, "in_", None)
    rest, rule = _split_repeat(list(args.words))
    days = parse_repeat(rule or getattr(args, "repeat", None) or "once")

    if args.command == "in" or flag_in:
        if days:
            raise UsageError("'alarm in' rings just once. For a repeating alarm use a "
                             "clock time, like: alarm set 7:30 every day")
        if flag_in and rest:
            raise UsageError("give either a time or --in, not both")
        delta, rest = (parse_duration(flag_in), rest) if flag_in else _take_duration(rest)
        target = (now + delta).replace(microsecond=0)
        clock, date = target.time(), target.date()
    else:
        if not rest:
            raise UsageError("What time? Try: alarm set 7:30")
        clock, rest = _take_time(rest)
        date = None if days else _next_date_for(clock, now)

    label = args.label if args.label is not None else " ".join(rest)
    alarms = store.load()
    alarm = Alarm(
        id=store.next_id(alarms),
        time=clock.isoformat(),
        label=label.strip(),
        days=days,
        date=date.isoformat() if date else None,
    )
    alarms.append(alarm)
    store.save(alarms)

    name = f' "{alarm.label}"' if alarm.label else ""
    out.write(
        f"⏰ Alarm {alarm.id}{name} set for {alarm.display_time()}, {alarm.describe_repeat()}.\n"
        f"   It will ring {format_when(alarm.next_trigger(now), now)}.\n"
        "   Remember: run `alarm start` and leave it open, or it can't ring!\n"
    )


def cmd_list(args, store: AlarmStore, now: datetime, out) -> None:
    alarms = store.load()
    if not alarms:
        out.write("You don't have any alarms yet. Try: alarm set 7:30\n")
        return
    rows = [("#", "TIME", "REPEATS", "STATUS", "NEXT RING", "NAME")]
    for a in sorted(alarms, key=lambda a: a.id):
        nxt = a.next_trigger(now)
        if not a.enabled:
            status, next_text = "off", "-"
        elif nxt is None:
            status, next_text = "missed", "-"  # one-time alarm whose time passed unrung
        else:
            status, next_text = "on", format_when(nxt, now)
        rows.append((str(a.id), a.display_time(), a.describe_repeat(), status, next_text, a.label))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        out.write("  ".join(cell.ljust(w) for cell, w in zip(r, widths)).rstrip() + "\n")


def _parse_ids(raw: Sequence[str]) -> list[int]:
    ids = []
    for item in raw:
        item = item.lstrip("#")
        if not item.isdigit():
            raise UsageError("Pick alarms by their number, like: alarm off 2 "
                             "(see the numbers with: alarm list)")
        ids.append(int(item))
    return ids


def _apply_to_ids(store: AlarmStore, ids: list[int],
                  change: Callable[[list[Alarm], Alarm], None]) -> None:
    missing: list[int] = []

    def mutate(alarms: list[Alarm]) -> None:
        by_id = {a.id: a for a in alarms}
        missing.extend(i for i in ids if i not in by_id)
        if missing:
            return  # all-or-nothing: don't half-apply a command with a typo in it
        for i in ids:
            change(alarms, by_id[i])

    store.update(mutate)
    if missing:
        raise LookupError(f"There's no alarm number {', '.join(map(str, missing))}. "
                          "See your alarms with: alarm list")


def cmd_delete(args, store, now, out) -> None:
    ids = _parse_ids(args.ids)
    _apply_to_ids(store, ids, lambda alarms, a: alarms.remove(a))
    out.write(f"🗑  Deleted alarm {', '.join(map(str, ids))}.\n")


def cmd_off(args, store, now, out) -> None:
    ids = _parse_ids(args.ids)

    def turn_off(alarms, a: Alarm) -> None:
        a.enabled = False

    _apply_to_ids(store, ids, turn_off)
    for i in ids:
        out.write(f"🔕 Alarm {i} is off. Turn it back on with: alarm on {i}\n")


def cmd_on(args, store, now, out) -> None:
    ids = _parse_ids(args.ids)
    turned_on: list[Alarm] = []

    def turn_on(alarms, a: Alarm) -> None:
        a.enabled = True
        if not a.is_recurring and a.next_trigger(now) is None:
            # An old one-time alarm: move it to the next time that clock time comes round.
            a.date = _next_date_for(a.clock_time, now).isoformat()
        turned_on.append(a)

    _apply_to_ids(store, ids, turn_on)
    for a in turned_on:
        out.write(f"🔔 Alarm {a.id} is on. It will ring {format_when(a.next_trigger(now), now)}.\n")


def cmd_start(args, store, now, out) -> None:
    for name in ("snooze", "grace", "ring_timeout"):
        if getattr(args, name) <= 0:
            raise UsageError(f"--{name.replace('_', '-')} must be more than 0")
    if args.ring_timeout >= args.grace * 60:
        # Alarms that come due while another is ringing are checked afterwards;
        # a ring longer than the grace period would make them count as missed.
        raise UsageError("--ring-timeout must be shorter than --grace")
    ringer = TerminalRinger(snooze_minutes=args.snooze, timeout=args.ring_timeout, out=out)
    Runner(store, ringer, snooze=timedelta(minutes=args.snooze),
           grace=timedelta(minutes=args.grace), out=out).run()


COMMANDS = {
    **dict.fromkeys(("set", "add", "in"), cmd_set),
    **dict.fromkeys(("list", "ls", "show"), cmd_list),
    **dict.fromkeys(("off", "disable", "pause"), cmd_off),
    **dict.fromkeys(("on", "enable", "unpause"), cmd_on),
    **dict.fromkeys(("delete", "remove", "rm"), cmd_delete),
    **dict.fromkeys(("start", "run"), cmd_start),
}


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    store: Optional[AlarmStore] = None,
    clock: Callable[[], datetime] = datetime.now,
    out=None,
    err=None,
) -> int:
    out = out or sys.stdout
    err = err or sys.stderr
    args = build_parser().parse_args(argv)
    if args.command in (None, "help"):
        out.write(CHEAT_SHEET)
        return EXIT_OK
    store = store or AlarmStore()
    try:
        COMMANDS[args.command](args, store, clock(), out)
    except (UsageError, ValueError) as exc:
        err.write(f"Oops! {exc}\n")
        return EXIT_USAGE
    except (LookupError, StoreError) as exc:
        err.write(f"Oops! {exc}\n")
        return EXIT_ERROR
    except KeyboardInterrupt:
        out.write("\n👋 Alarm clock stopped. Alarms can't ring until you run: alarm start\n")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
