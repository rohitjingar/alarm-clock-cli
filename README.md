# ⏰ alarm: a phone-style alarm clock for the terminal

A small, **dependency-free** Python CLI alarm clock that works like the alarm app on
your phone. You answer a few simple questions (time, AM or PM, repeat, title) and
leave the clock running.

```text
$ alarm set
⏰ New alarm   (press Ctrl+C to cancel)

  Time (like 7:30): 6:45
  AM or PM (am/pm): am
  Repeat:
     1) Only once
     2) Every day
     3) Weekdays (Mon to Fri)
     4) Weekends (Sat and Sun)
     5) Choose days
  Pick 1-5 (type a number): 5
     1) Mon   2) Tue   3) Wed   4) Thu   5) Fri   6) Sat   7) Sun
  Which days (like 1 3 5): 1 3 5
  Title (optional, press ENTER to skip): Gym

⏰ Alarm 1 "Gym" set: 6:45 AM, every Mon, Wed, Fri.
   It will ring Fri 6:45 AM (in 17 hours 16 minutes).
   Remember: run `alarm start` and leave it open, or it can't ring!
```

Built as a 30-minute exercise, then hardened through manual testing, usability feedback
and a code review. The requirements, design and plan were written **before** coding (see
[`docs/PLAN.md`](docs/PLAN.md)), and so were the revisions made after testing.

## Quick start

You need Python 3.9 or newer (macOS already has it). Nothing to install.

```bash
git clone https://github.com/rohitjingar/alarm-clock-cli.git
cd alarm-clock-cli
./alarm set      # answer the questions; pick a time 2 minutes from now
./alarm start    # leave this open; it rings at that time
```

**Optional:** make `alarm` work from any folder (so you can drop the `./`):

```bash
ln -s "$(pwd)/alarm" /usr/local/bin/alarm   # Apple Silicon + Homebrew: /opt/homebrew/bin/alarm
```

## Commands

There's one easy way to do each thing:

| What you want | Type this |
|---|---|
| Set a new alarm | `alarm set` (it asks: time, AM or PM, repeat, title) |
| See your alarms | `alarm list` (sorted by time, like a phone) |
| Change an alarm | `alarm edit 2` (same questions; ENTER keeps the current value) |
| Pause / un-pause one | `alarm off 2` / `alarm on 2` |
| Delete one | `alarm delete 2` |
| Start the clock | `alarm start` (leave the window open so alarms can ring!) |
| Forgot? | `alarm` |

```text
$ alarm list
#  TIME     REPEATS              STATUS  NEXT RING                             TITLE
2  6:45 AM  every Mon, Wed, Fri  on      Fri 6:45 AM (in 17 hours 16 minutes)  Gym
1  1:29 PM  once                 off     -                                     Demo
```

**When an alarm rings:** press **ENTER** to snooze, or type **stop** and press ENTER.
If nobody answers within a minute it snoozes by itself, up to 3 times.

**Answering the questions:**

- Time: `7:30` then pick AM or PM. You can also type `7:30pm` or `19:30` and the
  AM/PM question is skipped.
- Days: numbers (`1 3 5`) or names (`mon wed fri`).
- A wrong answer gets a short hint and the same question again. Ctrl+C cancels
  without saving anything.

<details><summary>Advanced options</summary>

- `alarm start --snooze 10 --grace 5 --ring-timeout 60` sets the snooze length in
  minutes, how late an alarm may still ring (e.g. after the computer slept), and how
  long it rings before auto-snoozing.
- Alarms live in `~/.alarmclock.json`. Set `ALARMCLOCK_FILE=/path` to use another file.
- `alarm start` re-reads that file every second, so you can `alarm set` in another window.
- The questions read plain lines, so they can be scripted:
  `printf "7:30\nam\n2\nWake up\n" | alarm set`.
- You can also use `python3 -m alarmclock` instead of `./alarm`, or `pip install .`.
</details>

## Design highlights

Full reasoning is in [`docs/PLAN.md`](docs/PLAN.md).

| Problem | Decision |
|---|---|
| A naive `now == "07:30"` check fires about 60 times in that minute, or **never** if the loop stalls | **Window-based scheduling**: each tick asks "did an occurrence fall in `(last_tick, now]`?" Each occurrence fires exactly once, and a stalled loop catches up |
| The computer slept through the alarm | Ring the **latest** missed occurrence if it is at most `--grace` minutes late; otherwise say **"missed"**. Never ring hours late, never stay silent |
| An alarm came due while `alarm start` wasn't running | One-time alarms that were never answered are caught up at startup (ring, or report missed). Recurring ones aren't, so a restart can't re-ring one you already stopped |
| "7:30": morning or evening? | **Never guess.** Like a phone's AM/PM switch, it asks. `19:30`, `07:30` and `7:30pm` are already clear, so they skip the question |
| Many ways to do one thing confuses people | **One path**: a phone-style question per field, the same flow for `set` and `edit` |
| The clock moves backwards (NTP) | Reset the window; never re-fire |
| Nobody answers | Auto-snooze after `--ring-timeout`, at most 3 times, then turn it off |
| Keys typed between rings | Thrown away when a ring starts, so a stray key can't answer an alarm it wasn't meant for |
| `alarm start` changes the file while you answer questions | The file is loaded **after** the questions (reload-modify-write), so nothing is overwritten |
| A crash mid-write would corrupt state | **Atomic writes** (temp file + `os.replace`). A bad file is reported, never overwritten |
| A hand-edited file has a bad value | **Validated on load**. `alarm start` keeps the last good copy and warns instead of crashing |
| "Alarm 3" after deleting alarm 3 | Alarm numbers are **never reused** |
| Time-based code is hard to test | Clock, sleep, keyboard, output and the ringer are **injected**. The scheduler, the run loop and the question flow are all tested without real waiting or typing |

