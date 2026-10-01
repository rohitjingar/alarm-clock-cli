"""The noise an alarm makes.

Every sound has one method, ``play(stop)``: make the sound once, and return early
if ``stop`` is set (the person answered). The ringer calls it in a loop while the
alarm rings. Real audio uses the OS's own player (no dependencies); if that is
missing or fails, we fall back to the terminal bell so an alarm is never silent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import threading
from typing import Callable, Optional, Sequence, TextIO

MAC_SOUNDS = ["/System/Library/Sounds/Glass.aiff", "/System/Library/Sounds/Hero.aiff",
              "/System/Library/Sounds/Ping.aiff"]
LINUX_SOUNDS = [
    ("paplay", "/usr/share/sounds/freedesktop/stereo/alarm-clock-elapsed.oga"),
    ("paplay", "/usr/share/sounds/freedesktop/stereo/bell.oga"),
    ("aplay", "/usr/share/sounds/alsa/Front_Center.wav"),
]


class TerminalBell:
    """The terminal's beep. Always available, but many terminals mute it."""

    name = "terminal beep"

    def __init__(self, out: TextIO, interval: float = 1.5):
        self.out = out
        self.interval = interval

    def play(self, stop: threading.Event) -> None:
        self.out.write("\a")
        self.out.flush()
        stop.wait(self.interval)


class CommandSound:
    """Plays a sound file with an OS command (afplay / paplay / aplay)."""

    def __init__(self, argv: Sequence[str], name: str, fallback: TerminalBell,
                 gap: float = 0.3):
        self.argv = list(argv)
        self.name = name
        self.fallback = fallback
        self.gap = gap
        self._broken = False

    def play(self, stop: threading.Event) -> None:
        if self._broken:
            self.fallback.play(stop)
            return
        try:
            proc = subprocess.Popen(self.argv, stdout=subprocess.DEVNULL,
                                    stderr=subprocess.DEVNULL)
        except OSError:
            self._broken = True  # player vanished: beep instead, never go silent
            self.fallback.play(stop)
            return
        while proc.poll() is None:
            if stop.wait(0.05):
                proc.terminate()  # answered mid-sound: go quiet right away
                proc.wait()
                return
        if proc.returncode != 0:
            self._broken = True  # e.g. no audio device; the beep is better than nothing
        stop.wait(self.gap)


class WindowsSound:
    name = "Windows alert"

    def __init__(self, interval: float = 1.0):
        self.interval = interval

    def play(self, stop: threading.Event) -> None:
        import winsound  # Windows-only module

        winsound.MessageBeep(winsound.MB_ICONHAND)
        stop.wait(self.interval)


def default_sound(
    out: TextIO,
    *,
    platform: str = sys.platform,
    which: Callable[[str], Optional[str]] = shutil.which,
    exists: Callable[[str], bool] = os.path.exists,
):
    """The best sound this computer can make. Lookups are injectable for tests."""
    bell = TerminalBell(out)
    if platform == "darwin" and which("afplay"):
        for path in MAC_SOUNDS:
            if exists(path):
                name = os.path.splitext(os.path.basename(path))[0]
                return CommandSound(["afplay", path], f"{name} (macOS sound)", bell)
    elif platform.startswith("linux"):
        for player, path in LINUX_SOUNDS:
            if which(player) and exists(path):
                return CommandSound([player, path], f"{os.path.basename(path)} ({player})", bell)
    elif platform == "win32":
        return WindowsSound()
    return bell
