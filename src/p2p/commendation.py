"""Recent expedition roster and explicitly clicked Steam profile comments."""
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import time

from PyQt6.QtCore import QObject, QProcess, QTimer, pyqtSignal

from src.common import get_appdata_path
from .identity_bridge import _read_json, _write_json, identity_directory
from .service import resolve_helper_path

DEFAULT_COMMENT = "+rep 好队友，感谢一起征战黑夜君临！"


def valid_id(value):
    return isinstance(value, str) and value.isascii() and value.isdigit() and len(value) == 17 and 76561197960265728 <= int(value) <= 76561202255233023


def current_steam_account():
    """Read the client's public active account number, never credentials."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r'Software\Valve\Steam\ActiveProcess') as key:
            value = winreg.QueryValueEx(key, 'ActiveUser')[0]
        return str(76561197960265728 + value) if type(value) is int and value > 0 else ''
    except (ImportError, OSError):
        return ''


class CommendationService(QObject):
    rows_changed = pyqtSignal(object)
    status_changed = pyqtSignal(str)

    def __init__(self, parent=None, *, clock=time.monotonic, recent_path=None, blocker=None):
        super().__init__(parent)
        self.clock = clock
        self.blocker = blocker
        self.enabled = True
        self.comment = DEFAULT_COMMENT
        self.peers = []
        self.roster = {}
        self.recent = []
        self.last_day_one = float('-inf')
        self.last_snapshot = float('-inf')
        self.empty_since = None
        self.feedback = {}
        self.busy_target = ''
        self._response = bytearray()
        self.recent_path = Path(recent_path or get_appdata_path('recent_teammates.json'))
        saved = _read_json(self.recent_path) or {}
        players = saved.get('players', [])
        for row in players[:2] if isinstance(players, list) else []:
            if isinstance(row, dict) and valid_id(row.get('steam_id')) and isinstance(row.get('name'), str):
                self.recent.append(dict(steam_id=row['steam_id'], name=row['name'][:128]))
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.ProcessChannelMode.SeparateChannels)
        self.process.started.connect(self._write_request)
        self.process.readyReadStandardOutput.connect(self._read_response)
        self.process.finished.connect(self._finished)
        self.process.errorOccurred.connect(self._process_error)
        self.timer = QTimer(self)
        self.timer.setInterval(500)
        self.timer.timeout.connect(self.tick)
        self.timer.start()

    def configure(self, enabled, comment):
        self.enabled = bool(enabled)
        self.comment = comment.strip()
        self._publish()

    def set_peers(self, peers):
        self.peers = [p for p in peers if valid_id(p.steam_id) and p.state == 'connected'][:2]
        self.last_snapshot = self.clock()
        if self.roster:
            for p in self.peers:
                if p.steam_id in self.roster or len(self.roster) < 2:
                    self.roster[p.steam_id] = dict(steam_id=p.steam_id, name=p.name[:128])
            if self.peers:
                self.empty_since = None
            elif self.empty_since is None:
                self.empty_since = self.clock()
        self.tick()

    def set_status(self, status):
        if status.state != 'steam_ready':
            self.last_snapshot = float('-inf')
            self.empty_since = None
        if status.state == 'waiting_for_game' and self.roster:
            self.finish_match()

    def on_day_one(self, detected_at):
        now = self.clock()
        if not self.enabled or not 0 <= now - detected_at <= 5 or detected_at - self.last_day_one < 120:
            return
        if self.roster:
            self.finish_match()
        self.last_day_one = detected_at
        self.roster = {p.steam_id: dict(steam_id=p.steam_id, name=p.name[:128]) for p in self.peers}
        self._tracking = True
        self.empty_since = None

    def finish_match(self):
        """Also exposed as a manual tray action for teams that stay in the lobby."""
        players = list(self.roster.values()) or [dict(steam_id=p.steam_id, name=p.name[:128]) for p in self.peers]
        if players:
            self.recent = players[:2]
            try:
                _write_json(self.recent_path, dict(players=self.recent))
            except OSError:
                self.status_changed.emit('无法保存上局队友，当前仍可点击点赞')
        self.roster = {}
        self._tracking = False
        self.empty_since = None
        self._publish()

    def tick(self):
        # DAY1 may arrive just before Steam's first teammate snapshot.
        if getattr(self, '_tracking', False) and not self.roster and self.peers:
            self.roster = {p.steam_id: dict(steam_id=p.steam_id, name=p.name[:128]) for p in self.peers}
        if (self.roster and self.empty_since is not None and self.clock() - self.empty_since >= 3
                and 0 <= self.clock() - self.last_snapshot <= 5):
            self.finish_match()
        self._publish()

    def _publish(self):
        account = current_steam_account()
        rows = []
        for p in self.recent if self.enabled else []:
            target = p['steam_id']
            text = self.feedback.get(target, '点击名字留言点赞')
            can_send = not self.busy_target
            if account:
                record = _read_json(identity_directory()/'commendations'/f'{account}-{target}.json')
                if record:
                    try:
                        seconds = (datetime.fromisoformat(record['cooldown_until']) - datetime.now(timezone.utc)).total_seconds()
                        if seconds > 0:
                            state = record.get('state')
                            remaining = f"{max(1, math.ceil(seconds/60))} 分钟" if state in {'rejected', 'precondition'} else f"{max(1, math.ceil(seconds/86400))} 天"
                            text = f"{'结果待核对' if state in {'sending', 'unknown'} else '已留言' if state == 'sent' else '稍后重试'} · 剩余 {remaining}"
                            can_send = False
                    except (KeyError, ValueError, TypeError):
                        text, can_send = '记录异常，请先核对主页', False
            if target == self.busy_target:
                text = '正在发送留言…'
            rows.append(dict(**p, status=text, enabled=can_send, comment=self.comment))
        if rows != getattr(self, '_last_rows', None):
            self._last_rows = rows
            self.rows_changed.emit(rows)

    def send(self, target):
        if not self.enabled or self.busy_target or not any(p['steam_id'] == target for p in self.recent):
            return
        if not self.comment or len(self.comment) > 500 or '\0' in self.comment:
            self.status_changed.emit('请在设置中填写 1–500 字留言')
            return
        if self.blocker and target in self.blocker.assigned.values():
            self.feedback[target] = '通信正在恢复，请稍后点击'
            self._publish()
            return
        helper = resolve_helper_path()
        if not helper.is_file():
            self.status_changed.emit('未找到留言组件')
            return
        self.busy_target = target
        self._request = dict(target=target, comment=self.comment, expected_account=current_steam_account())
        self._response.clear()
        self.process.setProgram(str(helper))
        self.process.setArguments(['--commend'])
        self.process.setWorkingDirectory(str(helper.parent))
        self.process.start()
        self._publish()

    def _write_request(self):
        self.process.write((json.dumps(self._request, ensure_ascii=False)+'\n').encode('utf-8'))
        self.process.closeWriteChannel()

    def _read_response(self):
        self._response.extend(bytes(self.process.readAllStandardOutput()))

    def _finished(self, *_):
        self._read_response()
        try:
            result = json.loads(self._response.decode('utf-8-sig').strip())
            message = result['message']
            if not isinstance(message, str):
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            message = '结果未收到，请先到主页核对再重试'
        self.feedback[self.busy_target] = message
        self.busy_target = ''
        self.status_changed.emit(message)
        self._publish()

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self.feedback[self.busy_target] = '留言组件启动失败'
            self.busy_target = ''
            self._publish()

    def stop(self):
        self.timer.stop()
        # The durable native reservation prevents duplicates if the app exits
        # during posting. Do not automatically replay a request on startup.
