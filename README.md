# ⏰ alarm — a terminal alarm clock

A small, **dependency-free** Python CLI alarm clock: one-time and recurring alarms,
labels, snooze/dismiss, persistence across restarts, and a scheduler that is
tested against the edge cases that make alarm clocks unreliable.

Built as a 30-minute exercise. Requirements, design decisions and the implementation
plan were written **before** coding: see [`docs/PLAN.md`](docs/PLAN.md).

## Quick start

Requires Python 3.9+. No third-party packages, not even for tests.

```bash
git clone https://github.com/rohitjingar/alarm-clock-cli.git && cd alarm-clock-cli
python3 -m alarmclock add --in 1m -l "Try me"   # or: pip install . && alarm ...
python3 -m alarmclock run                        # leave running; it rings in 1 minute
```

## Usage

```text
alarm add 07:30 -r weekdays -l "Wake up"   # recurring
alarm add 6:45pm                           # one-time: today if still ahead, else tomorrow
alarm add --in 25m -l Tea                  # relative (also 1h30m, 45s)
alarm add 09:00 -r mon,wed,fri             # specific days (also: daily, weekends)
alarm list                                 # alias: ls
alarm disable 2 / alarm enable 2           # keep it, but switch off/on
alarm remove 2 3                           # alias: rm
alarm run [--snooze 5] [--grace 5] [--ring-timeout 60]
```

```text
$ alarm list
ID  TIME   REPEAT    STATUS  NEXT                    LABEL
1   07:30  weekdays  on      Fri 07:30 (in 18h 38m)  Wake up
2   18:45  Mon,Wed   off     -
3   13:16  once      on      Thu 13:16 (in 25m)      Tea
```

When an alarm goes off, `run` shows a banner and rings the terminal bell until you
answer: **`s`/Enter = snooze**, **`d` = dismiss**. One-time alarms switch themselves
off once dismissed; recurring ones stay on.

Alarms live in `~/.alarmclock.json` (override with `ALARMCLOCK_FILE=/path`).
`run` reloads the file every second, so you can `alarm add` from another terminal
while it's running.

## Design highlights

Full reasoning is in [`docs/PLAN.md`](docs/PLAN.md). The decisions that matter most:

| Problem | Decision |
|---|---|
| A naive `now == "07:30"` check fires ~60× in that minute, or **never** if the loop stalls | **Window-based scheduling**: each tick asks "did an occurrence fall in `(last_tick, now]`?" Every occurrence fires exactly once and a stalled loop catches up |
| Laptop asleep through the alarm | Ring if ≤ `--grace` min late; otherwise print **"missed"** rather than ringing hours late or staying silent |
| Clock moves backwards (NTP) | Reset the window; never re-fire |
| Nobody answers | Auto-snooze after `--ring-timeout`, max 3 times, then give up |
| Crash mid-write corrupts state | **Atomic writes** (temp file + `os.replace`). A corrupt file is reported, never silently overwritten |
| Alarm file edited badly while `run` is going | Keep ringing with the last good copy and warn |
| `rm 1 99` with a typo | **All-or-nothing**: nothing changes and you get exit code 1 |
| Testability of time-based code | Clock, sleep, I/O and the ringer are **injected**. The scheduler and run loop are tested with a fake clock (no `sleep()` in tests) |

### Architecture

```text
alarmclock/
  models.py     Alarm + next_trigger(): single source of truth for "when does it ring?"  (pure)
  timeparse.py  "7:30am", "1h30m", "mon,wed" → typed values, friendly errors             (pure)
  scheduler.py  window-based due/missed detection, snooze state                          (pure)
  store.py      JSON persistence, atomic writes, reload-modify-write                     (I/O)
  ringer.py     terminal bell + snooze/dismiss prompt with timeout                       (I/O, swappable)
  runner.py     the `run` loop that wires store → scheduler → ringer
  cli.py        argparse commands, exit codes (0 ok, 1 error, 2 bad input)
```

## Testing

```bash
python3 -m unittest discover -s tests -t . -v
```

The 44 tests cover time and repeat parsing, `next_trigger` across days and weeks,
and every scheduler edge case in the table above. They also cover the full run loop
(snooze, then dismiss, then auto-disable; auto-snooze limit; alarms added while
running; a corrupt file mid-run), the real `select()`-based prompt over an OS pipe,
and the CLI commands with their exit codes.

Validated manually end-to-end as well: `add --in 3s` → `run --snooze 1` → ring →
snooze → re-ring (marked *snoozed*) → dismiss → alarm shows `off` in `list`.

## Bugs caught during review and testing

These came up while building and are worth showing, since they're why the tests exist:

1. **Relative time display off by one.** `--in 25m` printed "in 24m", because 24m59.4s
   was truncated. Fixed by rounding up, with a regression test.
2. **Lost keystrokes at the ring prompt.** A test feeding `"huh\ns\n"` timed out.
   The cause: `sys.stdin` is buffered, so `readline()` slurped both lines and
   `select()` then reported the fd empty. Fixed by reading the raw fd with an
   incremental UTF-8 decoder and our own line buffer.
3. **Stale input answering the next alarm.** After fix #2, extra keystrokes from an
   earlier ring could silently dismiss a later one. The buffer is now cleared on each ring.

## Limitations & what I'd do next

- **Foreground only.** `run` must be left open (a terminal or tmux). Next step: a
  `launchd`/`systemd` unit or a detached daemon with a PID file.
- **Terminal bell only.** `Ringer` is an interface, so an audio backend
  (`afplay`/`paplay`/`winsound`) can be dropped in.
- **Naive local time.** Times are wall-clock time like a bedside clock. No time-zone
  travel handling; around DST changes, alarms ring at the new local time.
- **No file locking.** Concurrent writers are low-risk for one user and mitigated by
  atomic, reload-modify-write updates. `fcntl` locking would close the gap.
- **Windows.** Works, but the ring prompt can't time out (no `select()` on console
  handles). Ring-timeout tests are skipped there.

## How AI was used

> _Edit this section into your own words. Reviewers explicitly care about it._

- **Requirements & design first.** I used Claude Code to turn the one-line brief
  into scenarios, in/out-of-scope requirements, design decisions and a plan
  ([`docs/PLAN.md`](docs/PLAN.md)) before writing any code. I kept scope to a
  reliable core over feature count.
- **Implementation in layers.** Pure logic came first (models, parsing, scheduler),
  then I/O at the edges, so the risky time logic could be tested deterministically.
- **Reviewing the output, not trusting it.** I checked the smoke-test output by eye
  and caught bug #1. A test caught bug #2. Reviewing that fix exposed bug #3. A
  meaningless assertion the AI wrote in one test was also caught and rewritten.
- **Validation.** Unit tests plus a real end-to-end run, before calling it done.
