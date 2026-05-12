"""Compatibility facade for UI stall diagnostics.

UI-facing code should import from ``app.services.ui_diagnostics_service``.
Low-level diagnostics code can import ``infra.diagnostics.ui_stall_probe`` directly.
"""

from infra.diagnostics.ui_stall_probe import (  # noqa: F401
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
