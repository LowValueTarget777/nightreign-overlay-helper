import ctypes
import ctypes.wintypes as wt
import sys

from src.logger import warning

if sys.platform == "win32":
    _user32 = ctypes.windll.user32
    _user32.IsWindow.argtypes = [wt.HWND]
    _user32.IsWindow.restype = wt.BOOL
    _user32.IsWindowVisible.argtypes = [wt.HWND]
    _user32.IsWindowVisible.restype = wt.BOOL
    _user32.IsIconic.argtypes = [wt.HWND]
    _user32.IsIconic.restype = wt.BOOL
    _user32.GetClientRect.argtypes = [wt.HWND, ctypes.POINTER(wt.RECT)]
    _user32.GetClientRect.restype = wt.BOOL
else:
    _user32 = None


def find_game_hwnd(title: str) -> int | None:
    if sys.platform != "win32":
        warning(f"find_game_hwnd: non-Windows platform ({sys.platform}), returning None.")
        return None
    try:
        from src.screencap.maa_win32_screencap import find_window_by_title
        return find_window_by_title(title)
    except Exception as e:
        warning(f"find_game_hwnd: failed to find window by title '{title}': {e}")
        return None


def get_client_size(hwnd: int) -> tuple[int, int] | None:
    """Return the target window's client size in physical pixels."""
    if sys.platform != "win32" or not hwnd:
        return None

    hwnd_value = wt.HWND(hwnd)
    rect = wt.RECT()
    if not _user32.IsWindow(hwnd_value) or not _user32.IsWindowVisible(hwnd_value):
        return None
    if _user32.IsIconic(hwnd_value) or not _user32.GetClientRect(hwnd_value, ctypes.byref(rect)):
        return None

    width = rect.right - rect.left
    height = rect.bottom - rect.top
    if width <= 0 or height <= 0:
        return None
    return width, height


def get_capture_geometry(hwnd: int, background: bool):
    """Read geometry only. Never activate, restore or resize the game."""
    import win32gui
    import win32api
    import win32con

    size = get_client_size(hwnd)
    if size is None:
        raise ValueError("Game window is not ready")
    x, y = win32gui.ClientToScreen(hwnd, (0, 0))
    client = (x, y, *size)
    if background:
        # Maa crops the WGC frame to the client area before returning it.
        return client, client
    else:
        monitor = win32api.MonitorFromWindow(hwnd, win32con.MONITOR_DEFAULTTONEAREST)
        left, top, right, bottom = win32api.GetMonitorInfo(monitor)["Monitor"]
    return client, (left, top, right - left, bottom - top)
