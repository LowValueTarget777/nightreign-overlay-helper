"""Independent, temporary teammate review window; never part of the Ping grid."""
from PyQt6.QtCore import QPoint, Qt, pyqtSignal
from PyQt6.QtWidgets import QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QPushButton, QSizePolicy, QVBoxLayout, QWidget

from .p2p_overlay import SteamProfileLabel, open_steam_profile
from src.p2p.encounters import encounter_text
from .utils import set_widget_always_on_top


class CommendationOverlayWidget(QWidget):
    commend_requested = pyqtSignal(str)
    dismiss_requested = pyqtSignal()
    right_click_signal = pyqtSignal()

    def __init__(self):
        super().__init__()
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setWindowTitle('队友留言点赞')
        set_widget_always_on_top(self)
        self.draggable = True
        self.scale = 1.0
        self.rows = []
        self.encounter_counts = None
        self.block_statuses = {}
        self._name_labels = {}
        self._commend_buttons = {}
        self._drag_position = None
        self.enabled = True
        self.only_show_when_game_foreground = False
        self.is_game_foreground = False
        self.is_menu_opened = False
        self.is_setting_opened = False
        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        self.panel = QFrame()
        self.panel.setStyleSheet('QFrame { background: rgba(18, 20, 24, 225); border-radius: 8px; }')
        root.addWidget(self.panel)
        layout = QVBoxLayout(self.panel)
        layout.setContentsMargins(12, 9, 12, 10)
        heading = QHBoxLayout()
        self.title_label = QLabel('队友点赞 · 两分钟后关闭')
        heading.addWidget(self.title_label, 1)
        self.close_button = QPushButton('×')
        self.close_button.setFixedSize(24, 24)
        self.close_button.setToolTip('收起点赞窗口')
        self.close_button.setStyleSheet('color: #ddd; background: transparent; border: none; font-size: 18px;')
        self.close_button.clicked.connect(lambda: self.dismiss_requested.emit())
        heading.addWidget(self.close_button)
        layout.addLayout(heading)
        self.grid = QGridLayout()
        self.grid.setColumnStretch(0, 1)
        layout.addLayout(self.grid)
        self.setWindowOpacity(0.85)
        self.setFixedWidth(330)
        self.reset_position()
        self.startTimer(50)

    def set_rows(self, rows):
        self.rows = list(rows)
        while self.grid.count():
            item = self.grid.takeAt(0)
            if item.widget() and not isinstance(item.widget(), SteamProfileLabel):
                item.widget().hide()
                item.widget().deleteLater()
        old, self._name_labels = self._name_labels, {}
        old_buttons, self._commend_buttons = self._commend_buttons, {}
        self.title_label.setStyleSheet(f'color: #66c0f4; font-size: {int(12*self.scale)}px;')
        for i, row in enumerate(self.rows):
            target = row['steam_id']
            label = old.pop(target, None) or SteamProfileLabel(target, self)
            label.setText(row['name'])
            label.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
            label.setStyleSheet(f"color: #66c0f4; font-size: {int(13*self.scale)}px;")
            label.setToolTip(f"在 Steam 内置浏览器打开个人资料页\n{row['name']}")
            self._name_labels[target] = label
            # Use the same release/drag protection as names, and keep each
            # target's action widget alive across status/count refreshes.
            button = old_buttons.pop(target, None) or SteamProfileLabel(target, self, self._request_commend)
            button.setText('点赞')
            button.setAlignment(Qt.AlignmentFlag.AlignCenter)
            button.setFixedSize(int(76*self.scale), int(28*self.scale))
            button.setEnabled(row['enabled'])
            button.setStyleSheet(
                f"color: {'#66c0f4' if row['enabled'] else '#888'}; font-size: {int(12*self.scale)}px; "
                "background: #28323c; border: 1px solid #455362; border-radius: 4px;")
            button.setToolTip(f"{row['status']}\n将发送：{row['comment']}\n同一账号对同一队友默认间隔七天")
            button.setAccessibleName('留言点赞')
            self._commend_buttons[target] = button
            count_text = encounter_text(self.encounter_counts, target)
            block_status = self.block_statuses.get(target)
            detail_text = ' · '.join(text for text in (count_text, row['status']) if text)
            if block_status:
                detail_text += f'\n通信：{block_status.text}'
            status = QLabel(detail_text)
            status.setTextFormat(Qt.TextFormat.PlainText)
            status.setWordWrap(True)
            status.setStyleSheet(f'color: #c8c8c8; font-size: {int(11*self.scale)}px;')
            if block_status:
                status.setToolTip(block_status.detail)
            self.grid.addWidget(label, i*2, 0)
            self.grid.addWidget(button, i*2, 1)
            self.grid.addWidget(status, i*2+1, 0, 1, 2)
            # Include new widgets in the layout before sizing the window;
            # deferred showing would temporarily move an active click target.
            label.show()
            button.show()
            status.show()
        for label in (*old.values(), *old_buttons.values()):
            label.hide()
            label.deleteLater()
        self.adjustSize()
        self._update_visibility()

    def set_encounter_counts(self, counts):
        self.encounter_counts = dict(counts) if counts is not None else None
        self.set_rows(self.rows)

    def set_block_statuses(self, statuses):
        self.block_statuses = dict(statuses)
        self.set_rows(self.rows)

    def _request_commend(self, target):
        if any(r['steam_id'] == target and r['enabled'] for r in self.rows):
            self.commend_requested.emit(target)

    def _open_steam_profile(self, target):
        if any(r['steam_id'] == target for r in self.rows):
            open_steam_profile(target, self)

    def update_ui_state(self, state):
        # Position belongs to this window, never inherit the latency coordinates.
        for key in ('enabled', 'only_show_when_game_foreground', 'is_game_foreground', 'is_menu_opened', 'is_setting_opened'):
            if (value := getattr(state, key, None)) is not None:
                setattr(self, key, value)
        if state.scale is not None:
            self.scale = max(0.5, min(2., state.scale))
            self.setFixedWidth(int(330*self.scale))
            self.set_rows(self.rows)
        self._update_visibility()

    def set_game_foreground(self, value):
        self.is_game_foreground = value
        self._update_visibility()

    def _update_visibility(self):
        visible = self.enabled and bool(self.rows)
        if self.only_show_when_game_foreground:
            visible &= self.is_game_foreground or self.is_menu_opened or self.is_setting_opened
        self.setVisible(bool(visible))

    def timerEvent(self, event):
        self._update_visibility()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_position = event.globalPosition().toPoint() - self.pos()
            event.accept()
        else:
            event.ignore()

    def mouseMoveEvent(self, event):
        if self._drag_position is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_position)
            event.accept()

    def mouseReleaseEvent(self, event):
        self._drag_position = None
        if event.button() == Qt.MouseButton.RightButton:
            self.right_click_signal.emit()
        event.accept()

    def reset_position(self):
        screen = QApplication.primaryScreen()
        if screen:
            rect = screen.availableGeometry()
            self.move(rect.right() - self.width() - 24, rect.top() + 280)
