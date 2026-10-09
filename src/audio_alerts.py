"""Opt-in timer voice cues. Timer decisions are independent of audio devices."""
from dataclasses import dataclass
import io
import math
from pathlib import Path
import struct
import tempfile
import time
import wave

from PyQt6.QtCore import QObject, pyqtSlot

from src.logger import warning


@dataclass(frozen=True)
class TimerSnapshot:
    """An immutable view of the updater's existing timers, in timer seconds."""

    observed_at: float
    timer_time: float
    time_scale: float = 1.0
    foreground: bool = False
    suspended: bool = False
    revision: int = 0
    day_token: int = 0
    day: int | None = None
    phase: int | None = None
    phase_remaining: float | None = None
    rain_token: float | None = None
    rain_remaining: float | None = None
    art_token: tuple | None = None
    art_remaining: float | None = None


# Each pair is (frequency, duration). Zero frequency is a silent gap.
# Circle starts rise, circle ends fall; rain has three urgent short pulses;
# an art ending has one longer descending chime. These fallback tones last < 0.5 s.
_TONES = {
    "circle_start": ((660, .10), (0, .06), (880, .14)),
    "circle_end": ((880, .10), (0, .06), (660, .14)),
    "rain": ((1046, .07), (0, .055), (1046, .07), (0, .055), (1046, .10)),
    "art": ((880, .10), (660, .10), (440, .16)),
}
_VOICE_DIRECTORY = Path(__file__).resolve().parent.parent / "assets" / "sounds"


def make_cue_wav(kind: str, volume: int) -> bytes:
    """Generate a quiet, click-free 16-bit mono WAV without third-party assets."""
    rate = 22050
    amplitude = 0.45 * max(0, min(100, int(volume))) / 100
    samples = bytearray()
    for frequency, duration in _TONES[kind]:
        count = round(rate * duration)
        fade = round(rate * .009)
        for index in range(count):
            envelope = min(1.0, index / fade, (count - 1 - index) / fade)
            value = amplitude * envelope * math.sin(2 * math.pi * frequency * index / rate)
            samples.extend(struct.pack("<h", round(value * 32767)))
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(samples)
    return output.getvalue()


