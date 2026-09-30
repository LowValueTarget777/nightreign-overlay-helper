import sys
import time
import os
from PyQt6.QtCore import QThread, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QIcon, QAction, QCursor
from PyQt6.QtWidgets import (
    QApplication, QSystemTrayIcon, QMenu
)

from src.ui.input import InputWorker
from src.ui.overlay import OverlayWidget
from src.ui.map_overlay import MapOverlayWidget
from src.ui.hp_overlay import HpOverlayWidget
from src.ui.p2p_overlay import P2POverlayUIState, P2POverlayWidget
from src.ui.settings import SettingsWindow
from src.ui.admin_prompt import show_admin_prompt
from src.p2p import P2PService, SteamPlaytimeService
from src.p2p.match_block import MatchBlockController
from src.p2p.commendation import CommendationService
from src.updater import Updater
from src.common import APP_FULLNAME, APP_VERSION, ICON_PATH
from src.logger import info, warning, error
from src.screencap import get_engine


def log_system_and_screen_info(app: QApplication):
    try:
        import platform
        system = platform.system()
        release = platform.release()
        version = platform.version()
        info(f"Operating System: {system} {release} ({version})")
    except Exception as e:
        warning(f"Error getting OS info: {e}")

    try:
        screens = app.screens()
        info(f"QApplication detected {len(screens)} screen(s):")
        for i, screen in enumerate(screens, start=1):
            size = screen.size()
            pos = screen.geometry().topLeft()
            dpi = screen.logicalDotsPerInch()
            device_pixel_ratio = screen.devicePixelRatio()
            phys_w = int(size.width() * device_pixel_ratio)
            phys_h = int(size.height() * device_pixel_ratio)
            info(f"    Screen {i}: {size.width()}x{size.height()} (logical) / {phys_w}x{phys_h} (physical) at ({pos.x()},{pos.y()}), DPI: {dpi}, Device Pixel Ratio: {device_pixel_ratio}")
    except Exception as e:
        warning(f"Error getting screens from QApplication: {e}")


