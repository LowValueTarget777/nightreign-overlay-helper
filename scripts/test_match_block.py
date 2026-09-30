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
        self.feed(peers, 6)

    def test_ping_ready_peer_starts_without_day1(self):
        self.feed([peer()])
        self.assertEqual(self.bridges[0].begins, [peer().steam_id])

    def test_missing_or_invalid_ping_waits_then_valid_ping_starts(self):
        from dataclasses import replace
        for ping in (None, -1, True):
            self.feed([replace(peer(), ping_ms=ping)])
            self.assertFalse(any(b.begins for b in self.bridges))
        self.feed([replace(peer(), ping_ms=0)])
        self.assertEqual(self.bridges[0].begins, [peer().steam_id])

    def test_three_expeditions_release_completed_slots_with_real_file_bridge(self):
        from datetime import datetime, timezone
        from pathlib import Path
        from tempfile import TemporaryDirectory
        from src.p2p import identity_bridge as ipc
        with TemporaryDirectory(prefix='nr-match-rounds-') as directory, \
                patch.object(ipc, 'identity_directory', return_value=Path(directory)), \
                patch.object(ipc, '_launch_worker') as launch:
            bridges = [ipc.IdentityBridge(slot=i) for i in (0, 1)]
            controller = MatchBlockController(bridges=bridges, legacy=Bridge(), clock=lambda: self.now)
            def status(bridge, state, transaction='', target=''):
                ipc._write_json(bridge._root/'status.json', dict(state=state, transaction=transaction, target=target,
                    message='test', accepts_begin=state in {'idle', 'restored', 'error'},
                    steam_lifecycle=ipc._MATCH_LIFECYCLE, updated_at=datetime.now(timezone.utc).isoformat()))
                bridge._poll()
            try:
                for bridge in bridges:
                    status(bridge, 'idle')
                    bridge.start()
                    bridge._timer.stop()
                controller.set_enabled(True)
                # Changed opponents, then rematch with the same opponents.
                for players in ([peer(1), peer(2)], [peer(3), peer(4)], [peer(3), peer(4)]):
                    self.now += 600
                    controller.set_peers(players)
                    self.now += 6
                    controller.set_peers(players)
                    self.assertEqual(len(controller.assigned), 2)
                    transactions = [b._pending for b in bridges]
                    self.assertTrue(all(transactions), 'next expedition failed to submit new requests')
                    for bridge, transaction, player in zip(bridges, transactions, players):
                        status(bridge, 'blocked', transaction, player.steam_id)
                    controller.set_peers([])
                    for bridge, transaction, player in zip(bridges, transactions, players):
                        self.assertTrue((bridge._root/f'restore-{transaction}.json').exists())
                        # Native completion removes the restore marker, retains
                        # the transaction in status, and writes a terminal result.
                        (bridge._root/f'restore-{transaction}.json').unlink()
                        ipc._write_json(bridge._root/'results'/f'{transaction}.json',
                            dict(state='restored', target=player.steam_id, transaction=transaction, message='done'))
                        status(bridge, 'restored', transaction, player.steam_id)
                    controller.tick()
                    self.assertFalse(controller.assigned, 'completed departed targets retained')
                    for bridge, transaction in zip(bridges, transactions):
                        self.assertEqual(bridge.status['state'], 'restored')
                        self.assertFalse((bridge._root/f'restore-{transaction}.json').exists(), 'completed restore reissued')
                launch.assert_not_called()
            finally:
                controller.stop()

    def test_two_nonfriends_hold_in_independent_slots(self):
        self.opening([peer(1), peer(2)])
        self.assertEqual([b.begins for b in self.bridges], [[peer(1).steam_id], [peer(2).steam_id]])
        self.feed([peer(1, 5), peer(2, 5)], 600)
        self.assertEqual([b.restores for b in self.bridges], [0, 0])

    def test_second_preparation_can_retry_past_opening_without_restarting_first(self):
        self.opening([peer(1), peer(2)])
        self.bridges[0].status.update(state='blocked')
        for _ in range(60):
            self.feed([peer(1, 5), peer(2)])
        self.assertEqual(self.bridges[1].status['state'], 'preparing')
        self.assertEqual(self.bridges[1].hold_target, peer(2).steam_id)
        self.assertEqual([b.restores for b in self.bridges], [0, 0])
        self.assertEqual([len(b.begins) for b in self.bridges], [1, 1])
        self.bridges[1].status.update(state='blocked')
        self.feed([peer(1, 5), peer(2, 5)])
        self.assertEqual(len(self.controller.assigned), 2)
        self.feed([peer(1, 5)])
        self.assertEqual([b.restores for b in self.bridges], [0, 1])

    def test_friend_unknown_and_preblocked_are_excluded(self):
        for relationship in (None, 1, 2, 3, 4, 5, 6):
            self.opening([peer(1, relationship)])
            self.assertFalse(any(b.begins for b in self.bridges))
            self.now += 200

    def test_longstanding_nonfriend_is_processed_once_when_ping_ready(self):
        self.feed([peer()])
        self.feed([peer()], 130)
        self.feed([peer()], 6)
        self.assertEqual(len(self.bridges[0].begins), 1)

    def test_departed_peer_only_is_restored(self):
        self.opening([peer(1), peer(2)])
        self.feed([peer(2)])
        self.assertEqual([b.restores for b in self.bridges], [1, 0])
        self.assertEqual(self.bridges[0].hold_target, '')

    def test_staying_in_room_across_day1_does_not_restore_or_double_block(self):
        self.opening([peer()])
        self.feed([peer(1, 5)], 600)
        self.feed([peer(1, 5)], 6)
        self.assertEqual(self.bridges[0].restores, 0)
        self.assertEqual(self.bridges[0].begins, [peer().steam_id])

    def test_stale_snapshot_restores_even_if_process_still_alive(self):
        self.opening([peer()])
        self.now += 6
        self.controller.tick()
        self.assertEqual(self.bridges[0].restores, 1)

    def test_helper_failure_restores_and_invalidates_snapshot(self):
        self.opening([peer()])
        self.controller.set_status(NS(state='runtime_error'))
        self.assertEqual(self.bridges[0].restores, 1)
        self.assertEqual(self.controller.last_snapshot, float("-inf"))

    def test_disabled_waits_and_enabling_midgame_processes_fresh_peers(self):
        self.controller.set_enabled(False)
        self.feed([peer()])
        self.assertFalse(self.bridges[0].begins)
        self.controller.set_enabled(True)
        self.assertEqual(self.bridges[0].begins, [peer().steam_id])

    def test_late_joiner_is_processed_when_ping_is_ready(self):
        self.feed([peer()], 200)
        self.assertEqual(self.bridges[0].begins, [peer().steam_id])

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

    def test_returning_same_peer_can_start_after_departure_recovery_finishes(self):
        self.feed([peer()])
        self.feed([])
        self.feed([peer()])
        self.assertEqual(len(self.bridges[0].begins), 1)
        self.bridges[0]._pending = None
        self.bridges[0].status.update(state='restored', accepts_begin=True)
        self.feed([peer()])
        self.assertEqual(len(self.bridges[0].begins), 2)

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

    def test_day1_signal_is_only_for_match_roster_when_timer_is_off(self):
        from src.updater import Updater
        detector = Mock()
        detector.detect.return_value = NS(day_detect_result=NS(start_day1=True))
        signal = Mock()
        updater = NS(dayx_detect_enabled=False, match_tracking_enabled=True,
                     day1_detect_region=(0, 0, 200, 100), dayx_detect_lang='chs',
                     hdr_processing_enabled=False, detector=detector,
                     _last_foreground=True, _match_day1_visible=False, expedition_started=signal)
        Updater.detect_and_update_dayx(updater)
        Updater.detect_and_update_dayx(updater)
        signal.emit.assert_called_once()
        updater._last_foreground = False
        updater._match_day1_visible = False
        Updater.detect_and_update_dayx(updater)
        signal.emit.assert_called_once()


if __name__ == '__main__': unittest.main()
