"""Per-target communication status and Qt presentation; no Steam operations."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

from dataclasses import replace
import unittest
from unittest.mock import patch

from PyQt6.QtCore import Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication

from scripts.test_match_block import Bridge, peer
from src.p2p.block_status import HISTORY_SECONDS, PeerBlockStatus
from src.p2p.match_block import MatchBlockController
from src.p2p.models import P2PStatus
from src.ui.commendation_overlay import CommendationOverlayWidget
from src.ui.p2p_overlay import P2POverlayWidget, P2POverlayUIState


class BlockStatusTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.now = 1000.
        self.bridges = [Bridge(), Bridge()]
        self.legacy = Bridge()
        self.controller = MatchBlockController(bridges=self.bridges, legacy=self.legacy, clock=lambda: self.now)
        self.controller.set_enabled(True)

    def state(self, n=1):
        return self.controller.peer_statuses[peer(n).steam_id]

    def report(self, index, state, **kwargs):
        bridge = self.bridges[index]
        bridge.status.update(state=state, **kwargs)
        if state in {'restored', 'error'}:
            bridge._pending = None
        self.controller.tick()

    def test_two_independent_targets_prepare_without_claiming_success(self):
        self.controller.set_peers([peer(1), peer(2)])
        self.assertEqual(self.state(1).text, '准备屏蔽')
        self.assertEqual(self.state(2).text, '准备屏蔽')
        self.report(0, 'blocked')
        self.assertEqual(self.state(1).text, '已屏蔽')
        self.assertEqual(self.state(2).text, '准备屏蔽')
        self.controller.set_peers([replace(peer(1), name='新名字'), peer(2)])
        self.assertEqual(self.state(1).name, '新名字')

    def test_departure_restore_retry_and_completion_survive_slot_release(self):
        self.controller.set_peers([peer(1), peer(2)])
        self.report(0, 'blocked')
        for _ in range(11):
            self.now += 1
            self.controller.set_peers([peer(2)])
        self.assertEqual(self.state().text, '解除中')
        self.assertFalse(self.state().active)
        self.report(0, 'recovery_pending', message='断网，等待恢复')
        self.assertEqual(self.state().text, '恢复待重试')
        self.report(0, 'restored')
        self.assertEqual(self.state().text, '已解除')
        self.assertFalse(self.state().pending)
        self.assertNotIn(0, self.controller.assigned)
        self.report(0, 'idle', target='', transaction='')
        self.assertEqual(self.state().text, '已解除')
        self.assertEqual(self.state(2).text, '准备屏蔽')
        self.now += HISTORY_SECONDS + 1
        self.controller.tick()
        self.assertNotIn(peer().steam_id, self.controller.peer_statuses)

    def test_retry_has_truthful_failure_then_returns_to_preparation(self):
        self.controller.set_peers([peer()])
        self.report(0, 'error', diagnostic_code='page_timeout', accepts_begin=True)
        self.assertEqual(self.state().text, '失败待重试')
        self.report(0, 'idle', diagnostic_code='', accepts_begin=True)
        self.assertEqual(self.state().text, '失败待重试')
        self.now += 30
        self.controller.set_peers([peer()])
        self.assertEqual(self.state().text, '准备屏蔽')
        self.assertEqual(len(self.bridges[0].begins), 2)

    def test_friends_original_block_unknown_relationship_and_invalid_ping(self):
        cases = ((3, '好友跳过'), (6, '好友跳过'), (5, '原有屏蔽 · 不解除'),
                 (None, '关系未确认 · 已跳过'))
        for relationship, text in cases:
            self.controller.set_peers([peer(1, relationship)])
            self.assertEqual(self.state().text, text)
        self.controller.set_peers([replace(peer(), ping_ms=None)])
        self.assertEqual(self.state().text, '等待有效延迟')
        self.assertFalse(self.bridges[0].begins)
        self.controller.set_enabled(False)
        self.assertFalse(self.controller.peer_statuses)

    def test_preexisting_web_block_is_not_tool_success_or_auto_restore(self):
        self.controller.set_peers([peer()])
        self.report(0, 'error', diagnostic_code='page_preblocked', accepts_begin=True)
        self.assertEqual(self.state().text, '原有屏蔽 · 不解除')
        self.assertFalse(self.state().pending)
        self.controller.set_peers([])
        self.controller.set_enabled(False)
        self.assertEqual(self.bridges[0].restores, 0)
        self.assertEqual(self.state().text, '原有屏蔽 · 不解除')

    def test_restart_recovers_unassigned_old_target_and_preserves_blank_heartbeat(self):
        target = peer().steam_id
        self.report(0, 'recovery_pending', target=target, transaction='a'*32)
        self.assertEqual(self.state().name, target)
        self.assertFalse(self.controller.assigned)
        self.assertEqual(self.state().text, '恢复待重试')
        self.report(0, 'recovery_pending', target='', transaction='', message='监督组件等待恢复')
        self.assertEqual(self.state().text, '恢复待重试')
        self.report(0, 'restoring', target=target, transaction='a'*32)
        self.assertEqual(self.state().text, '解除中')
        self.report(0, 'restored', target=target, transaction='a'*32)
        self.assertEqual(self.state().text, '已解除')
        self.report(0, 'preparing', target=target, transaction='a'*32, message='重新连接组件')
        self.assertEqual(self.state().text, '已解除')
        self.report(0, 'error', target='', transaction='', message='未找到组件')
        self.assertEqual(self.state().text, '已解除')

    def test_unconfirmed_idle_is_never_displayed_as_success(self):
        self.report(0, 'recovery_pending', target=peer().steam_id, transaction='a'*32)
        self.report(0, 'idle', target='', transaction='')
        self.assertEqual(self.state().text, '恢复待确认')
        self.now += HISTORY_SECONDS * 2
        self.controller.tick()
        self.assertTrue(self.state().pending)

    def test_game_exit_without_final_snapshot_marks_peer_as_history(self):
        self.controller.set_peers([peer()])
        self.report(0, 'blocked')
        self.controller.set_status(P2PStatus('game_exited', ''))
        self.assertEqual(self.state().text, '解除中')
        self.assertFalse(self.state().active)
        self.report(0, 'restored')
        self.now += HISTORY_SECONDS + 1
        self.controller.tick()
        self.assertNotIn(peer().steam_id, self.controller.peer_statuses)

    def test_terminal_is_not_erased_when_same_slot_gets_different_teammate(self):
        self.controller.set_peers([peer()])
        self.report(0, 'restored', accepts_begin=True)
        self.controller.set_peers([peer(2)])
        self.assertEqual(self.state().text, '已解除')
        self.assertEqual(self.state(2).text, '准备屏蔽')
        self.assertFalse(self.state().active)

    def test_emits_only_changes_and_returns_an_independent_snapshot(self):
        changes = []
        self.controller.peer_statuses_changed.connect(changes.append)
        self.controller.set_peers([peer()])
        self.assertEqual(len(changes), 1)
        self.controller.tick()
        self.assertEqual(len(changes), 1)
        snapshot = self.controller.peer_statuses
        snapshot.clear()
        self.assertIn(peer().steam_id, self.controller.peer_statuses)

    def test_real_bridge_startup_recovery_and_supervisor_heartbeat_keep_target(self):
        from datetime import datetime, timezone
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from src.p2p import identity_bridge as ipc
        with TemporaryDirectory() as directory, patch.object(ipc, 'identity_directory', return_value=Path(directory)), \
                patch.object(ipc, '_launch_worker') as launch:
            bridge = ipc.IdentityBridge(slot=0)
            controller = MatchBlockController(bridges=[bridge, Bridge()], legacy=Bridge(), clock=lambda: self.now)

            def report(state, target='', transaction=''):
                ipc._write_json(bridge._root / 'status.json', dict(state=state, target=target, transaction=transaction,
                    message='test', accepts_begin=state == 'idle', steam_lifecycle=ipc._MATCH_LIFECYCLE,
                    updated_at=datetime.now(timezone.utc).isoformat()))
                bridge._poll()

            try:
                report('recovery_pending', peer().steam_id, 'b'*32)
                bridge.start()
                bridge._timer.stop()
                self.assertFalse(bridge.pending)
                self.assertEqual(controller.peer_statuses[peer().steam_id].text, '恢复待重试')
                report('recovery_pending')
                self.assertEqual(controller.peer_statuses[peer().steam_id].text, '恢复待重试')
                report('restoring', peer().steam_id, 'b'*32)
                self.assertEqual(controller.peer_statuses[peer().steam_id].text, '解除中')
                report('restored', peer().steam_id, 'b'*32)
                report('idle')
                self.assertEqual(controller.peer_statuses[peer().steam_id].text, '已解除')
                launch.assert_not_called()
            finally:
                bridge.stop()

    def test_recent_completed_history_is_bounded_but_pending_is_retained(self):
        self.legacy.status.update(state='recovery_pending', target=peer(10).steam_id, transaction='a'*32)
        self.controller.tick()
        for number in range(1, 7):
            self.now += 1
            self.report(0, 'restored', target=peer(number).steam_id, transaction=f'{number:032x}')
        self.assertEqual(len(self.controller.peer_statuses), 5)
        self.assertIn(peer(10).steam_id, self.controller.peer_statuses)
        self.assertNotIn(peer().steam_id, self.controller.peer_statuses)


class BlockStatusOverlayTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        with patch('src.ui.p2p_overlay.set_widget_always_on_top'), patch('src.ui.commendation_overlay.set_widget_always_on_top'):
            self.overlay = P2POverlayWidget()
            self.commendation = CommendationOverlayWidget()
        self.players = [peer(1), peer(2)]
        self.overlay.set_status(P2PStatus('steam_ready', ''))
        self.overlay.set_peers(self.players)
        self.rows = [dict(steam_id=p.steam_id, name=p.name, enabled=True, status='可以点赞', comment='感谢') for p in self.players]
        self.commendation.set_rows(self.rows)
        self.overlay.show()
        self.app.processEvents()

    def tearDown(self):
        for widget in (self.overlay, self.commendation):
            widget.close()
            widget.deleteLater()
        self.app.processEvents()

    def statuses(self, **kwargs):
        return {p.steam_id: PeerBlockStatus(p.steam_id, p.name, 'blocked', '已屏蔽', active=True, pending=True, **kwargs)
                for p in self.players}

    def test_departed_status_uses_separate_footer_without_ping(self):
        statuses = self.statuses()
        self.overlay.set_block_statuses(statuses)
        self.overlay.set_peers([self.players[1]])
        statuses[self.players[0].steam_id] = replace(statuses[self.players[0].steam_id], state='restoring', text='解除中', active=False)
        self.overlay.set_block_statuses(statuses)
        self.assertEqual(len(self.overlay._name_labels), 1)
        footer = self.overlay.block_history.itemAt(0).widget()
        self.assertEqual(footer.text(), '近期队友 · Anonymous\n解除中')
        self.assertNotIn('ms', footer.text())
        self.overlay.set_peers([])
        self.overlay.set_status(P2PStatus('game_exited', ''))
        self.overlay.timerEvent(None)
        self.assertTrue(self.overlay.isVisible())
        self.overlay.set_block_statuses({})
        self.overlay.timerEvent(None)
        self.assertFalse(self.overlay.isVisible())

    def test_game_exit_hides_old_ping_even_before_empty_snapshot(self):
        self.overlay.set_block_statuses(self.statuses())
        self.overlay.set_status(P2PStatus('game_exited', ''))
        self.overlay.timerEvent(None)
        self.assertTrue(self.overlay.isVisible())
        self.assertEqual(self.overlay.peer_grid.count(), 0)
        self.assertEqual(self.overlay.block_history.count(), 2)
        self.assertEqual(self.overlay.status_label.text(), '游戏已退出 · 等待通信恢复')

    def test_status_refresh_preserves_both_profile_and_commendation_click_targets(self):
        statuses = self.statuses()
        for widget in (self.overlay, self.commendation):
            widget.set_block_statuses(statuses)
        self.app.processEvents()
        requests = []
        self.commendation.commend_requested.connect(requests.append)
        with patch('src.ui.p2p_overlay.QDesktopServices.openUrl', return_value=True) as opened:
            for widget, labels in ((self.overlay, '_name_labels'), (self.commendation, '_name_labels'),
                                   (self.commendation, '_commend_buttons')):
                label = getattr(widget, labels)[self.players[1].steam_id]
                QTest.mousePress(label, Qt.MouseButton.LeftButton, pos=label.rect().center())
                widget.set_block_statuses(statuses)
                self.app.processEvents()
                self.assertIs(getattr(widget, labels)[self.players[1].steam_id], label)
                QTest.mouseRelease(label, Qt.MouseButton.LeftButton, pos=label.rect().center())
                self.app.processEvents()
            self.assertEqual(opened.call_count, 2)
            for call in opened.call_args_list:
                self.assertEqual(call.args[0].toString(), f'steam://url/SteamIDPage/{self.players[1].steam_id}')
        self.assertEqual(requests, [self.players[1].steam_id])

    def test_long_plain_names_do_not_push_ping_outside_scaled_panel(self):
        long_name = '<b>长名字</b>' * 30
        self.overlay.set_peers([replace(self.players[0], name=long_name)])
        self.commendation.set_rows([dict(self.rows[0], name=long_name)])
        for scale in (.5, 1., 2.):
            self.overlay.update_ui_state(P2POverlayUIState(scale=scale))
            self.commendation.update_ui_state(P2POverlayUIState(scale=scale))
            self.overlay.set_block_statuses(self.statuses())
            self.commendation.set_block_statuses(self.statuses())
            self.app.processEvents()
            self.assertEqual(self.overlay.width(), int(310 * scale))
            name = self.overlay._name_labels[self.players[0].steam_id]
            ping = self.overlay.peer_grid.itemAtPosition(0, 1).widget()
            self.assertLessEqual(name.geometry().right(), ping.geometry().left())
            self.assertLess(ping.geometry().right(), self.overlay.width())
            self.assertEqual(name.textFormat(), Qt.TextFormat.PlainText)
            self.assertIn(long_name, name.toolTip())


if __name__ == '__main__':
    unittest.main()
