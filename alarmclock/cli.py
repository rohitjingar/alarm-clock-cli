"""Command-line interface. One easy path, like the alarm app on a phone:

    alarm set          asks: time, AM or PM, repeat, title
    alarm list         alarm edit 2      alarm off 2 / alarm on 2      alarm delete 2
    alarm start        the clock itself -- leave it open so alarms can ring
"""

from __future__ import annotations

import argparse
import re
import sys
import threading
from dataclasses import dataclass
from datetime import datetime, time, timedelta
from typing import Callable, Optional, Sequence, TextIO

from . import __version__
from .fmt import format_when
from .models import Alarm, AlarmDraft
from .ringer import TerminalRinger
from .runner import Runner
from .sound import TerminalBell, default_sound
from .store import AlarmStore, StoreError
from .wizard import Cancelled, Prompter, ask_alarm

EXIT_OK, EXIT_ERROR, EXIT_USAGE = 0, 1, 2

CHEAT_SHEET = """\
⏰ alarm: a simple alarm clock

  alarm set        set a new alarm (it asks you: time, AM or PM, repeat, title)
  alarm list       see all your alarms
  alarm edit 2     change alarm number 2
  alarm off 2      pause alarm number 2   (alarm on 2 turns it back on)
  alarm delete 2   delete alarm number 2
  alarm start      start the clock - leave it open so your alarms can ring!
  alarm sound      play the alarm sound, to check your volume

When an alarm rings: press ENTER to snooze, or type stop and press ENTER.
"""


class UsageError(Exception):
    pass


