# -*- coding: utf-8 -*-
"""Compact task-status panel used by the log tab."""

from __future__ import annotations

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QPlainTextEdit, QSizePolicy, QVBoxLayout

from infra.events import ui_signal_hub
from ui.presenters.task_status_presenter import (
    TaskStatusEntry,
    build_task_status_entry,
    render_task_status_lines,
    render_task_status_summary,
)


class TaskStatusPanel(QFrame):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: dict[str, TaskStatusEntry] = {}
        self._init_ui()
        ui_signal_hub.sig_task_progress.connect(
            self._on_task_progress,
            type=Qt.ConnectionType.QueuedConnection,
        )
        self._refresh_ui()

    def _init_ui(self):
        self.setObjectName("taskStatusPanel")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 8, 12, 8)
        layout.setSpacing(6)

        header = QHBoxLayout()
        header.setContentsMargins(0, 0, 0, 0)
        header.setSpacing(8)

        title_label = QLabel("Task Status")
        title_label.setObjectName("taskStatusPanelTitle")
        header.addWidget(title_label)

        self.summary_label = QLabel()
        self.summary_label.setObjectName("taskStatusPanelSummary")
        header.addWidget(self.summary_label, 1)

        layout.addLayout(header)

        self.details_edit = QPlainTextEdit(self)
        self.details_edit.setObjectName("taskStatusPanelDetails")
        self.details_edit.setReadOnly(True)
        self.details_edit.setMaximumHeight(110)
        self.details_edit.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.details_edit.document().setMaximumBlockCount(120)
        layout.addWidget(self.details_edit)

    def _ordered_entries(self) -> list[TaskStatusEntry]:
        return sorted(
            self._entries.values(),
            key=lambda entry: entry.updated_at,
            reverse=True,
        )

    def _refresh_ui(self):
        entries = self._ordered_entries()
        self.summary_label.setText(render_task_status_summary(entries))
        self.details_edit.setPlainText(render_task_status_lines(entries))

    def _on_task_progress(self, task_name: str, progress: int, message: str):
        entry = build_task_status_entry(task_name, progress, message)
        self._entries[entry.task_name] = entry
        self._refresh_ui()

    def shutdown(self):
        try:
            ui_signal_hub.sig_task_progress.disconnect(self._on_task_progress)
        except (RuntimeError, TypeError):
            pass
