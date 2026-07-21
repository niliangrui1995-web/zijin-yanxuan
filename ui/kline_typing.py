# -*- coding: utf-8 -*-
"""Static structural contracts for dynamically composed K-line Qt objects."""

from __future__ import annotations

from collections.abc import Callable
from typing import Protocol

from ui.kline_pool_state import KLinePoolState


class QtSignalProtocol(Protocol):
    """Minimal signal surface used by the K-line lifecycle code."""

    def connect(self, slot: Callable[..., object], *args: object, **kwargs: object) -> object: ...

    def disconnect(self, slot: Callable[..., object]) -> object: ...


class KLinePageProtocol(Protocol):
    renderProcessTerminated: QtSignalProtocol

    def runJavaScript(self, script: str, callback: Callable[[object], None]) -> object: ...


class KLineBrowserProtocol(Protocol):
    _kline_render_process_callback: Callable[..., object]

    def page(self) -> KLinePageProtocol: ...

    def parentWidget(self) -> object: ...

    def property(self, name: str) -> object: ...


class KLinePoolParticipantProtocol(Protocol):
    def transition(self, target: KLinePoolState, *, reason: str) -> KLinePoolState: ...


class KLineManagedWindowProtocol(KLinePoolParticipantProtocol, Protocol):
    browser: KLineBrowserProtocol

    def _browser_is_pool_healthy(self) -> bool: ...

    def close(self) -> object: ...

    def complete_pool_return(self) -> bool: ...

    def final_dispose(self) -> object: ...

    def setAttribute(self, *args: object) -> object: ...

    def windowTitle(self) -> str: ...


class SupportsStop(Protocol):
    def stop(self) -> object: ...


class SupportsClose(Protocol):
    def close(self) -> object: ...


class KLineGeometryProtocol(Protocol):
    def isNull(self) -> bool: ...


class KLineButtonProtocol(Protocol):
    def setText(self, text: str) -> object: ...

    def setToolTip(self, text: str) -> object: ...


class KLineOpenStagesProtocol(Protocol):
    def stop(self) -> tuple[object | None, object | None]: ...

    def reset_for_lease(self, started_at: float | None) -> object: ...

    def recover_browser(self, browser: KLineBrowserProtocol) -> object: ...


class KLineRecoveryDecisionProtocol(Protocol):
    allowed: bool


class KLineRuntimeLifecycleProtocol(Protocol):
    def begin_close(self) -> object: ...

    def request_recovery(self, browser: KLineBrowserProtocol) -> KLineRecoveryDecisionProtocol: ...


class KLineRecoveryWindowProtocol(KLinePoolParticipantProtocol, Protocol):
    _closing: bool
    _load_controller: SupportsClose
    _open_stages: KLineOpenStagesProtocol
    _rt_timer: SupportsStop | None
    _runtime_active: bool
    _runtime_lifecycle: KLineRuntimeLifecycleProtocol
    _shell_loaded: bool
    browser: KLineBrowserProtocol | None

    def _set_status_message(self, message: str, *, tone: str) -> object: ...
