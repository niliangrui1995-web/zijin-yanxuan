# -*- coding: utf-8 -*-
import sys
from types import SimpleNamespace

from PyQt6.QtGui import QColor, QImage

from scripts.capture_ui_audit_screenshots import _create_audit_main_window, _parse_args, _validate_saved_screenshots


def _save_image(path, width=1280, height=720, *, color="#0B1020", pulse=True):
    image = QImage(width, height, QImage.Format.Format_RGB32)
    image.fill(QColor(color))
    if pulse:
        red = QColor("#DC2626")
        for x in range(0, width, 4):
            image.setPixelColor(x, 42, red)
            image.setPixelColor(x, 43, red)
    assert image.save(str(path))


def test_strict_screenshot_validation_accepts_1280_dark_audit(tmp_path):
    main = tmp_path / "00_main_window.png"
    tab = tmp_path / "01_watchlist.png"
    _save_image(main)
    _save_image(tab, pulse=False)

    errors = _validate_saved_screenshots(
        [main, tab],
        width=1280,
        height=720,
        tabs=True,
        strict_tabs_min=1,
        theme_appearance="dark",
    )

    assert errors == []


def test_strict_tabs_min_defaults_to_dynamic_workspace_count():
    args = _parse_args(["--strict"])

    assert args.strict_tabs_min is None


def test_create_audit_main_window_uses_controlled_runtime(monkeypatch):
    created = []

    class DummyMainWindow:
        def __init__(self, **kwargs):
            created.append(kwargs)

    monkeypatch.setitem(sys.modules, "ui.main_window_qt", SimpleNamespace(MainWindowQT=DummyMainWindow))

    window = _create_audit_main_window()

    assert isinstance(window, DummyMainWindow)
    assert created == [
        {
            "startup_enabled": False,
            "background_prewarm": False,
            "kline_prewarm_enabled": False,
            "central_quotes_enabled": False,
            "restore_last_tab_enabled": False,
        }
    ]


def test_strict_screenshot_validation_flags_white_panel_and_missing_tabs(tmp_path):
    main = tmp_path / "00_main_window.png"
    _save_image(main, color="#FFFFFF", pulse=False)

    errors = _validate_saved_screenshots(
        [main],
        width=1280,
        height=720,
        tabs=True,
        strict_tabs_min=12,
        theme_appearance="dark",
    )

    assert any("white panel" in error for error in errors)
    assert any("red titlebar pulse" in error for error in errors)
    assert any("expected at least 12" in error for error in errors)


def test_strict_screenshot_validation_allows_light_theme_white_panels(tmp_path):
    main = tmp_path / "00_main_window.png"
    _save_image(main, color="#FFFFFF")

    errors = _validate_saved_screenshots(
        [main],
        width=1280,
        height=720,
        tabs=False,
        strict_tabs_min=0,
        theme_appearance="light",
    )

    assert not any("white panel" in error for error in errors)
