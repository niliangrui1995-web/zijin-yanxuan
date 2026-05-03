# -*- coding: utf-8 -*-
from PyQt6.QtGui import QColor, QImage

from scripts.capture_ui_audit_screenshots import _validate_saved_screenshots


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
    )

    assert errors == []


def test_strict_screenshot_validation_flags_white_panel_and_missing_tabs(tmp_path):
    main = tmp_path / "00_main_window.png"
    _save_image(main, color="#FFFFFF", pulse=False)

    errors = _validate_saved_screenshots(
        [main],
        width=1280,
        height=720,
        tabs=True,
        strict_tabs_min=12,
    )

    assert any("white panel" in error for error in errors)
    assert any("red titlebar pulse" in error for error in errors)
    assert any("expected at least 12" in error for error in errors)
