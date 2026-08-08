from __future__ import annotations

import ctypes
import json
import logging
import re
import sys
from dataclasses import asdict
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from PySide6.QtCore import QEasingCurve, QPointF, QPropertyAnimation, QRectF, QCoreApplication, QThread, QTimer, Qt, Signal
from PySide6.QtGui import (
    QAction,
    QCloseEvent,
    QColor,
    QFont,
    QIcon,
    QPainter,
    QPainterPath,
    QPen,
    QPixmap,
    QResizeEvent,
    QTextCharFormat,
    QTextCursor,
)
from PySide6.QtWidgets import (
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QInputDialog,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMainWindow,
    QMenu,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QSpinBox,
    QSystemTrayIcon,
    QTableWidget,
    QTableWidgetItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from pending_retry_store import PendingTelegramRetryStore
from settings import APP_DIR, APP_ICON_PATH, AppConfig, BRAND_LOGO_PATH, CHECK_MARK_PATH, LOG_PATH, UpTarget, ensure_data_dir
from store import SentVideoStore


APP_NAME = "PushToTelegram"
APP_WINDOW_TITLE = APP_NAME
TRAY_TOOLTIP = f"{APP_NAME} - B站投稿监控与推送"
APP_USER_MODEL_ID = "PushToTelegram.Desktop"


@lru_cache(maxsize=2048)
def build_search_index(text: str) -> str:
    """Build a cached Chinese, full-pinyin and initial-letter search index."""
    # pypinyin loads sizeable dictionaries. Defer it until the user actually searches.
    from pypinyin import Style, lazy_pinyin

    normalized = text.casefold()
    full_pinyin = "".join(lazy_pinyin(normalized))
    initials = "".join(lazy_pinyin(normalized, style=Style.FIRST_LETTER))
    return f"{normalized} {full_pinyin} {initials}"


def enable_high_dpi() -> None:
    if sys.platform != "win32":
        return
    try:
        ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(APP_USER_MODEL_ID)
    except Exception:
        pass
    try:
        ctypes.windll.user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except Exception:
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except Exception:
            try:
                ctypes.windll.user32.SetProcessDPIAware()
            except Exception:
                pass


def build_export_file_name() -> str:
    now = datetime.now()
    return f"{APP_NAME}_exportConfig_{now:%Y-%m-%d %H_%M_%S}.json"


class WorkerThread(QThread):
    log_message = Signal(str)
    error_message = Signal(str)
    task_done = Signal(str)
    task_error = Signal(str)

    def __init__(self, config: AppConfig, action: str) -> None:
        super().__init__()
        self._config = config
        self._action = action

    def run(self) -> None:
        try:
            from monitor_service import MonitorService
            from bili_client import BiliRiskControlError

            def emit_log(message: str) -> None:
                self.log_message.emit(message)
                QCoreApplication.processEvents()

            def emit_error(message: str) -> None:
                self.error_message.emit(message)
                QCoreApplication.processEvents()

            service = MonitorService(self._config, log=emit_log, error_log=emit_error)
            if self._action == "test":
                success = service.send_test_message()
                if success:
                    self.task_done.emit("Telegram 测试成功。")
                else:
                    self.task_done.emit("Telegram 测试已结束，本次失败已跳过。")
            elif self._action == "check":
                result = service.check_updates()
                self.task_done.emit(
                    f"检查完成：成功检查 {result.checked_count} 个目标，失败 {result.failed_count} 个，发送 {result.sent_count} 条链接。"
                )
            elif self._action == "recent":
                result = service.send_recent_videos()
                self.task_done.emit(
                    f"发送完成：成功检查 {result.checked_count} 个目标，失败 {result.failed_count} 个，发送 {result.sent_count} 条链接。"
                )
            elif self._action == "retry":
                result = service.retry_pending_send()
                self.task_done.emit(
                    f"重试发送完成：发送 {result.sent_count} 条链接，失败 {result.failed_count} 条。"
                )
            else:
                raise RuntimeError(f"未知任务类型：{self._action}")
        except BiliRiskControlError as exc:
            self.task_error.emit(f"__BILI_RISK__{exc}")
        except Exception as exc:
            self.task_error.emit(str(exc))


class PlainTextEdit(QTextEdit):
    def insertFromMimeData(self, source) -> None:  # type: ignore[override]
        self.insertPlainText(source.text())


class LogTextEdit(PlainTextEdit):
    def append_message(self, message: str, *, color: str | None = None) -> None:
        self.moveCursor(QTextCursor.MoveOperation.End)
        char_format = QTextCharFormat()
        if color:
            char_format.setForeground(QColor(color))
        self.textCursor().insertText(f"{message}\n", char_format)
        self.moveCursor(QTextCursor.MoveOperation.End)


class LogListWidget(QListWidget):
    def append_message(
        self,
        message: str,
        *,
        color: str | None = None,
        background: str | None = None,
    ) -> None:
        time_text = datetime.now().strftime("%H:%M")
        item = QListWidgetItem(f"  {message}    {time_text}")
        item.setForeground(QColor(color or "#475569"))
        if background:
            item.setBackground(QColor(background))
        item.setToolTip(message)
        self.addItem(item)
        self.scrollToBottom()


class NavIcon(QWidget):
    """Small native vector icon used by the sidebar navigation."""

    def __init__(self, icon_name: str, *, active: bool = False) -> None:
        super().__init__()
        self.icon_name = icon_name
        self.active = active
        self.setFixedSize(32, 32)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_active(self, active: bool) -> None:
        if self.active != active:
            self.active = active
            self.update()

    def paintEvent(self, event) -> None:  # type: ignore[override]
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)

        background = QColor("#3b6df6" if self.active else "#172846")
        foreground = QColor("#ffffff" if self.active else "#9fb1cc")
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(background)
        painter.drawRoundedRect(QRectF(0, 0, 32, 32), 9, 9)

        pen = QPen(foreground, 1.8)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        painter.setPen(pen)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        if self.icon_name == "overview":
            for rect in (
                QRectF(8, 8, 6, 6),
                QRectF(18, 8, 6, 6),
                QRectF(8, 18, 6, 6),
                QRectF(18, 18, 6, 6),
            ):
                painter.drawRoundedRect(rect, 1.5, 1.5)
        elif self.icon_name == "up":
            painter.drawEllipse(QRectF(13, 8, 6, 6))
            painter.drawRoundedRect(QRectF(8.5, 17, 15, 7), 3.5, 3.5)
        elif self.icon_name == "telegram":
            path = QPainterPath(QPointF(7.5, 15.2))
            path.lineTo(24.2, 8.2)
            path.lineTo(18.5, 24)
            path.lineTo(14.8, 18.2)
            path.closeSubpath()
            painter.drawPath(path)
            painter.drawLine(QPointF(14.8, 18.2), QPointF(20.8, 11.7))
        elif self.icon_name == "cookie":
            painter.drawEllipse(QRectF(8, 8, 16, 16))
            painter.drawEllipse(QRectF(12, 12, 1.2, 1.2))
            painter.drawEllipse(QRectF(17.8, 13.7, 1.2, 1.2))
            painter.drawEllipse(QRectF(14.6, 18.6, 1.2, 1.2))
        elif self.icon_name == "log":
            for y in (10.5, 16, 21.5):
                painter.drawEllipse(QRectF(8, y - 1, 2, 2))
                painter.drawLine(QPointF(13, y), QPointF(24, y))
        elif self.icon_name == "settings":
            for y, knob_x in ((10.5, 18), (16, 13), (21.5, 20.5)):
                painter.drawLine(QPointF(8, y), QPointF(24, y))
                painter.setBrush(background)
                painter.drawEllipse(QRectF(knob_x - 2, y - 2, 4, 4))
                painter.setBrush(Qt.BrushStyle.NoBrush)

        painter.end()


class StepperField(QWidget):
    valueChanged = Signal(float)

    def __init__(
        self,
        *,
        is_float: bool,
        minimum: float,
        maximum: float,
        step: float,
        decimals: int = 0,
    ) -> None:
        super().__init__()
        self.setObjectName("stepperField")
        layout = QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(10)

        if is_float:
            spin = QDoubleSpinBox()
            spin.setDecimals(decimals)
        else:
            spin = QSpinBox()
        spin.setButtonSymbols(QSpinBox.ButtonSymbols.NoButtons)
        spin.setRange(minimum, maximum)
        spin.setSingleStep(step)
        spin.setObjectName("stepperInput")
        spin.valueChanged.connect(lambda value: self.valueChanged.emit(float(value)))
        self.spin = spin

        button_col = QVBoxLayout()
        button_col.setContentsMargins(0, 0, 0, 0)
        button_col.setSpacing(8)
        self.up_button = QPushButton("▲")
        self.down_button = QPushButton("▼")
        self.up_button.setObjectName("stepperButton")
        self.down_button.setObjectName("stepperButton")
        self.up_button.clicked.connect(self.spin.stepUp)
        self.down_button.clicked.connect(self.spin.stepDown)
        button_col.addWidget(self.up_button)
        button_col.addWidget(self.down_button)

        button_wrap = QWidget()
        button_wrap.setLayout(button_col)
        button_wrap.setObjectName("stepperButtonsWrap")

        layout.addWidget(self.spin, 1)
        layout.addWidget(button_wrap, 0)

    def setValue(self, value: float) -> None:
        self.spin.setValue(value)

    def value(self) -> float:
        return float(self.spin.value())

    def setEnabled(self, enabled: bool) -> None:  # type: ignore[override]
        super().setEnabled(enabled)
        self.spin.setEnabled(enabled)
        self.up_button.setEnabled(enabled)
        self.down_button.setEnabled(enabled)


class BiliPulseWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        ensure_data_dir()

        self._mutex_handle = None
        self._tray_notice_logged = False
        self.tray_icon: QSystemTrayIcon | None = None
        self.tray_menu: QMenu | None = None
        self.tray_icon_resource: QIcon | None = None
        self._busy = False
        self._worker: WorkerThread | None = None
        self._autosave_ready = False
        self._syncing_up_table = False
        self._known_up_names = self._load_known_up_names()
        self._latest_check_times = self._load_latest_check_times()

        if not self._acquire_single_instance():
            self._activate_existing_instance()
            self._already_running = True
            return
        self._already_running = False

        self.config = AppConfig.load()
        self.logger = self._setup_logger()
        self.icon_path, self.brand_logo_path, self.check_icon_path = self._ensure_assets()
        self.auto_timer = QTimer(self)
        self.auto_timer.timeout.connect(self._trigger_auto_check)
        self.config_autosave_timer = QTimer(self)
        self.config_autosave_timer.setSingleShot(True)
        self.config_autosave_timer.timeout.connect(self._save_config_silent)

        self.setWindowTitle(APP_WINDOW_TITLE)
        self.setMinimumSize(1240, 820)
        self.resize(1480, 940)
        self.setWindowIcon(QIcon(str(self.icon_path)))

        self._build_ui()
        self._create_tray()
        self._load_config_to_ui()
        self._autosave_ready = True
        self._refresh_summary()
        self._refresh_retry_button()
        self._schedule_auto_check(log_message=False)

        self.log("软件已启动。")
        self.log("请先填写 Telegram 配置和 UP 主 UID。")

    def _build_ui(self) -> None:
        root = QWidget()
        root.setObjectName("root")
        self.setCentralWidget(root)

        shell = QVBoxLayout(root)
        shell.setContentsMargins(16, 16, 16, 16)
        shell.setSpacing(14)

        shell.addWidget(self._build_topbar(), 0)

        body = QHBoxLayout()
        body.setContentsMargins(0, 0, 0, 0)
        body.setSpacing(14)

        sidebar = QFrame()
        sidebar.setObjectName("sidebar")
        sidebar.setFixedWidth(258)
        sidebar_layout = QVBoxLayout(sidebar)
        sidebar_layout.setContentsMargins(14, 18, 14, 16)
        sidebar_layout.setSpacing(12)
        sidebar_layout.addWidget(self._build_sidebar_nav())
        sidebar_layout.addStretch(1)
        sidebar_layout.addWidget(self._build_sidebar_notes())

        self.main_scroll = QScrollArea()
        self.main_scroll.setObjectName("mainScroll")
        self.main_scroll.setWidgetResizable(True)
        self.main_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.main_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.main_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        main_host = QWidget()
        main_host.setObjectName("mainScrollHost")
        self.main_scroll.setWidget(main_host)

        main_layout = QVBoxLayout(main_host)
        main_layout.setContentsMargins(0, 0, 2, 4)
        main_layout.setSpacing(14)
        main_layout.addWidget(self._build_metrics_panel())

        workspace = QHBoxLayout()
        workspace.setContentsMargins(0, 0, 0, 0)
        workspace.setSpacing(18)
        self.source_card = self._build_source_card()
        self.log_card = self._build_log_card()
        self.source_card.setMinimumHeight(440)
        self.log_card.setMinimumHeight(440)
        workspace.addWidget(self.source_card, 3)
        workspace.addWidget(self.log_card, 2)
        main_layout.addLayout(workspace, 1)

        self.control_card = self._build_control_card()
        main_layout.addWidget(self.control_card)
        main_layout.addWidget(self._build_hidden_config_fields())

        body.addWidget(sidebar, 0)
        body.addWidget(self.main_scroll, 1)
        shell.addLayout(body, 1)
        self._apply_styles()

    def _build_topbar(self) -> QWidget:
        bar = QFrame()
        bar.setObjectName("topbar")
        self._apply_shadow(bar)
        layout = QHBoxLayout(bar)
        layout.setContentsMargins(18, 10, 18, 10)
        layout.setSpacing(14)

        brand = QWidget()
        brand.setObjectName("topbarBrand")
        brand_layout = QHBoxLayout(brand)
        brand_layout.setContentsMargins(0, 0, 0, 0)
        brand_layout.setSpacing(12)

        mark = QLabel()
        mark.setObjectName("topbarMark")
        mark.setFixedSize(46, 46)
        mark.setPixmap(
            QPixmap(str(self.brand_logo_path)).scaled(
                42,
                42,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)

        text_wrap = QVBoxLayout()
        text_wrap.setContentsMargins(0, 0, 0, 0)
        text_wrap.setSpacing(2)
        title = QLabel(APP_NAME)
        title.setObjectName("topbarTitle")
        subtitle = QLabel("B 站投稿提醒工具")
        subtitle.setObjectName("topbarSubtitle")
        text_wrap.addWidget(title)
        text_wrap.addWidget(subtitle)

        brand_layout.addWidget(mark)
        brand_layout.addLayout(text_wrap, 1)

        self.topbar_status = QLabel("● 本地服务已连接")
        self.topbar_status.setObjectName("topbarStatus")

        self.search_edit = QLineEdit()
        self.search_edit.setObjectName("searchEdit")
        self.search_edit.setPlaceholderText("搜索 UP、UID、拼音或首字母")
        self.search_edit.setFixedWidth(290)
        self.search_edit.textChanged.connect(self._apply_search_filter)

        actions = QHBoxLayout()
        actions.setContentsMargins(0, 0, 0, 0)
        actions.setSpacing(12)
        self.top_check_button = self._button("立即检查投稿", "primary", self.check_updates)
        self.retry_send_button = self._button("重试发送", "warm", self.retry_pending_send)
        self.export_button = self._button("导出配置", "soft", self.export_config)
        self.top_check_button.setMinimumWidth(142)
        self.retry_send_button.setMinimumWidth(108)
        self.export_button.setMinimumWidth(104)
        actions.addWidget(self.top_check_button)
        actions.addWidget(self.retry_send_button)
        actions.addWidget(self.export_button)

        layout.addWidget(brand, 0)
        layout.addStretch(1)
        layout.addWidget(self.search_edit, 0)
        layout.addWidget(self.topbar_status, 0)
        layout.addLayout(actions)
        return bar

    def _build_brand(self) -> QWidget:
        frame = self._sidebar_card("")
        frame.setObjectName("brandCard")
        self._apply_shadow(frame)
        layout = QHBoxLayout()
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(14)
        frame.layout().addLayout(layout)

        mark = QLabel()
        mark.setObjectName("brandMark")
        mark.setFixedSize(64, 64)
        mark.setPixmap(
            QPixmap(str(self.brand_logo_path)).scaled(
                60,
                60,
                Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            )
        )
        mark.setAlignment(Qt.AlignmentFlag.AlignCenter)

        text_wrap = QVBoxLayout()
        text_wrap.setContentsMargins(0, 2, 0, 0)
        text_wrap.setSpacing(3)

        badge = QLabel("B 站投稿提醒工具")
        badge.setObjectName("brandBadge")

        title = QLabel(APP_NAME)
        title.setObjectName("brandTitle")

        text_wrap.addWidget(badge)
        text_wrap.addWidget(title)
        layout.addWidget(mark)
        layout.addLayout(text_wrap, 1)
        return frame

    def _build_sidebar_nav(self) -> QWidget:
        nav = self._sidebar_card("工作台")
        nav.setObjectName("navCard")
        nav.layout().setSpacing(8)
        self.nav_items: dict[str, tuple[QPushButton, NavIcon, QLabel]] = {}
        for nav_key, key, label, active, handler in (
            ("overview", "总", "总览", True, self._show_overview_section),
            ("up", "UP", "UP 监控", False, self._show_up_section),
            ("telegram", "TG", "Telegram", False, self.show_telegram_config),
            ("cookie", "C", "Cookie", False, self.show_cookie_config),
        ):
            nav.layout().addWidget(self._nav_item(nav_key, key, label, active=active, handler=handler))
        system_label = QLabel("系统")
        system_label.setObjectName("navSectionLabel")
        nav.layout().addSpacing(14)
        nav.layout().addWidget(system_label)
        for nav_key, key, label, handler in (
            ("log", "志", "运行日志", self._show_log_section),
            ("settings", "设", "高级设置", self._show_settings_section),
        ):
            nav.layout().addWidget(self._nav_item(nav_key, key, label, active=False, handler=handler))
        return nav

    def _nav_item(self, nav_key: str, key: str, text: str, *, active: bool, handler) -> QPushButton:
        item = QPushButton()
        item.setObjectName("navItemActive" if active else "navItem")
        item.setCursor(Qt.CursorShape.PointingHandCursor)
        item.setFlat(True)
        item.setToolTip(f"打开{text}")
        item.setAccessibleName(text)
        layout = QHBoxLayout(item)
        layout.setContentsMargins(12, 9, 12, 9)
        layout.setSpacing(10)

        icon = NavIcon(nav_key, active=active)

        label = QLabel(text)
        label.setObjectName("navTextActive" if active else "navText")
        label.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        layout.addWidget(icon, 0)
        layout.addWidget(label, 1)
        self.nav_items[nav_key] = (item, icon, label)
        item.clicked.connect(lambda checked=False, key=nav_key, action=handler: self._handle_nav_click(key, action))
        return item

    def _handle_nav_click(self, nav_key: str, handler) -> None:
        self._set_nav_active(nav_key)
        handler()

    def _set_nav_active(self, nav_key: str) -> None:
        if not hasattr(self, "nav_items"):
            return
        for key, (item, icon, label) in self.nav_items.items():
            active = key == nav_key
            item.setObjectName("navItemActive" if active else "navItem")
            label.setObjectName("navTextActive" if active else "navText")
            icon.set_active(active)
            self._refresh_widget_style(item, label)

    def _refresh_widget_style(self, *widgets) -> None:
        for widget in widgets:
            widget.style().unpolish(widget)
            widget.style().polish(widget)
            widget.update()

    def _show_overview_section(self) -> None:
        if hasattr(self, "main_scroll"):
            self._animate_scroll_to(0)

    def _show_up_section(self) -> None:
        self._scroll_to_main_widget(getattr(self, "source_card", None))
        if hasattr(self, "up_table"):
            self.up_table.setFocus()

    def _show_log_section(self) -> None:
        self._scroll_to_main_widget(getattr(self, "log_card", None))
        if hasattr(self, "log_text"):
            self.log_text.setFocus()

    def _show_settings_section(self) -> None:
        self._scroll_to_main_widget(getattr(self, "control_card", None))

    def _scroll_to_main_widget(self, widget) -> None:
        if widget is not None and hasattr(self, "main_scroll"):
            target = max(0, widget.mapTo(self.main_scroll.widget(), QPointF(0, 0).toPoint()).y() - 14)
            self._animate_scroll_to(target)

    def _animate_scroll_to(self, target: int) -> None:
        scroll_bar = self.main_scroll.verticalScrollBar()
        target = max(scroll_bar.minimum(), min(target, scroll_bar.maximum()))
        if hasattr(self, "_scroll_animation"):
            self._scroll_animation.stop()
        self._scroll_animation = QPropertyAnimation(scroll_bar, b"value", self)
        self._scroll_animation.setDuration(260)
        self._scroll_animation.setStartValue(scroll_bar.value())
        self._scroll_animation.setEndValue(target)
        self._scroll_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._scroll_animation.start()

    def show_telegram_config(self) -> None:
        dialog = QDialog(self)
        dialog.setObjectName("configDialog")
        dialog.setWindowTitle("Telegram 配置")
        dialog.setModal(True)
        dialog.setMinimumWidth(640)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 22, 22, 20)
        layout.setSpacing(16)

        title = QLabel("Telegram 配置")
        title.setObjectName("dialogTitle")
        desc = QLabel("查看或更新 Bot Token 与 Chat ID，保存后会写入当前配置。")
        desc.setObjectName("dialogDesc")
        desc.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(desc)

        panel = QFrame()
        panel.setObjectName("configDialogPanel")
        grid = QGridLayout(panel)
        grid.setContentsMargins(18, 18, 18, 18)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)

        token_input = QLineEdit()
        token_input.setPlaceholderText("请输入 Bot Token")
        token_input.setText(self.token_edit.text())
        chat_input = QLineEdit()
        chat_input.setPlaceholderText("请输入 Chat ID")
        chat_input.setText(self.chat_id_edit.text())

        self._add_form_field(grid, 0, 0, "Bot Token", token_input)
        self._add_form_field(grid, 1, 0, "Chat ID", chat_input)
        layout.addWidget(panel)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(10)

        def apply_values() -> None:
            self.token_edit.setText(token_input.text().strip())
            self.chat_id_edit.setText(chat_input.text().strip())
            self._refresh_summary()

        def test_current_config() -> None:
            apply_values()
            self.test_telegram()

        def save_and_close() -> None:
            apply_values()
            if self.save_config():
                dialog.accept()

        test_button = self._button("测试 Telegram", "ghost", test_current_config)
        close_button = self._button("关闭", "soft", dialog.reject)
        save_button = self._button("保存配置", "primary", save_and_close)
        test_button.setMinimumWidth(132)
        close_button.setMinimumWidth(90)
        save_button.setMinimumWidth(112)
        button_row.addWidget(test_button)
        button_row.addStretch(1)
        button_row.addWidget(close_button)
        button_row.addWidget(save_button)
        layout.addLayout(button_row)

        dialog.exec()

    def show_cookie_config(self) -> None:
        dialog = QDialog(self)
        dialog.setObjectName("configDialog")
        dialog.setWindowTitle("Cookie 配置")
        dialog.setModal(True)
        dialog.setMinimumWidth(760)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 22, 22, 20)
        layout.setSpacing(16)

        title = QLabel("Cookie 配置")
        title.setObjectName("dialogTitle")
        desc = QLabel("查看当前 B 站 Cookie，也可以重新读取浏览器 Cookie 后保存。")
        desc.setObjectName("dialogDesc")
        desc.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(desc)

        panel = QFrame()
        panel.setObjectName("configDialogPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(18, 18, 18, 18)
        panel_layout.setSpacing(12)

        browser_checkbox = QCheckBox("优先自动读取浏览器 B 站 Cookie")
        browser_checkbox.setObjectName("softCheck")
        browser_checkbox.setChecked(self.browser_cookie_checkbox.isChecked())
        panel_layout.addWidget(browser_checkbox)

        cookie_input = PlainTextEdit()
        cookie_input.setPlaceholderText("平时可以留空，遇到风控或需要手动校验时再填。")
        cookie_input.setPlainText(self.cookie_text.toPlainText())
        cookie_input.setMinimumHeight(220)

        cookie_wrap = QWidget()
        cookie_layout = QVBoxLayout(cookie_wrap)
        cookie_layout.setContentsMargins(0, 0, 0, 0)
        cookie_layout.setSpacing(6)
        cookie_layout.addWidget(self._field_label("B 站 Cookie"))
        cookie_layout.addWidget(cookie_input)
        panel_layout.addWidget(cookie_wrap)
        layout.addWidget(panel)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(10)

        def apply_values() -> None:
            self.browser_cookie_checkbox.setChecked(browser_checkbox.isChecked())
            self.cookie_text.setPlainText(cookie_input.toPlainText().strip())
            self._refresh_summary()

        def read_browser_cookie() -> None:
            if self.fill_browser_cookie():
                browser_checkbox.setChecked(True)
                cookie_input.setPlainText(self.cookie_text.toPlainText())

        def save_and_close() -> None:
            apply_values()
            if self.save_config():
                dialog.accept()

        read_button = self._button("读取浏览器 Cookie", "ghost", read_browser_cookie)
        close_button = self._button("关闭", "soft", dialog.reject)
        save_button = self._button("保存配置", "primary", save_and_close)
        read_button.setMinimumWidth(148)
        close_button.setMinimumWidth(90)
        save_button.setMinimumWidth(112)
        button_row.addWidget(read_button)
        button_row.addStretch(1)
        button_row.addWidget(close_button)
        button_row.addWidget(save_button)
        layout.addLayout(button_row)

        dialog.exec()

    def _build_sidebar_actions(self) -> QWidget:
        card = self._sidebar_card("快速操作")
        self.sidebar_check_button = self._button("立即检查投稿", "primary", self.check_updates)
        self.sidebar_recent_button = self._button("补发最近投稿", "warm", self.send_recent_videos)
        self.sidebar_save_button = self._button("保存当前配置", "soft", self.save_config)
        self.sidebar_cookie_button = self._button("读取浏览器 Cookie", "ghost", self.fill_browser_cookie)
        self.sidebar_export_button = self._button("导出配置", "accent", self.export_config)
        for index, widget in enumerate((
            self.sidebar_check_button,
            self.sidebar_recent_button,
            self.sidebar_save_button,
            self.sidebar_cookie_button,
            self.sidebar_export_button,
        )):
            widget.setMinimumHeight(42)
            card.layout().addWidget(widget)
            if index < 4:
                card.layout().addSpacing(4)
        return card

    def _build_sidebar_notes(self) -> QWidget:
        card = self._sidebar_card("当前状态")
        card.setObjectName("statusCard")
        pill = QLabel("● 待命")
        pill.setObjectName("statusPill")
        pill.setAlignment(Qt.AlignmentFlag.AlignCenter)
        pill.setFixedSize(88, 34)
        self.status_pill = pill
        self.snapshot_targets = QLabel("监控 0 个目标")
        self.snapshot_targets.setObjectName("statusText")
        self.snapshot_auto = QLabel("自动检查：已关闭")
        self.snapshot_auto.setObjectName("statusText")
        self.snapshot_request = QLabel("请求节奏：--")
        self.snapshot_request.setObjectName("statusText")
        card.layout().addWidget(pill)
        card.layout().addWidget(self.snapshot_targets)
        card.layout().addWidget(self.snapshot_auto)
        card.layout().addWidget(self.snapshot_request)
        return card

    def _build_metrics_panel(self) -> QWidget:
        panel = QWidget()
        panel.setObjectName("metricsPanel")
        panel_layout = QVBoxLayout(panel)
        panel_layout.setContentsMargins(0, 0, 0, 0)
        panel_layout.setSpacing(12)

        hero = QFrame()
        hero.setObjectName("overviewHero")
        hero_layout = QHBoxLayout(hero)
        hero_layout.setContentsMargins(24, 18, 22, 18)
        hero_layout.setSpacing(18)

        hero_copy = QVBoxLayout()
        hero_copy.setContentsMargins(0, 0, 0, 0)
        hero_copy.setSpacing(4)
        eyebrow = QLabel("投稿监控 · 实时工作台")
        eyebrow.setObjectName("overviewEyebrow")
        title = QLabel("让每一次更新，都准时抵达")
        title.setObjectName("overviewTitle")
        subtitle = QLabel("集中查看监控目标、巡检节奏与推送状态")
        subtitle.setObjectName("overviewSubtitle")
        hero_copy.addWidget(eyebrow)
        hero_copy.addWidget(title)
        hero_copy.addWidget(subtitle)

        status_wrap = QFrame()
        status_wrap.setObjectName("overviewStatus")
        status_layout = QVBoxLayout(status_wrap)
        status_layout.setContentsMargins(16, 10, 16, 10)
        status_layout.setSpacing(2)
        status_label = QLabel("当前状态")
        status_label.setObjectName("overviewStatusLabel")
        self.overview_status_value = QLabel("● 系统待命")
        self.overview_status_value.setObjectName("overviewStatusValue")
        status_layout.addWidget(status_label)
        status_layout.addWidget(self.overview_status_value)

        hero_layout.addLayout(hero_copy, 1)
        hero_layout.addWidget(status_wrap, 0, Qt.AlignmentFlag.AlignVCenter)
        panel_layout.addWidget(hero)

        meta_row = QHBoxLayout()
        meta_row.setContentsMargins(0, 0, 0, 0)
        meta_row.setSpacing(12)

        target_card, self.summary_chip_up = self._build_metric_card("监控", "--")
        fetch_card, self.summary_chip_sync = self._build_metric_card("抓取", "--")
        auto_card, self.summary_chip_auto = self._build_metric_card("巡检", "--")
        request_card, self.summary_chip_request = self._build_metric_card("请求", "--")
        cookie_card, self.summary_chip_cookie = self._build_metric_card("消息", "--")
        for item in (target_card, fetch_card, auto_card, request_card, cookie_card):
            meta_row.addWidget(item, 1)

        action_bar = QWidget()
        action_bar.setObjectName("metricsActions")
        action_layout = QHBoxLayout(action_bar)
        action_layout.setContentsMargins(8, 0, 0, 0)
        action_layout.setSpacing(10)
        self.top_test_button = self._button("测试 Telegram", "ghost", self.test_telegram)
        self.fill_cookie_button = self._button("读取 Cookie", "ghost", self.fill_browser_cookie)
        self.top_test_button.setMinimumWidth(126)
        self.fill_cookie_button.setMinimumWidth(112)
        self.top_test_button.setMinimumHeight(42)
        self.fill_cookie_button.setMinimumHeight(42)
        action_layout.addWidget(self.top_test_button)
        action_layout.addWidget(self.fill_cookie_button)
        self.test_button = self.top_test_button
        meta_row.addWidget(action_bar, 0)
        panel_layout.addLayout(meta_row)
        return panel

    def _build_metric_card(self, label_text: str, value_text: str) -> tuple[QFrame, QLabel]:
        card = QFrame()
        card.setObjectName("metricCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(6)
        label = QLabel(label_text)
        label.setObjectName("metricCardLabel")
        value = QLabel(value_text)
        value.setObjectName("metricCardValue")
        value.setWordWrap(True)
        layout.addWidget(label)
        layout.addWidget(value)
        return card, value

    def _build_connection_card(self) -> QWidget:
        card, grid = self._form_card("Telegram", "", columns=1)
        card.setObjectName("railCard")
        self.token_edit = self._line_edit("请输入 Bot Token")
        self.chat_id_edit = self._line_edit("请输入 Chat ID")
        self._add_form_field(grid, 0, 0, "Bot Token", self.token_edit)
        self._add_form_field(grid, 1, 0, "Chat ID", self.chat_id_edit)

        self.test_button = self._button("测试 Telegram", "ghost", self.test_telegram)
        self.test_button.setMinimumHeight(38)
        grid.addWidget(self.test_button, 2, 0)
        return card

    def _build_source_card(self) -> QWidget:
        card = self._surface_card()
        card.setObjectName("targetCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(22, 18, 22, 18)
        head.setSpacing(12)
        title = QLabel("UP 主列表")
        title.setObjectName("cardTitle")
        self.add_up_button = self._button("添加", "accent", self.add_up_target, compact=True)
        self.add_up_button.setMinimumWidth(72)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.add_up_button)
        layout.addLayout(head)

        self.up_table = QTableWidget(0, 5)
        self.up_table.setObjectName("upTable")
        self.up_table.setHorizontalHeaderLabels(["名称", "UID", "最近检查", "状态", "操作"])
        for column in range(5):
            header_item = self.up_table.horizontalHeaderItem(column)
            if header_item is None:
                continue
            alignment = Qt.AlignmentFlag.AlignVCenter
            alignment |= Qt.AlignmentFlag.AlignLeft if column == 0 else Qt.AlignmentFlag.AlignCenter
            header_item.setTextAlignment(alignment)
        self.up_table.verticalHeader().setVisible(False)
        self.up_table.setShowGrid(False)
        self.up_table.setAlternatingRowColors(False)
        self.up_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.up_table.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.up_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.up_table.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.up_table.horizontalHeader().setStretchLastSection(False)
        for column in range(5):
            self.up_table.horizontalHeader().setSectionResizeMode(column, QHeaderView.ResizeMode.Fixed)
        self.up_table.itemChanged.connect(self._sync_up_text_from_table)
        layout.addWidget(self.up_table, 1)
        QTimer.singleShot(0, self._resize_up_table_columns)

        self.up_text = self._text_edit("在这里维护要监控的 UP 主列表，每行一个，支持 UID|备注。")
        self.up_text.hide()
        self.up_text.textChanged.connect(self._populate_up_table_from_text)
        return card

    def _build_cookie_card(self) -> QWidget:
        card = self._surface_card()
        card.setObjectName("railCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(12)

        cookie_top = QHBoxLayout()
        cookie_top.setSpacing(10)
        title = QLabel("B 站 Cookie")
        title.setObjectName("cardTitle")
        cookie_top.addWidget(title)
        cookie_top.addStretch(1)
        self.fill_cookie_button = self._button("读取", "ghost", self.fill_browser_cookie, compact=True)
        cookie_top.addWidget(self.fill_cookie_button)
        layout.addLayout(cookie_top)

        cookie_option_row = QHBoxLayout()
        cookie_option_row.setContentsMargins(0, 0, 0, 0)
        cookie_option_row.setSpacing(10)
        self.browser_cookie_checkbox = QCheckBox("优先自动读取浏览器 B 站 Cookie")
        self.browser_cookie_checkbox.setObjectName("softCheck")
        self.browser_cookie_checkbox.stateChanged.connect(lambda _: self._schedule_config_autosave())
        cookie_option_row.addWidget(self.browser_cookie_checkbox)
        cookie_option_row.addStretch(1)
        layout.addLayout(cookie_option_row)

        self.cookie_text = self._text_edit("平时可以留空，遇到风控或需要手动校验时再填。")
        self.cookie_text.setMinimumHeight(170)
        layout.addWidget(self.cookie_text, 1)
        return card

    def _build_control_card(self) -> QWidget:
        card = self._surface_card()
        card.setObjectName("settingsStrip")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(22, 16, 22, 16)
        title = QLabel("参数与推送")
        title.setObjectName("cardTitle")
        self.top_save_button = self._button("保存设置", "primary", self.save_config)
        self.top_save_button.setMinimumWidth(112)
        head.addWidget(title)
        head.addStretch(1)
        head.addWidget(self.top_save_button)
        layout.addLayout(head)

        grid = QGridLayout()
        grid.setContentsMargins(22, 16, 22, 18)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(8)
        for index in range(5):
            grid.setColumnStretch(index, 1)
        layout.addLayout(grid)

        self.auto_hours_spin = self._stepper_int(0, 24)
        self.fetch_count_spin = self._stepper_int(1, 30)
        self.first_sync_spin = self._stepper_int(1, 30)
        self.request_gap_min_spin = self._stepper_float(0.0, 60.0, 0.5, 1)
        self.request_gap_max_spin = self._stepper_float(0.0, 60.0, 0.5, 1)
        self.message_interval_spin = self._stepper_float(0.0, 60.0, 0.5, 1)
        for stepper in (
            self.auto_hours_spin,
            self.fetch_count_spin,
            self.first_sync_spin,
            self.request_gap_min_spin,
            self.request_gap_max_spin,
            self.message_interval_spin,
        ):
            stepper.hide()

        self.auto_hours_display = self._settings_display_edit()
        self.fetch_count_display = self._settings_display_edit()
        self.first_sync_display = self._settings_display_edit()
        self.request_range_display = self._settings_display_edit()
        self.cookie_mode_button = self._button("浏览器优先", "soft", self._toggle_cookie_mode)

        for edit in (
            self.auto_hours_display,
            self.fetch_count_display,
            self.first_sync_display,
            self.request_range_display,
        ):
            edit.editingFinished.connect(self._apply_settings_display_edits)

        self.browser_cookie_checkbox = QCheckBox("浏览器优先")
        self.browser_cookie_checkbox.hide()
        self.browser_cookie_checkbox.stateChanged.connect(lambda _: self._schedule_config_autosave())
        self.browser_cookie_checkbox.stateChanged.connect(lambda _: self._refresh_summary())

        self._add_form_field(grid, 0, 0, "自动检查", self.auto_hours_display)
        self._add_form_field(grid, 0, 1, "抓取条数", self.fetch_count_display)
        self._add_form_field(grid, 0, 2, "首轮同步", self.first_sync_display)
        self._add_form_field(grid, 0, 3, "请求间隔", self.request_range_display)
        self._add_form_field(grid, 0, 4, "Cookie", self.cookie_mode_button)

        self.check_button = self.top_check_button
        return card

    def _build_log_card(self) -> QWidget:
        card = self._surface_card()
        card.setObjectName("logCard")
        layout = QVBoxLayout(card)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        head = QHBoxLayout()
        head.setContentsMargins(22, 18, 22, 18)
        title = QLabel("运行日志")
        title.setObjectName("cardTitle")
        head.addWidget(title)
        head.addStretch(1)
        self.clear_log_button = self._button("清空", "ghost", self.clear_log_view, compact=True)
        self.clear_log_button.setFixedWidth(62)
        head.addWidget(self.clear_log_button)
        layout.addLayout(head)

        self.log_text = LogListWidget()
        self.log_text.setObjectName("logList")
        self.log_text.setMinimumHeight(380)
        self.log_text.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        layout.addWidget(self.log_text, 1)
        return card

    def _build_hidden_config_fields(self) -> QWidget:
        hidden = QWidget()
        hidden.hide()
        layout = QVBoxLayout(hidden)
        layout.setContentsMargins(0, 0, 0, 0)

        self.token_edit = self._line_edit("请输入 Bot Token")
        self.chat_id_edit = self._line_edit("请输入 Chat ID")
        self.youtube_text = self._text_edit("每行一个，支持频道 ID、频道链接、@handle|备注。")
        self.cookie_text = self._text_edit("平时可以留空，遇到风控或需要手动校验时再填。")
        self.export_dir_edit = self._line_edit("请选择导出目录")
        self.export_dir_edit.setReadOnly(True)
        self.select_export_dir_button = self._button("选择目录", "accent", self.select_export_dir, compact=True)

        self.sidebar_check_button = self.top_check_button
        self.sidebar_recent_button = self._button("补发最近投稿", "warm", self.send_recent_videos)
        self.sidebar_save_button = self.top_save_button
        self.sidebar_cookie_button = self.fill_cookie_button
        self.sidebar_export_button = self.export_button

        for widget in (
            self.token_edit,
            self.chat_id_edit,
            self.youtube_text,
            self.cookie_text,
            self.export_dir_edit,
            self.select_export_dir_button,
            self.sidebar_recent_button,
        ):
            layout.addWidget(widget)
        return hidden


    def _apply_shadow(self, widget) -> None:
        # Effects on container widgets rasterize their children. On Windows with
        # fractional DPI scaling that also softens text, so depth is drawn with
        # crisp borders and layered surfaces instead.
        widget.setGraphicsEffect(None)

    def _surface_card(self) -> QFrame:
        frame = QFrame()
        frame.setObjectName("surfaceCard")
        self._apply_shadow(frame)
        return frame

    def _sidebar_card(self, title: str) -> QFrame:
        frame = QFrame()
        frame.setObjectName("sidebarCard")
        self._apply_shadow(frame)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(14)
        if title:
            title_label = QLabel(title)
            title_label.setObjectName("sidebarCardTitle")
            layout.addWidget(title_label)
        return frame

    def _form_card(self, title: str, desc: str, *, columns: int) -> tuple[QFrame, QGridLayout]:
        card = self._surface_card()
        wrap = QVBoxLayout(card)
        wrap.setContentsMargins(16, 16, 16, 16)
        wrap.setSpacing(14)

        title_label = QLabel(title)
        title_label.setObjectName("cardTitle")
        wrap.addWidget(title_label)
        if desc:
            desc_label = QLabel(desc)
            desc_label.setObjectName("cardDesc")
            desc_label.setWordWrap(True)
            wrap.addWidget(desc_label)

        grid = QGridLayout()
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)
        for index in range(columns):
            grid.setColumnStretch(index, 1)
        wrap.addLayout(grid)
        return card, grid

    def _add_form_field(self, grid: QGridLayout, row: int, column: int, label: str, widget, *, span: int = 1) -> None:
        wrap = QWidget()
        layout = QVBoxLayout(wrap)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        layout.addWidget(self._field_label(label))
        layout.addWidget(widget)
        grid.addWidget(wrap, row, column, 1, span)

    def _field_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setObjectName("fieldLabel")
        return label

    def _line_edit(self, placeholder: str) -> QLineEdit:
        edit = QLineEdit()
        edit.setPlaceholderText(placeholder)
        edit.textChanged.connect(self._refresh_summary)
        edit.textChanged.connect(lambda _: self._schedule_config_autosave())
        return edit

    def _settings_display_edit(self) -> QLineEdit:
        edit = QLineEdit()
        edit.setObjectName("settingsDisplay")
        return edit

    def _text_edit(self, placeholder: str = "", read_only: bool = False, *, log_view: bool = False) -> QTextEdit:
        edit = LogTextEdit() if log_view else PlainTextEdit()
        edit.setPlaceholderText(placeholder)
        edit.setReadOnly(read_only)
        edit.textChanged.connect(self._refresh_summary)
        if not read_only:
            edit.textChanged.connect(self._schedule_config_autosave)
        return edit

    def _stepper_int(self, minimum: int, maximum: int) -> StepperField:
        field = StepperField(is_float=False, minimum=minimum, maximum=maximum, step=1)
        field.valueChanged.connect(lambda _: self._refresh_summary())
        field.valueChanged.connect(lambda _: self._schedule_config_autosave())
        return field

    def _stepper_float(self, minimum: float, maximum: float, step: float, decimals: int) -> StepperField:
        field = StepperField(is_float=True, minimum=minimum, maximum=maximum, step=step, decimals=decimals)
        field.valueChanged.connect(lambda _: self._refresh_summary())
        field.valueChanged.connect(lambda _: self._schedule_config_autosave())
        return field

    def _button(self, text: str, style_name: str, handler, *, compact: bool = False) -> QPushButton:
        button = QPushButton(text)
        button.setProperty("styleType", style_name)
        button.setProperty("compact", compact)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.clicked.connect(handler)
        return button

    def _chip(self, text: str) -> QLabel:
        chip = QLabel(text)
        chip.setObjectName("headerChip")
        return chip

    def _stat_chip(self, text: str) -> QLabel:
        chip = QLabel(text)
        chip.setObjectName("statChip")
        return chip

    def _metric_row(self, parent: QFrame, label: str) -> QLabel:
        row = QWidget()
        row.setObjectName("metricTile")
        layout = QVBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        label_widget = QLabel(label)
        label_widget.setObjectName("metricLabel")
        value_widget = QLabel("--")
        value_widget.setObjectName("metricValue")
        layout.addWidget(label_widget)
        layout.addWidget(value_widget)
        parent.layout().addWidget(row)
        return value_widget

    def add_up_target(self) -> None:
        uid, ok = QInputDialog.getText(self, "添加 UP 主", "请输入 UP 主 UID：")
        if not ok:
            return
        uid = uid.strip()
        if not uid.isdigit():
            QMessageBox.warning(self, "UID 不合法", "UID 必须是纯数字。")
            return
        if self._up_uid_exists(uid):
            QMessageBox.warning(self, "UID 已存在", "这个 UP 主已经在列表中。")
            return

        name, ok = QInputDialog.getText(self, "添加 UP 主", "请输入名称或备注（可留空）：")
        if not ok:
            name = ""
        self._append_up_table_row(name.strip(), uid, enabled=True)
        self._sync_up_text_from_table()

    def _load_known_up_names(self) -> dict[str, str]:
        try:
            store = SentVideoStore()
            try:
                names: dict[str, str] = {}
                for item in store.list_sent_videos():
                    if item.uid and item.up_name and item.uid not in names:
                        names[item.uid] = item.up_name
                return names
            finally:
                store.close()
        except Exception:
            return {}

    def _load_latest_check_times(self) -> dict[str, str]:
        if not LOG_PATH.exists():
            return {}
        latest: dict[str, str] = {}
        pattern = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}):\d{2}.*开始检查 UID (\d+)")
        try:
            for line in LOG_PATH.read_text(encoding="utf-8", errors="ignore").splitlines():
                match = pattern.search(line)
                if match:
                    latest[match.group(3)] = match.group(2)
        except Exception:
            return {}
        return latest

    def _fallback_up_label(self, uid: str) -> str:
        return self._known_up_names.get(uid, "B站 UP")

    def _latest_check_text(self, uid: str) -> str:
        return self._latest_check_times.get(uid, "--")

    def _up_uid_exists(self, uid: str, *, except_row: int | None = None) -> bool:
        for row in range(self.up_table.rowCount()):
            if except_row is not None and row == except_row:
                continue
            item = self.up_table.item(row, 1)
            if item and item.text().strip() == uid:
                return True
        return False

    def _serialize_up_target_line(self, target: UpTarget) -> str:
        if target.enabled:
            return f"{target.uid}|{target.label}" if target.label else target.uid
        return f"{target.uid}|{target.label}|0"

    def _parse_up_target_line(self, line: str) -> tuple[str, str, bool]:
        parts = (line.split("|", 2) + ["", ""])[:3]
        uid = parts[0].strip()
        label = parts[1].strip()
        enabled_text = parts[2].strip().casefold()
        enabled = enabled_text not in {"0", "false", "disabled", "停用", "禁用"}
        return uid, label, enabled

    def _populate_up_table_from_text(self) -> None:
        if self._syncing_up_table or not hasattr(self, "up_table"):
            return
        self._syncing_up_table = True
        self.up_table.setRowCount(0)
        for raw_line in self.up_text.toPlainText().splitlines():
            line = raw_line.strip()
            if not line:
                continue
            uid, label, enabled = self._parse_up_target_line(line)
            if uid:
                self._append_up_table_row(label, uid, enabled=enabled)
        self._syncing_up_table = False
        self._apply_search_filter()

    def _append_up_table_row(self, label: str, uid: str, *, enabled: bool = True) -> None:
        row = self.up_table.rowCount()
        self.up_table.insertRow(row)
        display_label = label or self._fallback_up_label(uid)
        values = [display_label, uid, self._latest_check_text(uid), "启用" if enabled else "停用", ""]
        for column, value in enumerate(values):
            item = QTableWidgetItem(value)
            if column == 0:
                item.setData(Qt.ItemDataRole.UserRole, label)
            if column == 3:
                item.setData(Qt.ItemDataRole.UserRole, enabled)
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEditable)
            if column == 0:
                item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
            elif column in (1, 2, 3, 4):
                item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.up_table.setItem(row, column, item)
        self.up_table.setCellWidget(row, 4, self._build_up_action_cell(row))
        self._set_up_row_enabled(row, enabled)
        self.up_table.setRowHeight(row, 52)

    def _set_up_row_enabled(self, row: int, enabled: bool) -> None:
        status_item = self.up_table.item(row, 3)
        if status_item is None:
            return
        status_item.setText("启用" if enabled else "停用")
        status_item.setData(Qt.ItemDataRole.UserRole, enabled)
        text_color = QColor("#0f172a" if enabled else "#94a3b8")
        status_color = QColor("#047857" if enabled else "#94a3b8")
        for column in range(self.up_table.columnCount()):
            item = self.up_table.item(row, column)
            if item is not None:
                item.setForeground(status_color if column == 3 else text_color)

    def _refresh_up_action_cells(self) -> None:
        if not hasattr(self, "up_table"):
            return
        for row in range(self.up_table.rowCount()):
            self.up_table.setCellWidget(row, 4, self._build_up_action_cell(row))
        QTimer.singleShot(0, self._resize_up_table_columns)

    def _build_up_action_cell(self, row: int) -> QWidget:
        wrap = QWidget()
        wrap.setObjectName("tableActionCell")
        layout = QHBoxLayout(wrap)
        layout.setContentsMargins(8, 7, 8, 7)
        layout.setSpacing(0)

        button = QPushButton("编辑")
        button.setObjectName("tableActionButton")
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        button.setFlat(True)
        button.setMinimumHeight(30)
        button.setMinimumWidth(52)
        button.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Fixed)
        button.adjustSize()
        button.setProperty("row", row)
        button.clicked.connect(lambda checked=False, target_row=row: self._edit_up_target(target_row))
        layout.addWidget(button)
        return wrap

    def _edit_up_target(self, row: int) -> None:
        if row < 0 or row >= self.up_table.rowCount():
            return

        name_item = self.up_table.item(row, 0)
        uid_item = self.up_table.item(row, 1)
        status_item = self.up_table.item(row, 3)
        if name_item is None or uid_item is None or status_item is None:
            return

        current_label = str(name_item.data(Qt.ItemDataRole.UserRole) or "")
        current_uid = uid_item.text().strip()
        current_enabled_data = status_item.data(Qt.ItemDataRole.UserRole)
        current_enabled = (
            bool(current_enabled_data)
            if isinstance(current_enabled_data, bool)
            else status_item.text().strip() != "停用"
        )

        dialog = QDialog(self)
        dialog.setObjectName("configDialog")
        dialog.setWindowTitle("编辑 UP 主")
        dialog.setModal(True)
        dialog.setMinimumWidth(520)

        layout = QVBoxLayout(dialog)
        layout.setContentsMargins(22, 22, 22, 20)
        layout.setSpacing(16)

        title = QLabel("编辑 UP 主")
        title.setObjectName("dialogTitle")
        desc = QLabel("修改名称或 UID 后会同步到当前监控列表。")
        desc.setObjectName("dialogDesc")
        desc.setWordWrap(True)
        layout.addWidget(title)
        layout.addWidget(desc)

        panel = QFrame()
        panel.setObjectName("configDialogPanel")
        grid = QGridLayout(panel)
        grid.setContentsMargins(18, 18, 18, 18)
        grid.setHorizontalSpacing(14)
        grid.setVerticalSpacing(12)

        name_input = QLineEdit()
        name_input.setPlaceholderText("可留空，留空时自动显示已知 UP 名称")
        name_input.setText(current_label)
        uid_input = QLineEdit()
        uid_input.setPlaceholderText("请输入 UP 主 UID")
        uid_input.setText(current_uid)
        enabled_checkbox = QCheckBox("启用监控")
        enabled_checkbox.setObjectName("softCheck")
        enabled_checkbox.setChecked(current_enabled)
        self._add_form_field(grid, 0, 0, "名称", name_input)
        self._add_form_field(grid, 1, 0, "UID", uid_input)
        grid.addWidget(enabled_checkbox, 2, 0)
        layout.addWidget(panel)

        button_row = QHBoxLayout()
        button_row.setContentsMargins(0, 0, 0, 0)
        button_row.setSpacing(10)
        delete_button = self._button("删除", "danger", lambda: None)
        close_button = self._button("关闭", "soft", dialog.reject)
        save_button = self._button("保存", "primary", lambda: None)
        delete_button.setMinimumWidth(90)
        close_button.setMinimumWidth(90)
        save_button.setMinimumWidth(96)

        def delete_row() -> None:
            display_name = name_item.text().strip() or current_uid
            should_delete = QMessageBox.question(
                dialog,
                "删除 UP 主",
                f"确定要从监控列表删除「{display_name}」吗？",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if should_delete != QMessageBox.StandardButton.Yes:
                return
            self._syncing_up_table = True
            self.up_table.removeRow(row)
            self._syncing_up_table = False
            self._refresh_up_action_cells()
            self._sync_up_text_from_table()
            self._apply_search_filter()
            dialog.accept()

        def save_row() -> None:
            new_uid = uid_input.text().strip()
            new_label = name_input.text().strip()
            new_enabled = enabled_checkbox.isChecked()
            if not new_uid.isdigit():
                QMessageBox.warning(dialog, "UID 不合法", "UID 必须是纯数字。")
                return
            if self._up_uid_exists(new_uid, except_row=row):
                QMessageBox.warning(dialog, "UID 已存在", "这个 UP 主已经在列表中。")
                return

            self._syncing_up_table = True
            name_item.setText(new_label or self._fallback_up_label(new_uid))
            name_item.setData(Qt.ItemDataRole.UserRole, new_label)
            uid_item.setText(new_uid)
            latest_item = self.up_table.item(row, 2)
            if latest_item is not None:
                latest_item.setText(self._latest_check_text(new_uid))
            self._set_up_row_enabled(row, new_enabled)
            self._syncing_up_table = False
            self._sync_up_text_from_table()
            dialog.accept()

        try:
            delete_button.clicked.disconnect()
        except TypeError:
            pass
        delete_button.clicked.connect(delete_row)
        try:
            save_button.clicked.disconnect()
        except TypeError:
            pass
        save_button.clicked.connect(save_row)
        button_row.addWidget(delete_button)
        button_row.addStretch(1)
        button_row.addWidget(close_button)
        button_row.addWidget(save_button)
        layout.addLayout(button_row)

        dialog.exec()

    def _resize_up_table_columns(self) -> None:
        if not hasattr(self, "up_table"):
            return
        available = max(500, self.up_table.viewport().width() - 2)
        fixed_recent = 112
        fixed_status = 74
        fixed_action = self._up_action_column_width()
        flexible = max(250, available - fixed_recent - fixed_status - fixed_action)
        name_width = max(138, min(235, int(flexible * 0.48)))
        uid_width = max(112, flexible - name_width)
        for column, width in enumerate((name_width, uid_width, fixed_recent, fixed_status, fixed_action)):
            self.up_table.setColumnWidth(column, width)

    def _up_action_column_width(self) -> int:
        base_width = 68
        for row in range(self.up_table.rowCount()):
            cell = self.up_table.cellWidget(row, 4)
            if cell is None:
                continue
            button = cell.findChild(QPushButton, "tableActionButton")
            if button is None:
                continue
            base_width = max(base_width, button.sizeHint().width() + 24)
        return base_width

    def _sync_up_text_from_table(self, *_args) -> None:
        if self._syncing_up_table or not hasattr(self, "up_table"):
            return
        lines: list[str] = []
        seen: set[str] = set()
        for row in range(self.up_table.rowCount()):
            name_item = self.up_table.item(row, 0)
            uid_item = self.up_table.item(row, 1)
            status_item = self.up_table.item(row, 3)
            label = name_item.text().strip() if name_item else ""
            uid = uid_item.text().strip() if uid_item else ""
            if not uid or not uid.isdigit() or uid in seen:
                continue
            if name_item and not (name_item.data(Qt.ItemDataRole.UserRole) or "") and label == self._fallback_up_label(uid):
                label = ""
            if status_item:
                enabled_data = status_item.data(Qt.ItemDataRole.UserRole)
                enabled = bool(enabled_data) if isinstance(enabled_data, bool) else status_item.text().strip() != "停用"
            else:
                enabled = True
            seen.add(uid)
            lines.append(self._serialize_up_target_line(UpTarget(uid=uid, label=label, enabled=enabled)))

        self._syncing_up_table = True
        self.up_text.setPlainText("\n".join(lines))
        self._syncing_up_table = False
        self._refresh_summary()
        self._schedule_config_autosave()
        self._apply_search_filter()

    def _apply_search_filter(self) -> None:
        if not hasattr(self, "up_table") or not hasattr(self, "search_edit"):
            return
        keyword = self.search_edit.text().strip().casefold()
        for row in range(self.up_table.rowCount()):
            row_text = " ".join(
                self.up_table.item(row, column).text()
                for column in range(self.up_table.columnCount())
                if self.up_table.item(row, column)
            )
            search_index = build_search_index(row_text)
            self.up_table.setRowHidden(row, bool(keyword and keyword not in search_index))

    def _toggle_cookie_mode(self) -> None:
        self.browser_cookie_checkbox.setChecked(not self.browser_cookie_checkbox.isChecked())

    def _apply_settings_display_edits(self) -> None:
        self.auto_hours_spin.setValue(self._first_number(self.auto_hours_display.text(), default=self.auto_hours_spin.value()))
        self.fetch_count_spin.setValue(self._first_number(self.fetch_count_display.text(), default=self.fetch_count_spin.value()))
        self.first_sync_spin.setValue(self._first_number(self.first_sync_display.text(), default=self.first_sync_spin.value()))
        request_numbers = self._numbers_from_text(self.request_range_display.text())
        if request_numbers:
            self.request_gap_min_spin.setValue(request_numbers[0])
            self.request_gap_max_spin.setValue(request_numbers[1] if len(request_numbers) > 1 else request_numbers[0])
        if hasattr(self, "message_interval_display"):
            self.message_interval_spin.setValue(
                self._first_number(self.message_interval_display.text(), default=self.message_interval_spin.value())
            )
        self._refresh_summary()
        self._schedule_config_autosave()

    def _numbers_from_text(self, text: str) -> list[float]:
        numbers: list[float] = []
        current = ""
        for char in text:
            if char.isdigit() or char == ".":
                current += char
            elif current:
                try:
                    numbers.append(float(current))
                except ValueError:
                    pass
                current = ""
        if current:
            try:
                numbers.append(float(current))
            except ValueError:
                pass
        return numbers

    def _first_number(self, text: str, *, default: float) -> float:
        numbers = self._numbers_from_text(text)
        return numbers[0] if numbers else default

    def _set_display_text(self, edit: QLineEdit, text: str) -> None:
        if not edit.hasFocus() and edit.text() != text:
            edit.setText(text)

    def resizeEvent(self, event: QResizeEvent) -> None:  # type: ignore[override]
        super().resizeEvent(event)
        QTimer.singleShot(0, self._resize_up_table_columns)

    def _apply_styles(self) -> None:
        self.setStyleSheet(
            f"""
            QWidget#root {{
                background: #f1f4f9;
                color: #142033;
                font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
                font-size: 10pt;
            }}
            QFrame#topbar {{
                background: #ffffff;
                border: 1px solid #dfe5ef;
                border-radius: 14px;
            }}
            QWidget#topbarBrand {{
                background: transparent;
            }}
            QLabel#topbarMark {{
                background: transparent;
                border-radius: 11px;
            }}
            QLabel#topbarTitle {{
                color: #101828;
                font-size: 15pt;
                font-weight: 700;
            }}
            QLabel#topbarSubtitle {{
                color: #7b879b;
                font-size: 8.5pt;
                font-weight: 500;
            }}
            QLabel#topbarStatus {{
                background: #ecfdf5;
                color: #087a56;
                border: 1px solid #c7f0df;
                border-radius: 9px;
                padding: 7px 12px;
                font-size: 9pt;
                font-weight: 600;
            }}
            QLineEdit#searchEdit {{
                background: #f6f8fc;
                border: 1px solid #e0e6f0;
                border-radius: 10px;
                color: #142033;
                padding: 0 14px;
                min-height: 36px;
                font-size: 9.5pt;
            }}
            QLineEdit#searchEdit:focus {{
                background: #ffffff;
                border: 1px solid #6e91f8;
            }}
            QScrollArea#mainScroll {{
                background: transparent;
                border: none;
            }}
            QWidget#mainScrollHost {{
                background: transparent;
            }}
            QScrollArea#mainScroll > QWidget > QWidget {{
                background: transparent;
            }}
            QFrame#sidebar {{
                background: #0c1830;
                border: none;
                border-radius: 16px;
            }}
            QFrame#brandCard {{
                background: #142442;
                border: 1px solid #24385b;
                border-radius: 13px;
            }}
            QFrame#sidebarCard {{
                background: #142442;
                border: 1px solid #24385b;
                border-radius: 13px;
            }}
            QFrame#navCard {{
                background: transparent;
                border: none;
                border-radius: 0;
            }}
            QLabel#navSectionLabel {{
                color: #7083a3;
                font-size: 8.5pt;
                font-weight: 600;
                padding: 4px 10px 5px 10px;
            }}
            QFrame#statusCard {{
                background: #142442;
                border: 1px solid #24385b;
                border-radius: 13px;
            }}
            QLabel#statusPill {{
                background: #123b38;
                color: #6ee7b7;
                border: 1px solid #1c5b50;
                border-radius: 9px;
                font-size: 9.5pt;
                font-weight: 600;
            }}
            QLabel#statusText {{
                color: #a9b9d2;
                font-size: 9pt;
                font-weight: 500;
            }}
            QPushButton#navItem {{
                background: transparent;
                border: 1px solid transparent;
                border-radius: 11px;
                padding: 0;
                min-height: 48px;
                text-align: left;
            }}
            QPushButton#navItem:hover {{
                background: #122441;
                border: 1px solid #1c3153;
            }}
            QPushButton#navItem:pressed {{
                background: #1a2f52;
                border: 1px solid #29466f;
            }}
            QPushButton#navItem:focus {{
                border: 1px solid #3d5e91;
            }}
            QPushButton#navItemActive {{
                background: #192d4d;
                border: 1px solid #29466f;
                border-radius: 11px;
                padding: 0;
                min-height: 48px;
                text-align: left;
            }}
            QPushButton#navItemActive:hover {{
                background: #203758;
            }}
            QLabel#navText {{
                color: #b7c4d8;
                font-size: 10pt;
                font-weight: 600;
            }}
            QLabel#navTextActive {{
                color: #ffffff;
                font-size: 10pt;
                font-weight: 600;
            }}
            QLabel#brandMark {{
                background: transparent;
                border: none;
                padding: 0;
            }}
            QLabel#brandBadge {{
                color: #8294af;
                font-size: 8.5pt;
                font-weight: 500;
            }}
            QLabel#brandTitle {{
                color: #ffffff;
                font-size: 17pt;
                font-weight: 700;
            }}
            QLabel#sidebarNote, QLabel#metricLabel {{
                color: #748197;
                font-size: 9pt;
            }}
            QLabel#sidebarCardTitle {{
                color: #ffffff;
                font-size: 10.5pt;
                font-weight: 600;
            }}
            QWidget#metricTile {{
                background: #f8fafc;
                border: 1px solid #e8edf4;
                border-radius: 10px;
                padding: 10px 12px;
            }}
            QLabel#metricValue {{
                color: #142033;
                font-size: 12pt;
                font-weight: 700;
            }}
            QFrame#overviewHero {{
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0,
                    stop:0 #101f3b, stop:0.58 #183460, stop:1 #24578b);
                border: 1px solid #203e68;
                border-radius: 14px;
                min-height: 86px;
            }}
            QLabel#overviewEyebrow {{
                color: #8fb1e7;
                font-size: 8.5pt;
                font-weight: 600;
            }}
            QLabel#overviewTitle {{
                color: #ffffff;
                font-size: 17pt;
                font-weight: 700;
            }}
            QLabel#overviewSubtitle {{
                color: #b9c9e0;
                font-size: 9pt;
                font-weight: 500;
            }}
            QFrame#overviewStatus {{
                background: rgba(8, 22, 43, 145);
                border: 1px solid rgba(157, 191, 234, 60);
                border-radius: 11px;
                min-width: 150px;
            }}
            QLabel#overviewStatusLabel {{
                color: #8ea6c7;
                font-size: 8pt;
                font-weight: 500;
            }}
            QLabel#overviewStatusValue {{
                color: #7ce5bd;
                font-size: 10pt;
                font-weight: 600;
            }}
            QFrame#surfaceCard, QFrame#summaryBand, QFrame#logCard,
            QFrame#targetCard, QFrame#railCard, QFrame#settingsStrip {{
                background: #ffffff;
                border: 1px solid #dfe5ee;
                border-radius: 14px;
            }}
            QWidget#heroCard {{
                background: transparent;
            }}
            QFrame#statusBox {{
                background: #f7f9fc;
                border: 1px solid #e6ebf3;
                border-radius: 11px;
                min-width: 250px;
            }}
            QFrame#metricCard {{
                background: #ffffff;
                border: 1px solid #dfe5ee;
                border-radius: 12px;
                min-height: 68px;
            }}
            QLabel#metricCardLabel {{
                color: #748197;
                font-size: 8.5pt;
                font-weight: 600;
            }}
            QLabel#metricCardValue {{
                color: #142033;
                font-size: 12.5pt;
                font-weight: 700;
            }}
            QFrame#cookieBlock {{
                background: #f8fafc;
                border: 1px solid #e6ebf3;
                border-radius: 12px;
            }}
            QLabel#sectionEyebrow {{
                color: #748197;
                font-size: 8.5pt;
                font-weight: 600;
            }}
            QLabel#headerTitle {{
                color: #142033;
                font-size: 21pt;
                font-weight: 700;
            }}
            QLabel#headerSubtitle, QLabel#cardDesc, QLabel#mutedText, QLabel#cardHint {{
                color: #748197;
                font-size: 9pt;
            }}
            QLabel#cardTitle {{
                color: #142033;
                font-size: 12.5pt;
                font-weight: 700;
            }}
            QLabel#fieldLabel {{
                color: #748197;
                font-size: 8.5pt;
                font-weight: 600;
            }}
            QLabel#summaryHero {{
                color: #142033;
                font-size: 12pt;
                font-weight: 700;
            }}
            QDialog#configDialog {{
                background: #f3f6fa;
            }}
            QFrame#configDialogPanel {{
                background: #ffffff;
                border: 1px solid #dfe5ee;
                border-radius: 13px;
            }}
            QLabel#dialogTitle {{
                color: #142033;
                font-size: 15pt;
                font-weight: 700;
            }}
            QLabel#dialogDesc {{
                color: #748197;
                font-size: 9pt;
                font-weight: 500;
            }}
            QTableWidget#upTable {{
                background: #ffffff;
                border: none;
                border-top: 1px solid #e8edf4;
                color: #24324a;
                alternate-background-color: #ffffff;
                selection-background-color: #edf3ff;
                selection-color: #142033;
                gridline-color: transparent;
                outline: none;
                font-size: 9.5pt;
            }}
            QTableWidget#upTable::item {{
                border-bottom: 1px solid #edf0f5;
                padding: 0 12px;
            }}
            QTableWidget#upTable::item:hover {{
                background: #f7f9fd;
            }}
            QTableWidget#upTable::item:selected {{
                background: #edf3ff;
                color: #142033;
                border-bottom: 1px solid #dbe6fb;
            }}
            QTableWidget#upTable::item:focus {{
                outline: none;
                border: none;
                border-bottom: 1px solid #dbe6fb;
            }}
            QWidget#tableActionCell {{
                background: transparent;
            }}
            QPushButton#tableActionButton {{
                background: transparent;
                color: #3567e8;
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 0 9px;
                min-height: 30px;
                max-height: 30px;
                min-width: 52px;
                font-size: 9pt;
                font-weight: 600;
            }}
            QPushButton#tableActionButton:hover {{
                background: #edf3ff;
                color: #2855cc;
                border: 1px solid #cedcfc;
            }}
            QPushButton#tableActionButton:pressed {{
                background: #dfe8ff;
                color: #2855cc;
            }}
            QHeaderView::section {{
                background: #f8fafc;
                color: #6f7d92;
                border: none;
                border-bottom: 1px solid #e6ebf3;
                padding: 10px 12px;
                font-size: 8.5pt;
                font-weight: 600;
            }}
            QListWidget#logList {{
                background: #ffffff;
                border: none;
                border-top: 1px solid #e8edf4;
                padding: 12px 16px 16px 16px;
                outline: none;
                color: #526078;
                font-size: 9.5pt;
            }}
            QListWidget#logList::item {{
                background: #f8fafc;
                border: 1px solid #e7ecf3;
                border-radius: 10px;
                margin: 4px 0;
                padding: 11px 12px;
                min-height: 34px;
            }}
            QListWidget#logList::item:hover {{
                background: #f3f6fb;
                border: 1px solid #dce4f0;
            }}
            QListWidget#logList::item:selected {{
                background: #edf3ff;
                color: #2855cc;
                border: 1px solid #d3dffc;
            }}
            QLabel#statChip {{
                background: #edf3ff;
                color: #3567e8;
                border: 1px solid #d3dffc;
                border-radius: 9px;
                padding: 7px 12px;
                font-size: 9pt;
                font-weight: 600;
            }}
            QLineEdit, QTextEdit {{
                background: #ffffff;
                border: 1px solid #d8e0eb;
                border-radius: 10px;
                padding: 9px 11px;
                color: #142033;
                selection-background-color: #dce7ff;
                font-size: 9.5pt;
            }}
            QLineEdit {{
                min-height: 30px;
            }}
            QTextEdit {{
                min-height: 88px;
            }}
            QLineEdit:focus, QTextEdit:focus {{
                border: 1px solid #5d82ef;
                background: #ffffff;
            }}
            QLineEdit#settingsDisplay {{
                background: #f9fbfd;
                border: 1px solid #dfe5ee;
                border-radius: 9px;
                padding: 0 14px;
                min-height: 36px;
                font-size: 9.5pt;
            }}
            QWidget#stepperField {{
                background: transparent;
            }}
            QSpinBox#stepperInput, QDoubleSpinBox#stepperInput {{
                background: #ffffff;
                border: 1px solid #d8e0eb;
                border-radius: 10px;
                padding: 9px 11px;
                color: #142033;
                font-size: 9.5pt;
                min-height: 30px;
            }}
            QSpinBox#stepperInput:focus, QDoubleSpinBox#stepperInput:focus {{
                border: 1px solid #5d82ef;
                background: #ffffff;
            }}
            QWidget#stepperButtonsWrap {{
                background: transparent;
            }}
            QPushButton#stepperButton {{
                background: #f6f8fc;
                color: #748197;
                border: 1px solid #dfe5ee;
                border-radius: 9px;
                min-width: 38px;
                max-width: 38px;
                min-height: 20px;
                max-height: 20px;
                font-size: 8.5pt;
                font-weight: 600;
                padding: 0;
            }}
            QPushButton#stepperButton:hover {{
                background: #edf3ff;
                border: 1px solid #cedcfc;
            }}
            QPushButton#stepperButton:pressed {{
                background: #e0eaff;
            }}
            QCheckBox#softCheck {{
                color: #35435a;
                font-size: 9.5pt;
                spacing: 10px;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border-radius: 9px;
                border: 1px solid #cfd8e5;
                background: #ffffff;
            }}
            QCheckBox::indicator:hover {{
                border: 1px solid #94a3b8;
                background: #f8fafc;
            }}
            QCheckBox::indicator:checked {{
                border: 1px solid #0b8b64;
                background: #0b8b64;
                image: url("{self.check_icon_path.as_posix()}");
            }}
            QCheckBox::indicator:checked:hover {{
                border: 1px solid #03664b;
                background: #03664b;
            }}
            QPushButton {{
                border-radius: 10px;
                padding: 0 14px;
                min-height: 38px;
                font-family: "Microsoft YaHei UI", "Microsoft YaHei", "Segoe UI";
                font-size: 9.5pt;
                font-weight: 600;
                border: 1px solid transparent;
            }}
            QPushButton[compact="true"] {{
                padding: 0 12px;
                min-height: 34px;
                font-size: 9pt;
                font-weight: 600;
            }}
            QPushButton[styleType="primary"] {{
                background: #3b6df6;
                color: #ffffff;
                border: 1px solid #3b6df6;
            }}
            QPushButton[styleType="primary"]:hover {{
                background: #315fda;
                border: 1px solid #315fda;
            }}
            QPushButton[styleType="primary"]:pressed {{
                background: #294fb5;
                border: 1px solid #294fb5;
            }}
            QPushButton[styleType="warm"] {{
                background: #fff7ed;
                color: #b35d13;
                border: 1px solid #f2d4b0;
            }}
            QPushButton[styleType="warm"]:hover {{
                background: #ffedd7;
            }}
            QPushButton[styleType="accent"] {{
                background: #edf3ff;
                color: #3567e8;
                border: 1px solid #d3dffc;
            }}
            QPushButton[styleType="accent"]:hover {{
                background: #dfe8ff;
                border: 1px solid #c1d2fb;
            }}
            QPushButton[styleType="ghost"] {{
                background: #f7f9fc;
                color: #35435a;
                border: 1px solid #dfe5ee;
            }}
            QPushButton[styleType="ghost"]:hover {{
                background: #eef3fb;
                border: 1px solid #cfd9e8;
            }}
            QPushButton[styleType="soft"] {{
                background: #ffffff;
                color: #35435a;
                border: 1px solid #dfe5ee;
            }}
            QPushButton[styleType="soft"]:hover {{
                background: #f7f9fc;
                border: 1px solid #ccd6e5;
            }}
            QPushButton[styleType="danger"] {{
                background: #fff1f2;
                color: #be123c;
                border: 1px solid #fecdd3;
            }}
            QPushButton[styleType="danger"]:hover {{
                background: #ffe4e6;
                border: 1px solid #fda4af;
            }}
            QPushButton:disabled {{
                background: #edf0f5;
                color: #a2adbd;
                border-color: #e6eaf0;
            }}
            QPushButton:focus {{
                border: 1px solid #7e9cf1;
            }}
            QScrollBar:vertical {{
                background: transparent;
                width: 8px;
                margin: 8px 1px 8px 1px;
            }}
            QScrollBar::handle:vertical {{
                background: #c2ccda;
                min-height: 28px;
                border-radius: 4px;
            }}
            QScrollBar::handle:vertical:hover {{
                background: #9eabbd;
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0;
            }}
            QMenu {{
                background: #ffffff;
                border: 1px solid #dfe5ee;
                border-radius: 11px;
                padding: 8px;
            }}
            QMenu::item {{
                padding: 8px 12px;
                border-radius: 8px;
                color: #142033;
            }}
            QMenu::item:selected {{
                background: #edf3ff;
                color: #3567e8;
            }}
            QToolTip {{
                background: #14213a;
                color: #ffffff;
                border: 1px solid #2d4266;
                padding: 6px 9px;
                font-size: 9pt;
            }}
            """
        )
    def _create_tray(self) -> None:
        if not QSystemTrayIcon.isSystemTrayAvailable():
            self.log("当前系统托盘不可用，托盘功能已跳过。")
            return
        self.tray_icon_resource = QIcon(str(self.icon_path))
        self.tray_icon = QSystemTrayIcon(self.tray_icon_resource, self)
        self.tray_icon.setToolTip(TRAY_TOOLTIP)
        self.tray_icon.activated.connect(self._on_tray_activated)

        self.tray_menu = QMenu(self)
        show_action = QAction("显示软件", self)
        show_action.triggered.connect(self.show_window)
        exit_action = QAction("退出", self)
        exit_action.triggered.connect(self.exit_application)
        self.tray_menu.addAction(show_action)
        self.tray_menu.addAction(exit_action)
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.show()
        self.log("托盘功能已启用，关闭窗口可隐藏到系统托盘。")

    def _load_config_to_ui(self) -> None:
        self.token_edit.setText(self.config.bot_token)
        self.chat_id_edit.setText(self.config.chat_id)
        self.up_text.setPlainText("\n".join(self._serialize_up_target_line(item) for item in self.config.up_targets))
        self.youtube_text.setPlainText(
            "\n".join(
                f"{item.channel_ref}|{item.label}" if item.label else item.channel_ref
                for item in self.config.youtube_targets
            )
        )
        self.cookie_text.setPlainText(self.config.bili_cookie)
        self.browser_cookie_checkbox.setChecked(self.config.use_browser_cookie)
        self.auto_hours_spin.setValue(self.config.auto_check_hours)
        self.fetch_count_spin.setValue(self.config.fetch_count)
        self.first_sync_spin.setValue(self.config.first_sync_count)
        self.request_gap_min_spin.setValue(self.config.request_interval_seconds_min)
        self.request_gap_max_spin.setValue(self.config.request_interval_seconds_max)
        self.message_interval_spin.setValue(getattr(self.config, "message_interval_seconds", 1.5))
        self.export_dir_edit.setText(self.config.export_dir)
        self._set_status_text("等待操作")
        self._refresh_retry_button()

    def _refresh_summary(self) -> None:
        bili_count = self._count_up_targets()
        youtube_count = self._count_youtube_targets()
        count = bili_count + youtube_count
        auto_text = "已关闭" if self.auto_hours_spin.value() == 0 else f"每 {int(self.auto_hours_spin.value())} 小时"
        fetch_text = f"{int(self.fetch_count_spin.value())} 条"
        request_min = self.request_gap_min_spin.value()
        request_max = max(request_min, self.request_gap_max_spin.value())
        request_text = f"{request_min:.0f} - {request_max:.0f} 秒"
        message_text = "合并发送"
        cookie_text = "浏览器优先" if self.browser_cookie_checkbox.isChecked() else "手动文本优先"

        if hasattr(self, "summary_chip_up"):
            self.summary_chip_up.setText(f"{count} 个目标")
        if hasattr(self, "summary_chip_sync"):
            self.summary_chip_sync.setText(fetch_text)
        if hasattr(self, "summary_chip_auto"):
            self.summary_chip_auto.setText(auto_text)
        if hasattr(self, "summary_chip_request"):
            self.summary_chip_request.setText(request_text)
        if hasattr(self, "summary_chip_cookie"):
            self.summary_chip_cookie.setText(message_text)
        if hasattr(self, "snapshot_targets"):
            self.snapshot_targets.setText(f"监控 {count} 个目标")
        if hasattr(self, "snapshot_auto"):
            self.snapshot_auto.setText(f"自动检查：{auto_text}")
        if hasattr(self, "snapshot_request"):
            self.snapshot_request.setText(f"请求节奏：{request_text}")
        if hasattr(self, "auto_hours_display"):
            self._set_display_text(self.auto_hours_display, "已关闭" if self.auto_hours_spin.value() == 0 else f"{int(self.auto_hours_spin.value())} 小时")
        if hasattr(self, "fetch_count_display"):
            self._set_display_text(self.fetch_count_display, f"{int(self.fetch_count_spin.value())} 条")
        if hasattr(self, "first_sync_display"):
            self._set_display_text(self.first_sync_display, f"{int(self.first_sync_spin.value())} 条")
        if hasattr(self, "request_range_display"):
            self._set_display_text(self.request_range_display, request_text)
        if hasattr(self, "message_interval_display"):
            self._set_display_text(self.message_interval_display, message_text)
        if hasattr(self, "cookie_mode_button"):
            self.cookie_mode_button.setText(cookie_text)

    def _pending_retry_count(self) -> int:
        try:
            return len(PendingTelegramRetryStore().load_items())
        except Exception:
            return 0

    def _refresh_retry_button(self) -> None:
        if not hasattr(self, "retry_send_button"):
            return
        pending_count = self._pending_retry_count()
        self.retry_send_button.setText(f"重试发送({pending_count})" if pending_count else "重试发送")
        self.retry_send_button.setEnabled((not self._busy) and pending_count > 0)
        if pending_count:
            self.retry_send_button.setToolTip(f"重新发送上次 Telegram 失败保留的 {pending_count} 条链接")
        else:
            self.retry_send_button.setToolTip("没有待重试发送的链接")

    def _count_up_targets(self) -> int:
        count = 0
        seen: set[str] = set()
        for raw_line in self.up_text.toPlainText().splitlines():
            line = raw_line.strip()
            if not line:
                continue
            uid, _label, enabled = self._parse_up_target_line(line)
            if uid.isdigit() and uid not in seen:
                seen.add(uid)
                if enabled:
                    count += 1
        return count

    def _count_youtube_targets(self) -> int:
        count = 0
        seen: set[str] = set()
        for raw_line in self.youtube_text.toPlainText().splitlines():
            line = raw_line.strip()
            if not line:
                continue
            channel_ref = line.split("|", 1)[0].strip()
            key = channel_ref.casefold()
            if channel_ref and key not in seen:
                seen.add(key)
                count += 1
        return count

    def _setup_logger(self) -> logging.Logger:
        logger = logging.getLogger(APP_NAME)
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        handler = logging.FileHandler(LOG_PATH, encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        logger.addHandler(handler)
        return logger

    def _is_success_log_message(self, message: str) -> bool:
        success_keywords = (
            "收集到待发送",
            "收集到待补发",
            "已成功发送到 Telegram",
            "Telegram 测试消息发送成功",
            "Telegram 测试成功",
        )
        return any(keyword in message for keyword in success_keywords)

    def log(self, message: str) -> None:
        self.logger.info(message)
        if self._is_success_log_message(message):
            self.log_text.append_message(message, color="#047857", background="#ecfdf3")
        else:
            self.log_text.append_message(message)

    def log_error(self, message: str) -> None:
        self.logger.error(message)
        self.log_text.append_message(message, color="#d93025")

    def _set_status_text(self, message: str) -> None:
        if hasattr(self, "summary_hero"):
            self.summary_hero.setText(message)
        if hasattr(self, "topbar_status"):
            if message == "等待操作":
                short_message = "本地服务已连接"
            elif len(message) <= 16:
                short_message = message
            elif "失败" in message or "有误" in message:
                short_message = "任务需要处理"
            elif "正在" in message:
                short_message = "任务执行中"
            elif "完成" in message or "成功" in message:
                short_message = "任务已完成"
            else:
                short_message = "状态已更新"
            self.topbar_status.setText(f"● {short_message}")
            if hasattr(self, "overview_status_value"):
                self.overview_status_value.setText(f"● {short_message}")
            if hasattr(self, "status_pill"):
                if "执行中" in short_message:
                    pill_text = "● 运行中"
                elif "处理" in short_message or "失败" in message:
                    pill_text = "● 待处理"
                else:
                    pill_text = "● 待命"
                self.status_pill.setText(pill_text)

    def clear_log_view(self) -> None:
        self.log_text.clear()

    def _schedule_config_autosave(self) -> None:
        if not self._autosave_ready:
            return
        self.config_autosave_timer.start(800)

    def _save_config_silent(self) -> bool:
        if not self._autosave_ready:
            return False
        try:
            self.config_autosave_timer.stop()
            config = self._collect_config(require_targets=False)
            config.save()
            self.config = config
            self._refresh_summary()
            self._schedule_auto_check(log_message=False)
            return True
        except Exception as exc:
            self.log(f"自动保存配置失败：{exc}")
            return False

    def save_config(self) -> bool:
        try:
            self.config_autosave_timer.stop()
            config = self._collect_config(require_targets=False)
            config.save()
            self.config = config
            self._refresh_summary()
            self._schedule_auto_check(log_message=True)
            self._set_status_text("配置已保存")
            self.log("配置已保存。")
            return True
        except Exception as exc:
            QMessageBox.critical(self, "保存失败", str(exc))
            return False

    def test_telegram(self) -> None:
        self._start_task("test", "正在测试 Telegram...")

    def check_updates(self) -> None:
        self._refresh_cookie_before_check()
        self._start_task("check", "正在检查最新投稿...")

    def send_recent_videos(self) -> None:
        self._start_task("recent", "正在发送最近投稿...")

    def retry_pending_send(self) -> None:
        if self._pending_retry_count() <= 0:
            self._refresh_retry_button()
            self.log("当前没有需要重试发送的链接。")
            return
        self._start_task("retry", "正在重试发送上次失败链接...")

    def select_export_dir(self) -> None:
        current_dir = self.export_dir_edit.text().strip() or str(APP_DIR)
        selected_dir = QFileDialog.getExistingDirectory(self, "选择导出目录", current_dir)
        if not selected_dir:
            return
        self.export_dir_edit.setText(selected_dir)
        self._set_status_text("已选择导出目录")
        self.log(f"导出目录已更新：{selected_dir}")

    def export_config(self) -> None:
        try:
            config = self._collect_config(require_targets=False)
            export_dir = config.export_dir.strip()
            if not export_dir:
                raise ValueError("请先选择导出目录。")

            Path(export_dir).mkdir(parents=True, exist_ok=True)
            store = SentVideoStore()
            try:
                sent_videos = [asdict(item) for item in store.list_sent_videos()]
            finally:
                store.close()

            payload = {
                "app_name": APP_NAME,
                "export_time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "telegram": {
                    "bot_token": config.bot_token,
                    "chat_id": config.chat_id,
                },
                "bilibili": {
                    "bili_cookie": config.bili_cookie,
                    "use_browser_cookie": config.use_browser_cookie,
                    "up_targets": [asdict(item) for item in config.up_targets],
                },
                "youtube": {
                    "youtube_targets": [asdict(item) for item in config.youtube_targets],
                },
                "push_settings": {
                    "auto_check_hours": config.auto_check_hours,
                    "fetch_count": config.fetch_count,
                    "first_sync_count": config.first_sync_count,
                    "request_interval_seconds_min": config.request_interval_seconds_min,
                    "request_interval_seconds_max": config.request_interval_seconds_max,
                    "message_interval_seconds": config.message_interval_seconds,
                },
                "export_settings": {
                    "export_dir": config.export_dir,
                },
                "sent_history": {
                    "total_sent_records": len(sent_videos),
                    "items": sent_videos,
                },
            }

            file_path = Path(export_dir) / build_export_file_name()
            file_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            self._set_status_text("配置已导出")
            self.log(f"配置已导出到指定位置：{file_path}")
            if self.tray_icon is not None and self.tray_icon.isVisible():
                self.tray_icon.showMessage(
                    APP_NAME,
                    "配置已导出到指定位置",
                    QSystemTrayIcon.MessageIcon.Information,
                    2200,
                )
        except Exception as exc:
            QMessageBox.critical(self, "导出失败", str(exc))

    def _read_browser_cookie_header(self, *, show_errors: bool) -> str | None:
        try:
            from bili_client import BrowserCookieReadError, export_browser_cookie_header

            cookie_header = export_browser_cookie_header()
        except BrowserCookieReadError as exc:
            message = str(exc)
            if show_errors and (not self._is_running_as_admin()) and "Cookie 数据库当前被占用" in message:
                should_restart = QMessageBox.question(
                    self,
                    "需要管理员权限",
                    f"{message}\n\n是否现在以管理员身份重启程序？",
                    QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                    QMessageBox.StandardButton.Yes,
                )
                if should_restart == QMessageBox.StandardButton.Yes:
                    self.relaunch_as_admin()
                return None
            if show_errors:
                QMessageBox.critical(self, "读取失败", message)
            else:
                self.log(f"检查前自动读取浏览器 Cookie 失败：{message}，将继续使用当前 Cookie。")
            return None
        except Exception as exc:
            if show_errors:
                QMessageBox.critical(self, "读取失败", f"读取浏览器 Cookie 失败：{exc}")
            else:
                self.log(f"检查前自动读取浏览器 Cookie 失败：{exc}，将继续使用当前 Cookie。")
            return None

        if not cookie_header:
            if show_errors:
                QMessageBox.warning(self, "未读取到 Cookie", "没有从浏览器中读到 B 站 Cookie，请确认浏览器已经登录 B 站。")
            else:
                self.log("检查前没有从浏览器读取到 B 站 Cookie，将继续使用当前 Cookie。")
            return None

        return cookie_header

    def _apply_browser_cookie_header(self, cookie_header: str, *, status_text: str, log_message: str) -> None:
        self.cookie_text.setPlainText(cookie_header)
        self._set_status_text(status_text)
        self.log(log_message)

    def _refresh_cookie_before_check(self) -> bool:
        cookie_header = self._read_browser_cookie_header(show_errors=False)
        if not cookie_header:
            return False
        self._apply_browser_cookie_header(
            cookie_header,
            status_text="已刷新浏览器 Cookie",
            log_message="检查前已自动读取浏览器 Cookie。",
        )
        return True

    def fill_browser_cookie(self) -> bool:
        cookie_header = self._read_browser_cookie_header(show_errors=True)
        if not cookie_header:
            return False
        self._apply_browser_cookie_header(
            cookie_header,
            status_text="已读取浏览器 Cookie",
            log_message="已自动读取浏览器 Cookie 到文本框。",
        )
        return True

    def relaunch_as_admin(self) -> None:
        if self._is_running_as_admin():
            QMessageBox.information(self, "已经是管理员", "当前程序已经在管理员模式下运行。")
            return

        try:
            self._try_save_before_restart()
            if getattr(sys, "frozen", False):
                executable = sys.executable
                parameters = ""
                workdir = str(Path(sys.executable).resolve().parent)
            else:
                script_path = Path(__file__).resolve()
                executable = sys.executable
                parameters = f'"{script_path}"'
                workdir = str(script_path.parent)

            result = ctypes.windll.shell32.ShellExecuteW(None, "runas", executable, parameters or None, workdir, 1)
            if int(result) <= 32:
                raise RuntimeError(f"ShellExecuteW 返回错误码：{result}")
        except Exception as exc:
            QMessageBox.critical(self, "提权失败", f"无法以管理员身份重启程序：{exc}")
            return

        self.log("正在以管理员身份重新启动程序。")
        QTimer.singleShot(200, self.exit_application)

    def _try_save_before_restart(self) -> None:
        self._save_config_silent()

    def _start_task(self, action: str, status: str) -> None:
        if self._busy:
            self._set_status_text("当前已有任务在运行")
            self.log_error("任务未开始：当前已经有任务在运行，已跳过本次操作。")
            return

        try:
            config = self._collect_config(require_targets=(action not in ("test", "retry")))
            config.save()
            self.config = config
        except Exception as exc:
            self._set_status_text("配置有误")
            self.log_error(f"任务未开始：配置有误，已跳过本次操作。{exc}")
            return

        self._busy = True
        self._set_controls_enabled(False)
        self._set_status_text(status)
        self._worker = WorkerThread(config, action)
        self._worker.log_message.connect(self.log)
        self._worker.error_message.connect(self.log_error)
        self._worker.task_done.connect(self._handle_task_done)
        self._worker.task_error.connect(self._handle_task_error)
        self._worker.finished.connect(self._handle_worker_finished)
        self._worker.start()

    def _handle_task_done(self, message: str) -> None:
        self._set_status_text(message)
        self.log(message)
        self._refresh_summary()
        self._refresh_retry_button()
        self._schedule_auto_check(log_message=False)

    def _handle_task_error(self, message: str) -> None:
        is_bili_risk = message.startswith("__BILI_RISK__")
        clean_message = message.removeprefix("__BILI_RISK__")
        self._set_status_text("B站风控" if is_bili_risk else "任务失败")
        self.log_error(f"任务失败：{clean_message}")
        if is_bili_risk:
            QMessageBox.critical(self, "B站风控", clean_message)
        self._refresh_retry_button()
        self._schedule_auto_check(log_message=False)

    def _handle_worker_finished(self) -> None:
        self._busy = False
        self._set_controls_enabled(True)
        if self._worker is not None:
            self._worker.deleteLater()
            self._worker = None
        self._refresh_retry_button()

    def _collect_config(self, require_targets: bool) -> AppConfig:
        targets = self._parse_targets(self.up_text.toPlainText())
        youtube_targets = self._parse_youtube_targets(self.youtube_text.toPlainText())
        if require_targets and not targets and not youtube_targets:
            raise ValueError("请至少填写一个 B站 UP 或 YouTube 频道。")

        return AppConfig(
            bot_token=self.token_edit.text().strip(),
            chat_id=self.chat_id_edit.text().strip(),
            up_targets=targets,
            youtube_targets=youtube_targets,
            bili_cookie=self.cookie_text.toPlainText().strip(),
            use_browser_cookie=self.browser_cookie_checkbox.isChecked(),
            auto_check_hours=int(self.auto_hours_spin.value()),
            fetch_count=int(self.fetch_count_spin.value()),
            first_sync_count=int(self.first_sync_spin.value()),
            request_interval_seconds_min=round(self.request_gap_min_spin.value(), 1),
            request_interval_seconds_max=round(max(self.request_gap_min_spin.value(), self.request_gap_max_spin.value()), 1),
            message_interval_seconds=round(self.message_interval_spin.value(), 1),
            export_dir=self.export_dir_edit.text().strip(),
        )

    def _parse_targets(self, raw_text: str) -> list[UpTarget]:
        targets: list[UpTarget] = []
        seen: set[str] = set()
        for line_no, raw_line in enumerate(raw_text.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            uid, label, enabled = self._parse_up_target_line(line)
            if not uid.isdigit():
                raise ValueError(f"第 {line_no} 行 UID 不合法：{line}")
            if uid in seen:
                continue
            seen.add(uid)
            targets.append(UpTarget(uid=uid, label=label, enabled=enabled))
        return targets

    def _parse_youtube_targets(self, raw_text: str):
        from settings import YouTubeTarget

        targets: list[YouTubeTarget] = []
        seen: set[str] = set()
        for line_no, raw_line in enumerate(raw_text.splitlines(), start=1):
            line = raw_line.strip()
            if not line:
                continue
            channel_ref, label = (line.split("|", 1) + [""])[:2] if "|" in line else (line, "")
            channel_ref = channel_ref.strip()
            label = label.strip()
            key = channel_ref.casefold()
            if not channel_ref:
                raise ValueError(f"第 {line_no} 行 YouTube 频道标识为空。")
            if key in seen:
                continue
            seen.add(key)
            targets.append(YouTubeTarget(channel_ref=channel_ref, label=label))
        return targets

    def _schedule_auto_check(self, log_message: bool) -> None:
        self.auto_timer.stop()
        hours = self.config.auto_check_hours
        if hours <= 0:
            if log_message:
                self.log("自动检查已关闭。")
            return
        self.auto_timer.start(hours * 60 * 60 * 1000)
        if log_message:
            self.log(f"已启用自动检查：每 {hours} 小时执行一次。")

    def _trigger_auto_check(self) -> None:
        if self._busy:
            self.log("自动检查到点，但当前有任务在执行，本次跳过。")
            return
        self.log("触发自动检查。")
        self._start_task("check", "正在自动检查最新投稿...")

    def _set_controls_enabled(self, enabled: bool) -> None:
        buttons = [
            self.sidebar_check_button,
            self.sidebar_save_button,
            self.sidebar_cookie_button,
            self.sidebar_recent_button,
            self.sidebar_export_button,
            self.top_test_button,
            self.top_save_button,
            self.top_check_button,
            self.retry_send_button,
            self.fill_cookie_button,
            self.test_button,
            self.check_button,
            self.export_button,
            self.select_export_dir_button,
            self.clear_log_button,
            self.add_up_button,
            self.cookie_mode_button,
        ]
        steppers = [
            self.auto_hours_spin,
            self.fetch_count_spin,
            self.first_sync_spin,
            self.request_gap_min_spin,
            self.request_gap_max_spin,
            self.message_interval_spin,
        ]
        for button in buttons:
            button.setEnabled(enabled)
        for stepper in steppers:
            stepper.setEnabled(enabled)
        self._refresh_retry_button()

    @staticmethod
    def _is_running_as_admin() -> bool:
        try:
            return bool(ctypes.windll.shell32.IsUserAnAdmin())
        except Exception:
            return False

    def _acquire_single_instance(self) -> bool:
        mutex_name = "Global\\PushToTelegram.SingleInstance"
        handle = ctypes.windll.kernel32.CreateMutexW(None, False, mutex_name)
        if not handle:
            return True
        self._mutex_handle = handle
        already_exists = ctypes.windll.kernel32.GetLastError() == 183
        if already_exists:
            self._release_single_instance()
            return False
        return True

    def _release_single_instance(self) -> None:
        if self._mutex_handle:
            ctypes.windll.kernel32.CloseHandle(self._mutex_handle)
            self._mutex_handle = None

    def _activate_existing_instance(self) -> None:
        if sys.platform != "win32":
            return
        hwnd = ctypes.windll.user32.FindWindowW(None, APP_WINDOW_TITLE)
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)
            ctypes.windll.user32.SetForegroundWindow(hwnd)

    def _on_tray_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.Trigger:
            self.toggle_window_visibility()

    def toggle_window_visibility(self) -> None:
        if self.isVisible() and not self.isMinimized():
            self.hide_window()
        else:
            self.show_window()

    def show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()
        self._tray_notice_logged = False

    def hide_window(self) -> None:
        self._save_config_silent()
        self.hide()
        if self.tray_icon is not None and self.tray_icon.isVisible() and not self._tray_notice_logged:
            self.tray_icon.showMessage(
                APP_NAME,
                "软件已隐藏到系统托盘，单击托盘图标可重新打开。",
                QSystemTrayIcon.MessageIcon.Information,
                2500,
            )
        self._tray_notice_logged = True

    def closeEvent(self, event: QCloseEvent) -> None:
        self._save_config_silent()
        if self.tray_icon is not None and self.tray_icon.isVisible():
            event.ignore()
            self.hide_window()
            return
        super().closeEvent(event)

    def exit_application(self) -> None:
        self._save_config_silent()
        self._dispose_tray()
        self._release_single_instance()
        QApplication.instance().quit()

    def _dispose_tray(self) -> None:
        tray = self.tray_icon
        menu = self.tray_menu
        if tray is not None:
            tray.setVisible(False)
            try:
                tray.activated.disconnect(self._on_tray_activated)
            except (RuntimeError, TypeError):
                pass
            tray.setContextMenu(None)
            tray.setIcon(QIcon())
            tray.deleteLater()
        if menu is not None:
            menu.clear()
            menu.deleteLater()
        self.tray_icon = None
        self.tray_menu = None
        self.tray_icon_resource = None

    def _ensure_assets(self) -> tuple[Path, Path, Path]:
        icon_path = APP_ICON_PATH
        brand_logo_path = BRAND_LOGO_PATH
        check_icon_path = CHECK_MARK_PATH
        resource_dir = Path(getattr(sys, "_MEIPASS", APP_DIR))
        bundled_icon_path = resource_dir / "artemis_symbol.ico"
        bundled_brand_logo_path = resource_dir / "artemis_symbol_1024.png"
        bundled_check_icon_path = resource_dir / "check_mark_green.png"

        if getattr(sys, "frozen", False) and bundled_icon_path.exists():
            icon_path = bundled_icon_path
        if getattr(sys, "frozen", False) and bundled_brand_logo_path.exists():
            brand_logo_path = bundled_brand_logo_path
        if getattr(sys, "frozen", False) and bundled_check_icon_path.exists():
            check_icon_path = bundled_check_icon_path

        check_icon_path.parent.mkdir(parents=True, exist_ok=True)
        if not icon_path.exists():
            raise RuntimeError(f"缺少应用图标资源：{icon_path}")
        if not brand_logo_path.exists():
            raise RuntimeError(f"缺少品牌 Logo 资源：{brand_logo_path}")

        Image = None
        ImageDraw = None
        if not check_icon_path.exists():
            # Pillow is only a development fallback. Frozen builds always carry both assets.
            import importlib

            Image = importlib.import_module("PIL.Image")
            ImageDraw = importlib.import_module("PIL.ImageDraw")

        if not check_icon_path.exists():
            assert Image is not None and ImageDraw is not None
            image = Image.new("RGBA", (24, 24), (0, 0, 0, 0))
            draw = ImageDraw.Draw(image)
            draw.line((6, 12, 10, 16), fill="white", width=3)
            draw.line((10, 16, 18, 7), fill="white", width=3)
            image.save(check_icon_path, format="PNG")

        return icon_path, brand_logo_path, check_icon_path


def main() -> None:
    enable_high_dpi()
    app = QApplication(sys.argv)
    app.setApplicationName(APP_WINDOW_TITLE)
    app.setQuitOnLastWindowClosed(False)
    app_font = QFont("Microsoft YaHei")
    app_font.setPointSizeF(10.0)
    app_font.setWeight(QFont.Weight.Normal)
    app_font.setHintingPreference(QFont.HintingPreference.PreferFullHinting)
    app_font.setStyleStrategy(QFont.StyleStrategy.PreferAntialias)
    app.setFont(app_font)

    window = BiliPulseWindow()
    if getattr(window, "_already_running", False):
        return
    window.showMaximized()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
