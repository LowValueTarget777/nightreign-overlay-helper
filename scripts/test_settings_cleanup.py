"""Settings migration and normal map worker regressions (offscreen, no services)."""
import os
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
import unittest
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
os.environ['APPDATA'] = str(Path('build/settings-cleanup/test-appdata').resolve())

import numpy as np
import yaml
from PyQt6.QtWidgets import QApplication, QCheckBox, QPushButton
from src.detector.map_detector import MapDetectParam, MapDetectResult
from src.detector.map_jobs import MapJobs


class NormalMapJobs(unittest.TestCase):
    def test_terrain_then_pattern_and_missing_terrain_retry_result(self):
        for terrain in (0, None):
            with self.subTest(terrain=terrain):
                calls = []
                class Detector:
                    def detect(self, engine, param):
                        calls.append(param)
                        return MapDetectResult(earth_shifting=terrain)
                jobs = MapJobs(Detector)
                try:
                    jobs.submit(MapDetectParam(img=np.zeros((2, 2, 3)),
                                do_match_pattern=True), 'request')
                    jobs._future.result(timeout=3)
                    self.assertEqual(jobs.poll('request')[1].earth_shifting, terrain)
                    self.assertTrue(calls[0].do_match_earth_shifting)
                    self.assertFalse(calls[0].do_match_pattern)
                    self.assertEqual(len(calls), 2 if terrain is not None else 1)
                    if terrain is not None:
                        self.assertTrue(calls[1].do_match_pattern)
                        self.assertEqual(calls[1].earth_shifting, terrain)
                finally:
                    jobs.close()

    def test_capture_reconnect_invalidates_old_requests_even_when_disabled(self):
        from src.updater import Updater
        holder = NS(_map_game_hwnd=10, _reset_map_requested=True, _map_epoch=4,
                    map_detect_enabled=False, update_map_overlay_images=Mock(),
                    set_to_detect_map_pattern_once=Mock(), hide_map_overlay=Mock())
        with patch('src.updater.get_engine', return_value=NS(_capture_hwnd=10)):
            Updater.detect_and_update_map(holder)
        self.assertEqual(holder._map_epoch, 5)
        self.assertFalse(holder._reset_map_requested)
        holder.update_map_overlay_images.assert_called_once_with(None)
        holder.set_to_detect_map_pattern_once.assert_called_once()


class SettingsMigration(unittest.TestCase):
    def test_removed_option_does_not_return_after_loading_old_settings(self):
        from src.ui.input import InputWorker
        from src.ui.overlay import OverlayWidget
        from src.ui.map_overlay import MapOverlayWidget
        from src.ui.hp_overlay import HpOverlayWidget
        from src.ui.p2p_overlay import P2POverlayWidget
        from src.ui.commendation_overlay import CommendationOverlayWidget
        from src.p2p import P2PService, SteamPlaytimeService
        from src.updater import Updater
        import src.ui.settings as settings
        app = QApplication.instance() or QApplication([])
        with TemporaryDirectory(dir='build/settings-cleanup') as directory:
            path = Path(directory) / 'settings.yaml'
            path.write_text(yaml.safe_dump({'partial_map_enabled': True,
                'map_detect_enabled': True, 'map_pattern_return_topk': 3,
                'auto_regions_enabled': False, 'map_region': [100, 120, 750, 750]}), encoding='utf-8')
            with patch.object(settings, 'SETTINGS_SAVE_PATH', str(path)), \
                 patch.object(settings, 'PRESET_SETTINGS_DIR', str(Path(directory)/'presets')), \
                 patch.object(P2PService, 'start'), patch.object(SteamPlaytimeService, 'set_enabled'):
                input_ = InputWorker()
                overlay, map_, hp, p2p = OverlayWidget(), MapOverlayWidget(), HpOverlayWidget(), P2POverlayWidget()
                review = CommendationOverlayWidget()
                service = P2PService()
                playtime = SteamPlaytimeService(key_store=Mock(load=lambda: None))
                updater = Updater(input_, overlay, map_, hp)
                window = settings.SettingsWindow(overlay, map_, updater, input_, p2p, service, playtime, commend_overlay=review)
                try:
                    self.assertTrue(updater.map_detect_enabled)
                    self.assertEqual(updater.map_pattern_return_topk, 3)
                    self.assertEqual(updater.map_region, [100, 120, 750, 750])
                    labels = [w.text() for kind in (QCheckBox, QPushButton) for w in window.findChildren(kind)]
                    self.assertFalse(any('丢失' in text or '推断' in text for text in labels))
                    self.assertFalse(window.match_block_checkbox.isChecked())
                    self.assertFalse(updater.match_block_enabled)
                    window.match_block_checkbox.setChecked(True)
                    self.assertTrue(updater.match_block_enabled)
                    window.commendation_text.setPlainText('合作愉快！\n+rep')
                    self.assertTrue(updater.match_tracking_enabled)
                    review.move(200, 300)
                    window.save_settings()
                    saved = yaml.safe_load(path.read_text(encoding='utf-8'))
                    self.assertNotIn('partial_map_enabled', saved)
                    self.assertTrue(saved['match_block_enabled'])
                    self.assertTrue(saved['commendation_enabled'])
                    self.assertEqual(saved['commendation_comment'], '合作愉快！\n+rep')
                    self.assertEqual((saved['commendation_x'], saved['commendation_y']), (200, 300))
                    self.assertEqual(saved['map_pattern_return_topk'], 3)
                    self.assertEqual(saved['map_region'], [100, 120, 750, 750])
                    window.load_settings()
                    self.assertTrue(updater.map_detect_enabled)
                    self.assertTrue(window.match_block_checkbox.isChecked())
                    self.assertEqual(window.commendation_text.toPlainText(), '合作愉快！\n+rep')
                    self.assertEqual((review.x(), review.y()), (200, 300))
                    window.p2p_enabled_checkbox.setChecked(False)
                    self.assertFalse(updater.match_block_enabled)
                    self.assertFalse(updater.match_tracking_enabled)
                finally:
                    window.close()
                    updater._map_jobs.close()
                    for w in (overlay, map_, hp, p2p, review): w.close()
                    service.stop()
                    playtime.stop()


if __name__ == '__main__':
    unittest.main()
