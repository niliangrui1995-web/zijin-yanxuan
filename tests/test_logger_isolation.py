import logging.handlers
import os
from datetime import datetime
from pathlib import Path

import core.logger as logger_module
from core.logger import get_logger


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def test_pytest_file_logging_uses_test_log_dir():
    marker = "pytest logger isolation probe"
    logger = get_logger("tests.logger_isolation")
    logger.warning(marker)

    test_log_dir = Path(os.environ["VCP_HUNTER_LOG_DIR"]).resolve()
    project_log_dir = Path(__file__).resolve().parents[1] / "data" / "logs"
    handlers = [
        handler
        for handler in logger.handlers
        if isinstance(handler, logging.handlers.RotatingFileHandler)
    ]

    assert handlers
    for handler in handlers:
        handler.flush()

    log_files = [Path(handler.baseFilename).resolve() for handler in handlers]
    assert all(path.parent == test_log_dir for path in log_files)
    assert all(not _is_relative_to(path, project_log_dir) for path in log_files)
    assert any(marker in path.read_text(encoding="utf-8") for path in log_files)

    project_log = project_log_dir / f"vcp_{datetime.now().strftime('%Y%m%d')}.log"
    if project_log.exists():
        assert marker not in project_log.read_text(encoding="utf-8")


def test_system_log_backpressure_throttles_allowed_info(monkeypatch):
    ticks = iter([100.0, 100.1, 104.0])
    monkeypatch.setattr(logger_module.time, "monotonic", lambda: next(ticks))
    guard = logger_module.system_log_backpressure(
        "F5",
        allowed_info_loggers=("core.rps_precomputer",),
    )

    def record(message: str, *, name: str = "core.rps_precomputer", level: int = logging.INFO):
        return logging.LogRecord(name, level, __file__, 1, message, (), None)

    assert guard.should_suppress(record("\n" + "=" * 60)) is True
    assert guard.should_suppress(record("[F5] 盘后一键预计算 -- 开始")) is False
    assert guard.should_suppress(record("[F5] 内存快照 [启动基线]: 1236 MB")) is True
    assert guard.should_suppress(record("[F5] 阶段1/3: 清空缓存,开始从 vipdoc 重读...")) is False
    assert guard.should_suppress(record("[F5] ⚠ 阶段2/3: RPS 矩阵计算返回空", level=logging.WARNING)) is False
    assert guard.suppressed_allowed_info == 2


def test_event_bus_handler_compacts_ui_stall_diagnostics(monkeypatch, qt_application):
    from domains.runtime import domain_events

    events = []

    def capture(level, text):
        events.append((level, text))

    domain_events.sig_system_log.connect(capture)
    gate = logger_module._UiDiagnosticFrontendGate(min_interval_sec=5.0)
    monkeypatch.setattr(logger_module, "_ui_diagnostic_frontend_gate", gate)
    ticks = iter([100.0, 100.1, 100.2, 106.0])
    monkeypatch.setattr(logger_module.time, "monotonic", lambda: next(ticks))

    handler = logger_module.EventBusHandler()
    handler.setFormatter(logging.Formatter("%(message)s"))

    def record(elapsed: str):
        return logging.LogRecord(
            "infra.diagnostics.ui_stall_probe",
            logging.WARNING,
            __file__,
            1,
            (
                "[事件] ui.stall.event_loop | "
                f"elapsed_ms={elapsed} | event_loop_gap_ms=883.572 | "
                "severity=critical | tab=system_log"
            ),
            (),
            None,
        )

    try:
        handler.emit(record("271.289"))
        handler.emit(record("141.513"))
        handler.emit(record("127.887"))
        handler.emit(record("858.572"))
    finally:
        domain_events.sig_system_log.disconnect(capture)

    assert len(events) == 2
    assert events[0][0] == "warn"
    assert "高频卡顿诊断" in events[0][1]
    assert "ui.stall" not in events[0][1]
    assert "前台=system_log" in events[0][1]
    assert "已合并 2 条" in events[1][1]
    assert "耗时=858.572" in events[1][1]
    assert "完整明细见文件日志" in events[1][1]
