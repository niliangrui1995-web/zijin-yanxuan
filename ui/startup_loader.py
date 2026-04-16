# -*- coding: utf-8 -*-
"""
ui/startup_loader.py
负责主窗口冷启动、缓存恢复和智能联机启动。
"""

from __future__ import annotations

import datetime
import os
import subprocess
import sys

from PyQt6.QtCore import QTimer

from core.event_bus import event_bus
from core.logger import get_logger
from core.task_manager import task_manager

log = get_logger(__name__)
ASIAN_DATA_SYNC_TIMEOUT_SEC = 120


def _normalize_log_detail(text: str, limit: int = 120) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    compact = " | ".join(part.strip() for part in raw.splitlines() if part.strip()) or raw
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3] + "..."


def _format_subprocess_failure(exc: Exception) -> tuple[str, str]:
    if isinstance(exc, subprocess.CalledProcessError):
        raw_detail = str(exc.stderr or "").strip() or str(exc.stdout or "").strip()
        summary = f"退出码 {exc.returncode}"
        summary_detail = _normalize_log_detail(raw_detail)
        if summary_detail:
            summary = f"{summary}：{summary_detail}"
        return summary, raw_detail

    message = str(exc or "").strip() or exc.__class__.__name__
    return message, message


class StartupLoader:
    """主窗口启动流程协调器。"""

    def __init__(self, main_window):
        self.mw = main_window
        self._closed = False
        self._deferred_timer = QTimer(main_window)
        self._deferred_timer.setSingleShot(True)
        self._deferred_timer.timeout.connect(self.deferred_data_load)
        self._smart_timer = QTimer(main_window)
        self._smart_timer.setSingleShot(True)
        self._smart_timer.timeout.connect(self.smart_startup)

    def schedule_startup(self):
        if self._closed:
            return
        self._deferred_timer.start(2500)
        self._smart_timer.start(4500)

    def shutdown(self):
        self._closed = True
        self._deferred_timer.stop()
        self._smart_timer.stop()
        for task_id in ("deferred_load", "asian_data_sync_bg", "smart_startup"):
            task_manager.abandon_task(task_id)

    def _alive(self):
        return (
            not self._closed
            and self.mw is not None
            and not getattr(self.mw, "_is_closing", False)
        )

    def _safe_call_in_ui(self, callback):
        if not self._alive():
            return
        try:
            self.mw._call_in_ui(lambda: callback() if self._alive() else None)
        except RuntimeError:
            pass

    def deferred_data_load(self):
        """延迟恢复历史缓存、实时缓存和 RPS 缓存。"""

        def _load_bg():
            if not self._alive():
                return

            cache_date = self.mw.data_provider.load_cache_from_disk()
            if cache_date and self._alive():
                count = len(self.mw.data_provider.cache_data)
                self._safe_call_in_ui(
                    lambda: getattr(self.mw, "lbl_code_count", None)
                    and self.mw.lbl_code_count.setText(f"标的池: {count}")
                )
                self._safe_call_in_ui(
                    lambda: self.mw.lbl_status.setText(
                        f"已加载 {count} 只标的缓存 (日期: {cache_date})"
                    )
                )

            self._safe_call_in_ui(
                lambda: self.mw.cache_manager.load_rt_cache(
                    getattr(getattr(self.mw, "_workspace", None), "get_rt_table", lambda: None)(),
                    lambda msg: self.mw.lbl_status.setText(msg),
                )
            )

            self.mw.cache_manager.try_load_rps_from_disk(
                self.mw.engine,
                data_provider=self.mw.data_provider,
                set_status_callback=lambda msg: self._safe_call_in_ui(
                    lambda: self.mw.lbl_status.setText(msg)
                ),
            )

            self._safe_call_in_ui(lambda: event_bus.sig_cache_bootstrap_ready.emit())

        task_manager.run_in_background(_load_bg, task_id="deferred_load")

        def _check_asian_data_bg():
            if not self._alive():
                return

            project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            output_dir = os.path.join(project_root, "data", "Cache")
            json_cache = os.path.join(output_dir, "asian_klines_latest.json")
            module_entry = os.path.join(
                project_root,
                "vcp",
                "fetchers",
                "asian_kline_fetcher.py",
            )

            needs_update = False
            if not os.path.exists(json_cache):
                needs_update = True
            else:
                mtime = os.path.getmtime(json_cache)
                mdate = datetime.date.fromtimestamp(mtime)
                if mdate < datetime.date.today():
                    needs_update = True

            if needs_update and os.path.exists(module_entry):
                log.info("[启动] 亚洲市场 JSON 非最新，后台静默增量同步中...")
                try:
                    creationflags = 0x08000000 if os.name == "nt" else 0
                    subprocess.run(
                        [
                            sys.executable,
                            "-m",
                            "vcp.fetchers.asian_kline_fetcher",
                            "--strict-sync",
                            "--output-dir",
                            output_dir,
                        ],
                        check=True,
                        cwd=project_root,
                        stdout=subprocess.PIPE,
                        stderr=subprocess.PIPE,
                        text=True,
                        encoding="utf-8",
                        errors="ignore",
                        creationflags=creationflags,
                        timeout=ASIAN_DATA_SYNC_TIMEOUT_SEC,
                    )
                    log.info("[启动] 亚洲市场静默同步完成，触发界面刷新。")
                    self._safe_call_in_ui(lambda: event_bus.sig_asian_klines_ready.emit())
                except subprocess.TimeoutExpired:
                    log.warning(
                        f"[启动] 亚洲市场后台静默同步超时({ASIAN_DATA_SYNC_TIMEOUT_SEC}s)，已跳过本次同步"
                    )
                except (OSError, subprocess.CalledProcessError, ValueError) as exc:
                    summary, raw_detail = _format_subprocess_failure(exc)
                    log.warning(f"[启动] 亚洲市场静默同步失败，已跳过本次更新（{summary}）")
                    if raw_detail:
                        log.debug(f"[启动] 亚洲市场静默同步原始输出: {raw_detail}")

        task_manager.run_in_background(_check_asian_data_bg, task_id="asian_data_sync_bg")

    def smart_startup(self):
        """异步检测网络；可联机时切到在线模式并驱动后续刷新。"""

        def _check_and_go_online():
            try:
                if not self._alive():
                    return
                if self.mw.data_provider.test_network(timeout=3):
                    if not self._alive():
                        return
                    self.mw.data_provider.set_online_mode(True)
                    log.info("[智能启动] 网络可用，已自动切换到联机模式")

                    try:
                        if not self._alive():
                            return
                        self.mw.data_provider.get_all_codes()
                        self.mw.data_provider.code2name = (
                            self.mw.data_provider._get_codes_from_vipdoc()
                        )
                        workspace = getattr(self.mw, "_workspace", None)
                        if workspace is not None:
                            self._safe_call_in_ui(
                                lambda: workspace.refresh_watchlist_names(
                                    self.mw.data_provider.code2name
                                )
                            )
                    except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                        log.error(f"[智能启动] 后台同步代码名称映射失败: {exc}")

                    self._safe_call_in_ui(lambda: self.mw._update_network_ui(True))
                    if hasattr(self.mw, "_on_smart_startup_online_done"):
                        self._safe_call_in_ui(self.mw._on_smart_startup_online_done)
                    self._safe_call_in_ui(self.auto_start_rt_if_ready)
                else:
                    log.info("[智能启动] 网络不可用，保持离线模式")
            except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
                log.error(f"[智能启动] 网络检测异常: {exc}")

        task_manager.run_in_background(_check_and_go_online, task_id="smart_startup")

    def auto_start_rt_if_ready(self):
        """启动完成后按条件自动开启盘中监控。"""
        try:
            if not self._alive():
                return

            from core.market_calendar import MarketCalendar

            if not MarketCalendar.is_market_active():
                log.info("[智能启动] 非交易时段，跳过盘中自动监控")
                return
            if not self.mw.data_provider.cache_data or len(self.mw.data_provider.cache_data) < 100:
                log.info("[智能启动] 数据不足，跳过盘中自动监控")
                return

            workspace = getattr(self.mw, "_workspace", None)
            if workspace is not None and workspace.auto_start_rt_monitor():
                log.info("[智能启动] 盘中监控已自动启动")
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError) as exc:
            log.error(f"[智能启动] 自动监控启动异常: {exc}")
