"""Offline timer-cue regression tests. Never access real audio/game devices."""
from dataclasses import replace
import io
import os
from pathlib import Path
import struct
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch
import wave

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QThread, pyqtSignal
from PyQt6.QtWidgets import QApplication

from src.audio_alerts import AudioAlertService, TimerSnapshot, _WindowsCuePlayer, make_cue_wav


class FakePlayer:
    def __init__(self):
        self.played = []
        self.stops = 0
        self.closed = False
        self.threads = []

    def play(self, kind, volume):
        self.played.append((kind, volume))
        self.threads.append(QThread.currentThread())

    def stop(self):
        self.stops += 1

    def close(self):
        self.closed = True


class AlertDecisions(unittest.TestCase):
    def setUp(self):
        self.now = 0.0
        self.player = FakePlayer()
        self.guard = Mock(return_value=True)
        self.service = AudioAlertService(player=self.player, clock=lambda: self.now, can_play=self.guard)
        self.service.configure(enabled=True)

    def tearDown(self):
        self.service.stop()

    def send(self, at, **values):
        self.now = at
        snapshot = TimerSnapshot(at, 1000 + at, foreground=True, **values)
        self.service.process_snapshot(snapshot)
        return snapshot

    def test_default_disabled_and_zero_volume_do_not_play(self):
        self.service.configure()
        self.send(0, rain_token=1, rain_remaining=10.1)
        self.send(.2, rain_token=1, rain_remaining=9.9)
        self.service.configure(enabled=True, volume=0)
        self.send(1, rain_token=2, rain_remaining=10.1)
        self.send(1.2, rain_token=2, rain_remaining=9.9)
        self.assertEqual(self.player.played, [])

    def test_all_four_circle_transitions_have_the_correct_sound_once(self):
        for phase in range(4):
            at = phase * 2
            self.send(at, day_token=1, day=1, phase=phase, phase_remaining=30.1)
            self.send(at + .2, day_token=1, day=1, phase=phase, phase_remaining=29.9)
            self.send(at + .4, day_token=1, day=1, phase=phase, phase_remaining=29.7)
        self.assertEqual(self.player.played, [(kind, 40) for kind in
                         ("circle_start", "circle_end", "circle_start", "circle_end")])

    def test_exact_threshold_and_next_day_rearm(self):
        for token in (1, 2):
            at = token * 2
            self.send(at, day_token=token, day=token, phase=0, phase_remaining=30.2)
            self.send(at + .2, day_token=token, day=token, phase=0, phase_remaining=30.0)
        self.assertEqual(len(self.player.played), 2)

    def test_night_boss_and_day_three_never_warn(self):
        for day, phase in ((1, 4), (3, 0)):
            self.send(day * 2, day_token=day, day=day, phase=phase, phase_remaining=30.1)
            self.send(day * 2 + .2, day_token=day, day=day, phase=phase, phase_remaining=29.9)
        self.assertEqual(self.player.played, [])

    def test_rain_exit_reentry_and_next_art_are_independent_events(self):
        self.send(0, rain_token=1, rain_remaining=10.1)
        self.send(.2, rain_token=1, rain_remaining=9.9)
        self.send(.4)
        self.send(1, rain_token=2, rain_remaining=10.1)
        self.send(1.2, rain_token=2, rain_remaining=9.9)
        self.send(2, art_token=(1, "scholar"), art_remaining=3.1)
        self.send(2.2, art_token=(1, "scholar"), art_remaining=2.9)
        self.send(3, art_token=(2, "scholar"), art_remaining=3.1)
        self.send(3.2, art_token=(2, "scholar"), art_remaining=2.9)
        self.assertEqual([kind for kind, _ in self.player.played], ["rain", "rain", "art", "art"])

    def test_category_switches_are_independent(self):
        self.service.configure(enabled=True, day=False, rain=False, art=True)
        self.send(0, day=1, phase=0, phase_remaining=30.1,
                  rain_token=1, rain_remaining=10.1, art_token=(1, "x"), art_remaining=3.1)
        self.send(.2, day=1, phase=0, phase_remaining=29.9,
                  rain_token=1, rain_remaining=9.9, art_token=(1, "x"), art_remaining=2.9)
        self.assertEqual(self.player.played, [("art", 40)])

    def test_initial_snapshot_below_threshold_never_catches_up(self):
        self.send(0, rain_token=1, rain_remaining=9.9)
        self.send(.2, rain_token=1, rain_remaining=9.7)
        self.assertEqual(self.player.played, [])

    def test_background_and_menu_thresholds_are_consumed(self):
        for token, gate in ((1, {"foreground": False}), (2, {"suspended": True})):
            at = token * 2
            self.send(at, rain_token=token, rain_remaining=10.1)
            self.now = at + .2
            self.service.process_snapshot(replace(TimerSnapshot(at + .2, 1000 + at + .2,
                foreground=True, rain_token=token, rain_remaining=9.9), **gate))
            self.send(at + .4, rain_token=token, rain_remaining=9.7)
        self.assertEqual(self.player.played, [])

    def test_first_foreground_sample_cannot_cross_a_background_boundary(self):
        self.now = 0
        self.service.process_snapshot(TimerSnapshot(0, 1000, foreground=False,
                                                     rain_token=1, rain_remaining=10.1))
        self.send(.2, rain_token=1, rain_remaining=9.9)
        self.assertEqual(self.player.played, [])

    def test_current_ui_guard_rechecks_queued_foreground_snapshot(self):
        self.send(0, rain_token=1, rain_remaining=10.1)
        self.guard.return_value = False
        self.send(.2, rain_token=1, rain_remaining=9.9)
        self.guard.return_value = True
        self.send(.4, rain_token=1, rain_remaining=9.7)
        self.assertEqual(self.player.played, [])

    def test_timer_jump_gap_stale_queue_and_clock_jump_are_silent(self):
        cases = [
            (TimerSnapshot(.2, 1000.2, foreground=True, rain_token=1, rain_remaining=1), .2),
            (TimerSnapshot(2, 1002, foreground=True, rain_token=1, rain_remaining=8.1), 2),
            (TimerSnapshot(.2, 1000.2, foreground=True, rain_token=1, rain_remaining=9.9), 2),
            (TimerSnapshot(.2, 900, foreground=True, rain_token=1, rain_remaining=9.9), .2),
        ]
        for current, now in cases:
            with self.subTest(current=current):
                self.service._previous = None
                self.service._consumed.clear()
                self.service._consumed_order.clear()
                self.send(0, rain_token=1, rain_remaining=10.1)
                self.now = now
                self.service.process_snapshot(current)
        self.assertEqual(self.player.played, [])

    def test_manual_day_adjustment_skips_day_but_does_not_mute_other_timers(self):
        self.send(0, day=1, phase=0, phase_remaining=30.1,
                  rain_token=1, rain_remaining=10.1)
        self.send(.2, revision=1, day=1, phase=0, phase_remaining=29.9,
                  rain_token=1, rain_remaining=9.9)
        self.send(1, revision=1, day=1, phase=1, phase_remaining=30.1,
                  art_token=(1, "x"), art_remaining=3.1)
        self.send(1.2, revision=2, day=1, phase=1, phase_remaining=29.9,
                  art_token=(1, "x"), art_remaining=2.9)
        self.assertEqual(self.player.played, [("rain", 40), ("art", 40)])

    def test_returning_from_current_ui_guard_cannot_cross_an_old_boundary(self):
        self.guard.return_value = False
        self.send(0, rain_token=1, rain_remaining=10.1)
        self.guard.return_value = True
        self.send(.2, rain_token=1, rain_remaining=9.9)
        self.assertEqual(self.player.played, [])

    def test_rewind_does_not_repeat_same_circle_but_restart_does(self):
        self.send(0, day_token=1, day=1, phase=0, phase_remaining=30.1)
        self.send(.2, day_token=1, day=1, phase=0, phase_remaining=29.9)
        self.send(1, revision=1, day_token=1, day=1, phase=0, phase_remaining=30.1)
        self.send(1.2, revision=1, day_token=1, day=1, phase=0, phase_remaining=29.9)
        self.send(2, revision=1, day_token=2, day=1, phase=0, phase_remaining=30.1)
        self.send(2.2, revision=1, day_token=2, day=1, phase=0, phase_remaining=29.9)
        self.assertEqual(len(self.player.played), 2)

    def test_next_day_and_repeated_day_detection_do_not_rearm_existing_rain_or_art(self):
        for category, token, limit in (("rain", 1, 10), ("art", (1, "x"), 3)):
            at = 0 if category == "rain" else 5
            self.send(at, day_token=1, day=1,
                      **{category + "_token": token, category + "_remaining": limit + .1})
            self.send(at + .2, day_token=2, day=1,
                      **{category + "_token": token, category + "_remaining": limit - .1})
            self.send(at + 1, day_token=3, day=2,
                      **{category + "_token": token, category + "_remaining": limit + .1})
            self.send(at + 1.2, day_token=3, day=2,
                      **{category + "_token": token, category + "_remaining": limit - .1})
        self.assertEqual(self.player.played, [("rain", 40), ("art", 40)])

    def test_scaled_timer_seconds_cross_threshold_normally(self):
        self.service.process_snapshot(TimerSnapshot(0, 1000, time_scale=2, foreground=True,
                                                     rain_token=1, rain_remaining=10.2))
        self.now = .2
        self.service.process_snapshot(TimerSnapshot(.2, 1000.4, time_scale=2, foreground=True,
                                                     rain_token=1, rain_remaining=9.8))
        self.assertEqual(self.player.played, [("rain", 40)])

    def test_clock_scale_change_does_not_catch_up(self):
        self.send(0, rain_token=1, rain_remaining=10.1)
        self.now = .2
        self.service.process_snapshot(TimerSnapshot(.2, 1000.2, time_scale=2, foreground=True,
                                                     rain_token=1, rain_remaining=9.9))
        self.assertEqual(self.player.played, [])

    def test_expired_and_invalid_timer_values_never_warn(self):
        for token, value in enumerate((-1.0, float("nan"), None)):
            self.send(token * 2, art_token=(token, "x"), art_remaining=3.1)
            self.send(token * 2 + .2, art_token=(token, "x"), art_remaining=value)
        self.assertEqual(self.player.played, [])

    def test_simultaneous_events_do_not_queue_or_overlap(self):
        self.send(0, day=1, phase=0, phase_remaining=30.1,
                  rain_token=1, rain_remaining=10.1, art_token=(1, "x"), art_remaining=3.1)
        self.send(.2, day=1, phase=0, phase_remaining=29.9,
                  rain_token=1, rain_remaining=9.9, art_token=(1, "x"), art_remaining=2.9)
        self.send(1, day=1, phase=0, phase_remaining=29.1,
                  rain_token=1, rain_remaining=9.1, art_token=(1, "x"), art_remaining=2.1)
        self.assertEqual(self.player.played, [("rain", 40)])

    def test_preview_works_in_settings_with_master_off_and_keeps_playing(self):
        self.service.configure(enabled=False, volume=27)
        self.guard.return_value = False
        self.assertTrue(self.service.preview("art"))
        before = self.player.stops
        self.send(.1, suspended=True)
        self.assertEqual(self.player.stops, before)
        self.send(.6, suspended=True)
        self.assertGreater(self.player.stops, before)
        self.assertEqual(self.player.played, [("art", 27)])

    def test_device_failure_is_contained_and_explicit_preview_can_retry(self):
        self.player.play = Mock(side_effect=RuntimeError("no device"))
        with patch("src.audio_alerts.warning") as log:
            self.send(0, rain_token=1, rain_remaining=10.1)
            self.send(.2, rain_token=1, rain_remaining=9.9)
            self.send(1, rain_token=2, rain_remaining=10.1)
            self.send(1.2, rain_token=2, rain_remaining=9.9)
            self.assertEqual(log.call_count, 1)
            self.assertEqual(self.player.play.call_count, 1)
            self.player.play.side_effect = None
            self.assertTrue(self.service.preview())
        self.assertEqual(self.player.play.call_count, 2)

    def test_stop_closes_backend_and_rejects_pending_snapshots(self):
        self.send(0, rain_token=1, rain_remaining=10.1)
        self.service.stop()
        self.send(.2, rain_token=1, rain_remaining=9.9)
        self.assertFalse(self.service.preview())
        self.assertTrue(self.player.closed)
        self.assertEqual(self.player.played, [])

    def test_invalid_volume_clamps_and_invalid_snapshot_is_ignored(self):
        self.service.configure(volume=999)
        self.assertEqual(self.service.volume, 100)
        self.service.configure(volume="bad")
        self.assertEqual(self.service.volume, 40)
        self.service.process_snapshot(None)
        self.assertEqual(self.player.played, [])


