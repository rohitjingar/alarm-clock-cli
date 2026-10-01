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
        title = f"ALARM #{alarm.id}  {alarm.display_time()}"
        if alarm.label:
            title += f"  -  {alarm.label}"
        if snoozed:
            title += "  (snoozed)"
        bar = "=" * max(50, len(title) + 4)
        self.out.write(
            f"\n\a{bar}\n  {title}\n{bar}\n"
            f"  [s]nooze {self.snooze_minutes} min  /  [d]ismiss   (Enter = snooze)\n> "
        )
        self.out.flush()

        self._pending = ""  # leftover keystrokes from an earlier ring must not answer this one
        stop = threading.Event()
        bell = threading.Thread(target=self._bell_loop, args=(stop,), daemon=True)
        bell.start()
        try:
            return self._prompt()
        finally:
            stop.set()
            bell.join()

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
            if answer in ("", "s", "snooze"):
                return Action.SNOOZE
            if answer in ("d", "dismiss", "stop", "x"):
                return Action.DISMISS
            self.out.write("  type 's' to snooze or 'd' to dismiss\n> ")
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