@dataclass
class Context:
    store: AlarmStore
    clock: Callable[[], datetime]  # read *after* questions are answered, not before
    out: TextIO
    prompter: Prompter


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
    p = FriendlyParser(prog="alarm", usage="alarm COMMAND", description=CHEAT_SHEET,
                       formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    sub = p.add_subparsers(dest="command", metavar="COMMAND", title="commands")

    sub.add_parser("set", help="set a new alarm (asks: time, AM or PM, repeat, title)")
    sub.add_parser("list", help="see all your alarms")
    sub.add_parser("edit", help="change an alarm: alarm edit 2").add_argument(
        "id", metavar="NUMBER")
    for name, help_ in (("off", "pause an alarm: alarm off 2"),
                        ("on", "turn an alarm back on: alarm on 2"),
                        ("delete", "delete an alarm: alarm delete 2")):
        sub.add_parser(name, help=help_).add_argument("ids", nargs="+", metavar="NUMBER")

    start = sub.add_parser("start", help="start the clock so alarms can ring (leave it open)")
    start.add_argument("--snooze", type=int, default=5, metavar="MINUTES",
                       help="how long a snooze lasts (default 5)")
    start.add_argument("--grace", type=int, default=5, metavar="MINUTES",
                       help="still ring alarms up to this late, e.g. after the computer "
                            "slept (default 5)")
    start.add_argument("--ring-timeout", type=int, default=60, metavar="SECONDS",
                       help="if nobody answers for this long, snooze (default 60)")

    sub.add_parser("sound", help="play the alarm sound, to check your volume")
    sub.add_parser("help", help="show what I can do")
    return p


def _next_date_for(clock: time, now: datetime):
    """One-time alarm date: today if that time is still ahead, else tomorrow."""
    today = datetime.combine(now.date(), clock)
    return (today if today > now else today + timedelta(days=1)).date()


def _apply_draft(alarm: Alarm, draft: AlarmDraft, now: datetime) -> None:
    alarm.time = draft.clock.isoformat()
    alarm.days = list(draft.days)
    alarm.label = draft.label.strip()
    alarm.date = None if draft.days else _next_date_for(draft.clock, now).isoformat()
    alarm.enabled = True  # like a phone: setting or editing an alarm switches it on


def _confirm(ctx: Context, alarm: Alarm, verb: str, now: datetime) -> None:
    name = f' "{alarm.label}"' if alarm.label else ""
    ctx.out.write(
        f"\n⏰ Alarm {alarm.id}{name} {verb}: {alarm.display_time()}, {alarm.describe_repeat()}.\n"
        f"   It will ring {format_when(alarm.next_trigger(now), now)}.\n"
        "   Remember: run `alarm start` and leave it open, or it can't ring!\n"
    )


def cmd_set(args, ctx: Context) -> None:
    ctx.prompter.say("⏰ New alarm   (press Ctrl+C to cancel)\n")
    draft = ask_alarm(ctx.prompter)
    # Load only after the questions, so we never overwrite changes `alarm start`
    # made while the person was answering.
    alarms = ctx.store.load()
    now = ctx.clock()  # one reading for both scheduling and the message
    alarm = Alarm(id=ctx.store.next_id(alarms), time=draft.clock.isoformat())
    _apply_draft(alarm, draft, now)
    alarms.append(alarm)
    ctx.store.save(alarms)
    _confirm(ctx, alarm, "set", now)


def cmd_edit(args, ctx: Context) -> None:
    alarm_id = _parse_ids([args.id])[0]
    current = next((a for a in ctx.store.load() if a.id == alarm_id), None)
    if current is None:
        raise LookupError(_no_such_alarm([alarm_id]))
    ctx.prompter.say(f"✏️  Editing alarm {alarm_id}. Press ENTER to keep what's in "
                     "[brackets].   (Ctrl+C to cancel)\n")
    draft = ask_alarm(ctx.prompter, AlarmDraft(current.clock_time, current.days, current.label))
    now = ctx.clock()
    edited: list[Alarm] = []

    def change(alarms, a: Alarm) -> None:
        _apply_draft(a, draft, now)
        edited.append(a)

    _apply_to_ids(ctx.store, [alarm_id], change)
    _confirm(ctx, edited[0], "updated", now)


def cmd_list(args, ctx: Context) -> None:
    alarms = ctx.store.load()
    if not alarms:
        ctx.out.write("You don't have any alarms yet. Set one with: alarm set\n")
        return
    now = ctx.clock()
    rows = [("#", "TIME", "REPEATS", "STATUS", "NEXT RING", "TITLE")]
    for a in sorted(alarms, key=lambda a: (a.clock_time, a.id)):  # by time, like a phone
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
        ctx.out.write("  ".join(cell.ljust(w) for cell, w in zip(r, widths)).rstrip() + "\n")


def _parse_ids(raw: Sequence[str]) -> list[int]:
    ids = []
    for item in raw:
        item = item.lstrip("#")
        if not item.isdigit():
            raise UsageError("Pick alarms by their number, like: alarm off 2 "
                             "(see the numbers with: alarm list)")
        ids.append(int(item))
    return ids


def _no_such_alarm(ids: Sequence[int]) -> str:
    return (f"There's no alarm number {', '.join(map(str, ids))}. "
            "See your alarms with: alarm list")


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
        raise LookupError(_no_such_alarm(missing))


def cmd_delete(args, ctx: Context) -> None:
    ids = _parse_ids(args.ids)
    _apply_to_ids(ctx.store, ids, lambda alarms, a: alarms.remove(a))
    ctx.out.write(f"🗑  Deleted alarm {', '.join(map(str, ids))}.\n")


def cmd_off(args, ctx: Context) -> None:
    ids = _parse_ids(args.ids)

    def turn_off(alarms, a: Alarm) -> None:
        a.enabled = False

    _apply_to_ids(ctx.store, ids, turn_off)
    for i in ids:
        ctx.out.write(f"🔕 Alarm {i} is off. Turn it back on with: alarm on {i}\n")


def cmd_on(args, ctx: Context) -> None:
    ids = _parse_ids(args.ids)
    now = ctx.clock()
    turned_on: list[Alarm] = []

    def turn_on(alarms, a: Alarm) -> None:
        a.enabled = True
        if not a.is_recurring and a.next_trigger(now) is None:
            # An old one-time alarm: move it to the next time that clock time comes round.
            a.date = _next_date_for(a.clock_time, now).isoformat()
        turned_on.append(a)

    _apply_to_ids(ctx.store, ids, turn_on)
    for a in turned_on:
        ctx.out.write(f"🔔 Alarm {a.id} is on. It will ring {format_when(a.next_trigger(now), now)}.\n")


def cmd_start(args, ctx: Context) -> None:
    for name in ("snooze", "grace", "ring_timeout"):
        if getattr(args, name) <= 0:
            raise UsageError(f"--{name.replace('_', '-')} must be more than 0")
    if args.ring_timeout >= args.grace * 60:
        # Alarms that come due while another is ringing are checked afterwards;
        # a ring longer than the grace period would make them count as missed.
        raise UsageError("--ring-timeout must be shorter than --grace")
    sound = default_sound(ctx.out)
    ctx.out.write(f"🔊 Sound: {sound.name}{_BELL_TIP if isinstance(sound, TerminalBell) else ''}\n")
    ringer = TerminalRinger(snooze_minutes=args.snooze, timeout=args.ring_timeout,
                            sound=sound, out=ctx.out)
    runner = Runner(ctx.store, ringer, snooze=timedelta(minutes=args.snooze),
                    grace=timedelta(minutes=args.grace), clock=ctx.clock, out=ctx.out)
    try:
        runner.run()
    except KeyboardInterrupt:
        ctx.out.write("\n👋 Alarm clock stopped. Alarms can't ring until you run: alarm start\n")


_BELL_TIP = " (can't hear it? turn on your terminal's bell sound)"


def cmd_sound(args, ctx: Context) -> None:
    """Like previewing a ringtone: play the alarm sound once to check the volume."""
    sound = default_sound(ctx.out)
    tip = _BELL_TIP if isinstance(sound, TerminalBell) else ""
    ctx.out.write(f"🔊 Playing the alarm sound: {sound.name}{tip}\n")
    sound.play(threading.Event())
    ctx.out.write("   That's how your alarms will sound.\n")


COMMANDS = {
    "set": cmd_set,
    "edit": cmd_edit,
    "list": cmd_list,
    "off": cmd_off,
    "on": cmd_on,
    "delete": cmd_delete,
    "start": cmd_start,
    "sound": cmd_sound,
}


def main(
    argv: Optional[Sequence[str]] = None,
    *,
    store: Optional[AlarmStore] = None,
    clock: Callable[[], datetime] = datetime.now,
    inp: Optional[TextIO] = None,
    out: Optional[TextIO] = None,
    err: Optional[TextIO] = None,
) -> int:
    inp = inp or sys.stdin
    out = out or sys.stdout
    err = err or sys.stderr
    args = build_parser().parse_args(argv)
    if args.command in (None, "help"):
        out.write(CHEAT_SHEET)
        return EXIT_OK
    ctx = Context(store or AlarmStore(), clock, out, Prompter(inp, out))
    try:
        COMMANDS[args.command](args, ctx)
    except Cancelled:
        out.write("\nCancelled. Nothing was changed.\n")
        return EXIT_ERROR
    except (UsageError, ValueError) as exc:
        err.write(f"Oops! {exc}\n")
        return EXIT_USAGE
    except (LookupError, StoreError) as exc:
        err.write(f"Oops! {exc}\n")
        return EXIT_ERROR
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
