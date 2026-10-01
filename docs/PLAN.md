# Alarm Clock CLI — Requirements, Design & Plan

Written *before* implementation. Time box: ~30 minutes.

## 1. Problem definition

"Build an alarm clock as a Python CLI." No spec. So the first job is to decide
what an alarm clock *must* do to be genuinely useful, and what to cut.

Core job of an alarm clock: **reliably alert me at a time I chose, and let me
snooze or dismiss it.** Everything else is secondary.

### Users & scenarios
1. "Wake me at 07:00 on weekdays" — recurring alarm that survives restarts.
2. "Ping me in 25 minutes" — quick one-off timer-style alarm.
3. "What alarms do I have and when is the next one?"
4. "Turn off my weekend alarm this week without deleting it."
5. When it rings: snooze for a few minutes, or dismiss.

### Functional requirements (in scope)
| # | Requirement |
|---|-------------|
| F1 | Add an alarm at an absolute time (`07:30`, `7:30am`, `19:05`) or relative (`--in 25m`, `--in 1h30m`) |
| F2 | Optional label and repeat rule: `once` (default), `daily`, `weekdays`, `weekends`, or explicit days (`mon,wed,fri`) |
| F3 | List alarms with their **next ring time** |
| F4 | Remove, enable, disable alarms by ID |
| F5 | Alarms persist across process restarts (JSON file) |
| F6 | `run` command watches alarms and rings them (terminal bell + visible banner) |
| F7 | When ringing: snooze (configurable minutes) or dismiss |
| F8 | One-time alarms auto-disable after they fire |

### Non-functional requirements
- **Zero dependencies**: Python 3.9+ standard library only (incl. tests), so a reviewer can clone & run.
- **Correctness over features**: no double-firing, no silent misses, no corrupt state file.
- **Testable**: time and I/O are injected, so scheduling logic is unit-tested without sleeping.
- Clear errors for bad input (exit code 2, human message, no stack traces).

### Explicitly out of scope (and why)
| Cut | Reason |
|-----|--------|
| Background daemon / OS service | Platform-specific (launchd/systemd/Task Scheduler); `run` in a terminal/tmux is enough for a CLI MVP |
| Audio files / cross-platform sound | Platform-specific players; terminal bell is portable. Easy extension point (`Ringer`) |
| Time zones / DST correctness | Use naive local wall-clock time, like a bedside clock. Documented limitation |
| File locking for concurrent writers | Low risk for a single user; mitigated with atomic writes + reload-before-write |
| Web UI / DB | Forbidden by the brief |

## 2. Key design decisions

1. **Edge-triggered scheduling over a time window, not "is it HH:MM now?"**
   Naive approach: every second, check `now.strftime("%H:%M") == alarm.time`.
   This fires up to 60 times in that minute, or misses entirely if the loop
   stalls. Instead each tick asks: *does the alarm's next trigger fall in
   `(last_tick, now]`?* Each occurrence fires exactly once, and a stalled loop
   still catches up.

2. **Late alarms: grace period.** If the machine slept through an alarm (laptop
   lid closed), ringing hours late is wrong, but staying silent is also wrong.
   Rule: ring if ≤ `--grace` minutes late (default 5); otherwise print a
   "missed" notice.

3. **Clock going backwards** (NTP adjustment): reset the window, fire nothing.

4. **Alarms that already passed when `run` starts do not fire** — the window
   starts at startup time.

5. **One-time alarms store a concrete date** (computed at creation: next
   occurrence of that time). Recurring alarms store weekdays. Both share one
   `next_trigger(after)` function → single source of truth used by `list` and `run`.

6. **Snooze is in-memory** in the `run` process (snoozing is only meaningful
   while it's running). Snoozing a one-time alarm keeps it alive until dismissed.

7. **Persistence**: JSON at `~/.alarmclock.json` (override with `ALARMCLOCK_FILE`).
   Atomic write (temp file + `os.replace`) so a crash never corrupts it. `run`
   reloads the file each tick, so `alarm add` in another terminal is picked up live.

8. **Ringing UX**: banner + repeated bell on a background thread; prompt
   `[s]nooze / [d]ismiss`. If unanswered for `--ring-timeout` seconds, it
   auto-snoozes (an alarm nobody answered has not done its job yet), capped at
   3 auto-snoozes so it can't nag forever.

## 3. Architecture

```
alarmclock/
  models.py     Alarm dataclass, repeat rules, next_trigger()      (pure)
  timeparse.py  parse "7:30am", "19:05", "1h30m", "mon,wed"        (pure)
  store.py      JSON load/save, atomic writes, id allocation        (I/O)
  scheduler.py  window-based due/missed detection, snooze state     (pure, clock injected)
  ringer.py     terminal ringing + snooze/dismiss prompt            (I/O, swappable)
  cli.py        argparse commands; wires everything together
```
Pure logic is separated from I/O so the risky parts (time maths, scheduling
edge cases) are covered by fast deterministic tests with a fake clock.

## 4. Implementation plan
1. `models` + `timeparse` with tests (time parsing, next_trigger across days/weeks).
2. `store` with tests (round-trip, atomic write, missing/corrupt file).
3. `scheduler` with tests (fires once, catches up after stall, grace/missed, clock backwards, snooze, one-time disable).
4. `ringer` (thin, interactive) + `cli` wiring + CLI tests via `main(argv)`.
5. Manual validation: add an alarm 1 min out, `run`, snooze, dismiss.
6. README: usage, decisions, limitations, AI usage.

## 5. Validation strategy
- Unit tests for every edge case listed in §2 (fake clock, no sleeping).
- CLI tests against a temp `ALARMCLOCK_FILE`.
- Manual end-to-end run in a real terminal (recorded).

---

## 6. Revisions after testing (added after implementation)

The plan above is kept as written. These are the places where reality disagreed with it.

- **Decision 4 was wrong.** "Alarms that already passed when `run` starts do not fire"
  turned out to mean: set `--in 30s`, start the clock 5 seconds late, and the alarm
  *silently expires*. That breaks the core requirement (no silent misses).
  *Revised:* at startup, one-time alarms that are still on (so never answered) are
  caught up: they ring if within grace, otherwise they are reported missed and turned
  off. Recurring alarms are still not caught up, because without per-occurrence history
  a restart could re-ring one the user already stopped.
- **Decision 1 needed refining.** In a window that contains several occurrences (a
  long sleep), the *latest* one decides: it may still be within grace.
- **Decision 8 (ringing UX).** Keys typed before a ring are discarded, so a stray
  keystroke can't answer an alarm.
- **CLI language (two iterations).** Usability feedback from a non-technical user:
  the flag-style CLI read as "developer-friendly". A one-line plain-English version
  (`alarm set 7:30 wake up every weekday`) was the first fix. Then came the final
  decision: **one path only, modelled on a phone's alarm app**. `alarm set` asks one
  question per field (time, AM or PM, repeat: once / every day / weekdays / weekends /
  choose days, then title), and `alarm edit N` reuses the same questions with current
  values as defaults. All times are shown in 12-hour AM/PM. "7:30" is never guessed:
  like a phone's AM/PM switch, it asks. Fewer ways to do a thing means fewer ways to
  get it wrong, and a cleaner demo.
- **Ids are never reused**, and the alarm file is **validated on load**. Both came
  from the code review.
