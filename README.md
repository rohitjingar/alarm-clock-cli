# ⏰ alarm: a simple terminal alarm clock

A small, **dependency-free** Python CLI alarm clock with commands in plain English:

```text
alarm set 7:30 wake up every weekday
alarm in 10 minutes tea
alarm start
```

It has one-time and repeating alarms, names, snooze and stop, and alarms that survive
restarts. The scheduler is tested against the edge cases that make alarm clocks
unreliable.

Built as a 30-minute exercise, then hardened through manual testing and a review.
The requirements, design and plan were written **before** coding (see
[`docs/PLAN.md`](docs/PLAN.md)), and so were the revisions made after testing.

## Quick start

You need Python 3.9 or newer (macOS already has it). Nothing to install.

```bash
git clone https://github.com/rohitjingar/alarm-clock-cli.git
cd alarm-clock-cli
./alarm in 1 minute test        # set an alarm for 1 minute from now
./alarm start                   # leave this open; it rings in 1 minute
```

**Optional:** make `alarm` work from any folder (so you can drop the `./`):

```bash
ln -s "$(pwd)/alarm" /usr/local/bin/alarm   # Apple Silicon + Homebrew: /opt/homebrew/bin/alarm
```

## Commands

| What you want | Type this |
|---|---|
| Set an alarm | `alarm set 7:30` (also `7:30am`, `6pm`, `19:05`) |
| ...with a name | `alarm set 7:30 wake up` |
| ...that repeats | `alarm set 7:30 wake up every weekday` (also `every day`, `every weekend`, `every monday and friday`) |
| Ring once after a while | `alarm in 10 minutes tea` (also `1 hour`, `30 seconds`, `1 hour 30 minutes`) |
| See your alarms | `alarm list` |
| Pause / un-pause one | `alarm off 2` / `alarm on 2` |
| Delete one | `alarm delete 2` |
| Start the clock | `alarm start` (leave the window open so alarms can ring!) |
| Forgot? | `alarm` (shows examples) |

```text
$ alarm list
#  TIME   REPEATS         STATUS  NEXT RING                           NAME
1  07:30  every weekday   on      Fri 07:30 (in 18 hours 11 minutes)  wake up
2  18:00  every Mon, Fri  on      Fri 18:00 (in 1 day 4 hours)        dinner
3  13:18  once            off     -                                   tea
```

**When an alarm rings:** press **ENTER** to snooze, or type **stop** and press ENTER.
If nobody answers within a minute it snoozes by itself, up to 3 times.

Mistakes get a plain answer, not a stack trace:

```text
$ alarm set 7
Oops! Is '7' morning or evening? Say 7am or 7pm.
$ alarm set 25:00
Oops! '25:00' isn't a time on a clock. Try something like 7:30 or 19:05.
```

<details><summary>Advanced options</summary>

- `alarm start --snooze 10 --grace 5 --ring-timeout 60` sets the snooze length in
  minutes, how late an alarm may still ring (e.g. after the computer slept), and how
  long it rings before auto-snoozing.
- Alarms live in `~/.alarmclock.json`. Set `ALARMCLOCK_FILE=/path` to use another file.
- `alarm start` re-reads that file every second, so you can `alarm set` in another window.
- The original flag style still works: `add 07:30 -l NAME -r weekdays`, `add --in 25m`,
  `rm`, `enable`, `disable`, `run`. You can also use `python3 -m alarmclock` instead of
  `./alarm`, or `pip install .` for an `alarm` command.
</details>

## Design highlights

Full reasoning is in [`docs/PLAN.md`](docs/PLAN.md).

| Problem | Decision |
|---|---|
| A naive `now == "07:30"` check fires about 60 times in that minute, or **never** if the loop stalls | **Window-based scheduling**: each tick asks "did an occurrence fall in `(last_tick, now]`?" Each occurrence fires exactly once, and a stalled loop catches up |
| The computer slept through the alarm | Ring the **latest** missed occurrence if it is at most `--grace` minutes late; otherwise say **"missed"**. Never ring hours late, never stay silent |
| An alarm came due while `alarm start` wasn't running | One-time alarms that were never answered are caught up at startup (ring, or report missed). Recurring ones aren't, so a restart can't re-ring one you already stopped |
| The clock moves backwards (NTP) | Reset the window; never re-fire |
| Nobody answers | Auto-snooze after `--ring-timeout`, at most 3 times, then turn it off |
| Keys typed between rings | Thrown away when a ring starts, so a stray key can't answer an alarm it wasn't meant for |
| A crash mid-write would corrupt state | **Atomic writes** (temp file + `os.replace`). A bad file is reported, never overwritten |
| A hand-edited file has a bad value | **Validated on load**. `alarm start` keeps the last good copy and warns instead of crashing |
| "Alarm 3" after deleting alarm 3 | Alarm numbers are **never reused** |
| `alarm delete 1 99` with a typo | **All-or-nothing**: nothing changes |
| Time-based code is hard to test | Clock, sleep, I/O and the ringer are **injected**. The scheduler and the whole run loop are tested on a fake clock |
| A non-technical user | Plain-English commands and errors, and ENTER to snooze. Typing "s" snoozes: if someone meant "stop", it rings again, which is the safe way to be wrong |

