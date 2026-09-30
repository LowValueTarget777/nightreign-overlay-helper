"""Opt-in, account-scoped counts of nonfriend teammates at expedition start."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time
import uuid

from PyQt6.QtCore import QObject, QLockFile, pyqtSignal

from src.common import get_appdata_path
from .commendation import current_steam_account, valid_id
from .identity_bridge import _write_json

ROSTER_SECONDS = 60
DAY_ONE_DEBOUNCE = 120


class EncounterService(QObject):
    counts_changed = pyqtSignal(object)
    status_changed = pyqtSignal(str)

    def __init__(self, parent=None, *, directory=None, clock=time.monotonic,
                 utc_now=lambda: datetime.now(timezone.utc), account_getter=current_steam_account):
        super().__init__(parent)
        self.directory = Path(directory or get_appdata_path('encounters'))
        self.clock, self.utc_now, self.account_getter = clock, utc_now, account_getter
        self.enabled = False
        self.account = ''
        self.players = {}
        self.round = None
        self.candidates = {}
        self.saved_round = None
        self.peers = []
        self.last_snapshot = float('-inf')
        self.last_day_one = float('-inf')
        self.storage_error = False

    def configure(self, enabled):
        was_enabled = self.enabled
        self.enabled = bool(enabled)
        if self.enabled:
            self._ensure_account(reload=not was_enabled)
            self._record()
        self._publish()

    def _read(self):
        path = self.directory / f'{self.account}.json'
        if not path.exists():
            return dict(schema=1, account=self.account, players={}, last_round=None)
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError('history too large')
        book = json.loads(path.read_text(encoding='utf-8'))
        if not isinstance(book, dict) or book.get('schema') != 1 or book.get('account') != self.account:
            raise ValueError('invalid history')
        players = book.get('players')
        if not isinstance(players, dict):
            raise ValueError('invalid players')
        for target, row in players.items():
            if (not valid_id(target) or not isinstance(row, dict)
                    or type(row.get('count')) is not int or not 1 <= row['count'] <= 1_000_000_000
                    or not isinstance(row.get('name'), str) or len(row['name']) > 128):
                raise ValueError('invalid player count')
        previous = book.get('last_round')
        if previous is not None:
            if (not isinstance(previous, dict) or not isinstance(previous.get('id'), str)
                    or type(previous.get('started')) not in (int, float)
                    or not math.isfinite(previous['started'])):
                raise ValueError('invalid round')
            if datetime.fromisoformat(previous['utc']).tzinfo is None:
                raise ValueError('invalid round date')
            for key in ('members', 'seen'):
                ids = previous.get(key)
                if not isinstance(ids, list) or len(ids) > 2 or len(ids) != len(set(ids)) or not all(valid_id(i) for i in ids):
                    raise ValueError('invalid round roster')
            if not set(previous['seen']).issubset(previous['members']) or not set(previous['seen']).issubset(players):
                raise ValueError('invalid round counts')
        return book

    def _ensure_account(self, *, reload=False):
        account = self.account_getter()
        account = account if valid_id(account) else ''
        changed = account != self.account
        if changed:
            self.account, self.players, self.saved_round = account, {}, None
            self.round = None
            self.candidates = {}
            self.last_day_one = float('-inf')
        if changed or reload:
            self.storage_error = False
            if account:
                try:
                    book = self._read()
                    self.players, self.saved_round = book['players'], book['last_round']
                except (OSError, ValueError, KeyError, TypeError):
                    self.storage_error = True
                    self.status_changed.emit('次数记录无法读取，已暂停写入；原文件保留')
            self._publish()
        return bool(account) and not self.storage_error

    def _same_start(self, previous, started, utc):
        if previous is None:
            return False
        mono_delta = started - previous['started']
        utc_delta = (utc - datetime.fromisoformat(previous['utc'])).total_seconds()
        # The boot clock plus UTC distinguish a replayed DAY1 after restarting
        # the assistant from a new expedition or a Windows reboot.
        return (abs(mono_delta) < DAY_ONE_DEBOUNCE and abs(utc_delta) < DAY_ONE_DEBOUNCE
                and abs(mono_delta - utc_delta) < 10)

    def on_day_one(self, detected_at):
        if not self.enabled or not 0 <= self.clock() - detected_at <= 5:
            return
        if not self._ensure_account() or detected_at - self.last_day_one < DAY_ONE_DEBOUNCE:
            return
        self.last_day_one = detected_at
        self.candidates = {}
        utc = self.utc_now()
        if self._same_start(self.saved_round, detected_at, utc):
            self.round = dict(self.saved_round)
        else:
            self.round = dict(id=uuid.uuid4().hex, started=detected_at, utc=utc.isoformat(), members=[], seen=[])
        self._record()
        self._publish()

    def set_peers(self, peers):
        self.peers = [p for p in peers if valid_id(p.steam_id) and p.state in {'connected', 'connecting'}][:2]
        self.last_snapshot = self.clock()
        if self.enabled and self._ensure_account():
            self._record()

    def set_status(self, status):
        if status.state != 'steam_ready':
            self.last_snapshot = float('-inf')
        if status.state in {'waiting_for_game', 'disabled'}:
            self.round = None
            self.candidates = {}

    def _record(self):
        if not self.enabled or not self.account or self.storage_error or self.round is None:
            return
        if (0 <= self.clock() - self.round['started'] <= ROSTER_SECONDS
                and 0 <= self.clock() - self.last_snapshot <= 5):
            for p in self.peers:
                members = set(self.round['members']) | self.candidates.keys()
                if p.steam_id in members or len(members) < 2:
                    self.candidates[p.steam_id] = p
        members, seen = self.round['members'], self.round['seen']
        # Ignored (5) is still a nonfriend: our name-display feature may have
        # blocked communication before DAY1. Friends (3/6) and unknowns skip.
        if not any(p.steam_id not in members or (p.relationship in {0, 5} and p.steam_id not in seen) for p in self.candidates.values()):
            return
        lock = None
        try:
            self.directory.mkdir(parents=True, exist_ok=True)
            lock = QLockFile(str(self.directory / f'{self.account}.lock'))
            if not lock.tryLock(0):
                self.status_changed.emit('次数记录正在使用，下一次队友更新重试')
                return
            book = self._read()
            previous = book['last_round']
            active = self.round
            if previous and previous['id'] != active['id']:
                if self._same_start(previous, active['started'], datetime.fromisoformat(active['utc'])):
                    active = previous
                elif datetime.fromisoformat(previous['utc']) > datetime.fromisoformat(active['utc']):
                    self.round = None
                    return
            elif previous:
                active = previous
            active = dict(active, members=list(active['members']), seen=list(active['seen']))
            for p in self.candidates.values():
                if p.steam_id not in active['members'] and len(active['members']) < 2:
                    active['members'].append(p.steam_id)
                if p.steam_id in active['members'] and p.steam_id not in active['seen'] and p.relationship in {0, 5}:
                    old = book['players'].get(p.steam_id, {})
                    book['players'][p.steam_id] = dict(count=old.get('count', 0) + 1, name=p.name[:128])
                    active['seen'].append(p.steam_id)
            book['last_round'] = active
            if book['players'] != self.players or active != previous:
                _write_json(self.directory / f'{self.account}.json', book)
            # Only publish a count and remember deduplication after durable save.
            self.players, self.round, self.saved_round = book['players'], active, active
            self.status_changed.emit('次数已保存；本局同一人掉线重连不会重复计数')
            self._publish()
        except (OSError, ValueError, KeyError, TypeError):
            self.status_changed.emit('次数保存失败，下一次队友更新重试；原记录保留')
        finally:
            if lock is not None and lock.isLocked():
                lock.unlock()

    def _publish(self):
        counts = {target: row['count'] for target, row in self.players.items()} if self.enabled else None
        if counts != getattr(self, '_last_counts', object()):
            self._last_counts = counts
            self.counts_changed.emit(counts)
        if not self.enabled:
            self.status_changed.emit('次数记录已关闭，已有记录保留')
        elif not self.account:
            self.status_changed.emit('次数记录等待 Steam 登录')
        elif self.round is None and not self.storage_error:
            self.status_changed.emit('等待下一局 DAY1，开始记录非好友队友次数')


def encounter_text(counts, target):
    """Omit unrecorded players, including friends and unknown relationships."""
    count = counts.get(target, 0) if counts is not None else 0
    return f'累计匹配 {count} 次' if count else ''
