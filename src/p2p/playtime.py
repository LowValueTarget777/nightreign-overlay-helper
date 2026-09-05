from __future__ import annotations

import json
import re
import time
from dataclasses import dataclass
from pathlib import Path

import pywintypes
import win32crypt
from PyQt6.QtCore import QObject, QTimer, QUrl, QUrlQuery, pyqtSignal
from PyQt6.QtNetwork import QNetworkAccessManager, QNetworkReply, QNetworkRequest

from src.common import get_appdata_path
from src.logger import warning

from .models import P2PPeer
from .public_playtime import parse_public_playtime

NIGHTREIGN_APP_ID = 2622380
STEAM_OWNED_GAMES_URL = "https://api.steampowered.com/IPlayerService/GetOwnedGames/v1/"
STEAM_API_KEY_URL = "https://steamcommunity.com/dev/apikey"
API_KEY_PATTERN = re.compile(r"^[0-9A-Fa-f]{32}$")
KEY_FILE = Path(get_appdata_path("steam_web_api_key.dat"))
RETRY_DELAY_SECONDS = 300


@dataclass(frozen=True)
class PlaytimeResult:
    state: str
    minutes: int | None = None
    message: str = ""


def format_playtime(result: PlaytimeResult) -> str:
    if result.state == "loading":
        return "查询中"
    if result.state == "available" and result.minutes is not None:
        if result.minutes < 60:
            return f"{result.minutes} 分钟"
        return f"{result.minutes / 60:.1f} 小时"
    if result.state == "private":
        return "未公开"
    if result.state == "missing_key":
        return "未配置"
    return "时长不可用"


def parse_playtime_response(raw_data: bytes) -> PlaytimeResult:
    payload = json.loads(raw_data.decode("utf-8"))
    response = payload.get("response")
    if not isinstance(response, dict):
        raise TypeError("Steam Web API response is missing")

    games = response.get("games")
    if not isinstance(games, list):
        return PlaytimeResult("private", message="游戏详情未公开")

    for game in games:
        if not isinstance(game, dict) or game.get("appid") != NIGHTREIGN_APP_ID:
            continue
        minutes = game.get("playtime_forever")
        if isinstance(minutes, int) and minutes >= 0:
            return PlaytimeResult("available", minutes=minutes)

    # A connected Nightreign peer necessarily owns the game. Steam omitting it
    # therefore means that the account's game details are not available here.
    return PlaytimeResult("private", message="游戏详情未公开")


