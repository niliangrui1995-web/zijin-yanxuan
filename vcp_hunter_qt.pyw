import sys
import os
import io
import faulthandler

# pythonw.exe 没有控制台, stdout/stderr 可能为 None → print() 会崩溃
# 安全兜底: 重定向到日志文件
# Redirection removed for debugging!

# 启用 faulthandler：C 级段错误（Polars/NumPy/Qt 底层崩溃）时写入崩溃现场到文件
# 为什么不输出到 stderr？因为 pythonw.exe 没有控制台，stderr 可能是 None
_crash_log_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'data')
os.makedirs(_crash_log_dir, exist_ok=True)
_crash_log_path = os.path.join(_crash_log_dir, 'crash_report.log')
_crash_log_file = open(_crash_log_path, 'a', encoding='utf-8')
faulthandler.enable(file=_crash_log_file, all_threads=True)


def main():
    # 增加高分屏支持
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"

    from PyQt6.QtWidgets import QApplication, QMessageBox
    import traceback

    def ui_exception_hook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        tb_list = traceback.format_exception(exc_type, exc_value, exc_traceback)
        tb_text = "".join(tb_list)
        
        if sys.stderr:
            sys.stderr.write(f"Uncaught exception:\\n{tb_text}\\n")
            sys.stderr.flush()

        friendly_msg = f"⚙️ 程序运行时发生未处理的系统异常！\\n\\n【错误类型】: {exc_type.__name__}\\n【错误提示】: {str(exc_value)}\\n\\n系统不会因此立刻崩溃，但某些功能可能无法按预期运行。您可以尝试继续使用，或者重启客户端。"
        
        app = QApplication.instance()
        if app:
            msg_box = QMessageBox()
            msg_box.setIcon(QMessageBox.Icon.Critical)
            msg_box.setWindowTitle("⚠️ 系统故障 (Human Readable Error)")
            msg_box.setText(friendly_msg)
            msg_box.setDetailedText(tb_text)
            # 兼容新主题皮肤
            msg_box.setStyleSheet(app.styleSheet())
            msg_box.exec()
        else:
            print("Crash early before QApplication creation.")

    sys.excepthook = ui_exception_hook

    # WebEngine 的 Chromium 内核要求在 QApplication 创建之前完成 OpenGL 共享初始化
    # 如果不提前 import，后面懒加载 K 线窗口时会直接崩溃
    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    except ImportError:
        pass  # 如果没装 WebEngine 包，后续再报错

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
