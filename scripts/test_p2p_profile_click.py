"""Exercise actual Qt mouse events without launching Steam or game services."""
import os
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt6.QtCore import QEvent, QPoint, QPointF, Qt
from PyQt6.QtGui import QMouseEvent
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from src.p2p.models import P2PPeer, P2PStatus
from src.ui.p2p_overlay import P2POverlayWidget


class ProfileClickTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.open_patch = patch("src.ui.p2p_overlay.QDesktopServices.openUrl", return_value=True)
        self.open_url = self.open_patch.start()
        with patch("src.ui.p2p_overlay.set_widget_always_on_top"):
            self.overlay = P2POverlayWidget()
        self.peers = [
            P2PPeer("76561198000000001", "<b>队友一</b>", 30, 0.99, "new", "connected"),
            P2PPeer("76561198000000002", "队友二", None, None, "legacy", "connected"),
        ]
        self.overlay.set_status(P2PStatus("steam_ready", ""))
        self.overlay.set_peers(self.peers)
        self.overlay.show()
        self.app.processEvents()

    def tearDown(self):
        self.overlay.close()
        self.overlay.deleteLater()
        self.app.processEvents()
        self.open_patch.stop()

    def name(self, index=0):
        return self.overlay._name_labels[self.peers[index].steam_id]

    def click(self, widget, button=Qt.MouseButton.LeftButton):
        QTest.mouseClick(widget, button, pos=widget.rect().center())
        self.app.processEvents()

    def test_each_name_opens_its_own_profile_after_mouse_release(self):
        for i in range(2):
            label = self.name(i)
            self.open_url.reset_mock()
            QTest.mousePress(label, Qt.MouseButton.LeftButton, pos=label.rect().center())
            self.open_url.assert_not_called()
            QTest.mouseRelease(label, Qt.MouseButton.LeftButton, pos=label.rect().center())
            self.open_url.assert_not_called()
            self.app.processEvents()
            self.assertEqual(self.open_url.call_count, 1)
            self.assertEqual(self.open_url.call_args.args[0].toString(),
                             f"steam://url/SteamIDPage/{self.peers[i].steam_id}")

    def test_refresh_during_click_preserves_target_and_name_as_plain_text(self):
        label = self.name()
        self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
        QTest.mousePress(label, Qt.MouseButton.LeftButton, pos=label.rect().center())
        self.overlay.set_peers(self.peers)
        self.overlay.set_playtimes({})
        self.app.processEvents()
        self.assertIs(self.name(), label)
        QTest.mouseRelease(label, Qt.MouseButton.LeftButton, pos=label.rect().center())
        self.app.processEvents()
        self.assertEqual(self.open_url.call_count, 1)

    def test_drag_from_name_moves_overlay_without_opening_profile(self):
        self.overlay._set_draggable(True)
        label = self.name()
        start = label.rect().center()
        old_position = self.overlay.pos()
        QTest.mousePress(label, Qt.MouseButton.LeftButton, pos=start)
        delta = QPoint(QApplication.startDragDistance() + 20, 0)
        event = QMouseEvent(QEvent.Type.MouseMove, QPointF(start + delta),
                            QPointF(label.mapToGlobal(start) + delta),
                            Qt.MouseButton.NoButton, Qt.MouseButton.LeftButton,
                            Qt.KeyboardModifier.NoModifier)
        self.app.sendEvent(label, event)
        QTest.mouseRelease(label, Qt.MouseButton.LeftButton, pos=start)
        self.app.processEvents()
        self.assertEqual(self.overlay.pos(), old_position + delta)
        self.open_url.assert_not_called()

    def test_simple_click_also_works_when_draggable(self):
        self.overlay._set_draggable(True)
        self.click(self.name())
        self.open_url.assert_called_once()

    def test_right_click_still_opens_context_menu(self):
        calls = []
        self.overlay.right_click_signal.connect(lambda: calls.append(True))
        self.click(self.name(), Qt.MouseButton.RightButton)
        self.assertEqual(calls, [True])
        self.open_url.assert_not_called()

    def test_ping_and_middle_button_do_not_open_profile(self):
        self.click(self.overlay.peer_grid.itemAtPosition(0, 1).widget())
        self.click(self.name(), Qt.MouseButton.MiddleButton)
        self.open_url.assert_not_called()

    def test_release_outside_name_cancels_click(self):
        label = self.name()
        QTest.mousePress(label, Qt.MouseButton.LeftButton, pos=label.rect().center())
        QTest.mouseRelease(label, Qt.MouseButton.LeftButton, pos=QPoint(-10, -10))
        self.app.processEvents()
        self.open_url.assert_not_called()

    def test_departed_peer_does_not_open_stale_profile(self):
        self.overlay.set_peers([])
        self.overlay._open_steam_profile(self.peers[0].steam_id)
        self.open_url.assert_not_called()

    def test_invalid_id_never_reaches_url_handler(self):
        for steam_id in ("", "../bad", "76561198000000001/extra", "１" * 17, "9" * 21):
            self.overlay.set_peers([P2PPeer(steam_id, "name", 1, 1, "new", "connected")])
            self.overlay._open_steam_profile(steam_id)
        self.open_url.assert_not_called()

    def test_launch_failure_shows_feedback(self):
        self.open_url.return_value = False
        with patch("src.ui.p2p_overlay.QToolTip.showText") as tooltip:
            self.click(self.name())
            tooltip.assert_called_once()


if __name__ == "__main__":
    unittest.main()
