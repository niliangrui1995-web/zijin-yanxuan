from collections.abc import Callable
from typing import Any

from PyQt6.QtCore import QObject, QTimer

from core.logger import get_logger

log = get_logger(__name__)


class ScanF5IncrementalCoordinator:
    """Own the deferred F5 incremental-scan lifecycle outside ``ScanTab``."""

    def __init__(
        self,
        *,
        timer_parent: QObject,
        delay_ms: int,
        settings_key: str,
        is_shutting_down: Callable[[], bool],
        is_scan_running: Callable[[], bool],
        resolve_target_date: Callable[[], str],
        normalize_target_date: Callable[[str], str],
        start_incremental_scan: Callable[[str], bool],
        settings_reader: Callable[[], Any],
        run_callback: Callable[[], bool],
        pending_changed: Callable[[bool], None],
    ) -> None:
        self._settings_key = settings_key
        self._is_shutting_down = is_shutting_down
        self._is_scan_running = is_scan_running
        self._resolve_target_date = resolve_target_date
        self._normalize_target_date = normalize_target_date
        self._start_incremental_scan = start_incremental_scan
        self._settings_reader = settings_reader
        self._run_callback = run_callback
        self._pending_changed = pending_changed
        self._pending = False
        self.timer = QTimer(timer_parent)
        self.timer.setSingleShot(True)
        self.timer.setInterval(max(0, int(delay_ms)))
        self.timer.timeout.connect(self.run_pending)
        self._publish_pending()

    def schedule(self) -> bool:
        if self._is_shutting_down() or self._pending:
            return False
        self._pending = True
        self._publish_pending()
        self.timer.start()
        return True

    def run_pending(self) -> bool:
        self.timer.stop()
        self._pending = False
        self._publish_pending()
        if self._is_shutting_down():
            return False
        return bool(self._run_callback())

    def run_now(self) -> bool:
        if self._is_shutting_down():
            return False
        if self._is_scan_running():
            log.info("[扫描] F5后自动补扫跳过：当前已有扫描任务运行中")
            return False

        target_date = self._resolve_target_date()
        target_key = self._normalize_target_date(target_date)
        if not self._start_incremental_scan(target_date):
            return False
        if target_key:
            settings = self._settings_reader()
            settings.setValue(self._settings_key, target_key)
            settings.sync()
        return True

    def shutdown(self) -> None:
        self.timer.stop()
        self._pending = False
        self._publish_pending()

    def _publish_pending(self) -> None:
        self._pending_changed(self._pending)


def build_scan_f5_incremental_coordinator(
    tab: Any,
    *,
    delay_ms: int,
    settings_key: str,
) -> ScanF5IncrementalCoordinator:
    """Wire the coordinator to a scan tab while keeping the tab constructor thin."""
    return ScanF5IncrementalCoordinator(
        timer_parent=tab,
        delay_ms=delay_ms,
        settings_key=settings_key,
        is_shutting_down=lambda: tab._shutting_down,
        is_scan_running=lambda: tab.worker is not None and tab.worker.isRunning(),
        resolve_target_date=lambda: tab._resolve_incremental_scan_date(),
        normalize_target_date=lambda value: tab._normalize_scan_date(value),
        start_incremental_scan=lambda value: tab.start_scan(value, value, merge_mode=True),
        settings_reader=lambda: tab._settings,
        run_callback=lambda: tab.run_auto_incremental_scan_after_f5(),
        pending_changed=lambda pending: setattr(tab, "_pending_f5_auto_incremental", pending),
    )
