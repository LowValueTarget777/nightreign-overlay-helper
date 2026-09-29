"""File IPC for the detached Steam identity/recovery worker.

Stopping the Qt application requests restoration; it never terminates the worker.
The native process owns the durable journal and all Steam/account operations.
"""
from __future__ import annotations

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import subprocess
import time
import uuid

from PyQt6.QtCore import QObject, QTimer, pyqtSignal

from src.logger import info, warning
from .service import resolve_helper_path

_STATES = {"idle", "preparing", "blocked", "restoring", "restored", "recovery_pending", "error"}
_FINAL = {"restored", "error"}
_STEAM_LIFECYCLE = "on_demand_v1"
_BUSY = {"preparing", "blocked", "restoring", "recovery_pending"}


def identity_directory() -> Path:
    return Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local")) / "NightreignOverlayHelper" / "identity"


def _write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + "." + uuid.uuid4().hex + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as output:
            json.dump(value, output, ensure_ascii=False)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def _read_json(path: Path) -> dict | None:
    try:
        if path.stat().st_size > 65536:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else None
    except (OSError, ValueError):
        return None


def _launch_worker(path: Path, slot=None):
    options = dict(stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, close_fds=True, cwd=str(path.parent))
    if os.name == "nt":
        options["creationflags"] = subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        options["start_new_session"] = True
    args = ["--identity-worker"] if slot is None else ["--match-worker", str(slot)]
    return subprocess.Popen([str(path), *args], **options)


