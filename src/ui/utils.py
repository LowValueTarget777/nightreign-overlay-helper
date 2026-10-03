from PyQt6.QtWidgets import QWidget, QApplication
from PyQt6.QtCore import Qt
from src.logger import info, warning, error


def set_widget_always_on_top(widget: QWidget):
    try:
        import win32gui
        import win32con

        # Overlay windows must remain above the borderless game without ever
        # becoming the foreground/input-focus window.  The main and P2P
        # overlays deliberately remain mouse-interactive for their context
        # menu and settings-time dragging; map/HP overlays additionally use
        # WindowTransparentForInput at their call sites.
        widget.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        widget.setWindowFlag(Qt.WindowType.WindowDoesNotAcceptFocus, True)
        widget.setFocusPolicy(Qt.FocusPolicy.NoFocus)

        hwnd = widget.winId().__int__()
        # Enforce the native style as well as Qt's focus policy. This leaves
        # TOPMOST and mouse interaction intact, but prevents mouse activation
        # of the overlay from transferring foreground ownership from the game.
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        win32gui.SetWindowLong(
            hwnd, win32con.GWL_EXSTYLE, ex_style | win32con.WS_EX_NOACTIVATE
        )
        win32gui.SetWindowPos(hwnd, win32con.HWND_TOPMOST, 
                                0, 0, 0, 0, 
                                win32con.SWP_NOSIZE |
                                win32con.SWP_NOMOVE |
                                win32con.SWP_NOACTIVATE |
                                win32con.SWP_NOOWNERZORDER)
        info(f"Window HWND: {hwnd} set to TOPMOST.")
    except Exception as e:
        warning(f"Error setting system always on top: {e}")


def is_window_in_foreground(window_title: str) -> bool:
    """
    检查精确标题的游戏窗口是否在 Windows 的最前面。
    """
    try:
        import win32gui
        from src.screencap.hwnd_resolver import is_game_window
        active_window_handle = win32gui.GetForegroundWindow()
        return is_game_window(active_window_handle, window_title)
    except Exception as e:
        return False


def get_qt_screen_by_region(region: tuple[int]) -> QWidget:
    """
    根据物理像素 region 获取对应的 QScreen 对象。
    """
    x, y, w, h = region
    app: QApplication = QApplication.instance()
    screens = app.screens()
    for screen in screens:
        sx = screen.geometry().x()
        sy = screen.geometry().y()
        sw = screen.geometry().width()
        sh = screen.geometry().height()
        ratio = screen.devicePixelRatio()
        phys_sw = int(sw * ratio)
        phys_sh = int(sh * ratio)
        if sx <= x <= sx + phys_sw and sy <= y <= sy + phys_sh:
            return screen
    raise ValueError(f"Region {region} is out of all screen bounds")


def region_to_qt_region(region: tuple[int]):
    screen = get_qt_screen_by_region(region)
    x, y, w, h = region
    sx = screen.geometry().x()
    sy = screen.geometry().y()
    ratio = screen.devicePixelRatio()
    qx = sx + int((x - sx) / ratio)
    qy = sy + int((y - sy) / ratio)
    qw = int(w / ratio)
    qh = int(h / ratio)
    return (qx, qy, qw, qh)

    

def process_region_to_adapt_scale(region: tuple[int], scale: float) -> tuple[int]:
    """
    处理一个region的大小，使其能够适配指定的缩放比例。
    即 w/scale, h/scale为整数。
    """
    x, y, w, h = region
    new_w = int(int(w / scale) * scale)
    new_h = int(int(h / scale) * scale)
    return [x, y, new_w, new_h]
