"""Performance regression contracts. Run: python -m unittest scripts.test_performance

Uses synthetic screenshots and offscreen Qt; never opens the game or Steam.
"""
import os
from pathlib import Path
import threading
import time
from types import SimpleNamespace as NS
import unittest
from unittest.mock import patch

os.environ['QT_QPA_PLATFORM'] = 'offscreen'
os.environ['APPDATA'] = str(Path('build/performance/unittest-appdata').resolve())

import numpy as np
from PIL import Image
from PyQt6.QtWidgets import QApplication
from src.performance import ByteCache
from src.detector.utils import grab_region
from src.detector.map_detector import MapDetector, MapDetectParam, MapDetectResult, MapPatternMatchResult
from src.detector.map_jobs import MapJobs
from src.detector.partial_map import LandmarkEvidence
from src.screencap.errors import ScreencapRuntimeError
from src.ui.overlay import OverlayWidget, OverlayUIState
from src.ui.map_overlay import MapOverlayWidget, MapOverlayUIState
from src.ui.hp_overlay import HpOverlayWidget, HpOverlayUIState


class CacheAndPixels(unittest.TestCase):
    def test_byte_budget_and_oversize_bypass(self):
        cache = ByteCache(8)
        a = cache.get('a', lambda: np.zeros(4, dtype='uint8'))
        cache.get('b', lambda: np.ones(4, dtype='uint8'))
        self.assertIs(cache.get('a', lambda: None), a)
        cache.get('c', lambda: np.ones(4, dtype='uint8'))
        self.assertEqual(set(cache._items), {'a', 'c'})
        cache.get('large', lambda: np.ones(16, dtype='uint8'))
        self.assertEqual(cache.bytes, 8)
        self.assertNotIn('large', cache._items)

    def test_crop_pixels_and_ownership(self):
        pixels = np.arange(60 * 80 * 4, dtype='uint8').reshape(60, 80, 4)
        frame = Image.fromarray(pixels)
        frame.info['screen_origin'] = (-100, 50)
        engine = NS(grab_fullscreen=lambda: frame)
        cropped = grab_region(engine, (-90, 55, 30, 20))
        np.testing.assert_array_equal(np.array(cropped), pixels[5:25, 10:40])
        cropped.putpixel((0, 0), (0, 0, 0, 0))
        self.assertEqual(frame.getpixel((10, 5)), tuple(pixels[5, 10]))
        with self.assertRaises(ScreencapRuntimeError):
            grab_region(engine, (-101, 50, 10, 10))
        with self.assertRaises(ScreencapRuntimeError):
            grab_region(engine, (-30, 50, 11, 10))

    def test_overlay_cache_keeps_dynamic_header_and_returns_owned_images(self):
        detector = MapDetector()
        pattern = detector.info.patterns[0]
        first = MapPatternMatchResult(pattern, pattern.nightlord, 1, 0)
        second = MapPatternMatchResult(pattern, None, 2, 3)
        image = detector._draw_overlay_image(first, (750, 750), 0)
        pixels = image.tobytes()
        image.paste((255, 0, 0, 255), (0, 0, 750, 750))
        self.assertEqual(detector._draw_overlay_image(first, (750, 750), 0).tobytes(), pixels)
        updated = detector._draw_overlay_image(second, (750, 750), 2)
        self.assertNotEqual(updated.tobytes(), pixels)
        detector._overlay_layers.clear()
        self.assertEqual(updated.tobytes(), detector._draw_overlay_image(second, (750, 750), 2).tobytes())
        self.assertLessEqual(detector._overlay_layers.bytes, detector._overlay_layers.max_bytes)

    def test_ranking_reuses_only_unchanged_evidence(self):
        evidence = LandmarkEvidence()
        patterns = [NS(id=1, pos_constructions={(1, 1): NS(type=30)}),
                    NS(id=2, pos_constructions={(1, 1): NS(type=40)})]
        identity = lambda value: value
        for _ in range(2): evidence.observe({(1, 1): 30})
        original = evidence.rank(patterns, identity)
        evidence.observe({})
        self.assertIs(original, evidence.rank(patterns, identity))
        for _ in range(2): evidence.observe({(1, 1): 40})
        self.assertEqual(evidence.rank(patterns, identity)[0][0].id, 2)
        evidence.clear()
        self.assertEqual(evidence.rank(patterns, identity)[0][1:], (0, 0))


