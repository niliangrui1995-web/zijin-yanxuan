import faulthandler
import os
import sys
import traceback

if sys.stderr is None:
    os.environ["TQDM_DISABLE"] = "1"

from core.runtime_env import (
    append_bootstrap_event,
    configure_qt_webengine_runtime,
    log_runtime_env_report,
    relaunch_into_project_venv_if_needed,
    set_windows_app_user_model_id,
)
from core.single_instance import (
    acquire_single_instance_lock,
    is_entry_script_process_running,
    is_single_instance_running,
)

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))

append_bootstrap_event(
    PROJECT_ROOT,
    "process.start",
    extra={
        "executable": sys.executable,
        "script": __file__,
        "argv0": sys.argv[0] if sys.argv else "",
    },
)

if is_single_instance_running():
    append_bootstrap_event(PROJECT_ROOT, "process.exit", extra={"reason": "single_instance_running"})
    sys.exit(0)

if is_entry_script_process_running(__file__):
    append_bootstrap_event(PROJECT_ROOT, "process.exit", extra={"reason": "entry_script_running"})
    sys.exit(0)

# Prefer the project-local interpreter before importing Qt or other native modules.
relaunch_into_project_venv_if_needed(PROJECT_ROOT, script_path=__file__)
append_bootstrap_event(PROJECT_ROOT, "runtime_env.ready", extra={"executable": sys.executable})

SINGLE_INSTANCE_LOCK = acquire_single_instance_lock()
if SINGLE_INSTANCE_LOCK.already_running:
    append_bootstrap_event(PROJECT_ROOT, "process.exit", extra={"reason": "single_instance_lock_exists"})
    sys.exit(0)
append_bootstrap_event(PROJECT_ROOT, "single_instance.lock.acquired")

CRASH_LOG_DIR = os.path.join(PROJECT_ROOT, "data")
os.makedirs(CRASH_LOG_DIR, exist_ok=True)
CRASH_LOG_PATH = os.path.join(CRASH_LOG_DIR, "crash_report.log")
CRASH_LOG_FILE = open(CRASH_LOG_PATH, "a", encoding="utf-8")  # noqa: SIM115 - faulthandler owns it for process life.
faulthandler.enable(file=CRASH_LOG_FILE, all_threads=True)
append_bootstrap_event(PROJECT_ROOT, "crash_log.ready", extra={"path": CRASH_LOG_PATH})


def main():
    append_bootstrap_event(PROJECT_ROOT, "qt_main.enter")
    os.environ["QT_ENABLE_HIGHDPI_SCALING"] = "1"
    configure_qt_webengine_runtime()
    set_windows_app_user_model_id()
    append_bootstrap_event(PROJECT_ROOT, "qt_runtime.configured")

    from PyQt6.QtCore import QCoreApplication, Qt

    QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)

    from PyQt6.QtWidgets import QApplication, QMessageBox

    app = QApplication(sys.argv)
    append_bootstrap_event(PROJECT_ROOT, "qapplication.created")

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

    append_bootstrap_event(PROJECT_ROOT, "main_window.construct.begin")
    window = MainWindowQT(splash=splash)
    append_bootstrap_event(PROJECT_ROOT, "main_window.construct.ready")

    splash.set_progress(95, "准备进入主工作台...")
    window.show()

    splash.close()
    splash.deleteLater()

    try:
        append_bootstrap_event(PROJECT_ROOT, "qt_event_loop.enter")
        exit_code = app.exec()
    finally:
        SINGLE_INSTANCE_LOCK.release()
        append_bootstrap_event(
            PROJECT_ROOT,
            "qt_event_loop.exit",
            extra={"exit_code": locals().get("exit_code", "unknown")},
        )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