### Architecture

```text
alarm               launcher script: ./alarm ...
alarmclock/
  cli.py        commands -> one handler each; shared Context (store, clock, I/O); exit codes
  wizard.py     the phone-style questions: time -> AM/PM -> repeat -> title (set + edit)
  models.py     Alarm / AlarmDraft + next_trigger(): "when does it ring?"                 (pure)
  timeparse.py  "7:30", "7:30pm", "19:30", "mon wed fri" -> values; AM/PM ambiguity      (pure)
  scheduler.py  window-based due/missed detection, startup catch-up, snooze state        (pure)
  store.py      JSON persistence, validation, atomic writes, ids never reused             (I/O)
  ringer.py     terminal bell + snooze/stop prompt with timeout                           (I/O, swappable)
  runner.py     the `alarm start` loop: store -> scheduler -> ringer
  fmt.py        12-hour times ("7:30 AM") and "in 9 hours 3 minutes"
```

Both `set` and `edit` produce an `AlarmDraft` from the same questions, then go through
one save path. Input and output are passed in, so the question flow is tested by
"typing" answers in the tests.

## Testing

```bash
python3 -m unittest discover -s tests -t . -v
```

There are **67 tests**, using only the standard library. They pass on Python 3.9.6
(macOS system Python) and 3.14. They cover:

- the question flow (every repeat option, AM/PM, 12 o'clock, wrong answers asked
  again, cancelling, editing with ENTER-to-keep)
- `next_trigger` across days and weeks
- every scheduler edge case in the table above
- the run loop on a fake clock
- the real `select()`-based ring prompt over an OS pipe
- every command's messages and exit codes

Each regression test was checked by **temporarily undoing its fix and confirming the test
fails**.

## Engineering log: what testing and review caught

| # | Found by | Problem | Fix |
|---|---|---|---|
| 1 | Reading smoke-test output | `--in 25m` said "in 24m" (24m59.4s truncated) | Round up |
| 2 | Unit test | Fast typing or pasting at the ring prompt was lost: buffered `sys.stdin` plus `select()` | Read the raw fd with our own line buffer |
| 3 | **Manual testing** | An alarm due a few seconds before `alarm start` began **silently expired**. A design decision in the original plan was wrong | Startup catch-up for unanswered one-time alarms |
| 4 | **Manual testing** | A `d` typed while snoozed would dismiss the next ring instantly | Throw away typeahead when a ring starts |
| 5 | **Manual testing** | `alarm: command not found`: the docs assumed an installed command | `./alarm` launcher; docs fixed |
| 6 | **Code review** | After a long sleep, the *earliest* missed occurrence was taken, so a recurring alarm only 2 minutes late **never rang** | Use the latest occurrence in the window |
| 7 | **Code review** | A bad value in the alarm file passed loading, then **crashed `alarm start`** | Validate every field on load |
| 8 | **Code review** | Deleting the newest alarm let the next one **reuse its number** (and inherit its snooze) | Ids are never reused (`next_id` is stored) |
| 9 | **Code review** | `--ring-timeout` longer than `--grace` made queued alarms count as missed | Rejected with a clear message |
| 10 | **Usability feedback** | The flag style (`add 07:30 -r weekdays -l ...`), then even one-line English, still felt "developer-friendly". 24-hour times confused people | **One phone-style path**: a question per field, AM/PM everywhere, `alarm edit` reuses the same questions |
| 11 | **Code review** | The current time was read before the questions, so a slow answer could land a one-time alarm on the wrong day; also a split-second race in the confirmation | Read the clock after answering, once per save |

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
- **Directing the product, not just the code.** After using it myself, I steered the
  interface twice: from flags to plain English, then to a single phone-style flow with
  AM/PM, because the target user is someone who has never used a terminal.
- **Reviewing, not trusting.** Bugs were caught at every stage: reading output, tests,
  **my own manual testing** (the AI's own design decision 4 was wrong in practice), and
  explicit senior-level review passes. A meaningless assertion the AI wrote in one
  test was also caught and rewritten.
- **Validation.** Every fix has a regression test, each proven to fail without its fix,
  plus manual end-to-end runs on two Python versions.
