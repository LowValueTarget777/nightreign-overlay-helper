"""DAY1-gated communication hold; the independent native workers own recovery.

No HUD/name recognition. Steam friendship is an exclusion, not evidence of how
the game matched a player. Only recently appearing nonfriends are candidates.
"""
import time

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from src.logger import info
from .identity_bridge import IdentityBridge


class MatchBlockController(QObject):
    status_changed = pyqtSignal(str)

    def __init__(self, parent=None, *, bridges=None, legacy=None, clock=time.monotonic):
        super().__init__(parent)
        self.clock = clock
        self.bridges = bridges if bridges is not None else [IdentityBridge(slot=0), IdentityBridge(slot=1)]
        # Always recover older HUD-version journals too, even with this feature off.
        self.legacy = legacy if legacy is not None else IdentityBridge()
        self.enabled = False
        self.peers = {}
        self.first_seen = {}
        self.last_snapshot = float('-inf')
        self.day_one = None
        self.last_day_one = float('-inf')
        self.attempted = set()
        self.assigned = {}
        self.message = "自动显示 Steam 名字已关闭"
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self.tick)

    def start(self):
        for bridge in [self.legacy, *self.bridges]:
            bridge.start()
        self.timer.start()

    def stop(self):
        self.set_enabled(False)
        self.timer.stop()
        for bridge in [self.legacy, *self.bridges]:
            bridge.stop()

    def set_enabled(self, enabled):
        enabled = bool(enabled)
        if self.enabled == enabled:
            return
        self.enabled = enabled
        self.day_one = None  # toggling never reuses a previous expedition's permit
        if not enabled:
            for bridge in self.bridges:
                bridge.restore()
        self.tick()

    def set_peers(self, peers):
        """Fresh complete native snapshots, including unchanged/empty lists."""
        now = self.clock()
        incoming = {p.steam_id: p for p in peers}
        self.first_seen = {key: self.first_seen.get(key, now) for key in incoming}
        self.peers = incoming
        self.last_snapshot = now
        self.tick()

    def set_status(self, status):
        if status.state != "steam_ready":
            # Only a fresh snapshot may renew a hold after helper errors.
            self.last_snapshot = float('-inf')
            self.day_one = None
            self.tick()

    def on_day_one(self, detected_at):
        now = self.clock()
        if not self.enabled or not 0 <= now - detected_at <= 5 or detected_at - self.last_day_one < 120:
            return
        # A teammate who stays in the room can remain held across expeditions;
        # only fresh presence continues that lease. Never submit another block
        # for a target whose current transaction is still owned by a worker.
        self.day_one = self.last_day_one = detected_at
        self.attempted = set(self.assigned.values())
        info("Match name: DAY1 accepted; waiting for fresh nonfriend peers")
        self.tick()

    def _message(self, value):
        if self.message != value:
            self.message = value
            self.status_changed.emit(value)

    def tick(self):
        now = self.clock()
        fresh = 0 <= now - self.last_snapshot <= 5
        for index, target in list(self.assigned.items()):
            bridge = self.bridges[index]
            peer = self.peers.get(target)
            # Steam may report Ignored/Blocked after our block; that isn't a
            # reason to treat a still-connected teammate as a newly found friend.
            keep = self.enabled and fresh and peer is not None and peer.relationship != 3
            if not keep and bridge.status.get("state") not in {"restoring", "recovery_pending"}:
                bridge.restore()
            bridge.authorize_hold(target if keep and bridge.status.get("state") in {"preparing", "blocked"} else "")
            if bridge.status.get("state") in {"idle", "restored", "error"} and not bridge.pending:
                bridge.authorize_hold("")
                del self.assigned[index]

        # Do not start anything while older recovery is unresolved. Other slots
        # can stay blocked normally; their independent owners cannot starve one
        # another's recovery during a network outage.
        recovering = self.legacy.status.get("state") not in {"idle", "restored"} or any(
            b.status.get("state") in {"restoring", "recovery_pending"} for b in self.bridges)
        if self.enabled and fresh and not recovering and self.day_one is not None and 5 <= now - self.day_one <= 45:
            candidates = [p for p in self.peers.values() if p.relationship == 0
                          and p.state == "connected" and p.steam_id not in self.attempted
                          and self.day_one - 120 <= self.first_seen[p.steam_id] <= self.day_one + 30]
            for peer in candidates:
                available = next((i for i, b in enumerate(self.bridges)
                                  if i not in self.assigned and b.status.get("accepts_begin")), None)
                if available is None:
                    break
                bridge = self.bridges[available]
                bridge.authorize_hold(peer.steam_id)  # publish authorization before request
                if bridge.begin(peer.steam_id):
                    self.assigned[available] = peer.steam_id
                    self.attempted.add(peer.steam_id)
                    info("Match name: nonfriend hold requested")
                else:
                    bridge.authorize_hold("")

        busy = [b.status.get("message", "") for b in [self.legacy, *self.bridges]
                if b.status.get("state") in {"preparing", "blocked", "restoring", "recovery_pending", "error"}]
        if busy:
            message = "；".join(dict.fromkeys(busy))
        elif not self.enabled:
            message = "自动显示 Steam 名字已关闭；无待恢复操作"
        elif self.day_one is None:
            message = "已开启：等待 DAY1；好友一律跳过"
        elif now - self.day_one <= 45:
            message = "已检测 DAY1，等待开局非好友队友；好友及较早入队者跳过"
        else:
            message = "本次开局检查结束；无待恢复操作，等待下一次 DAY1"
        self._message(message)