class SteamApiKeyStore:
    """Store the user's own Steam Web API key encrypted for this Windows user."""

    def __init__(self, path: Path = KEY_FILE):
        self.path = path

    def load(self) -> str | None:
        if not self.path.is_file():
            return None
        try:
            _, decrypted = win32crypt.CryptUnprotectData(
                self.path.read_bytes(), None, None, None, 0
            )
            key = decrypted.decode("ascii")
            return key if API_KEY_PATTERN.fullmatch(key) else None
        except (OSError, UnicodeError, pywintypes.error) as exc:
            warning(f"Unable to load the encrypted Steam Web API key: {exc}")
            return None

    def save(self, key: str):
        normalized = key.strip()
        if not API_KEY_PATTERN.fullmatch(normalized):
            raise ValueError("Steam Web API Key 应为 32 位十六进制字符")
        encrypted = win32crypt.CryptProtectData(
            normalized.encode("ascii"),
            "Nightreign Overlay Helper Steam Web API Key",
            None,
            None,
            None,
            0x1,  # CRYPTPROTECT_UI_FORBIDDEN
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_suffix(self.path.suffix + ".tmp")
        temporary.write_bytes(encrypted)
        temporary.replace(self.path)

    def clear(self):
        self.path.unlink(missing_ok=True)


class SteamPlaytimeService(QObject):
    results_changed = pyqtSignal(object)
    status_changed = pyqtSignal(str)

    def __init__(self, parent: QObject | None = None, key_store: SteamApiKeyStore | None = None):
        super().__init__(parent)
        self.enabled = False
        self._key_store = key_store or SteamApiKeyStore()
        self._api_key = self._key_store.load()
        self._peers: list[P2PPeer] = []
        self._results: dict[str, PlaytimeResult] = {}
        self._retry_after: dict[str, float] = {}
        self._pending: dict[str, QNetworkReply] = {}
        self._network = QNetworkAccessManager(self)
        self._retry_timer = QTimer(self)
        self._retry_timer.setInterval(60_000)
        self._retry_timer.timeout.connect(self._query_missing_peers)

    @property
    def has_api_key(self) -> bool:
        return self._api_key is not None

    @property
    def results(self) -> dict[str, PlaytimeResult]:
        return dict(self._results)

    def set_enabled(self, enabled: bool):
        if self.enabled == enabled:
            self._emit_status()
            return
        self.enabled = enabled
        if not enabled:
            self._retry_timer.stop()
            self._abort_pending()
            self._results.clear()
            self._retry_after.clear()
            self.results_changed.emit({})
        else:
            self._retry_timer.start()
            self._query_missing_peers()
        self._emit_status()

    def set_peers(self, peers: list[P2PPeer]):
        self._peers = list(peers)
        if self.enabled:
            self._query_missing_peers()

    def save_api_key(self, key: str):
        normalized = key.strip()
        self._key_store.save(normalized)
        self._api_key = normalized
        self._abort_pending()
        self._results.clear()
        self._retry_after.clear()
        self.results_changed.emit({})
        self._query_missing_peers()
        self._emit_status(
            "API Key 已安全保存，等待查询队友"
            if self.enabled else "API Key 已安全保存；游戏时长查询仍关闭"
        )

    def clear_api_key(self):
        self._key_store.clear()
        self._api_key = None
        self._abort_pending()
        self._retry_after.clear()
        self._results.clear()
        self.results_changed.emit(dict(self._results))
        self._query_missing_peers()
        self._emit_status()

    def stop(self):
        self._retry_timer.stop()
        self._abort_pending()

    def _query_missing_peers(self):
        if not self.enabled:
            return
        now = time.monotonic()
        for peer in self._peers:
            steam_id = peer.steam_id
            if steam_id in self._pending:
                continue
            if now < self._retry_after.get(steam_id, 0):
                continue
            if len(self._pending) >= 2:
                break
            if not re.fullmatch(r"[0-9]{17}", steam_id):
                continue
            self._start_query(steam_id)

    def _start_query(self, steam_id: str, *, use_api: bool = False):
        query = QUrlQuery()
        query.addQueryItem("key", self._api_key or "")
        query.addQueryItem("steamid", steam_id)
        query.addQueryItem("include_appinfo", "false")
        query.addQueryItem("include_played_free_games", "true")
        query.addQueryItem("appids_filter[0]", str(NIGHTREIGN_APP_ID))
        url = QUrl(STEAM_OWNED_GAMES_URL)
        url.setQuery(query)
        if not use_api:
            url = QUrl(f"https://steamcommunity.com/profiles/{steam_id}/?l=english")

        request = QNetworkRequest(url)
        request.setTransferTimeout(10_000)
        request.setAttribute(QNetworkRequest.Attribute.CookieLoadControlAttribute, QNetworkRequest.LoadControl.Manual)
        request.setAttribute(QNetworkRequest.Attribute.CookieSaveControlAttribute, QNetworkRequest.LoadControl.Manual)
        request.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute, QNetworkRequest.RedirectPolicy.SameOriginRedirectPolicy)
        request.setHeader(QNetworkRequest.KnownHeaders.UserAgentHeader, "nightreign-overlay-helper")
        reply = self._network.get(request)
        self._pending[steam_id] = reply
        self._results[steam_id] = PlaytimeResult("loading")
        reply.finished.connect(lambda peer_id=steam_id: self._on_finished(peer_id, use_api=use_api))
        self.results_changed.emit(dict(self._results))
        self._emit_status()

    def _on_finished(self, steam_id: str, *, use_api: bool = False):
        reply = self._pending.pop(steam_id, None)
        if reply is None:
            return
        if not use_api:
            try:
                minutes = (
                    parse_public_playtime(bytes(reply.readAll()))
                    if reply.error() == QNetworkReply.NetworkError.NoError else None
                )
            except (ValueError, UnicodeError):
                minutes = None
            finally:
                reply.deleteLater()
            if minutes is None and self._api_key and self.enabled:
                self._start_query(steam_id, use_api=True)
                return
            self._results[steam_id] = (
                PlaytimeResult("available", minutes=minutes)
                if minutes is not None else PlaytimeResult("unavailable", message="公开页面未提供时长或暂时无法访问")
            )
            self._retry_after[steam_id] = time.monotonic() + (1800 if minutes is not None else RETRY_DELAY_SECONDS)
            self.results_changed.emit(dict(self._results))
            self._emit_status()
            return
        try:
            status_code = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if reply.error() != QNetworkReply.NetworkError.NoError:
                if status_code in {401, 403}:
                    result = PlaytimeResult("error", message="API Key 无效或无权访问")
                    status_message = "API Key 无效或无权访问"
                else:
                    result = PlaytimeResult("error", message="网络查询失败")
                    status_message = "游戏时长查询暂时失败"
                self._retry_after[steam_id] = time.monotonic() + RETRY_DELAY_SECONDS
                self._results[steam_id] = result
                self._emit_status(status_message)
            else:
                self._results[steam_id] = parse_playtime_response(bytes(reply.readAll()))
                self._retry_after[steam_id] = time.monotonic() + (
                    1800 if self._results[steam_id].state == "available" else RETRY_DELAY_SECONDS
                )
                self._emit_status()
        except (TypeError, ValueError, UnicodeError) as exc:
            self._results[steam_id] = PlaytimeResult("error", message="响应格式异常")
            self._retry_after[steam_id] = time.monotonic() + RETRY_DELAY_SECONDS
            warning(f"Unable to parse Steam playtime response: {exc}")
            self._emit_status("Steam 返回了无法识别的游戏时长数据")
        finally:
            reply.deleteLater()
            self.results_changed.emit(dict(self._results))

    def _abort_pending(self):
        pending = list(self._pending.values())
        self._pending.clear()
        for reply in pending:
            reply.abort()
            reply.deleteLater()

    def _emit_status(self, override: str | None = None):
        if override:
            message = override
        elif not self.enabled:
            message = "游戏时长查询已关闭"
        elif self._pending:
            message = f"正在查询 {len(self._pending)} 名队友的游戏时长"
        else:
            message = "优先查询公开页面（免 Key）" + (
                "；API 备用已配置" if self._api_key else "；未提供时长时显示不可用"
            )
        self.status_changed.emit(message)
