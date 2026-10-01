"""Command-line interface: `alarm add|list|remove|enable|disable|run`."""

from __future__ import annotations

import argparse
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


class UsageError(Exception):
    pass


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="alarm",
        description="A terminal alarm clock. Alarms persist in ~/.alarmclock.json "
        "(override with ALARMCLOCK_FILE). Run `alarm run` to have them ring.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", required=True, metavar="COMMAND")

    add = sub.add_parser("add", help="add an alarm",
                         description="Examples: alarm add 07:30 -r weekdays -l 'Wake up' | "
                                     "alarm add 6:45pm | alarm add --in 25m -l Tea")
    add.add_argument("time", nargs="?", help="time of day: 07:30, 7:30am, 19:05")
    add.add_argument("--in", dest="in_", metavar="DURATION",
                     help="ring after a duration instead: 25m, 1h30m, 45s")
    add.add_argument("-l", "--label", default="", help="what the alarm is for")
    add.add_argument("-r", "--repeat", default="once",
                     help="once (default), daily, weekdays, weekends, or e.g. mon,wed,fri")

    sub.add_parser("list", aliases=["ls"], help="list alarms and when they ring next")

    for name, aliases, help_ in (
        ("remove", ["rm"], "delete alarm(s)"),
        ("enable", [], "switch alarm(s) on"),
        ("disable", [], "switch alarm(s) off without deleting"),
    ):
        cmd = sub.add_parser(name, aliases=aliases, help=help_)
        cmd.add_argument("ids", nargs="+", type=int, metavar="ID")

    run = sub.add_parser("run", help="watch alarms and ring them (foreground)")
    run.add_argument("--snooze", type=int, default=5, metavar="MIN",
                     help="snooze length in minutes (default 5)")
    run.add_argument("--grace", type=int, default=5, metavar="MIN",
                     help="still ring alarms up to this many minutes late, e.g. after "
                          "the computer slept (default 5)")
    run.add_argument("--ring-timeout", type=int, default=60, metavar="SEC",
                     help="auto-snooze if unanswered for this long (default 60)")
    return p


def cmd_add(args, store: AlarmStore, now: datetime, out) -> None:
    if bool(args.time) == bool(args.in_):
        raise UsageError("give either a TIME or --in DURATION (exactly one)")
    days = parse_repeat(args.repeat)

    if args.in_:
        if days:
            raise UsageError("--in creates a one-time alarm; it can't be combined with --repeat")
        target = (now + parse_duration(args.in_)).replace(microsecond=0)
        clock, date = target.time(), target.date()
    else:
        clock = parse_time(args.time)
        date = None
        if not days:  # one-time: today if still ahead, else tomorrow
            today = datetime.combine(now.date(), clock)
            date = (today if today > now else today + timedelta(days=1)).date()

    alarms = store.load()
    alarm = Alarm(
        id=store.next_id(alarms),
        time=clock.isoformat(),
        label=args.label.strip(),
        days=days,
        date=date.isoformat() if date else None,
    )
    alarms.append(alarm)
    store.save(alarms)

    label = f" {alarm.label!r}" if alarm.label else ""
    out.write(f"Added alarm #{alarm.id}{label} at {alarm.display_time()} "
              f"({alarm.describe_repeat()}), next: {format_when(alarm.next_trigger(now), now)}\n")
    out.write("Tip: keep `alarm run` open in a terminal so it can ring.\n")


def cmd_list(args, store: AlarmStore, now: datetime, out) -> None:
    alarms = store.load()
    if not alarms:
        out.write("No alarms. Add one with: alarm add 07:30\n")
        return
    rows = [("ID", "TIME", "REPEAT", "STATUS", "NEXT", "LABEL")]
    for a in sorted(alarms, key=lambda a: a.id):
        nxt = a.next_trigger(now)
        if not a.enabled:
            status, next_text = "off", "-"
        elif nxt is None:
            status, next_text = "expired", "-"
        else:
            status, next_text = "on", format_when(nxt, now)
        rows.append((str(a.id), a.display_time(), a.describe_repeat(), status, next_text, a.label))
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        out.write("  ".join(cell.ljust(w) for cell, w in zip(r, widths)).rstrip() + "\n")


def _apply_to_ids(store: AlarmStore, ids: Sequence[int], change: Callable[[list, Alarm], None]) -> list[int]:
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
        raise LookupError(f"no alarm with id {', '.join(map(str, missing))} (see `alarm list`)")
    return list(ids)


def cmd_remove(args, store, now, out) -> None:
    done = _apply_to_ids(store, args.ids, lambda alarms, a: alarms.remove(a))
    out.write(f"Removed alarm(s) {', '.join(f'#{i}' for i in done)}.\n")


def _set_enabled(value: bool, verb: str):
    def handler(args, store, now, out) -> None:
        refreshed: list[Alarm] = []

        def change(alarms, a: Alarm) -> None:
            a.enabled = value
            if value and not a.is_recurring and a.next_trigger(now) is None:
                # Re-enabling an expired one-time alarm: move it to the next occurrence.
                today = datetime.combine(now.date(), a.clock_time)
                a.date = (today if today > now else today + timedelta(days=1)).date().isoformat()
            refreshed.append(a)

        _apply_to_ids(store, args.ids, change)
        for a in refreshed:
            when = a.next_trigger(now) if value else None
            suffix = f", next: {format_when(when, now)}" if when else ""
            out.write(f"{verb} alarm #{a.id}{suffix}.\n")
    return handler


def cmd_run(args, store, now, out) -> None:
    for name in ("snooze", "grace", "ring_timeout"):
        if getattr(args, name) <= 0:
            raise UsageError(f"--{name.replace('_', '-')} must be positive")
    ringer = TerminalRinger(snooze_minutes=args.snooze, timeout=args.ring_timeout, out=out)
    Runner(store, ringer, snooze=timedelta(minutes=args.snooze),
           grace=timedelta(minutes=args.grace), out=out).run()


COMMANDS = {
    "add": cmd_add,
    "list": cmd_list, "ls": cmd_list,
    "remove": cmd_remove, "rm": cmd_remove,
    "enable": _set_enabled(True, "Enabled"),
    "disable": _set_enabled(False, "Disabled"),
    "run": cmd_run,
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
    store = store or AlarmStore()
    try:
        COMMANDS[args.command](args, store, clock(), out)
    except (UsageError, ValueError) as exc:
        err.write(f"alarm: error: {exc}\n")
        return EXIT_USAGE
    except (LookupError, StoreError) as exc:
        err.write(f"alarm: error: {exc}\n")
        return EXIT_ERROR
    except KeyboardInterrupt:
        out.write("\nAlarm clock stopped.\n")
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
