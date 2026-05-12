# -*- coding: utf-8 -*-
"""
ui/components/notification_service.py
盘中突破桌面通知 + 可选声音提醒
"""

import os

from app.services.ui_task_service import (
    ProcessSubprocessError,
    run_process,
    windows_no_window_creationflags,
)
from core.logger import get_logger

log = get_logger(__name__)

_TRAY_ATTR = "_vcp_notification_tray"


def notify_breakout(code: str, name: str, status: str, *, sound: bool = True):
    """发送桌面通知 + 可选声音提醒。"""
    title = "🔔 VCP 突破信号"
    message = f"{name}({code})\n{status}"

    try:
        _send_tray_notification(title, message)
    except (ImportError, RuntimeError, AttributeError, OSError) as e:
        try:
            _send_windows_toast(title, message)
        except (OSError, ProcessSubprocessError, RuntimeError) as toast_error:
            log.debug(f"[通知] tray/toast 均发送失败: {e}; {toast_error}")

    if sound:
        try:
            _play_alert_sound()
        except (ImportError, RuntimeError, OSError) as e:
            log.debug(f"[通知] 提示音播放失败: {e}")


def _get_or_create_tray_icon(app):
    from PyQt6.QtWidgets import QSystemTrayIcon

    tray = getattr(app, _TRAY_ATTR, None)
    if isinstance(tray, QSystemTrayIcon):
        return tray

    tray = QSystemTrayIcon(app)
    try:
        app_icon = app.windowIcon()
        if app_icon is not None and not app_icon.isNull():
            tray.setIcon(app_icon)
    except (AttributeError, RuntimeError):
        pass

    tray.setVisible(True)
    setattr(app, _TRAY_ATTR, tray)
    return tray


def _send_tray_notification(title: str, message: str):
    """通过单一 QSystemTrayIcon 实例发送系统托盘通知。"""
    from PyQt6.QtWidgets import QApplication, QSystemTrayIcon

    app = QApplication.instance()
    if not app:
        return

    tray = _get_or_create_tray_icon(app)
    tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 5000)


def _send_windows_toast(title: str, message: str):
    """Windows 10/11 原生 toast 通知。"""
    if os.name != "nt":
        return
    ps_script = """
    param([string]$Title, [string]$Message)
    [Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] | Out-Null
    $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
    $texts = $template.GetElementsByTagName('text')
    $texts[0].AppendChild($template.CreateTextNode($Title)) | Out-Null
    $texts[1].AppendChild($template.CreateTextNode($Message)) | Out-Null
    $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
    [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('VCPHunter').Show($toast)
    """
    run_process(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", ps_script, str(title), str(message)],
        capture_output=True,
        timeout=5,
        creationflags=windows_no_window_creationflags(),
        check=False,
    )


def _play_alert_sound():
    """播放系统默认提醒音。"""
    if os.name == "nt":
        import winsound
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