### Architecture

```text
alarm               launcher script: ./alarm ...
alarmclock/
  models.py     Alarm + next_trigger(): the single source of truth for "when does it ring?"  (pure)
  timeparse.py  "7:30 am", "10 minutes", "every monday and friday" -> values, friendly errors  (pure)
  scheduler.py  window-based due/missed detection, startup catch-up, snooze state            (pure)
  store.py      JSON persistence, validation, atomic writes, ids never reused                 (I/O)
  ringer.py     terminal bell + snooze/stop prompt with timeout                               (I/O, swappable)
  runner.py     the `alarm start` loop: store -> scheduler -> ringer
  cli.py        plain-English commands; exit codes 0 ok, 1 error, 2 bad input
```

## Testing

```bash
python3 -m unittest discover -s tests -t . -v
```

There are **63 tests**, using only the standard library. They pass on Python 3.9.6
(macOS system Python) and 3.14. They cover:

- parsing of everything a person might type
- `next_trigger` across days and weeks
- every scheduler edge case in the table above
- the full run loop on a fake clock
- the real `select()`-based prompt over an OS pipe
- every command, including its error messages and exit codes

Each regression test below was checked by **temporarily undoing its fix and confirming
the test fails**.

Validated end-to-end by hand as well: set an alarm, start the clock late, snooze, type a
stray key while snoozed, then stop it. The alarm then shows `off` in `alarm list`.

## Engineering log: what testing and review caught

| # | Found by | Problem | Fix |
|---|---|---|---|
| 1 | Reading smoke-test output | `--in 25m` said "in 24m" (24m59.4s truncated) | Round up |
| 2 | Unit test | Fast typing or pasting at the ring prompt was lost: buffered `sys.stdin` plus `select()` | Read the raw fd with our own line buffer |
| 3 | **Manual testing** | An alarm due a few seconds before `alarm start` began **silently expired**. A design decision in the original plan was wrong | Startup catch-up for unanswered one-time alarms |
| 4 | **Manual testing** | A `d` typed while snoozed would dismiss the next ring instantly | Throw away typeahead when a ring starts |
| 5 | **Manual testing** | `alarm: command not found`: the docs assumed an installed command | `./alarm` launcher; docs fixed |
| 6 | **Usability feedback** | Commands were developer-style (`add -r weekdays -l ...`) | Plain-English CLI; old flags kept as aliases |
| 7 | **Code review** | After a long sleep, the *earliest* missed occurrence was taken, so a recurring alarm only 2 minutes late **never rang** | Use the latest occurrence in the window |
| 8 | **Code review** | A bad value in the alarm file passed loading, then **crashed `alarm start`** | Validate every field on load |
| 9 | **Code review** | Deleting the newest alarm let the next one **reuse its number** (and inherit its snooze) | Ids are never reused (`next_id` is stored) |
| 10 | **Code review** | `--ring-timeout` longer than `--grace` made queued alarms count as missed | Rejected with a clear message |

## Limitations and next steps

- **Foreground only.** `alarm start` must stay open (a terminal or tmux). Next step: a
  `launchd`/`systemd` service.
- **Terminal bell only.** `Ringer` is an interface, so an audio backend
  (`afplay`/`paplay`/`winsound`) can be added.
- **Snoozes live in memory.** Quitting `alarm start` while an alarm is snoozed loses the
  snooze, but a one-time alarm is caught up on the next start.
- **Naive local time.** It works like a bedside clock: no time-zone travel handling, and
  around DST changes alarms ring at the new local time.
- **No file locking.** Concurrent writers are low-risk for one user and mitigated by
  atomic, reload-modify-write updates. `fcntl` locking would close the gap.
- **Windows.** Works, but the ring prompt can't time out (no `select()` on console
  handles). The ring-timeout tests are skipped there.

## How AI was used

> _Edit this section into your own words. Reviewers explicitly care about it._

- **Requirements and design first.** Claude Code turned the one-line brief into
  scenarios, scope cuts, design decisions and a plan ([`docs/PLAN.md`](docs/PLAN.md))
  before any code was written.
- **Implementation in layers.** Pure logic came first (models, parsing, scheduler), then
  I/O at the edges, so the risky time logic could be tested deterministically.
- **Reviewing, not trusting.** Bugs 1–2 were caught by checking output and by tests.
  Bugs 3–6 came from **me actually using it**: the AI's own design decision 4 was
  wrong in practice. Bugs 7–10 came from an explicit senior-level review pass. A
  meaningless assertion the AI wrote in one test was also caught and rewritten.
- **Validation.** Every fix has a regression test, each proven to fail without its fix,
  plus manual end-to-end runs on two Python versions.