class WorkerContracts(unittest.TestCase):
    def test_first_candidate_is_available_before_the_job_finishes(self):
        started, release = threading.Event(), threading.Event()
        class Detector:
            partial_inference = None
            def detect(self, engine, param):
                param.on_progress(MapDetectResult(rendering_complete=False,
                    overlay_images=[Image.new('RGBA', (2, 2))]))
                started.set()
                release.wait(3)
                return MapDetectResult(rendering_complete=True)
        jobs = MapJobs(Detector)
        param = MapDetectParam(map_region=(0, 0, 2, 2), img=np.zeros((2, 2, 3)), infer_partial=True)
        try:
            jobs.submit(param, 'current', 0)
            self.assertTrue(started.wait(3))
            progress = jobs.poll('current')[1]
            self.assertFalse(progress.rendering_complete)
            self.assertEqual(len(progress.overlay_images), 1)
            self.assertTrue(jobs.busy)
            self.assertIsNone(jobs.poll('current'))
            release.set()
            jobs._future.result(timeout=3)
            self.assertTrue(jobs.poll('current')[1].rendering_complete)
        finally:
            release.set()
            jobs.close()

    def test_updater_rejects_results_after_region_changes(self):
        from src.updater import Updater, DoMatchMapPatternFlag
        from unittest.mock import Mock
        jobs = MapJobs(lambda: NS(partial_inference=None,
                                 detect=lambda *_: MapDetectResult(earth_shifting=0)))
        holder = NS(_map_jobs=jobs, _map_request_version=0, _map_epoch=1,
                    _partial_game_hwnd=1, map_region=(0, 0, 2, 2), partial_map_enabled=False,
                    map_detect_enabled=True, map_pattern_return_topk=1, hdr_processing_enabled=False,
                    update_map_overlay_images=Mock())
        holder._map_context = lambda: Updater._map_context(holder)
        holder.set_to_detect_map_pattern_once = lambda: Updater.set_to_detect_map_pattern_once(holder)
        holder._map_context_seen = holder._map_context()[1:]
        param = MapDetectParam(map_region=holder.map_region, img=np.zeros((2, 2, 3)),
                               do_match_pattern=True, earth_shifting=0)
        try:
            jobs.submit(param, holder._map_context(), holder._map_epoch)
            jobs._future.result(timeout=3)
            holder.map_region = (10, 10, 2, 2)
            Updater._poll_map_job(holder)
            holder.update_map_overlay_images.assert_not_called()
            self.assertEqual(holder.do_match_map_pattern_flag, DoMatchMapPatternFlag.PREPARE)
        finally:
            jobs.close()

    def test_single_job_snapshot_progress_and_stale_result(self):
        started, release = threading.Event(), threading.Event()
        seen = []

        class Detector:
            partial_inference = None
            def detect(self, engine, param):
                seen.append(param.img.copy())
                param.on_progress(MapDetectResult(rendering_complete=False, inference_message='partial'))
                started.set()
                if not release.wait(3):
                    raise TimeoutError('test worker not released')
                return MapDetectResult(inference_message='done')

        jobs = MapJobs(Detector)
        pixels = np.zeros((20, 20, 3), dtype='uint8')
        param = MapDetectParam(map_region=(0, 0, 20, 20), img=pixels, infer_partial=True)
        try:
            self.assertTrue(jobs.submit(param, 'old', 1))
            self.assertTrue(started.wait(3))
            pixels[:] = 255
            self.assertFalse(jobs.submit(param, 'new', 1))
            self.assertIsNone(jobs.poll('new'))  # Old progress is discarded too.
            self.assertTrue(jobs.busy)
            release.set()
            jobs._future.result(timeout=3)
            self.assertIsNone(jobs.poll('new'))
            self.assertFalse(jobs.busy)
            self.assertFalse(seen[0].any())
        finally:
            release.set()
            jobs.close()

    def test_evidence_reset_and_exception_recovery(self):
        resets = []
        class Detector:
            partial_inference = None
            def detect(self, engine, param):
                resets.append(self.partial_inference)
                self.partial_inference = 'confirmed'
                if len(resets) == 1:
                    raise ValueError('bad frame')
                return MapDetectResult(inference_message='done')
        jobs = MapJobs(Detector)
        param = MapDetectParam(map_region=(0, 0, 2, 2), img=np.zeros((2, 2, 3)), infer_partial=True)
        try:
            for epoch in (1, 1, 2):
                self.assertTrue(jobs.submit(param, epoch, epoch))
                try:
                    jobs._future.result(timeout=3)
                except ValueError:
                    pass
                if len(resets) == 1:
                    with self.assertRaises(ValueError): jobs.poll(epoch)
                else:
                    self.assertEqual(jobs.poll(epoch)[1].inference_message, 'done')
            self.assertEqual(resets, [None, 'confirmed', None])
        finally:
            jobs.close()

    def test_timer_updates_continue_while_map_work_is_pending(self):
        # Exercise the real updater loop with a blocked map worker and fake capture.
        from src.updater import Updater
        from src.screencap import EngineStatus
        release, started = threading.Event(), threading.Event()
        class Detector:
            partial_inference = None
            def detect(self, engine, param):
                started.set()
                release.wait(3)
                return MapDetectResult()
        app = QApplication.instance() or QApplication([])
        overlay, map_, hp = OverlayWidget(), MapOverlayWidget(), HpOverlayWidget()
        from unittest.mock import Mock
        updater = Updater(Mock(), overlay, map_, hp)
        updater._map_jobs.close()
        updater._map_jobs = MapJobs(Detector)
        param = MapDetectParam(map_region=(0, 0, 2, 2), img=np.zeros((2, 2, 3)), infer_partial=True)
        updater._map_jobs.submit(param, 'active', 0)
        self.assertTrue(started.wait(3))
        ticks = []
        updater.detect_and_update_all = lambda: None
        updater.check_game_foreground = lambda: True
        def update_timer():
            ticks.append(time.monotonic())
            if len(ticks) == 4:
                updater.stop()
                release.set()
        updater.update_phase_timer = update_timer
        engine = Mock(status=EngineStatus.CONNECTED)
        try:
            with patch('src.updater.get_engine', return_value=engine):
                updater.run()
            self.assertEqual(len(ticks), 4)
            self.assertLess(ticks[-1] - ticks[0], 1.5)
        finally:
            release.set()
            updater._map_jobs.close()
            for widget in (overlay, map_, hp): widget.close()


