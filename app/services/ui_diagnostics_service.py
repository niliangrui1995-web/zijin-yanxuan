"""UI-facing diagnostics helpers."""

from infra.diagnostics.ui_stall_probe import (
    StallThresholds,
    UiStallProbe,
    get_ui_stall_probe,
    install_ui_stall_probe,
    ui_stall_span,
)

__all__ = [
    "StallThresholds",
    "UiStallProbe",
    "get_ui_stall_probe",
    "install_ui_stall_probe",
    "ui_stall_span",
]
