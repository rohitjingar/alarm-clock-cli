"""Makes noise in the terminal and asks the user to snooze or dismiss.

Kept deliberately thin and behind a one-method interface (`ring`) so the run
loop can be tested with a fake, and so a real audio backend can be swapped in.
"""

from __future__ import annotations

import codecs
import os
import select
import sys
import threading
import time
from enum import Enum
from typing import Optional, TextIO

from .fmt import plural
from .models import Alarm


class Action(Enum):
    SNOOZE = "snooze"
    DISMISS = "dismiss"
    TIMEOUT = "timeout"  # nobody answered


class TerminalRinger:
    def __init__(
        self,
        snooze_minutes: int,
        timeout: float = 60.0,
        bell_interval: float = 1.5,
        out: Optional[TextIO] = None,
        inp: Optional[TextIO] = None,
    ):
        self.snooze_minutes = snooze_minutes
        self.timeout = timeout
        self.bell_interval = bell_interval
        self.out = out or sys.stdout
        self.inp = inp or sys.stdin
        # We read the raw fd ourselves: select() can't see data already sitting in
        # TextIOWrapper's buffer, so mixing it with inp.readline() loses typeahead.
        self._pending = ""
        self._decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

    def ring(self, alarm: Alarm, snoozed: bool = False) -> Action:
        name = alarm.label.upper() if alarm.label else "ALARM"
        details = f"Alarm #{alarm.id} - {alarm.display_time()}"
        if snoozed:
            details += " - snoozed"
        bar = "=" * 52
        self.out.write(
            f"\n\a{bar}\n  ⏰ ⏰ ⏰   {name}   ⏰ ⏰ ⏰\n  {details}\n{bar}\n"
            f"  Press ENTER to snooze for {plural(self.snooze_minutes, 'minute')}.\n"
            "  Type  stop  and press ENTER to turn it off.\n> "
        )
        self.out.flush()

        self._discard_typeahead()
        stop = threading.Event()
        bell = threading.Thread(target=self._bell_loop, args=(stop,), daemon=True)
        bell.start()
        try:
            return self._prompt()
        finally:
            stop.set()
            bell.join()

    def _discard_typeahead(self) -> None:
        """Keystrokes typed before this ring (e.g. a 'd' typed while snoozed) sit in
        our buffer or the terminal's input queue; they must not answer this alarm."""
        self._pending = ""
        if os.name == "nt":
            return
        fd = self.inp.fileno()
        while select.select([fd], [], [], 0)[0]:
            if not os.read(fd, 1024):
                break  # EOF

    def _bell_loop(self, stop: threading.Event) -> None:
        while not stop.wait(self.bell_interval):
            self.out.write("\a")
            self.out.flush()

    def _prompt(self) -> Action:
        deadline = time.monotonic() + self.timeout
        while True:
            line = self._readline(deadline)
            if line is None:
                self.out.write("\n")
                return Action.TIMEOUT
            answer = line.strip().lower()
            if answer in ("", "s", "snooze", "z", "zz"):
                # "s" stays snooze: if someone meant "stop", the alarm rings again,
                # which is the safe way to be wrong.
                return Action.SNOOZE
            if answer in ("stop", "d", "dismiss", "x", "off", "q", "quit", "done"):
                return Action.DISMISS
            self.out.write("  Press ENTER to snooze, or type stop to turn it off.\n> ")
            self.out.flush()

    def _readline(self, deadline: float) -> Optional[str]:
        """A line of input, or None if the deadline passes (or stdin is closed)."""
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            return None
        if os.name == "nt":
            # select() does not work on Windows console handles; block instead.
            return self.inp.readline() or None
        fd = self.inp.fileno()
        while "\n" not in self._pending:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            ready, _, _ = select.select([fd], [], [], remaining)
            if not ready:
                return None
            chunk = os.read(fd, 1024)
            if not chunk:
                # EOF (stdin closed / not interactive): keep ringing until timeout.
                time.sleep(max(0.0, deadline - time.monotonic()))
                return None
            self._pending += self._decoder.decode(chunk)
        line, self._pending = self._pending.split("\n", 1)
        return line + "\n"
