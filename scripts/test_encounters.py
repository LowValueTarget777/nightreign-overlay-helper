"""Encounter history: real durable files, match boundaries, and Qt name reuse."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from dataclasses import replace
from datetime import datetime, timedelta, timezone
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from PyQt6.QtCore import QLockFile, Qt
from PyQt6.QtTest import QTest
from PyQt6.QtWidgets import QApplication, QLabel
from src.p2p.encounters import EncounterService
from src.p2p.models import P2PPeer, P2PStatus
from src.ui.p2p_overlay import P2POverlayWidget
from src.ui.commendation_overlay import CommendationOverlayWidget

ACCOUNT = '76561198049468922'
OTHER_ACCOUNT = str(int(ACCOUNT) + 10)
TARGET = str(int(ACCOUNT) + 1)


def peer(target=TARGET, relationship=0, name='队友'):
    return P2PPeer(target, name, 42, 1., 'legacy', 'connected', relationship)


class EncounterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.now = 1000.
        self.account = ACCOUNT
        self.services = []
        self.service = self.new_service()
        self.counts = None
        self.service.counts_changed.connect(lambda counts: setattr(self, 'counts', counts))

    def new_service(self):
        service = EncounterService(directory=self.root, clock=lambda: self.now,
            utc_now=lambda: datetime(2026, 9, 30, tzinfo=timezone.utc) + timedelta(seconds=self.now),
            account_getter=lambda: self.account)
        self.services.append(service)
        return service

    def tearDown(self):
        for service in self.services:
            service.deleteLater()
        self.app.processEvents()
        self.tmp.cleanup()

    def start(self, players=None, service=None):
        service = service or self.service
        service.configure(True)
        service.set_peers(players if players is not None else [peer()])
        service.on_day_one(self.now)

    def book(self, account=ACCOUNT):
        return json.loads((self.root / f'{account}.json').read_text(encoding='utf-8'))

    def test_default_off_does_not_create_history_and_lobby_does_not_count(self):
        self.service.set_peers([peer()])
        self.service.on_day_one(self.now)
        self.assertFalse(list(self.root.iterdir()))
        self.service.configure(True)
        self.service.set_peers([peer()])
        self.assertEqual(self.counts, {})
        self.assertFalse(list(self.root.iterdir()))

    def test_two_nonfriends_count_once_despite_refresh_reconnect_and_name_change(self):
        players = [peer(), peer(str(int(TARGET) + 1))]
        self.start(players)
        for i in range(10):
            self.now += 1
            self.service.set_peers([] if i % 2 else [replace(p, name='<b>新名字</b>', state='connecting') for p in players])
        self.service.set_peers(players)
        self.service.on_day_one(self.now)
        self.assertEqual(self.counts, {p.steam_id: 1 for p in players})
        self.assertEqual(self.book()['last_round']['seen'], [p.steam_id for p in players])

    def test_same_player_in_next_round_increments_and_updates_name(self):
        self.start()
        self.now += 1800
        self.service.set_peers([peer(name='改名之后')])
        self.service.on_day_one(self.now)
        self.assertEqual(self.counts[TARGET], 2)
        self.assertEqual(self.book()['players'][TARGET]['name'], '改名之后')

    def test_friends_unknown_and_pending_relationships_skip_blocked_nonfriends_count(self):
        for relationship in (None, 1, 2, 3, 4, 6, 7, 8):
            self.now += 1800
            self.start([peer(relationship=relationship)])
            self.assertNotIn(TARGET, self.counts)
        self.now += 1800
        self.start([peer(relationship=5)])
        self.assertEqual(self.counts[TARGET], 1)

    def test_unknown_relationship_becoming_known_and_late_initial_snapshot(self):
        self.start([])
        self.now += 2
        self.service.set_peers([peer(relationship=None)])
        self.assertNotIn(TARGET, self.counts)
        self.now += 2
        self.service.set_peers([peer()])
        self.assertEqual(self.counts[TARGET], 1)

    def test_stale_day1_or_stale_snapshot_cannot_count(self):
        self.service.configure(True)
        self.service.set_peers([peer()])
        self.now += 6
        self.service.on_day_one(self.now - 6)
        self.assertIsNone(self.service.round)
        self.service.on_day_one(self.now)
        self.assertNotIn(TARGET, self.counts)
        self.service.set_status(P2PStatus('helper_error', 'offline'))
        self.service.configure(True)
        self.assertNotIn(TARGET, self.counts)
        self.service.set_peers([peer()])
        self.assertEqual(self.counts[TARGET], 1)

    def test_only_first_two_start_players_and_no_later_lobby_enrollment(self):
        friend = peer(str(int(TARGET) + 1), relationship=3)
        self.start([peer(), friend])
        self.now += 10
        third = peer(str(int(TARGET) + 2))
        self.service.set_peers([friend, third])
        self.assertNotIn(third.steam_id, self.counts)
        self.now += 1800
        self.service.on_day_one(self.now)
        self.now += 61
        self.service.set_peers([peer(str(int(TARGET) + 3))])
        self.assertNotIn(str(int(TARGET) + 3), self.counts)

    def test_toggle_preserves_counts_and_does_not_duplicate_current_round(self):
        self.start()
        self.service.configure(False)
        self.assertIsNone(self.counts)
        self.assertEqual(self.book()['players'][TARGET]['count'], 1)
        self.service.configure(True)
        self.assertEqual(self.counts[TARGET], 1)
        self.service.set_peers([peer()])
        self.assertEqual(self.counts[TARGET], 1)

    def test_restart_loads_history_but_requires_day1_and_deduplicates_replay(self):
        self.start()
        self.now += 5
        restarted = self.new_service()
        restarted.configure(True)
        restarted.set_peers([peer()])
        self.assertIsNone(restarted.round)
        restarted.on_day_one(self.now)
        self.assertEqual(restarted.players[TARGET]['count'], 1)
        self.now += 1800
        restarted.set_peers([peer()])
        restarted.on_day_one(self.now)
        self.assertEqual(restarted.players[TARGET]['count'], 2)

    def test_windows_reboot_clock_does_not_confuse_new_round_with_previous(self):
        self.start()
        old_utc = datetime(2026, 10, 1, tzinfo=timezone.utc)
        self.now = 10.
        restarted = self.new_service()
        restarted.utc_now = lambda: old_utc
        self.start(service=restarted)
        self.assertEqual(restarted.players[TARGET]['count'], 2)

    def test_account_switch_separates_history_and_waits_for_new_day1(self):
        self.start()
        self.account = OTHER_ACCOUNT
        self.service.set_peers([peer()])
        self.assertEqual(self.counts, {})
        self.assertIsNone(self.service.round)
        self.service.on_day_one(self.now)
        self.assertEqual(self.counts[TARGET], 1)
        self.assertEqual(self.book(ACCOUNT)['players'][TARGET]['count'], 1)
        self.assertEqual(self.book(OTHER_ACCOUNT)['players'][TARGET]['count'], 1)
        self.account = ''
        self.service.set_peers([peer()])
        self.assertEqual(self.counts, {})

    def test_failed_save_retries_without_advancing_count_or_losing_pending_player(self):
        self.service.configure(True)
        self.service.set_peers([peer()])
        with patch('src.p2p.encounters._write_json', side_effect=OSError('disk unavailable')):
            self.service.on_day_one(self.now)
            self.assertNotIn(TARGET, self.counts)
            self.assertEqual(self.service.round['seen'], [])
        self.now += 90
        self.service.set_peers([])
        self.assertEqual(self.counts[TARGET], 1)
        self.assertEqual(self.book()['players'][TARGET]['count'], 1)

    def test_corrupt_history_is_preserved_and_not_overwritten(self):
        path = self.root / f'{ACCOUNT}.json'
        path.write_text('{damaged', encoding='utf-8')
        self.start()
        self.assertTrue(self.service.storage_error)
        self.assertEqual(path.read_text(encoding='utf-8'), '{damaged')

    def test_concurrent_instances_share_round_and_lock_contention_retries(self):
        self.service.configure(True)
        self.service.set_peers([peer()])
        second = self.new_service()
        second.configure(True)
        second.set_peers([peer()])
        lock = QLockFile(str(self.root / f'{ACCOUNT}.lock'))
        self.assertTrue(lock.tryLock(0))
        try:
            self.service.on_day_one(self.now)
            self.assertNotIn(TARGET, self.counts)
        finally:
            lock.unlock()
        self.service.set_peers([peer()])
        second.on_day_one(self.now)
        self.assertEqual(second.players[TARGET]['count'], 1)
        self.assertEqual(self.book()['players'][TARGET]['count'], 1)

    def test_count_refresh_preserves_plain_text_click_target_and_separate_windows(self):
        with patch('src.ui.p2p_overlay.set_widget_always_on_top'), patch('src.ui.commendation_overlay.set_widget_always_on_top'):
            ping, review = P2POverlayWidget(), CommendationOverlayWidget()
            try:
                ping.set_peers([peer(name='<b>队友</b>')])
                review.set_rows([dict(steam_id=TARGET, name='队友', status='点击名字留言点赞', enabled=True, comment='合作愉快')])
                ping.show()
                self.app.processEvents()
                label = ping._name_labels[TARGET]
                self.assertEqual(label.textFormat(), Qt.TextFormat.PlainText)
                with patch('src.ui.p2p_overlay.QDesktopServices.openUrl', return_value=True) as opened:
                    QTest.mousePress(label, Qt.MouseButton.LeftButton, pos=label.rect().center())
                    ping.set_encounter_counts({TARGET: 3})
                    review.set_encounter_counts({TARGET: 3})
                    self.assertIs(label, ping._name_labels[TARGET])
                    QTest.mouseRelease(label, Qt.MouseButton.LeftButton, pos=label.rect().center())
                    self.app.processEvents()
                    opened.assert_called_once()
                    self.assertIn(TARGET, opened.call_args.args[0].toString())
                self.assertTrue(any(w.text() == '累计匹配 3 次' for w in ping.findChildren(QLabel)))
                self.assertTrue(any('累计匹配 3 次 · 点击' in w.text() for w in review.findChildren(QLabel)))
                ping.set_encounter_counts(None)
                self.app.sendPostedEvents(None, 0)
                self.assertEqual(ping.peer_grid.count(), 3)
                self.assertIs(label, ping._name_labels[TARGET])
            finally:
                ping.close(); review.close()
                ping.deleteLater(); review.deleteLater()


if __name__ == '__main__':
    unittest.main()