class WidgetContracts(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_identical_state_does_not_reapply_styles(self):
        widget = OverlayWidget()
        state = OverlayUIState(art_color='#abcdef', day_text='1:20', visible=True)
        try:
            with patch.object(widget, '_apply_scale', wraps=widget._apply_scale) as scale:
                widget.update_ui_state(state)
                for _ in range(20): widget.update_ui_state(state)
                self.assertEqual(scale.call_count, 1)
            widget.update_ui_state(OverlayUIState(only_show_when_game_foreground=True,
                                                  is_game_foreground=False))
            self.assertFalse(widget.isVisible())
            widget.update_ui_state(OverlayUIState(is_game_foreground=True))
            self.assertTrue(widget.isVisible())
        finally:
            widget.close()

    def test_geometry_updates_keep_unchanged_coordinates(self):
        hp, overlay = HpOverlayWidget(), OverlayWidget()
        try:
            with patch('src.ui.hp_overlay.region_to_qt_region', side_effect=lambda rect: rect):
                hp.update_ui_state(HpOverlayUIState(x=10, y=20, w=100, h=5))
                hp.update_ui_state(HpOverlayUIState(x=10, y=20, w=200, h=5))
                self.assertEqual(hp.hpbar_region, (10, 20, 200, 5))
                self.assertEqual(hp.width(), 200)
            with patch.object(overlay, 'move', wraps=overlay.move) as move:
                overlay.update_ui_state(OverlayUIState(x=10, y=20))
                overlay.update_ui_state(OverlayUIState(x=30, y=20))
                move.assert_called_with(30, 20)
        finally:
            hp.close()
            overlay.close()

    def test_hidden_map_stops_refresh_and_selection_survives_progress(self):
        widget = MapOverlayWidget()
        try:
            widget.setWindowOpacity(0)
            widget.update_ui_state(MapOverlayUIState(opacity=0))
            self.assertFalse(widget._refresh_timer.isActive())
            widget.update_ui_state(MapOverlayUIState(opacity=1, map_pattern_matching=True))
            self.assertTrue(widget._refresh_timer.isActive())
            images = [Image.new('RGBA', (10, 10), color) for color in ('red', 'green', 'blue')]
            widget.set_overlay_images(images[:2], [1, 2], True)
            widget.next_overlay_image()
            widget.set_overlay_images(images, [1, 2, 3], True)
            self.assertEqual(widget.map_pattern_idx, 1)
            widget.update_ui_state(MapOverlayUIState(visible=False, opacity=0))
            for _ in range(20): widget.timerEvent(None)
            self.assertFalse(widget._refresh_timer.isActive())
        finally:
            widget.close()


if __name__ == '__main__':
    unittest.main()
