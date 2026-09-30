"""No Steam/network calls: match roster, explicit Qt click and durable cooldown UI."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from datetime import datetime, timedelta, timezone
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch, Mock
import unittest
from PyQt6.QtWidgets import QApplication
from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from src.p2p.commendation import CommendationService
from src.p2p.identity_bridge import _write_json
from src.p2p.models import P2PPeer, P2PStatus
from src.ui.p2p_overlay import P2POverlayWidget
from src.ui.commendation_overlay import CommendationOverlayWidget

ACCOUNT = '76561198049468922'
def peers():
    return [P2PPeer(str(int(ACCOUNT)+i), f'<b>队友{i}</b>', 42, 1, 'legacy', 'connected', 0) for i in (1, 2)]

class CommendationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.app = QApplication.instance() or QApplication([])
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = 1000.
        self.patches = [patch('src.p2p.commendation.current_steam_account', return_value=ACCOUNT),
                        patch('src.p2p.commendation.identity_directory', return_value=self.root)]
        for p in self.patches: p.start()
        self.service = CommendationService(clock=lambda: self.now, recent_path=self.root/'recent.json')
        self.service.timer.stop()
        self.rows = []
        self.service.rows_changed.connect(lambda rows: setattr(self, 'rows', rows))
    def tearDown(self):
        self.service.stop()
        self.service.deleteLater()
        self.app.processEvents()
        for p in reversed(self.patches): p.stop()
        self.tmp.cleanup()
    def feed(self, players, seconds=1):
        self.now += seconds
        self.service.set_peers(players)
    def start(self):
        self.feed(peers())
        self.service.on_day_one(self.now)
    def end(self):
        self.feed([])
        self.feed([], 3)
    def test_leaving_nonfriend_opens_review_while_invited_friend_stays(self):
        from dataclasses import replace
        with patch.object(self.service.process, 'start') as start:
            players = [peers()[0], replace(peers()[1], relationship=3)]
            self.feed(players)
            self.service.on_day_one(self.now)
            self.feed(players[1:])
            self.assertFalse(self.rows)
            self.feed(players[1:], 3)
            self.assertEqual([r['steam_id'] for r in self.rows], [players[0].steam_id])
            self.end()
            self.assertEqual([r['steam_id'] for r in self.rows], [p.steam_id for p in peers()])
            start.assert_not_called()
    def test_departure_review_deadlines_are_independent_and_not_renewed(self):
        self.start()
        self.feed(peers()[1:])
        self.feed(peers()[1:], 3)
        self.assertEqual(len(self.rows), 1)
        self.feed(peers()[1:], 90)
        self.end()
        self.assertEqual(len(self.rows), 2)
        self.now += 27
        self.service.tick()
        self.assertEqual([r['steam_id'] for r in self.rows], [peers()[1].steam_id])
        self.now += 94
        self.service.tick()
        self.assertFalse(self.rows)
    def test_dismiss_clears_review_without_sending(self):
        self.start(); self.end()
        with patch.object(self.service.process, 'start') as start:
            self.service.dismiss()
            self.assertFalse(self.rows)
            self.service.send(peers()[0].steam_id)
            start.assert_not_called()
    def test_brief_empty_or_helper_failure_does_not_finish_match(self):
        self.start()
        self.feed([])
        self.feed(peers(), 1)
        self.assertFalse(self.rows)
        self.feed([])
        self.service.set_status(P2PStatus('helper_error', 'failed'))
        self.now += 10
        self.service.tick()
        self.assertFalse(self.rows)
        self.feed(peers())
        self.feed([])
        self.now += 6
        self.service.tick()
        self.feed([])
        self.assertFalse(self.rows, 'stale helper gap counted as confirmed departure')
        self.feed([], 3)
        self.assertEqual(len(self.rows), 2)
    def test_reconnecting_session_is_not_a_departure(self):
        from dataclasses import replace
        self.start()
        self.feed([replace(p, state='connecting') for p in peers()])
        self.feed([replace(p, state='connecting') for p in peers()], 10)
        self.assertFalse(self.rows)

    def test_helper_restart_waiting_does_not_finish_but_game_exit_does(self):
        self.start()
        self.service.set_status(P2PStatus('waiting_for_game', 'starting helper'))
        self.assertFalse(self.rows)
        self.service.set_status(P2PStatus('game_exited', 'game closed'))
        self.assertEqual(len(self.rows), 2)
    def test_manual_finish_supports_staying_in_room_and_restart_keeps_names(self):
        self.feed(peers())
        self.service.finish_match()
        restarted = CommendationService(recent_path=self.root/'recent.json')
        try:
            self.assertEqual([p['steam_id'] for p in restarted.recent], [p.steam_id for p in peers()])
            restarted.tick()
            self.assertEqual(restarted._last_rows, [])
        finally: restarted.stop(); restarted.deleteLater()
    def test_review_only_after_end_and_auto_closes_at_two_minutes(self):
        self.start()
        self.assertFalse(self.rows)
        self.end()
        self.assertEqual(len(self.rows), 2)
        self.now += 119
        self.service.tick()
        self.assertEqual(len(self.rows), 2)
        self.now += 1
        self.service.tick()
        self.assertEqual(self.rows, [])
        with patch.object(self.service.process, 'start') as start:
            self.service.send(peers()[0].steam_id)
            start.assert_not_called()
    def test_next_day1_immediately_hides_previous_review(self):
        self.start()
        self.now += 1800
        self.end()
        self.assertEqual(len(self.rows), 2)
        self.now += 10
        self.feed(peers())
        self.assertEqual(len(self.rows), 2)
        self.service.on_day_one(self.now)
        self.assertFalse(self.rows)
    def test_day1_with_staying_team_does_not_open_previous_review(self):
        self.start()
        self.now += 180
        self.feed(peers())
        self.service.on_day_one(self.now)
        self.assertFalse(self.rows)
    def test_day1_before_peer_snapshot_records_first_two_names(self):
        self.service.on_day_one(self.now)
        self.feed(peers())
        self.end()
        self.assertEqual(len(self.rows), 2)
    def test_cooldown_success_and_unknown_disable_only_matching_account_target(self):
        self.start(); self.end()
        for state in ('sent', 'sending', 'unknown'):
            _write_json(self.root/'commendations'/f'{ACCOUNT}-{peers()[0].steam_id}.json',
                dict(state=state, cooldown_until=(datetime.now(timezone.utc)+timedelta(days=6)).isoformat()))
            self.service.tick()
            self.assertFalse(self.rows[0]['enabled'])
            self.assertTrue(self.rows[1]['enabled'])
        with patch('src.p2p.commendation.current_steam_account', return_value=str(int(ACCOUNT)+10)):
            self.service.tick()
            self.assertTrue(self.rows[0]['enabled'])
    def test_expired_cooldown_enables_click(self):
        self.start(); self.end()
        _write_json(self.root/'commendations'/f'{ACCOUNT}-{peers()[0].steam_id}.json',
            dict(state='sent', cooldown_until=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat()))
        self.service.tick()
        self.assertTrue(self.rows[0]['enabled'])
    def test_invalid_comment_and_pending_restore_cannot_launch(self):
        self.start(); self.end()
        with patch.object(self.service.process, 'start') as start:
            self.service.configure(True, ' ')
            self.service.send(peers()[0].steam_id)
            start.assert_not_called()
            self.service.configure(True, '感谢！')
            self.service.blocker = Mock(assigned={0: peers()[0].steam_id})
            self.service.send(peers()[0].steam_id)
            start.assert_not_called()
    def test_real_name_click_is_queued_plain_text_and_sent_once(self):
        self.start(); self.end()
        with patch('src.ui.commendation_overlay.set_widget_always_on_top'), patch.object(self.service.process, 'start') as start:
            widget = CommendationOverlayWidget()
            widget.set_rows(self.rows)
            self.service.rows_changed.connect(widget.set_rows)
            widget.commend_requested.connect(self.service.send)
            widget.show(); self.app.processEvents()
            with patch('src.p2p.commendation.resolve_helper_path', return_value=self.root/'helper.exe'):
                (self.root/'helper.exe').touch()
                label = widget._name_labels[peers()[0].steam_id]
                self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
                QTest.mousePress(label, Qt.MouseButton.LeftButton, pos=label.rect().center())
                widget.set_rows(self.rows)
                self.assertIs(label, widget._name_labels[peers()[0].steam_id])
                QTest.mouseRelease(label, Qt.MouseButton.LeftButton, pos=label.rect().center())
                start.assert_not_called()
                self.app.processEvents()
                start.assert_called_once()
                self.assertEqual(self.service._request['target'], peers()[0].steam_id)
                self.service.send(peers()[0].steam_id)
                start.assert_called_once()
            widget.close(); widget.deleteLater()
    def test_commendation_has_separate_window_position_close_and_visibility(self):
        from src.ui.p2p_overlay import P2POverlayUIState
        with patch('src.ui.p2p_overlay.set_widget_always_on_top'), patch('src.ui.commendation_overlay.set_widget_always_on_top'):
            ping = P2POverlayWidget()
            review = CommendationOverlayWidget()
            self.service.rows_changed.connect(review.set_rows)
            review.dismiss_requested.connect(self.service.dismiss)
            ping.move(50, 60)
            review.move(200, 300)
            review.update_ui_state(P2POverlayUIState(x=50, y=60))
            self.assertEqual((review.x(), review.y()), (200, 300))
            self.start(); self.end()
            self.app.processEvents()
            self.assertTrue(review.isVisible())
            self.assertFalse(hasattr(ping, 'commend_rows'))
            QTest.mouseClick(review.close_button, Qt.MouseButton.LeftButton)
            self.assertFalse(review.isVisible())
            ping.close(); review.close()
            ping.deleteLater(); review.deleteLater()

if __name__ == '__main__': unittest.main()
