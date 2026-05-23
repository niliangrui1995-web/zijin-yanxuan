# -*- coding: utf-8 -*-
from PyQt6.QtWidgets import QApplication

from ui.splash_screen import SplashScreen


def test_set_progress_does_not_process_global_event_loop(monkeypatch):
    QApplication.instance() or QApplication([])
    calls = []

    def fail_process_events(*args, **kwargs):
        calls.append((args, kwargs))
        raise AssertionError("Splash progress must not process the global event loop")

    monkeypatch.setattr(QApplication, "processEvents", fail_process_events)

    splash = SplashScreen()
    try:
        splash.set_progress(90, "loading")

        assert calls == []
        assert splash.progress.value() == 90
        assert splash.lbl_status.text() == "loading"
    finally:
        splash.close()
        splash.deleteLater()


def test_splash_uses_compact_opaque_brand_frame():
    QApplication.instance() or QApplication([])

    splash = SplashScreen()
    try:
        assert splash.width() == 420
        assert splash.height() == 340
        assert splash.windowTitle() == "紫金研选"
        assert splash.lbl_sub.text() == ""
        assert splash.lbl_sub.isHidden()
        assert splash.lbl_status.text() == "准备启动环境..."
        assert "#F4F7FF" in splash.lbl_brand.styleSheet()
        assert "#B91C1C" in splash.progress.styleSheet()
        assert "#93C5FD" in splash.progress.styleSheet()
    finally:
        splash.close()
        splash.deleteLater()
