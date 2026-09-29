"""Offline production policy tests: no Steam, account or native processes."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
import unittest
from types import SimpleNamespace as NS
from unittest.mock import Mock, patch
from PyQt6.QtCore import QCoreApplication
from PyQt6.QtWidgets import QApplication
from src.p2p.match_block import MatchBlockController
from src.p2p.models import P2PPeer, parse_helper_message


class Bridge:
    def __init__(self):
        self.status = dict(state='idle', message='', accepts_begin=True)
        self._pending = None
        self.hold_target = ''
        self.begins = []
        self.restores = 0
        self.start = Mock()
        self.stop = Mock()

    def begin(self, target):
        self.begins.append(target)
        self._pending = 'transaction'
        self.status.update(state='preparing', accepts_begin=False)
        return True

    def restore(self):
        self.hold_target = ''
        if self._pending:
            self.restores += 1
            self.status.update(state='restoring', accepts_begin=False)

    @property
    def pending(self):
        return self._pending is not None

    def authorize_hold(self, target):
        self.hold_target = target


def peer(n=1, relationship=0):
    return P2PPeer(str(76561198049468922+n), 'Anonymous', 42, 1., 'legacy', 'connected', relationship)


class MatchPolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def setUp(self):
        self.now = 1000.
        self.bridges = [Bridge(), Bridge()]
        self.legacy = Bridge()
        self.controller = MatchBlockController(bridges=self.bridges, legacy=self.legacy, clock=lambda: self.now)
        self.controller.set_enabled(True)

    def feed(self, peers, seconds=1):
        self.now += seconds
        self.controller.set_peers(peers)

    def opening(self, peers):
        self.feed(peers)
        self.controller.on_day_one(self.now)
        self.feed(peers, 6)

    def test_lobby_has_no_blocks_and_manual_timer_cannot_authorize(self):
        for _ in range(20): self.feed([peer()], 10)
        self.assertFalse(any(b.begins for b in self.bridges))

    def test_two_nonfriends_hold_in_independent_slots(self):
        self.opening([peer(1), peer(2)])
        self.assertEqual([b.begins for b in self.bridges], [[peer(1).steam_id], [peer(2).steam_id]])
        self.feed([peer(1, 5), peer(2, 5)], 600)
        self.assertEqual([b.restores for b in self.bridges], [0, 0])

    def test_friend_unknown_and_preblocked_are_excluded(self):
        for relationship in (None, 1, 2, 3, 4, 5, 6):
            self.opening([peer(1, relationship)])
            self.assertFalse(any(b.begins for b in self.bridges))
            self.now += 200

    def test_longstanding_lobby_nonfriend_is_excluded(self):
        self.feed([peer()])
        self.feed([peer()], 130)
        self.controller.on_day_one(self.now)
        self.feed([peer()], 6)
        self.assertFalse(any(b.begins for b in self.bridges))

    def test_departed_peer_only_is_restored(self):
        self.opening([peer(1), peer(2)])
        self.feed([peer(2)])
        self.assertEqual([b.restores for b in self.bridges], [1, 0])
        self.assertEqual(self.bridges[0].hold_target, '')

    def test_staying_in_room_across_day1_does_not_restore_or_double_block(self):
        self.opening([peer()])
        self.feed([peer(1, 5)], 600)
        self.controller.on_day_one(self.now)
        self.feed([peer(1, 5)], 6)
        self.assertEqual(self.bridges[0].restores, 0)
        self.assertEqual(self.bridges[0].begins, [peer().steam_id])

    def test_stale_snapshot_restores_even_if_process_still_alive(self):
        self.opening([peer()])
        self.now += 6
        self.controller.tick()
        self.assertEqual(self.bridges[0].restores, 1)

    def test_helper_failure_restores_and_invalidates_day1(self):
        self.opening([peer()])
        self.controller.set_status(NS(state='runtime_error'))
        self.assertEqual(self.bridges[0].restores, 1)
        self.assertIsNone(self.controller.day_one)

    def test_disable_restore_and_enable_cannot_reuse_day1(self):
        self.opening([peer()])
        self.controller.set_enabled(False)
        self.assertEqual(self.bridges[0].restores, 1)
        self.controller.set_enabled(True)
        self.feed([peer(2)])
        self.assertFalse(self.bridges[1].begins)

    def test_late_or_duplicate_day1_is_not_authorization(self):
        self.feed([peer()])
        self.controller.on_day_one(self.now-10)
        self.assertIsNone(self.controller.day_one)
        self.controller.on_day_one(self.now)
        day = self.controller.day_one
        self.now += 3
        self.controller.on_day_one(self.now)
        self.assertEqual(self.controller.day_one, day)

    def test_late_joiner_outside_opening_is_excluded(self):
        self.controller.on_day_one(self.now)
        self.feed([peer()], 35)
        self.assertFalse(any(b.begins for b in self.bridges))

    def test_recovery_pending_blocks_new_operations(self):
        self.legacy.status.update(state='recovery_pending')
        self.opening([peer()])
        self.assertFalse(any(b.begins for b in self.bridges))

    def test_recovered_target_not_reblocked_this_run(self):
        self.opening([peer()])
        self.bridges[0]._pending = None
        self.bridges[0].status.update(state='restored', accepts_begin=True)
        self.feed([peer()])
        self.assertEqual(len(self.bridges[0].begins), 1)
        self.assertFalse(self.bridges[1].begins)

    def test_shutdown_keeps_native_recovery_owner(self):
        self.opening([peer()])
        self.controller.stop()
        self.assertEqual(self.bridges[0].restores, 1)
        for b in [self.legacy, *self.bridges]: b.stop.assert_called_once()

    def test_missing_relationship_in_old_helper_is_unknown(self):
        import json
        data = dict(type='snapshot', peers=[dict(steam_id=peer().steam_id, name='x', api='legacy', state='connected')])
        self.assertIsNone(parse_helper_message(json.dumps(data))[1][0].relationship)
        data['peers'][0]['relationship'] = True
        with self.assertRaises(ValueError): parse_helper_message(json.dumps(data))

    def test_day1_signal_uses_actual_detection_even_when_timer_is_off(self):
        from src.updater import Updater
        detector = Mock()
        detector.detect.return_value = NS(day_detect_result=NS(start_day1=True))
        updater = NS(dayx_detect_enabled=False, match_block_enabled=True,
                     day1_detect_region=(0, 0, 200, 100), dayx_detect_lang='chs',
                     hdr_processing_enabled=False, detector=detector,
                     _last_foreground=True, _match_day1_visible=False,
                     expedition_started=NS(emit=self.controller.on_day_one))
        self.feed([peer()])
        with patch('src.updater.time.monotonic', return_value=self.now):
            Updater.detect_and_update_dayx(updater)
        self.feed([peer()], 6)
        self.assertEqual(self.bridges[0].begins, [peer().steam_id])
        day = self.controller.day_one
        with patch('src.updater.time.monotonic', return_value=self.now):
            Updater.detect_and_update_dayx(updater)
        self.assertEqual(self.controller.day_one, day)

    def test_background_day1_does_not_authorize_block(self):
        from src.updater import Updater
        detector = Mock()
        detector.detect.return_value = NS(day_detect_result=NS(start_day1=True))
        signal = Mock()
        updater = NS(dayx_detect_enabled=False, match_block_enabled=True,
                     day1_detect_region=(0, 0, 200, 100), dayx_detect_lang='chs',
                     hdr_processing_enabled=False, detector=detector,
                     _last_foreground=False, _match_day1_visible=False, expedition_started=signal)
        Updater.detect_and_update_dayx(updater)
        signal.emit.assert_not_called()


if __name__ == '__main__': unittest.main()
