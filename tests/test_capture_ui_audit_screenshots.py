# -*- coding: utf-8 -*-
import sys
from types import SimpleNamespace

from PyQt6.QtGui import QColor, QImage

from scripts.capture_ui_audit_screenshots import (
    DEFAULT_STRICT_STALL_CRITICAL_MAX,
    DEFAULT_STRICT_STALL_EVENT_LOOP_CRITICAL_MAX,
    DEFAULT_STRICT_STALL_MAX_MS,
    DEFAULT_STRICT_STALL_TOTAL_MAX,
    _create_audit_main_window,
    _parse_args,
    _validate_saved_screenshots,
    _validate_ui_stall_budget,
)


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
    assert args.strict_stall_total_max == DEFAULT_STRICT_STALL_TOTAL_MAX
    assert args.strict_stall_critical_max == DEFAULT_STRICT_STALL_CRITICAL_MAX
    assert args.strict_stall_event_loop_critical_max == DEFAULT_STRICT_STALL_EVENT_LOOP_CRITICAL_MAX
    assert args.strict_stall_max_ms == DEFAULT_STRICT_STALL_MAX_MS


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


def test_strict_ui_stall_budget_accepts_snapshot_within_limits():
    errors = _validate_ui_stall_budget(
        {
            "installed": True,
            "total_count": 2,
            "critical_count": 1,
            "event_loop_critical_count": 1,
            "max_elapsed_ms": 180.0,
        },
        total_max=4,
        critical_max=2,
        event_loop_critical_max=2,
        max_elapsed_ms=250.0,
    )

    assert errors == []


def test_strict_ui_stall_budget_flags_budget_regressions():
    errors = _validate_ui_stall_budget(
        {
            "installed": True,
            "total_count": 9,
            "critical_count": 5,
            "event_loop_critical_count": 4,
            "max_elapsed_ms": 1400.0,
        },
        total_max=8,
        critical_max=4,
        event_loop_critical_max=3,
        max_elapsed_ms=1200.0,
    )

    assert any("UI stall total count 9 exceeded budget 8" in error for error in errors)
    assert any("critical UI stall count 5 exceeded budget 4" in error for error in errors)
    assert any("critical event-loop stall count 4 exceeded budget 3" in error for error in errors)
    assert any("max UI stall 1400.0ms exceeded budget 1200.0ms" in error for error in errors)


def test_strict_ui_stall_budget_requires_installed_probe():
    assert _validate_ui_stall_budget(
        {"installed": False},
        total_max=1,
        critical_max=1,
        event_loop_critical_max=1,
        max_elapsed_ms=1.0,
    ) == ["UI stall probe was not installed"]