class IdentityBridge(QObject):
    changed = pyqtSignal(object)

    def __init__(self, parent=None, *, slot=None):
        super().__init__(parent)
        if slot not in (None, 0, 1):
            raise ValueError("invalid match worker slot")
        self._slot = slot
        self.hold_target = ""
        self._root = identity_directory() if slot is None else identity_directory() / f"match-{slot}"
        self._lifecycle = _STEAM_LIFECYCLE if slot is None else "match_hold_v1"
        self._client = uuid.uuid4().hex
        self._pending: str | None = None
        self._pending_since = 0.0
        self._started = False
        self._last_launch = -100.0
        self._last_lease = -100.0
        self._worker = None
        self._worker_path = None
        self._last_status_log = None
        self._last_start_log = (None, -100.0)
        self._last_failure_log = (None, -100.0)
        self._compatible_worker = False
        self.status = {"state": "idle", "target": "", "transaction": "",
                       "message": "队友身份识别尚未启动", "accepts_begin": False}
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._poll)

    def start(self):
        """Start/reconnect to the worker, including recovery when the game is closed."""
        self._started = True
        self._timer.start()
        self._poll()

    def begin(self, steam_id) -> bool:
        steam_id = str(steam_id)
        if (len(steam_id) != 17 or not steam_id.isascii() or not steam_id.isdigit()
                or not 76561197960265728 <= int(steam_id) <= 76561202255233023):
            return False
        if not self._started or self._pending is not None:
            return False
        self._poll()
        if (self._pending is not None or not self._compatible_worker or not self.status.get("accepts_begin")
                or self.status["state"] not in {"idle", "restored", "error"}):
            return False
        transaction = uuid.uuid4().hex
        try:
            _write_json(self._root / "requests" / (transaction + ".json"), {
                "transaction": transaction, "target": steam_id, "client": self._client,
                "created_at": datetime.now(timezone.utc).isoformat(),
            })
        except OSError:
            self._set("error", "无法保存识别请求，尚未操作 Steam", steam_id, transaction)
            return False
        self._pending = transaction
        self._pending_since = time.monotonic()
        # Synchronous: the matcher can capture the transaction immediately on return.
        self._set("preparing", "正在确认队友和原始通信状态", steam_id, transaction)
        return True

    def restore(self):
        self.hold_target = ""
        transaction = self._pending or self.status.get("transaction", "")
        if not transaction or len(transaction) != 32:
            return
        try:
            _write_json(self._root / ("restore-" + transaction + ".json"), {"transaction": transaction})
            self._set("restoring", "已请求立即恢复通信", self.status.get("target", ""), transaction)
        except OSError:
            self._set("recovery_pending", "无法发送提前恢复请求；独立组件仍会按时恢复通信",
                      self.status.get("target", ""), transaction)

    @property
    def pending(self):
        return self._pending is not None

    def authorize_hold(self, steam_id):
        if self.hold_target != steam_id:
            self.hold_target = steam_id
            self._last_lease = -100.0

    def stop(self):
        """Leave recovery running after the parent app closes; never terminate it."""
        if self.status["state"] in {"preparing", "blocked", "restoring", "recovery_pending"} or self._pending:
            self.restore()
        self._started = False
        self._compatible_worker = False
        self._timer.stop()
        self._drop_lease()

    def _drop_lease(self):
        # A pre-on-demand worker owns the same singleton. Renewing our lease
        # would keep its eager Steam session alive indefinitely after upgrade.
        self._last_lease = -100.0
        try:
            (self._root / "clients" / (self._client + ".json")).unlink(missing_ok=True)
        except OSError:
            pass

    def _set(self, state: str, message: str, target: str = "", transaction: str = "", **extra):
        value = dict(state=state, message=message, target=target, transaction=transaction,
                     accepts_begin=False, **extra)
        if value != self.status:
            self.status = value
            self.changed.emit(dict(value))

    def _apply(self, value: dict, *, allow_begin=False):
        if value.get("state") not in _STATES:
            return
        result = {key: value.get(key, "") for key in ("state", "target", "transaction", "message")}
        result["accepts_begin"] = allow_begin and bool(value.get("accepts_begin", False))
        if result != self.status:
            self.status = result
            self.changed.emit(dict(result))

    def _poll(self):
        if not self._started:
            return
        try:
            self._poll_worker()
        finally:
            # Only allowlisted state and booleans enter diagnostics. The UI may
            # contain player IDs or remote error text; none of that is logged.
            diagnostic = (self.status.get("state") if self.status.get("state") in _STATES else "unknown",
                          self._compatible_worker, bool(self.status.get("accepts_begin")), self._pending is not None)
            if diagnostic != self._last_status_log:
                self._last_status_log = diagnostic
                state, compatible, accepts, pending = diagnostic
                info(f"Identity worker state={state} compatible={compatible} accepts_begin={accepts} pending={pending}")

    def _warn_worker(self, reason, helper, exitcode=None):
        # A failed child may be retried every five seconds. Keep the first
        # failure and changed exit codes, but repeat identical warnings at most
        # once per thirty seconds. Never include exception or stderr contents.
        key = (reason, str(helper), exitcode)
        previous, logged_at = self._last_failure_log
        now = time.monotonic()
        if key != previous or now - logged_at >= 30:
            self._last_failure_log = (key, now)
            suffix = f" exitcode={exitcode}" if exitcode is not None else ""
            warning(f"Identity worker {reason}: helper={helper}{suffix}")

    def _poll_worker(self):
        now = time.monotonic()
        if self._worker is not None:
            exitcode = self._worker.poll()
            if isinstance(exitcode, int):
                self._warn_worker("process exited", self._worker_path, exitcode)
                self._worker = None
        self._compatible_worker = False
        # Capability must be observed before writing any heartbeat. In
        # particular, stale status may still belong to a live legacy owner;
        # starting another process does not mean we won its singleton lock.
        value = _read_json(self._root / "status.json")
        fresh = False
        if value and value.get("state") in _STATES:
            try:
                age = (datetime.now(timezone.utc) - datetime.fromisoformat(value["updated_at"])).total_seconds()
                fresh = -5 <= age < 8
            except (KeyError, ValueError, TypeError):
                pass
        if self._pending:
            result = _read_json(self._root / "results" / (self._pending + ".json"))
            if result and result.get("transaction") == self._pending and result.get("state") in _FINAL:
                self._pending = None
                self._apply(result)
        if fresh:
            if self._pending and value.get("transaction") == self._pending and value.get("state") in _FINAL:
                self._pending = None
                self._apply(value)
            if value.get("steam_lifecycle") != self._lifecycle:
                self._drop_lease()
                state = ("recovery_pending" if self._pending or value["state"] in _BUSY
                         else "restored" if value["state"] == "restored" else "error")
                self._set(state, "检测到旧版身份组件；请关闭旧版助手，等待组件完成恢复并自动退出，新识别已暂停",
                          value.get("target") or self.status.get("target", ""),
                          self._pending or value.get("transaction", ""))
                return
            if now - self._last_lease >= 1:
                try:
                    _write_json(self._root / "clients" / (self._client + ".json"),
                                {"client": self._client, "hold_target": self.hold_target})
                    self._last_lease = now
                except OSError:
                    self._set("recovery_pending", "无法更新识别组件状态；已有恢复操作会独立继续",
                              self.status.get("target", ""), self._pending or self.status.get("transaction", ""))
                    return
            self._compatible_worker = True
            if (self._pending and now - self._pending_since > 30
                  and value.get("state") == "idle"
                  and not (self._root / "requests" / (self._pending + ".json")).exists()
                  and not self._pending_on_disk()):
                lost = self._pending
                self._pending = None
                self._set("error", "识别请求未完成，未发现待恢复操作；可以重试",
                          self.status.get("target", ""), lost)
                return
            if self._pending is None or value.get("transaction") == self._pending or value.get("state") == "recovery_pending":
                self._apply(value, allow_begin=True)
            return
        self._drop_lease()
        if self.status.get("accepts_begin"):
            self._set("recovery_pending" if self._pending else "preparing", "正在等待恢复组件确认版本",
                      self.status.get("target", ""), self._pending or self.status.get("transaction", ""))
        if now - self._last_launch >= 5:
            self._last_launch = now
            helper = resolve_helper_path()
            if not helper.is_file():
                self._warn_worker("file missing", helper)
                self._set("error", "未找到支持队友身份识别的联机组件")
                return
            try:
                previous, logged_at = self._last_start_log
                if previous != str(helper) or now - logged_at >= 30:
                    self._last_start_log = (str(helper), now)
                    info(f"Starting identity worker: helper={helper}")
                self._worker = _launch_worker(helper) if self._slot is None else _launch_worker(helper, self._slot)
                self._worker_path = helper
                self._set("recovery_pending" if self._pending else "preparing",
                          "正在连接独立恢复组件", self.status.get("target", ""),
                          self._pending or self.status.get("transaction", ""))
            except OSError:
                self._warn_worker("launch failed", helper)
                self._set("error", "无法启动身份识别组件；请重新打开应用以继续恢复")

    def _pending_on_disk(self) -> bool:
        claim = _read_json(self._root / "active-request.json")
        if claim and claim.get("transaction") == self._pending:
            return True
        for path in self._root.glob("journal-*.json"):
            journal = _read_json(path)
            if journal is None or (journal.get("transaction") == self._pending and journal.get("recovery_needed")):
                return True
        return False