class WavAndBackend(unittest.TestCase):
    def test_pcm_volume_silence_short_duration_and_distinct_patterns(self):
        all_waves = []
        for kind in ("circle_start", "circle_end", "rain", "art"):
            data = make_cue_wav(kind, 40)
            all_waves.append(data)
            with wave.open(io.BytesIO(data)) as wav:
                self.assertEqual((wav.getnchannels(), wav.getsampwidth(), wav.getframerate()), (1, 2, 22050))
                self.assertLess(wav.getnframes() / wav.getframerate(), .5)
                raw = wav.readframes(wav.getnframes())
            values = struct.unpack("<" + "h" * (len(raw) // 2), raw)
            self.assertGreater(max(values), 1000)
            self.assertLessEqual(max(values), 6000)
            with wave.open(io.BytesIO(make_cue_wav(kind, 0))) as wav:
                self.assertEqual(set(wav.readframes(wav.getnframes())), {0})
        self.assertEqual(len(set(all_waves)), 4)

    def test_async_file_cache_and_cleanup_without_any_real_sound(self):
        player = _WindowsCuePlayer()
        with patch("winsound.PlaySound") as play:
            player.play("rain", 40)
            directory = Path(player._directory.name)
            first = Path(play.call_args.args[0])
            self.assertTrue(first.is_file())
            import winsound
            self.assertTrue(play.call_args.args[1] & winsound.SND_ASYNC)
            player.play("rain", 40)
            self.assertEqual(len(list(directory.iterdir())), 1)
            player.play("art", 20)
            self.assertFalse(directory.exists())
            directory = Path(player._directory.name)
            player.close()
            self.assertFalse(directory.exists())
            self.assertEqual(play.call_args.args, (None, 0))


class UpdaterSnapshots(unittest.TestCase):
    def holder(self):
        from src.updater import Phase
        return NS(get_time=lambda: 112.0, current_phase=Phase.FIRST_CIRCLE_STABLE,
                  day=1, phase_start_time=100.0, in_rain_start_time=105.0,
                  art_type="scholar", art_start_time=100.0,
                  is_setting_opened=False, is_menu_opened=False,
                  _audio_day_sequence=7, _audio_timer_revision=2)

    def config(self):
        return NS(time_scale=1.0, day_period_seconds=[270, 180, 210, 180],
                  deadly_nightrain_seconds=110,
                  art_info={"scholar": {"duration": 20, "delay": 2}},
                  foward_day_seconds=30, back_day_seconds=30)

    def test_snapshot_reads_same_timer_units_and_respects_art_delay_without_mutation(self):
        from src.updater import Updater
        holder = self.holder()
        before = holder.__dict__.copy()
        with patch("src.updater.Config.get", return_value=self.config()):
            snapshot = Updater.get_timer_snapshot(holder, True)
        self.assertEqual((snapshot.phase_remaining, snapshot.rain_remaining, snapshot.art_remaining), (258, 103, 10))
        self.assertEqual((snapshot.day_token, snapshot.revision), (7, 2))
        self.assertEqual(holder.__dict__, before)

    def test_expired_art_snapshot_does_not_clear_existing_timer(self):
        from src.updater import Updater
        holder = self.holder()
        holder.get_time = lambda: 140
        with patch("src.updater.Config.get", return_value=self.config()):
            snapshot = Updater.get_timer_snapshot(holder, True)
        self.assertLess(snapshot.art_remaining, 0)
        self.assertEqual(holder.art_type, "scholar")
        self.assertEqual(holder.art_start_time, 100)

    def test_menu_settings_and_manual_adjustments_invalidate_audio_boundary(self):
        from src.updater import Updater
        holder = self.holder()
        holder.is_menu_opened = True
        with patch("src.updater.Config.get", return_value=self.config()):
            self.assertTrue(Updater.get_timer_snapshot(holder, True).suspended)
            Updater.foward_day_by_shortcut(holder)
            self.assertEqual(holder._audio_timer_revision, 3)
            Updater.back_day_by_shortcut(holder)
            self.assertEqual(holder._audio_timer_revision, 4)
            self.assertEqual(holder.phase_start_time, 100)

    def test_each_day_start_has_a_new_identity(self):
        from src.updater import Updater
        holder = self.holder()
        holder.set_to_detect_map_pattern_once = Mock()
        with patch("src.updater.info"):
            for start in (Updater.start_day1, Updater.start_day2, Updater.start_day3, Updater.start_day1):
                before = holder._audio_day_sequence
                start(holder)
                self.assertEqual(holder._audio_day_sequence, before + 1)


class QueuedDelivery(unittest.TestCase):
    def test_worker_emission_plays_only_on_receiver_main_thread(self):
        app = QApplication.instance() or QApplication([])
        player = FakePlayer()
        service = AudioAlertService(player=player, clock=lambda: .2, can_play=lambda: True)
        service.configure(enabled=True)
        service.process_snapshot(TimerSnapshot(0, 1000, foreground=True, rain_token=1, rain_remaining=10.1))

        class Sender(QThread):
            snapshot = pyqtSignal(object)

            def run(self):
                self.snapshot.emit(TimerSnapshot(.2, 1000.2, foreground=True, rain_token=1, rain_remaining=9.9))

        worker = Sender()
        worker.snapshot.connect(service.process_snapshot)
        worker.start()
        self.assertTrue(worker.wait(2000))
        self.assertEqual(player.played, [])
        app.processEvents()
        self.assertEqual(player.played, [("rain", 40)])
        self.assertEqual(player.threads, [app.thread()])
        service.stop()


if __name__ == "__main__":
    unittest.main()
