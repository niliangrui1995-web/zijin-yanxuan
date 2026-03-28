import sys
import os
import io

# pythonw.exe 没有控制台, stdout/stderr 可能为 None → print() 会崩溃
# 安全兜底: 重定向到日志文件
if sys.stdout is None or sys.stderr is None:
    _log_dir = os.path.join(os.path.dirname(__file__), 'data', 'Cache')
    os.makedirs(_log_dir, exist_ok=True)
    _log = open(os.path.join(_log_dir, 'vcp_launch.log'), 'w', encoding='utf-8')
    if sys.stdout is None:
        sys.stdout = _log
    if sys.stderr is None:
        sys.stderr = _log
# 即使有控制台, 也强制 UTF-8 编码避免 GBK 编码错误
elif hasattr(sys.stdout, 'buffer'):
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')


def main():
    # 增加高分屏支持
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

    from PyQt6.QtWidgets import QApplication
    app = QApplication(sys.argv)

    # === 单实例锁：防止多开 ===
    import ctypes
    _mutex_name = 'VCPHunterQuantTerminal_SingleInstance'
    _mutex_handle = ctypes.windll.kernel32.CreateMutexW(None, False, _mutex_name)
    _last_error = ctypes.windll.kernel32.GetLastError()
    _ERROR_ALREADY_EXISTS = 183
    if _last_error == _ERROR_ALREADY_EXISTS:
        from PyQt6.QtWidgets import QMessageBox
        QMessageBox.warning(None, "紫金研选量化终端",
            "程序已在运行中，请勿重复启动。\n\n"
            "如需重新启动，请先关闭已有窗口。")
        ctypes.windll.kernel32.CloseHandle(_mutex_handle)
        sys.exit(0)

    # ========== 闪屏：立即展示，减少等待焦虑 ==========
    from ui.splash_screen import SplashScreen
    splash = SplashScreen()
    splash.show()
    app.processEvents()

    splash.set_progress(10, "加载主题样式...")

    import qdarkstyle
    app.setStyleSheet(qdarkstyle.load_stylesheet(qt_api='pyqt6'))

    # 全局字体设置 (更好的西文和数字渲染，Web风格)
    font = app.font()
    font.setFamily("Segoe UI")
    font.setPointSize(10)
    app.setFont(font)

    splash.set_progress(30, "初始化数据引擎...")

    # 加载主窗口（这一步最耗时）
    from ui.main_window_qt import MainWindowQT
    splash.set_progress(50, "构建界面组件...")

    window = MainWindowQT(splash=splash)

    splash.set_progress(95, "即将就绪...")
    window.show()

    # 主窗口显示后关闭闪屏
    splash.close()
    splash.deleteLater()

    _exit_code = app.exec()
    # Cleanup: release mutex on exit
    ctypes.windll.kernel32.CloseHandle(_mutex_handle)
    sys.exit(_exit_code)

if __name__ == '__main__':
    main()
