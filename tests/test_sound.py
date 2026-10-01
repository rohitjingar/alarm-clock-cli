import io
import sys
import threading
import time
import unittest
from unittest import mock

from alarmclock.cli import EXIT_OK, main
from alarmclock.sound import CommandSound, TerminalBell, WindowsSound, default_sound


def bell():
    out = io.StringIO()
    return TerminalBell(out, interval=0), out


class CommandSoundTest(unittest.TestCase):
    def test_answering_cuts_the_sound_off_immediately(self):
        fallback, _ = bell()
        long_sound = CommandSound([sys.executable, "-c", "import time; time.sleep(10)"],
                                  "long", fallback)
        stop = threading.Event()
        threading.Timer(0.2, stop.set).start()
        started = time.monotonic()
        long_sound.play(stop)
        self.assertLess(time.monotonic() - started, 2.0)

    def test_missing_player_falls_back_to_the_beep(self):
        fallback, out = bell()
        sound = CommandSound(["no-such-audio-player-xyz"], "missing", fallback)
        sound.play(threading.Event())
        sound.play(threading.Event())
        self.assertEqual(out.getvalue(), "\a\a")  # never silent

    def test_failing_player_falls_back_from_then_on(self):
        fallback, out = bell()
        sound = CommandSound([sys.executable, "-c", "import sys; sys.exit(1)"], "broken",
                             fallback, gap=0)
        sound.play(threading.Event())  # fails (e.g. no audio device)
        sound.play(threading.Event())  # so this one beeps
        self.assertEqual(out.getvalue(), "\a")


class DefaultSoundTest(unittest.TestCase):
    def pick(self, platform, programs=(), files=()):
        return default_sound(io.StringIO(), platform=platform,
                             which=lambda p: f"/usr/bin/{p}" if p in programs else None,
                             exists=lambda f: any(f.endswith(name) for name in files))

    def test_mac_uses_afplay_with_a_system_sound(self):
        sound = self.pick("darwin", ["afplay"], ["Glass.aiff"])
        self.assertEqual(sound.argv, ["afplay", "/System/Library/Sounds/Glass.aiff"])
        self.assertEqual(sound.name, "Glass (macOS sound)")

    def test_linux_uses_the_freedesktop_alarm_sound(self):
        sound = self.pick("linux", ["paplay"], ["alarm-clock-elapsed.oga"])
        self.assertEqual(sound.argv[0], "paplay")

    def test_windows(self):
        self.assertIsInstance(self.pick("win32"), WindowsSound)

    def test_nothing_available_means_terminal_beep(self):
        self.assertIsInstance(self.pick("darwin"), TerminalBell)
        self.assertIsInstance(self.pick("linux"), TerminalBell)


class SoundCommandTest(unittest.TestCase):
    def test_alarm_sound_plays_once(self):
        fake = mock.Mock(name="sound")
        fake.name = "Glass (macOS sound)"
        out = io.StringIO()
        with mock.patch("alarmclock.cli.default_sound", return_value=fake):
            code = main(["sound"], out=out, err=io.StringIO(), inp=io.StringIO())
        self.assertEqual(code, EXIT_OK)
        fake.play.assert_called_once()
        self.assertIn("Playing the alarm sound: Glass", out.getvalue())


if __name__ == "__main__":
    unittest.main()
