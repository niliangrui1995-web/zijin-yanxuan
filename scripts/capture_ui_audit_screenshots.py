# -*- coding: utf-8 -*-
"""Capture UI audit screenshots for the main window and shared overlays.

This script is intentionally lightweight and does not edit project files. It
disables the startup prewarm paths that make visual snapshots noisy, then
captures the main window, loaded workspace tabs, command palette, and optional
dialogs/K-line. Loading a real tab can still trigger that tab's own normal
read-only data refresh, so use ``--no-tabs`` when only shell chrome is needed.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.runtime_env import configure_qt_webengine_runtime


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture UI audit screenshots.")
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "tmp" / "ui_audit_screenshots_win",
        help="Directory where screenshots are written.",
    )
    parser.add_argument(
        "--offscreen",
        action="store_true",
        help="Use the Qt offscreen platform for CI/smoke validation.",
    )
    parser.add_argument(
        "--tabs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Capture all workspace tabs.",
    )
    parser.add_argument(
        "--command-palette",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Capture the command palette overlay.",
    )
    parser.add_argument(
        "--dialogs",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Capture scan/settings/confirm dialogs.",
    )
    parser.add_argument(
        "--kline",
        action=argparse.BooleanOptionalAction,
        default=False,
        help="Capture a K-line window preview. Disabled by default because QWebEngine can be slow.",
    )
    parser.add_argument("--wait-ms", type=int, default=320, help="Event-loop settle time after UI actions.")
    parser.add_argument("--width", type=int, default=1440, help="Main window screenshot width.")
    parser.add_argument("--height", type=int, default=900, help="Main window screenshot height.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Fail when screenshots do not meet audit dimensions, tab coverage, or dark-theme checks.",
    )
    parser.add_argument(
        "--strict-tabs-min",
        type=int,
        default=12,
        help="Minimum numbered tab screenshots required in strict mode.",
    )
    return parser.parse_args()


def _configure_environment(args: argparse.Namespace) -> None:
    configure_qt_webengine_runtime()
    os.environ.setdefault("QT_AUTO_SCREEN_SCALE_FACTOR", "0")
    os.environ.setdefault("QT_SCALE_FACTOR", "1")
    if args.offscreen:
        os.environ["QT_QPA_PLATFORM"] = "offscreen"


class _NoopStartupOrchestrator:
    def schedule_startup(self):
        return None

    def shutdown(self):
        return None


def _disable_noisy_startup_paths() -> None:
    import ui.main_window_qt as main_window_qt
    import ui.workspaces.classic_workspace as classic_workspace
    from app.services.ui_runtime_service import MarketCalendar
    from domains.earnings.scheduler import EarningsScheduler
    from ui.tabs.asian_market_tab import AsianMarketTab

    main_window_qt.create_startup_orchestrator = lambda _parent: _NoopStartupOrchestrator()
    main_window_qt.kline_manager.prewarm = lambda *args, **kwargs: None
    main_window_qt.ApplicationBootstrap.install_central_quotes = lambda self: None
    classic_workspace.ClassicWorkspace.BACKGROUND_PREWARM_DELAY_MS = 60_000_000
    classic_workspace.ClassicWorkspace._start_background_tab_prewarm = lambda self: None
    AsianMarketTab._ensure_runtime_started = lambda self: None
    AsianMarketTab._worker_resume_auto_refresh = lambda self: None
    AsianMarketTab._worker_trigger_refresh = lambda self: None
    EarningsScheduler.start_patrol = lambda self: None
    MarketCalendar._schedule_asian_holiday_refresh = classmethod(lambda cls, market, years: None)


def _settle(app, wait_ms: int) -> None:
    deadline = time.perf_counter() + max(wait_ms, 0) / 1000
    while time.perf_counter() < deadline:
        app.processEvents()
        time.sleep(0.01)
    app.processEvents()


def _save_widget(widget, path: Path) -> bool:
    pixmap = widget.grab()
    path.parent.mkdir(parents=True, exist_ok=True)
    return bool(pixmap.save(str(path)))


def _sample_image_ratio(image, predicate, *, step: int = 12) -> float:
    width = image.width()
    height = image.height()
    if width <= 0 or height <= 0:
        return 0.0
    total = 0
    matched = 0
    for y in range(0, height, max(1, step)):
        for x in range(0, width, max(1, step)):
            color = image.pixelColor(x, y)
            if color.alpha() <= 0:
                continue
            total += 1
            if predicate(color):
                matched += 1
    return matched / total if total else 0.0


def _has_large_white_panel(image) -> bool:
    return _sample_image_ratio(
        image,
        lambda color: color.red() >= 245 and color.green() >= 245 and color.blue() >= 245,
        step=16,
    ) > 0.16


def _has_titlebar_pulse_pixels(image) -> bool:
    width = image.width()
    scan_height = min(56, image.height())
    if width <= 0 or scan_height <= 0:
        return False
    hits = 0
    for y in range(0, scan_height, 2):
        for x in range(0, width, 8):
            color = image.pixelColor(x, y)
            if color.red() >= 120 and color.green() <= 95 and color.blue() <= 105:
                hits += 1
                if hits >= 16:
                    return True
    return False


def _validate_saved_screenshots(
    saved: list[Path],
    *,
    width: int,
    height: int,
    tabs: bool,
    strict_tabs_min: int,
) -> list[str]:
    from PyQt6.QtGui import QImage

    errors: list[str] = []
    by_name = {path.name: path for path in saved}
    main_path = by_name.get("00_main_window.png")
    if main_path is None:
        errors.append("missing 00_main_window.png")
    else:
        image = QImage(str(main_path))
        if image.isNull():
            errors.append("main screenshot could not be read")
        else:
            if image.width() != width or image.height() != height:
                errors.append(f"main screenshot is {image.width()}x{image.height()}, expected {width}x{height}")
            if not _has_titlebar_pulse_pixels(image):
                errors.append("main screenshot does not show the red titlebar pulse strip")

    if tabs:
        tab_count = sum(1 for path in saved if path.name[:2].isdigit() and path.name[:2] not in {"00", "90", "91", "92", "93", "94"})
        if tab_count < strict_tabs_min:
            errors.append(f"captured {tab_count} tab screenshots, expected at least {strict_tabs_min}")

    for path in saved:
        image = QImage(str(path))
        if image.isNull():
            errors.append(f"{path.name} could not be read")
            continue
        if _has_large_white_panel(image):
            errors.append(f"{path.name} appears to contain a large white panel")
    return errors


def _capture_main_and_tabs(
    app,
    window,
    output: Path,
    wait_ms: int,
    capture_tabs: bool,
    *,
    width: int,
    height: int,
) -> list[Path]:
    saved: list[Path] = []
    target_width = max(960, width)
    target_height = max(600, height)
    try:
        window.setMinimumSize(min(window.minimumWidth(), target_width), min(window.minimumHeight(), target_height))
    except (AttributeError, RuntimeError, TypeError):
        pass
    if hasattr(window, "showNormal"):
        window.showNormal()
    window.resize(target_width, target_height)
    window.show()
    if hasattr(window, "showNormal"):
        window.showNormal()
    window.resize(target_width, target_height)
    window.raise_()
    window.activateWindow()
    _settle(app, wait_ms)

    main_path = output / "00_main_window.png"
    if _save_widget(window, main_path):
        saved.append(main_path)

    if not capture_tabs:
        return saved

    workspace = getattr(window, "_workspace", None)
    tabs = getattr(window, "tabs", None)
    specs = list(getattr(workspace, "_tab_specs", []) or [])
    if workspace is None or tabs is None:
        return saved

    for index, spec in enumerate(specs):
        key = str(spec.get("key") or f"tab_{index}").strip()
        if not key:
            key = f"tab_{index}"
        try:
            workspace.ensure_tab_loaded(key, reason="screenshot")
            tabs.setCurrentIndex(index)
            _settle(app, wait_ms)
            path = output / f"{index + 1:02d}_{key}.png"
            if _save_widget(window, path):
                saved.append(path)
        except Exception as exc:  # noqa: BLE001 - screenshot helper should keep going.
            print(f"[capture] tab failed key={key}: {exc}", file=sys.stderr)
    return saved


def _capture_command_palette(app, window, output: Path, wait_ms: int) -> Path | None:
    try:
        window._open_command_palette()
        _settle(app, wait_ms)
        dialog = getattr(window, "_command_palette", None)
        if dialog is None:
            return None
        path = output / "90_command_palette.png"
        return path if _save_widget(dialog, path) else None
    except Exception as exc:  # noqa: BLE001
        print(f"[capture] command palette failed: {exc}", file=sys.stderr)
        return None


def _capture_dialogs(app, window, output: Path, wait_ms: int) -> list[Path]:
    saved: list[Path] = []
    try:
        from ui.components.message_box import ThemedQuestionDialog
        from ui.components.scan_dialogs import VCPScanRangeDialog, VCPScanSettingsDialog

        dialog_specs = [
            ("91_scan_range_dialog.png", VCPScanRangeDialog(window)),
            ("92_scan_settings_dialog.png", VCPScanSettingsDialog({}, parent=window)),
            ("93_confirm_dialog.png", ThemedQuestionDialog(window, "Audit Preview", "Confirm dialog visual state.")),
        ]
        for filename, dialog in dialog_specs:
            dialog.show()
            _settle(app, wait_ms)
            path = output / filename
            if _save_widget(dialog, path):
                saved.append(path)
            dialog.close()
    except Exception as exc:  # noqa: BLE001
        print(f"[capture] dialogs failed: {exc}", file=sys.stderr)
    return saved


def _capture_kline(app, window, output: Path, wait_ms: int) -> Path | None:
    try:
        from ui.kline_window_qt import KLineChartWindow

        dialog = KLineChartWindow(
            window,
            "600519",
            "Kline Preview",
            window.data_provider,
            vcp_data={},
            code_list=["600519"],
            current_idx=0,
        )
        dialog.show()
        deadline = time.perf_counter() + 5.0
        while time.perf_counter() < deadline:
            _settle(app, max(wait_ms, 120))
            if getattr(dialog, "df", None) is not None and getattr(dialog, "_pending_chart_status", None) is None:
                break
        path = output / "94_kline_window.png"
        saved = _save_widget(dialog, path)
        dialog.close()
        return path if saved else None
    except Exception as exc:  # noqa: BLE001
        print(f"[capture] kline failed: {exc}", file=sys.stderr)
        return None


def main() -> int:
    args = _parse_args()
    _configure_environment(args)

    if args.kline:
        from PyQt6.QtCore import QCoreApplication, Qt

        QCoreApplication.setAttribute(Qt.ApplicationAttribute.AA_ShareOpenGLContexts)
        try:
            import PyQt6.QtWebEngineWidgets  # noqa: F401
        except Exception as exc:  # noqa: BLE001 - keep the rest of the audit usable.
            print(f"[capture] webengine preflight failed: {exc}", file=sys.stderr)

    from PyQt6.QtWidgets import QApplication

    _disable_noisy_startup_paths()
    from app.services.ui_runtime_service import background_job_runner
    from ui.main_window_qt import MainWindowQT

    app = QApplication.instance() or QApplication(sys.argv)
    args.output.mkdir(parents=True, exist_ok=True)

    window = MainWindowQT()
    window._save_ui_state = lambda: None
    saved = _capture_main_and_tabs(
        app,
        window,
        args.output,
        args.wait_ms,
        args.tabs,
        width=args.width,
        height=args.height,
    )

    if args.command_palette:
        path = _capture_command_palette(app, window, args.output, args.wait_ms)
        if path:
            saved.append(path)
    if args.dialogs:
        saved.extend(_capture_dialogs(app, window, args.output, args.wait_ms))
    if args.kline:
        path = _capture_kline(app, window, args.output, args.wait_ms)
        if path:
            saved.append(path)

    validation_errors: list[str] = []
    if args.strict:
        validation_errors = _validate_saved_screenshots(
            saved,
            width=max(960, args.width),
            height=max(600, args.height),
            tabs=args.tabs,
            strict_tabs_min=args.strict_tabs_min,
        )

    workspace = getattr(window, "_workspace", None)
    if workspace is not None:
        try:
            workspace.shutdown()
        except Exception as exc:  # noqa: BLE001
            print(f"[capture] workspace shutdown failed: {exc}", file=sys.stderr)
    window.close()
    background_job_runner.shutdown()
    _settle(app, 120)

    for path in saved:
        print(path)
    if validation_errors:
        for error in validation_errors:
            print(f"[capture] strict validation failed: {error}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