if __name__ == "__main__":
    info("=" * 40)
    info(f"Starting app v{APP_VERSION}...")

    QApplication.setAttribute(Qt.ApplicationAttribute.AA_Use96Dpi)
    QApplication.setHighDpiScaleFactorRoundingPolicy(Qt.HighDpiScaleFactorRoundingPolicy.PassThrough)

    app = QApplication(sys.argv)

    log_system_and_screen_info(app)

    # 防止因没有窗口而导致程序退出
    # （需在启动弹窗前设置，避免弹窗关闭时被判定为“最后一个窗口关闭”而退出程序）
    app.setQuitOnLastWindowClosed(False)

    # 启动时检测管理员权限并弹窗提示（未提权时游戏内将无法正常监听按键）
    if not show_admin_prompt():
        time.sleep(0.5)  # 等待以管理员身份启动的新实例拉起
        os._exit(0)

    # 创建对象
    input = InputWorker()
    overlay = OverlayWidget()
    map_overlay = MapOverlayWidget()
    hp_overlay = HpOverlayWidget()
    p2p_overlay = P2POverlayWidget()
    p2p_service = P2PService()
    playtime_service = SteamPlaytimeService()
    p2p_service.status_changed.connect(p2p_overlay.set_status)
    p2p_service.peers_changed.connect(p2p_overlay.set_peers)
    p2p_service.peers_changed.connect(playtime_service.set_peers)
    playtime_service.results_changed.connect(p2p_overlay.set_playtimes)

    updater = Updater(input, overlay, map_overlay, hp_overlay)
    match_block = MatchBlockController()
    p2p_service.snapshot_received.connect(match_block.set_peers)
    p2p_service.status_changed.connect(match_block.set_status)
    commendation = CommendationService(blocker=match_block)
    p2p_service.snapshot_received.connect(commendation.set_peers)
    p2p_service.status_changed.connect(commendation.set_status)
    updater.expedition_started.connect(commendation.on_day_one)
    commendation.rows_changed.connect(p2p_overlay.set_commend_rows)
    p2p_overlay.commend_requested.connect(commendation.send)
    updater.game_foreground_changed.connect(p2p_overlay.set_game_foreground)
    settings_window = SettingsWindow(
        overlay, map_overlay, updater, input, p2p_overlay, p2p_service, playtime_service, match_block, commendation
    )
    
    # 创建系统托盘图标和菜单
    tray_icon = QSystemTrayIcon()
    tray_icon.setIcon(QIcon(ICON_PATH))
    tray_icon.setToolTip(APP_FULLNAME)
    match_block.status_changed.connect(lambda text: tray_icon.setToolTip((APP_FULLNAME + "\n" + text)[:127]))

    menu = QMenu()
    settings_action = QAction("设置")
    def show_settings():
        settings_window.show()
        settings_window.activateWindow()
        settings_window.raise_()
    # Let the popup finish closing and release its input grab before
    # activating the settings window.
    settings_action.triggered.connect(lambda: QTimer.singleShot(0, show_settings))
    menu.addAction(settings_action)
    commend_action = QAction("结束本局 / 显示队友点赞入口")
    commend_action.triggered.connect(lambda: commendation.finish_match())
    menu.addAction(commend_action)
    quit_action = QAction("退出")
    quit_action.triggered.connect(app.quit)
    menu.addAction(quit_action)
    menu.addSeparator()
    tray_icon.setContextMenu(menu)
    tray_icon.show()
    
    def show_menu_at_cursor_pos():
        cursor_pos = QCursor.pos()
        # The signal comes from mouseReleaseEvent. Return to Qt first so its
        # automatic mouse grab is released before the popup takes over.
        QTimer.singleShot(0, lambda: menu.popup(cursor_pos))
    def on_menu_show():
        overlay.is_menu_opened = True
        map_overlay.is_menu_opened = True
        p2p_overlay.update_ui_state(P2POverlayUIState(is_menu_opened=True))
        updater.is_menu_opened = True
        # info("Menu opened")
    def on_menu_hide():
        overlay.is_menu_opened = False
        map_overlay.is_menu_opened = False
        p2p_overlay.update_ui_state(P2POverlayUIState(is_menu_opened=False))
        updater.is_menu_opened = False
        # info("Menu closed")

    overlay.right_click_signal.connect(show_menu_at_cursor_pos)
    p2p_overlay.right_click_signal.connect(show_menu_at_cursor_pos)
    menu.aboutToShow.connect(on_menu_show)
    menu.aboutToHide.connect(on_menu_hide)

    # 启动输入监听
    input_thread = QThread()
    input.moveToThread(input_thread)
    input_thread.started.connect(input.run)
    input_thread.start()
    
    # 设置并启动后台检测器
    updater_thread = QThread()
    updater.moveToThread(updater_thread)
    updater_thread.started.connect(updater.run)
    updater_thread.start()

    # 设置加载完成后启动，确保联机信息启用状态已经生效。
    p2p_service.start()
    match_block.start()

    # 清理：程序退出时，停止worker并等待线程结束
    def on_quit():
        info("Stopping worker thread...")
        playtime_service.stop()
        commendation.stop()
        match_block.stop()
        p2p_service.stop()
        updater.stop()
        input.stop()
        updater_thread.requestInterruption()
        input_thread.requestInterruption()
        updater_thread.quit()
        input_thread.quit()
        # Never kill a worker inside a native capture/matching call. Wait for
        # cooperative completion before releasing any of its resources.
        for name, thread in (("Updater", updater_thread), ("Input", input_thread)):
            while not thread.wait(1000):
                warning(f"Waiting for {name} worker to finish safely.")
            info(f"{name} thread stopped.")
        info("All Thread stopped.")

        try:
            get_engine().shutdown()
        except Exception as e:
            warning(f"Error shutting down screencap engine: {e}")

        tray_icon.deleteLater()

    app.aboutToQuit.connect(on_quit)
    
    overlay.show()

    try:
        exit_code = app.exec() 
        info(f"QApp event loop finished with exit code {exit_code}.")
    except Exception as e:
        exit_code = 1
        error(f"Exception in app exec: {e}")

    settings_window.save_settings()

    time.sleep(1)
    os._exit(exit_code)
