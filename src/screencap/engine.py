import time

import cv2
import numpy as np
from PIL import Image

from src.common import GAME_WINDOW_TITLE
from src.logger import info, warning
from src.screencap.errors import ScreencapInitError, ScreencapRuntimeError
from src.screencap.hwnd_resolver import find_game_hwnd, get_client_size, get_capture_geometry
from src.screencap.native_loader import get_native_dll_dir, get_native_dll_path
from src.screencap.types import EngineStatus, ScreencapMode


class ScreencapEngine:
    WINDOW_STABLE_SECONDS = 5.0
    MIN_FRAME_SIZE = (640, 360)

    def __init__(self):
        self._status: EngineStatus = EngineStatus.UNINITIALIZED
        self._mgr = None
        self._selected_method_name: str | None = None
        self._last_frame: Image.Image | None = None
        self._last_frame_time: float = 0.0
        self._pending_hwnd: int | None = None
        self._pending_client_size: tuple[int, int] | None = None
        self._pending_since: float = 0.0
        self._capture_hwnd = None
        self._background_capture = False

    @property
    def status(self) -> EngineStatus:
        return self._status

    @property
    def selected_method_name(self) -> str | None:
        return self._selected_method_name

    def _reset_pending_window(self) -> None:
        self._pending_hwnd = None
        self._pending_client_size = None
        self._pending_since = 0.0

    def _discard_manager(self) -> None:
        manager = self._mgr
        self._mgr = None
        self._selected_method_name = None
        self._last_frame = None
        self._last_frame_time = 0.0
        if manager is not None:
            try:
                manager.inactive()
            except Exception as e:
                warning(f"ScreencapEngine: inactive() failed while discarding session: {e}")

    def _require_stable_window(self) -> int:
        hwnd = find_game_hwnd(GAME_WINDOW_TITLE)
        if hwnd is None:
            self._reset_pending_window()
            raise ScreencapInitError(
                "window_not_found",
                f"Game window '{GAME_WINDOW_TITLE}' not found.",
            )

        client_size = get_client_size(hwnd)
        if client_size is None:
            self._reset_pending_window()
            raise ScreencapInitError(
                "game_not_ready",
                "Game window client area is not ready.",
            )

        now = time.monotonic()
        if hwnd != self._pending_hwnd or client_size != self._pending_client_size:
            self._pending_hwnd = hwnd
            self._pending_client_size = client_size
            self._pending_since = now

        stable_for = now - self._pending_since
        if stable_for < self.WINDOW_STABLE_SECONDS:
            remaining = self.WINDOW_STABLE_SECONDS - stable_for
            raise ScreencapInitError(
                "game_not_ready",
                f"Waiting for game window to stabilize ({remaining:.1f}s remaining).",
            )
        return hwnd

    @staticmethod
    def _decode_frame(info_obj) -> Image.Image:
        if (
            info_obj is None
            or info_obj.width <= 0
            or info_obj.height <= 0
            or info_obj.channels not in (3, 4)
            or not info_obj.data
        ):
            raise ScreencapRuntimeError("invalid_frame", "Maa returned an empty or invalid frame.")

        arr = np.frombuffer(info_obj.data, dtype=np.uint8)
        row_len = info_obj.step if info_obj.step > 0 else info_obj.width * info_obj.channels
        required_size = info_obj.height * row_len
        if row_len < info_obj.width * info_obj.channels or arr.size < required_size:
            raise ScreencapRuntimeError(
                "invalid_frame",
                f"Maa returned a truncated frame buffer ({arr.size} < {required_size}).",
            )

        arr = arr[:required_size].reshape(info_obj.height, row_len)
        arr = arr[:, : info_obj.width * info_obj.channels]
        arr = arr.reshape(info_obj.height, info_obj.width, info_obj.channels)

        if info_obj.channels == 4:
            arr = cv2.cvtColor(arr, cv2.COLOR_BGRA2RGB)
        else:
            arr = cv2.cvtColor(arr, cv2.COLOR_BGR2RGB)
        return Image.fromarray(arr)

    def initialize(self, mode: ScreencapMode = ScreencapMode.AUTO) -> None:
        try:
            self._status = EngineStatus.INITIALIZING

            dll_path = get_native_dll_path()
            if not dll_path.exists():
                raise ScreencapInitError(
                    "dll_not_found",
                    f"MaaWin32Screencap.dll not found at {dll_path}",
                )

            hwnd = self._require_stable_window()

            from src.screencap.maa_win32_screencap import Manager, set_dll_path

            set_dll_path(str(get_native_dll_dir()))

            if mode == ScreencapMode.BACKGROUND:
                methods = Manager.METHOD_FRAMEPOOL
            else:
                # ScreenDC and the window-cropped DXGI method call
                # ensure_window_on_screen(), which resizes a borderless fullscreen
                # window to rcWork and exposes the Windows taskbar.
                # Do not benchmark GDI/PrintWindow against GPU capture: they
                # can return a correctly sized white frame and win the speed
                # test without capturing the game's swap chain. The desktop
                # variant also preserves the screen-coordinate region contract.
                methods = Manager.METHOD_DXGI_DESKTOP_DUP

            self._mgr = Manager(hwnd=hwnd, methods=methods)
            self._capture_hwnd = hwnd
            self._background_capture = mode == ScreencapMode.BACKGROUND

            if not self._mgr.connect():
                raise ScreencapInitError(
                    "connect_failed",
                    "Manager.connect() returned False.",
                )

            self._selected_method_name = self._mgr.selected_unit_name()
            first_frame = self._decode_frame(self._mgr.screencap())
            min_width, min_height = self.MIN_FRAME_SIZE
            if first_frame.width < min_width or first_frame.height < min_height:
                raise ScreencapInitError(
                    "game_not_ready",
                    f"Captured frame is too small: {first_frame.width}x{first_frame.height}.",
                )

            self._last_frame = first_frame
            self._annotate_geometry(first_frame)
            self._last_frame_time = time.time()
            info(f"ScreencapEngine initialized, selected method: {self._selected_method_name}")
            self._status = EngineStatus.CONNECTED
            self._reset_pending_window()

        except ScreencapInitError:
            manager_was_created = self._mgr is not None
            if manager_was_created:
                self._discard_manager()
                self._reset_pending_window()
            self._status = EngineStatus.FAILED
            raise
        except Exception as e:
            manager_was_created = self._mgr is not None
            if manager_was_created:
                self._discard_manager()
                self._reset_pending_window()
            self._status = EngineStatus.FAILED
            raise ScreencapInitError("connect_failed", str(e)) from e

    def _annotate_geometry(self, frame):
        client, bounds = get_capture_geometry(self._capture_hwnd, self._background_capture)
        if frame.size != bounds[2:]:
            raise ScreencapRuntimeError("geometry_changed", "Capture size does not match window/monitor geometry.")
        frame.info["screen_origin"] = bounds[:2]
        frame.info["client_rect"] = client

    def grab_fullscreen(self) -> Image.Image:
        if self._status != EngineStatus.CONNECTED or self._mgr is None:
            raise ScreencapRuntimeError("not_connected", "Engine is not connected.")

        try:
            from src.config import Config
            cache_interval = Config.get().update_interval
        except Exception:
            cache_interval = 0.0

        now = time.time()
        if (
            self._last_frame is not None
            and cache_interval > 0
            and (now - self._last_frame_time) < cache_interval
        ):
            return self._last_frame

        try:
            img = self._decode_frame(self._mgr.screencap())
            self._annotate_geometry(img)
            self._last_frame = img
            self._last_frame_time = now
            return img

        except ScreencapRuntimeError:
            self._discard_manager()
            self._status = EngineStatus.FAILED
            self._reset_pending_window()
            raise
        except Exception as e:
            self._discard_manager()
            self._status = EngineStatus.FAILED
            self._reset_pending_window()
            raise ScreencapRuntimeError("grab_failed", str(e)) from e

    def shutdown(self) -> None:
        self._discard_manager()
        self._reset_pending_window()
        self._status = EngineStatus.SHUTDOWN
        info("ScreencapEngine shutdown.")
