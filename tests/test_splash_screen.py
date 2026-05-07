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