def _read_voice_wav(path: Path, volume: int) -> tuple[bytes, float]:
    """Validate bundled PCM audio and scale its samples without changing timing."""
    with wave.open(str(path), "rb") as wav:
        if (wav.getnchannels(), wav.getsampwidth(), wav.getcomptype()) != (1, 2, "NONE"):
            raise ValueError("voice cue must be uncompressed 16-bit mono WAV")
        rate = wav.getframerate()
        frames = wav.getnframes()
        if rate <= 0 or frames <= 0:
            raise ValueError("voice cue is empty or has an invalid sample rate")
        samples = wav.readframes(frames)
        if len(samples) != frames * 2:
            raise ValueError("voice cue has truncated PCM samples")
    gain = max(0, min(100, int(volume))) / 100
    scaled = bytearray(len(samples))
    for index, (sample,) in enumerate(struct.iter_unpack("<h", samples)):
        struct.pack_into("<h", scaled, index * 2, round(sample * gain))
    output = io.BytesIO()
    with wave.open(output, "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(rate)
        wav.writeframes(scaled)
    return output.getvalue(), frames / rate


class _WindowsCuePlayer:
    """Use process-local asynchronous playback, independent of pygame's lifetime."""

    def __init__(self, *, voice_directory=None):
        # Delay both OS audio access and temporary files until an actual cue.
        self._directory = None
        self._volume = None
        self._paths = {}
        self._durations = {}
        self._voice_directory = Path(voice_directory) if voice_directory is not None else _VOICE_DIRECTORY
        self._warned_assets = set()
        self._playing = False

    def play(self, kind: str, volume: int) -> float:
        """Start playback and return the actual WAV duration in wall seconds."""
        import winsound

        if kind not in _TONES:
            raise ValueError(f"Unknown sound cue: {kind}")
        if self._volume != volume:
            self.stop()
            self._clear_cache()
            self._volume = volume
        if self._directory is None:
            self._directory = tempfile.TemporaryDirectory(prefix="nightreign-cues-")
        if kind not in self._paths:
            path = Path(self._directory.name) / (kind + ".wav")
            try:
                data, duration = _read_voice_wav(self._voice_directory / (kind + ".wav"), volume)
            except (OSError, EOFError, wave.Error, ValueError, struct.error) as exc:
                if kind not in self._warned_assets:
                    warning(f"Voice cue {kind} unavailable; using a short tone: {exc}")
                    self._warned_assets.add(kind)
                data = make_cue_wav(kind, volume)
                with wave.open(io.BytesIO(data), "rb") as wav:
                    duration = wav.getnframes() / wav.getframerate()
            path.write_bytes(data)
            self._paths[kind] = str(path)
            self._durations[kind] = duration
        winsound.PlaySound(self._paths[kind], winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        self._playing = True
        return self._durations[kind]

    def stop(self):
        if self._playing:
            import winsound
            winsound.PlaySound(None, 0)
            self._playing = False

    def _clear_cache(self):
        if self._directory is not None:
            self._directory.cleanup()
            self._directory = None
        self._paths.clear()
        self._durations.clear()

    def close(self):
        self.stop()
        self._clear_cache()


class AudioAlertService(QObject):
    """Consume queued snapshots on the GUI thread; never queue missed sounds.

    Connect ``Updater.timer_snapshot`` to ``process_snapshot``. The optional
    can_play callback rechecks current UI/foreground state at playback time.
    A player and monotonic clock can be injected for device-free regression tests.
    """

    MAX_SAMPLE_GAP = 0.8
    MAX_SAMPLE_AGE = 0.8
    MIN_CUE_INTERVAL = 0.55

    def __init__(self, parent=None, *, can_play=None, player=None, clock=None):
        super().__init__(parent)
        self.enabled = False
        self.day_enabled = True
        self.rain_enabled = True
        self.art_enabled = True
        self.volume = 40
        self._can_play = can_play or self._game_is_foreground
        self._player = player if player is not None else _WindowsCuePlayer()
        self._clock = clock or time.monotonic
        self._previous = None
        self._previous_allowed = False
        self._consumed = set()
        self._consumed_order = []
        self._last_played_at = -math.inf
        self._playing_until = -math.inf
        self._preview_until = -math.inf
        self._failed = False
        self._closed = False

    @staticmethod
    def _game_is_foreground():
        from src.common import GAME_WINDOW_TITLE
        from src.ui.utils import is_window_in_foreground
        return is_window_in_foreground(GAME_WINDOW_TITLE)

    def configure(self, *, enabled=False, day=True, rain=True, art=True, volume=40):
        options = (bool(enabled), bool(day), bool(rain), bool(art))
        previous = (self.enabled, self.day_enabled, self.rain_enabled, self.art_enabled)
        previous_volume = self.volume
        self.enabled, self.day_enabled, self.rain_enabled, self.art_enabled = options
        try:
            self.volume = max(0, min(100, int(volume)))
        except (ValueError, TypeError, OverflowError):
            self.volume = 40
        if previous != options:
            self._previous = None
            self._previous_allowed = False
        if self.enabled and not previous[0]:
            self._failed = False
        if not self.enabled or self.volume == 0 or self.volume != previous_volume:
            self._stop_playback()

    @staticmethod
    def _events(snapshot):
        events = {}
        if snapshot.day in (1, 2) and snapshot.phase in (0, 1, 2, 3):
            kind = "circle_start" if snapshot.phase % 2 == 0 else "circle_end"
            events["day"] = (("day", snapshot.day_token, snapshot.day, snapshot.phase),
                             snapshot.phase_remaining, 30.0, kind)
        if snapshot.rain_token is not None:
            events["rain"] = (("rain", snapshot.rain_token), snapshot.rain_remaining, 10.0, "rain")
        if snapshot.art_token is not None:
            events["art"] = (("art", snapshot.art_token), snapshot.art_remaining, 3.0, "art")
        return events

    def _consume(self, token):
        if token not in self._consumed:
            self._consumed.add(token)
            self._consumed_order.append(token)
            # Bound memory during long-running sessions, retaining several days.
            if len(self._consumed_order) > 128:
                self._consumed.remove(self._consumed_order.pop(0))

    @pyqtSlot(object)
    def process_snapshot(self, snapshot):
        if self._closed or not isinstance(snapshot, TimerSnapshot):
            return
        now = self._clock()
        previous = self._previous
        previous_allowed = self._previous_allowed
        self._previous = snapshot
        events = self._events(snapshot)
        old_events = self._events(previous) if previous else {}
        gate = (self.enabled and self.volume > 0 and snapshot.foreground
                and not snapshot.suspended and 0 <= now - snapshot.observed_at <= self.MAX_SAMPLE_AGE)
        continuous = False
        expected = 0.0
        if previous is not None:
            elapsed = snapshot.observed_at - previous.observed_at
            expected = elapsed * snapshot.time_scale
            continuous = (
                0 < elapsed <= self.MAX_SAMPLE_GAP
                and 0 < snapshot.time_scale == previous.time_scale
                and previous.foreground and not previous.suspended
                and abs(snapshot.timer_time - previous.timer_time - expected) <= .20
            )
        if gate:
            try:
                gate = bool(self._can_play())
            except Exception:
                gate = False
        self._previous_allowed = gate
        if not gate and now >= self._preview_until:
            self._stop_playback()

        candidates = []
        for category, (token, remaining, threshold, kind) in events.items():
            if remaining is None or not math.isfinite(remaining):
                continue
            if remaining > threshold or token in self._consumed:
                continue
            # Consume even when disabled/backgrounded/jumped: an old threshold
            # must not replay after returning, toggling options or rewinding.
            self._consume(token)
            old = old_events.get(category)
            if not (gate and previous_allowed and continuous and remaining > 0
                    and old is not None and old[0] == token):
                continue
            if category == "day" and snapshot.revision != previous.revision:
                continue
            old_remaining = old[1]
            if (old_remaining is None or not math.isfinite(old_remaining)
                    or old_remaining <= threshold
                    or abs(old_remaining - remaining - expected) > .20):
                continue
            if getattr(self, category + "_enabled"):
                candidates.append(kind)
        # No queue and no overlapping pile-up: rain takes priority over an art
        # ending, then the more distant circle warning. All events are consumed.
        if (candidates and now >= self._playing_until
                and now - self._last_played_at >= self.MIN_CUE_INTERVAL):
            kind = min(candidates, key=lambda cue: {"rain": 0, "art": 1}.get(cue, 2))
            self._play(kind, now)

    def _play(self, kind, now):
        if self._closed or self._failed or self.volume <= 0:
            return False
        try:
            duration = float(self._player.play(kind, self.volume))
            if not math.isfinite(duration) or duration <= 0:
                raise ValueError("audio backend returned an invalid cue duration")
            # WAV loading/scaling can take time. Count from completed playback
            # setup rather than the snapshot's earlier timestamp.
            self._last_played_at = max(now, self._clock())
            self._playing_until = self._last_played_at + duration
            return True
        except Exception as exc:
            self._failed = True
            warning(f"Sound alerts unavailable; timers continue normally: {exc}")
            return False

    def preview(self, kind="circle_start"):
        """An explicit settings preview may play while automatic cues are muted."""
        if kind not in _TONES:
            kind = "circle_start"
        # A deliberate retry permits recovery after an audio device reappears.
        self._failed = False
        now = self._clock()
        played = self._play(kind, now)
        if played:
            self._preview_until = self._playing_until
        return played

    def _stop_playback(self):
        self._playing_until = -math.inf
        self._preview_until = -math.inf
        try:
            self._player.stop()
        except Exception as exc:
            if not self._failed:
                warning(f"Unable to stop sound cue: {exc}")
            self._failed = True

    def stop(self):
        self._closed = True
        self._previous = None
        try:
            self._player.close()
        except Exception as exc:
            warning(f"Unable to close sound cues cleanly: {exc}")
