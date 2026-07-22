from __future__ import annotations

import json

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
)

from app.services.runtime_health_service import collect_runtime_health, export_runtime_health_report


def _quote_health_summary(request_stats: dict) -> str:
    count_keys = (
        "recent_network_result_count",
        "recent_cache_hit_count",
        "recent_stale_result_count",
    )
    if not all(key in request_stats for key in count_keys):
        return f"行情批次 {request_stats.get('recent_batch_count', 0)}"
    quote_time = str(request_stats.get("recent_latest_quote_time") or "").strip()
    quote_clock = quote_time.split("T", 1)[1][:8] if "T" in quote_time else quote_time or "--:--:--"
    return (
        f"行情 {quote_clock} | "
        f"联网 {request_stats.get('recent_network_result_count', 0)}/"
        f"缓存 {request_stats.get('recent_cache_hit_count', 0)}/"
        f"过期 {request_stats.get('recent_stale_result_count', 0)}"
    )


class RuntimeHealthDialog(QDialog):
    def __init__(self, main_window, parent=None):
        super().__init__(parent or main_window)
        self._main_window = main_window
        self._last_report: dict | None = None
        self.setWindowTitle("运行时健康")
        self.resize(980, 680)
        self._init_ui()
        self.refresh()

    def _init_ui(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        header = QFrame(self)
        header_layout = QHBoxLayout(header)
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(8)

        self.summary_label = QLabel("", header)
        self.summary_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        header_layout.addWidget(self.summary_label, 1)

        self.btn_refresh = QPushButton("刷新", header)
        self.btn_refresh.clicked.connect(self.refresh)
        header_layout.addWidget(self.btn_refresh, 0)

        self.btn_export = QPushButton("导出 JSON", header)
        self.btn_export.clicked.connect(self.export_json)
        header_layout.addWidget(self.btn_export, 0)

        self.btn_close = QPushButton("关闭", header)
        self.btn_close.clicked.connect(self.accept)
        header_layout.addWidget(self.btn_close, 0)

        layout.addWidget(header)

        self.report_edit = QPlainTextEdit(self)
        self.report_edit.setReadOnly(True)
        self.report_edit.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        layout.addWidget(self.report_edit, 1)

    @staticmethod
    def _summary_text(report: dict) -> str:
        tasks = report.get("background_tasks") or {}
        timers = report.get("timers") or {}
        events = report.get("event_bus") or {}
        process = report.get("process") or {}
        webengine = report.get("webengine") or {}
        quotes = report.get("quotes") or {}
        quote_stats = quotes.get("request_stats") or {}
        f5_cache = report.get("f5_cache") or {}
        quote_summary = _quote_health_summary(quote_stats)
        return (
            f"任务 {tasks.get('count', 0)} | "
            f"Timer {timers.get('active', 0)}/{timers.get('total', 0)} | "
            f"事件订阅 {events.get('total_receivers', 0)} | "
            f"线程 {process.get('thread_count', '-')} | "
            f"WebEngine {webengine.get('count', 0)} | "
            f"{quote_summary} | "
            f"F5 {f5_cache.get('trade_date') or '暂无'}"
        )

    def refresh(self) -> None:
        self._last_report = collect_runtime_health(self._main_window)
        self.summary_label.setText(self._summary_text(self._last_report))
        self.report_edit.setPlainText(json.dumps(self._last_report, ensure_ascii=False, indent=2))

    def export_json(self) -> None:
        report = self._last_report or collect_runtime_health(self._main_window)
        project_root = getattr(self._main_window, "_project_root", None)
        output_path = export_runtime_health_report(
            self._main_window,
            project_root=project_root,
            report=report,
        )
        self.summary_label.setText(f"{self._summary_text(report)} | 已导出 {output_path}")
