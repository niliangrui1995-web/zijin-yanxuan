import faulthandler
import os
import sys
import traceback

from core.runtime_env import (
    configure_qt_webengine_runtime,
    log_runtime_env_report,
    relaunch_into_project_venv_if_needed,
    set_windows_app_user_model_id,
)
from core.single_instance import acquire_single_instance_lock

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

# Prefer the project-local interpreter before importing Qt or other native modules.
relaunch_into_project_venv_if_needed(PROJECT_ROOT, script_path=__file__)

SINGLE_INSTANCE_LOCK = acquire_single_instance_lock()
if SINGLE_INSTANCE_LOCK.already_running:
    sys.exit(0)

CRASH_LOG_DIR = os.path.join(PROJECT_ROOT, "data")
os.makedirs(CRASH_LOG_DIR, exist_ok=True)
CRASH_LOG_PATH = os.path.join(CRASH_LOG_DIR, "crash_report.log")
CRASH_LOG_FILE = open(CRASH_LOG_PATH, "a", encoding="utf-8")
faulthandler.enable(file=CRASH_LOG_FILE, all_threads=True)


def main():
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
    configure_qt_webengine_runtime()
    set_windows_app_user_model_id()

    from PyQt6.QtWidgets import QApplication, QMessageBox

    def ui_exception_hook(exc_type, exc_value, exc_traceback):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_traceback)
            return

        tb_text = "".join(traceback.format_exception(exc_type, exc_value, exc_traceback))
        if sys.stderr:
            sys.stderr.write(f"Uncaught exception:\n{tb_text}\n")
            sys.stderr.flush()

        friendly_msg = (
            "程序运行时发生了未处理异常。\n\n"
            f"错误类型: {exc_type.__name__}\n"
            f"错误信息: {exc_value}\n\n"
            "程序未必会立刻退出，但部分功能可能不可用。"
        )

        app = QApplication.instance()
        if app is None:
            return

        msg_box = QMessageBox()
        msg_box.setIcon(QMessageBox.Icon.Critical)
        msg_box.setWindowTitle("系统异常")
        msg_box.setText(friendly_msg)
        msg_box.setDetailedText(tb_text)
        msg_box.setStyleSheet(app.styleSheet())
        msg_box.exec()

    sys.excepthook = ui_exception_hook

    try:
        from PyQt6.QtWebEngineWidgets import QWebEngineView  # noqa: F401
    except ImportError:
        pass

    from ui.styles.global_qss import generate_global_qss
    from ui.theme import theme_manager

    app = QApplication(sys.argv)
    log_runtime_env_report(PROJECT_ROOT)
    app.setStyleSheet(generate_global_qss(theme_manager.current_theme))

    # 优化维度三：全局配置 Microsoft YaHei UI 提高中英排版抗锯齿效果
    font = app.font()
    font.setFamily("Microsoft YaHei UI")
    font.setPointSize(10)
    app.setFont(font)

    from ui.splash_screen import SplashScreen

    splash = SplashScreen()
    splash.set_progress(10, "加载主题与字体...")
    splash.show()
    app.processEvents()

    splash.set_progress(30, "初始化本地数据引擎...")

    from ui.main_window_qt import MainWindowQT

    splash.set_progress(50, "恢复主工作台界面...")

    window = MainWindowQT(splash=splash)

    splash.set_progress(95, "准备进入主工作台...")
    window.show()

    splash.close()
    splash.deleteLater()

    try:
        exit_code = app.exec()
    finally:
        SINGLE_INSTANCE_LOCK.release()
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
