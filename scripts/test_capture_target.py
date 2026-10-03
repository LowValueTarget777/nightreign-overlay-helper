"""Window selection and common detector entry, without touching desktop/Steam."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from unittest.mock import Mock, patch
from types import SimpleNamespace as NS
from PIL import Image
from src.common import GAME_WINDOW_TITLE
from src.screencap import hwnd_resolver as windows
from src.screencap.engine import ScreencapEngine
from src.screencap.errors import ScreencapRuntimeError
from src.screencap.types import EngineStatus


class WindowTargetTests(unittest.TestCase):
    def setUp(self):
        self.titles = {1: GAME_WINDOW_TITLE+' - 文件资源管理器', 2: GAME_WINDOW_TITLE,
                       3: GAME_WINDOW_TITLE+' - Google Chrome'}
        self.visible = set(self.titles)
        self.gui = Mock()
        self.gui.EnumWindows.side_effect = lambda visit, data: [visit(h, data) for h in self.titles]
        self.gui.IsWindow.side_effect = lambda h: h in self.titles
        self.gui.IsWindowVisible.side_effect = lambda h: h in self.visible
        self.gui.GetWindowText.side_effect = lambda h: self.titles[h]
        self.patch = patch.dict('sys.modules', win32gui=self.gui)
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def test_explorer_in_front_cannot_win_game_selection(self):
        self.assertEqual(windows.find_game_hwnd(GAME_WINDOW_TITLE), 2)
        self.titles = {3: self.titles[3], 1: self.titles[1], 2: self.titles[2]}
        self.assertEqual(windows.find_game_hwnd(GAME_WINDOW_TITLE), 2)

    def test_no_game_does_not_fall_back_to_folder_or_browser(self):
        del self.titles[2]
        self.assertIsNone(windows.find_game_hwnd(GAME_WINDOW_TITLE))

    def test_hidden_and_ambiguous_exact_windows_are_not_selected(self):
        self.visible.remove(2)
        self.assertIsNone(windows.find_game_hwnd(GAME_WINDOW_TITLE))
        self.visible.add(2)
        self.titles[4] = GAME_WINDOW_TITLE.lower()
        self.visible.add(4)
        self.assertIsNone(windows.find_game_hwnd(GAME_WINDOW_TITLE))

    def test_case_insensitive_exact_title_and_foreground_gate(self):
        from src.ui.utils import is_window_in_foreground
        self.titles[2] = GAME_WINDOW_TITLE.lower()
        self.assertEqual(windows.find_game_hwnd(GAME_WINDOW_TITLE), 2)
        self.gui.GetForegroundWindow.return_value = 1
        self.assertFalse(is_window_in_foreground(GAME_WINDOW_TITLE))
        self.gui.GetForegroundWindow.return_value = 2
        self.assertTrue(is_window_in_foreground(GAME_WINDOW_TITLE))

    def test_existing_capture_invalidated_if_target_changes_to_folder(self):
        engine = ScreencapEngine()
        engine._capture_hwnd = 1
        engine._status = EngineStatus.CONNECTED
        manager = engine._mgr = Mock()
        with patch.object(engine, '_decode_frame', return_value=Image.new('RGB', (2560, 1440))):
            with self.assertRaises(ScreencapRuntimeError) as failure:
                engine.grab_fullscreen()
        self.assertEqual(failure.exception.code, 'window_changed')
        self.assertEqual(engine.status, EngineStatus.FAILED)
        manager.inactive.assert_called_once()

    def test_game_geometry_annotation_is_preserved(self):
        engine = ScreencapEngine()
        engine._capture_hwnd = 2
        frame = Image.new('RGB', (2560, 1440))
        with patch('src.screencap.engine.get_capture_geometry', return_value=((0, 0, 2560, 1440), (0, 0, 2560, 1440))):
            engine._annotate_geometry(frame)
        self.assertEqual(frame.info['client_rect'], (0, 0, 2560, 1440))

    def test_wrong_folder_geometry_skips_both_detectors_then_game_restores_both(self):
        from src.updater import Updater
        frame = Image.new('RGB', (2560, 1440))
        frame.info['client_rect'] = (0, 0, 1200, 800)
        holder = NS(auto_regions_enabled=True, _auto_regions={}, _auto_regions_message=None,
                    auto_regions_status=Mock(), current_is_full_map=False, set_to_detect_map_pattern_once=Mock(),
                    hide_map_overlay=Mock(), hp_overlay_ui_state_signal=Mock(),
                    detector=NS(hp_detector=NS(recent_lengths=[], last_valid_length=None, stable_count=0)),
                    detect_and_update_dayx=Mock(), detect_and_update_in_rain=Mock(), detect_and_update_map=Mock(),
                    detect_and_update_hp=Mock(), detect_and_update_art=Mock())
        with patch('src.updater.get_engine', return_value=NS(grab_fullscreen=lambda: frame)), patch('src.updater.info') as log:
            Updater.detect_and_update_all(holder)
            holder.detect_and_update_dayx.assert_not_called()
            holder.detect_and_update_map.assert_not_called()
            self.assertIn('16:9', holder._auto_regions_message)
            frame.info['client_rect'] = (0, 0, 2560, 1440)
            Updater.detect_and_update_all(holder)
            Updater.detect_and_update_all(holder)
            self.assertEqual(holder.detect_and_update_dayx.call_count, 2)
            self.assertEqual(holder.detect_and_update_map.call_count, 2)
            self.assertEqual(log.call_count, 2)  # Only changed region state is logged.


if __name__ == '__main__': unittest.main()
