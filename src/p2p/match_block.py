"""Ping-ready communication hold; independent native workers own recovery.

No HUD/name recognition. Steam friendship is an exclusion, not evidence of how
the game matched a player. Known friends and ambiguous relationships are excluded.
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
        self.last_snapshot = float('-inf')
        self.attempted = set()
        self.assigned = {}
        self.departed = set()
        self.message = "自动显示 Steam 名字已关闭"
        self._last_diagnostic = None
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
        if not enabled:
            for bridge in self.bridges:
                bridge.restore()
        self.tick()

    def set_peers(self, peers):
        """Fresh complete native snapshots, including unchanged/empty lists."""
        now = self.clock()
        incoming = {p.steam_id: p for p in peers}
        self.departed.update(set(self.assigned.values()) - set(incoming))
        # A failed/completed attempt stays suppressed for this continuous presence.
        # Departure permits a new attempt on a future encounter, after recovery.
        self.attempted.intersection_update(set(incoming) | set(self.assigned.values()))
        self.peers = incoming
        self.last_snapshot = now
        self.tick()

    def set_status(self, status):
        if status.state != "steam_ready":
            # Only a fresh snapshot may renew a hold after helper errors.
            self.last_snapshot = float('-inf')
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
            # Release completed slots before considering departure. Otherwise a
            # departed target's retained transaction ID can re-enter restoration
            # forever and gate every later expedition.
            if bridge.status.get("state") in {"idle", "restored", "error"} and not bridge.pending:
                bridge.authorize_hold("")
                del self.assigned[index]
                if target not in self.peers or target in self.departed:
                    self.attempted.discard(target)
                self.departed.discard(target)
                info(f"Match name: slot released slot={index} state={bridge.status.get('state')}")
                continue
            peer = self.peers.get(target)
            # Steam may report Ignored/Blocked after our block; that isn't a
            # reason to treat a still-connected teammate as a newly found friend.
            keep = (self.enabled and fresh and peer is not None and peer.state == 'connected'
                    and peer.relationship != 3 and target not in self.departed)
            if not keep and bridge.status.get("state") not in {"restoring", "recovery_pending"}:
                bridge.restore()
            bridge.authorize_hold(target if keep and bridge.status.get("state") in {"preparing", "blocked"} else "")

        # Do not start anything while older recovery is unresolved. Other slots
        # can stay blocked normally; their independent owners cannot starve one
        # another's recovery during a network outage.
        recovering = self.legacy.status.get("state") not in {"idle", "restored"} or any(
            b.status.get("state") in {"restoring", "recovery_pending"} for b in self.bridges)
        reason = ('disabled' if not self.enabled else 'stale_peers' if not fresh else
                  'recovery_pending' if recovering else 'ping_ready')
        candidates = []
        if self.enabled and fresh and not recovering:
            candidates = [p for p in self.peers.values() if p.relationship == 0
                          and p.state == "connected" and type(p.ping_ms) is int and p.ping_ms >= 0
                          and p.steam_id not in self.attempted]
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

        diagnostic = (reason, len(self.peers), sum(p.relationship == 0 for p in self.peers.values()),
                      len(candidates), len(self.assigned))
        if diagnostic != self._last_diagnostic:
            self._last_diagnostic = diagnostic
            info(f"Match name: gate={reason} peers={diagnostic[1]} nonfriends={diagnostic[2]} "
                 f"eligible={diagnostic[3]} assigned={diagnostic[4]}")

        busy = [b.status.get("message", "") for b in [self.legacy, *self.bridges]
                if b.status.get("state") in {"preparing", "blocked", "restoring", "recovery_pending", "error"}]
        if busy:
            message = "；".join(dict.fromkeys(busy))
        elif not self.enabled:
            message = "自动显示 Steam 名字已关闭；无待恢复操作"
        else:
            message = "已开启：读到非好友队友有效延迟后尝试屏蔽；好友一律跳过"
        self._message(message)
