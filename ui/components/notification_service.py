# -*- coding: utf-8 -*-
"""
ui/components/notification_service.py
盘中突破桌面通知 + 可选声音提醒 (#15)

为什么需要？
盘中监控发现突破信号时，用户可能在看别的窗口。
没有主动通知的话，关键信号会被错过。

使用方式:
    from ui.components.notification_service import notify_breakout
    notify_breakout("sh600519", "贵州茅台", "放量突破")
"""

import os
from core.logger import get_logger

log = get_logger(__name__)


def notify_breakout(code: str, name: str, status: str, *, sound: bool = True):
    """发送桌面通知 + 可选声音提醒

    参数:
        code: 股票代码
        name: 股票名称
        status: 突破状态文本
        sound: 是否播放提示音(默认 True)
    """
    title = "🚀 VCP 突破信号"
    message = f"{name}({code})\n{status}"

    # 1. 桌面通知 — 优先使用 QSystemTrayIcon
    try:
        _send_tray_notification(title, message)
    except Exception as e:
        # 退化为 Windows toast 通知
        try:
            _send_windows_toast(title, message)
        except Exception:
            log.warning(f"[通知] 桌面通知发送失败: {e}")

    # 2. 声音提醒 — 使用系统自带的默认提示音
    if sound:
        try:
            _play_alert_sound()
        except Exception:
            pass


def _send_tray_notification(title: str, message: str):
    """通过 QSystemTrayIcon 发送系统托盘通知"""
    from PyQt6.QtWidgets import QApplication, QSystemTrayIcon
    from PyQt6.QtGui import QIcon

    app = QApplication.instance()
    if not app:
        return

    # 查找已有的 tray icon，如果没有就创建一个临时的
    tray = None
    for widget in app.allWidgets():
        if isinstance(widget, QSystemTrayIcon):
            tray = widget
            break

    if tray is None:
        tray = QSystemTrayIcon(app)
        tray.setVisible(True)

    tray.showMessage(title, message, QSystemTrayIcon.MessageIcon.Information, 5000)


def _send_windows_toast(title: str, message: str):
    """Windows 10/11 原生 toast 通知(ctypes 调用)"""
    if os.name != 'nt':
        return
    try:
        # 使用 PowerShell 调用 BurntToast 或系统通知
        import subprocess
        ps_script = f'''
        [Windows.UI.Notifications.ToastNotificationManager,Windows.UI.Notifications,ContentType=WindowsRuntime] | Out-Null
        $template = [Windows.UI.Notifications.ToastNotificationManager]::GetTemplateContent([Windows.UI.Notifications.ToastTemplateType]::ToastText02)
        $texts = $template.GetElementsByTagName('text')
        $texts[0].AppendChild($template.CreateTextNode('{title}')) | Out-Null
        $texts[1].AppendChild($template.CreateTextNode('{message}')) | Out-Null
        $toast = [Windows.UI.Notifications.ToastNotification]::new($template)
        [Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('VCPHunter').Show($toast)
        '''
        subprocess.run(
            ['powershell', '-Command', ps_script],
            capture_output=True, timeout=5,
            creationflags=0x08000000  # CREATE_NO_WINDOW
        )
    except Exception:
        pass


def _play_alert_sound():
    """播放系统默认提醒音"""
    if os.name == 'nt':
        import winsound
        # 使用 Windows 系统 Asterisk 提示音（非阻塞）
        winsound.MessageBeep(winsound.MB_ICONASTERISK)
