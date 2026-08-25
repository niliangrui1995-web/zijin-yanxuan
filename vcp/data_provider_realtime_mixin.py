import time

from core.logger import get_logger
from core.market_calendar import MarketCalendar
from infra.market_data.warehouse_quote_reader import read_latest_quotes
from infra.tasks.lifecycle import CancellationToken, call_with_supported_kwargs
from vcp.data_provider_local import build_offline_quotes
from vcp.data_provider_quotes import (
    ensure_eastmoney_quote_state,
    ensure_hithink_quote_state,
    enter_eastmoney_cooldown,
    enter_hithink_cooldown,
    log_quote_fallback,
    request_eastmoney_quote_batch,
    request_hithink_quote_batch,
    request_sina_quote_batch,
    request_tencent_quote_batch,
    sanitize_hithink_error,
    to_eastmoney_secid,
)
from vcp.data_provider_realtime import fetch_eastmoney_quotes_with_split_retry, summarize_probe_error
from vcp.data_provider_realtime import fetch_realtime_quotes_batch as fetch_realtime_quotes_batch_runtime

_log = get_logger(__name__)

RT_QUOTE_BATCH_SIZE = 20
RT_QUOTE_MIN_BATCH_SIZE = 5
RT_QUOTE_BATCH_PAUSE_SEC = 0.12
RT_QUOTE_DEDUP_WINDOW_SEC = 8.5
RT_EASTMONEY_COOLDOWN_SEC = 120.0
NETWORK_PROBE_FALLBACK_RESERVE_SEC = 0.2
_WAREHOUSE_QUOTE_ERRORS = (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError)


def _normalize_offline_quote_codes(codes) -> tuple[str, ...]:
    return tuple(dict.fromkeys(str(code or "").strip() for code in (codes or []) if str(code or "").strip()))


def _network_probe_source_timeout(remaining_sec: float, remaining_sources: int, *, primary: bool) -> float:
    """Give the primary probe first chance while reserving time for each fallback."""
    remaining_sec = max(0.0, float(remaining_sec))
    remaining_sources = max(1, int(remaining_sources))
    if remaining_sources == 1:
        return remaining_sec
    if primary:
        fallback_reserve = min(NETWORK_PROBE_FALLBACK_RESERVE_SEC, remaining_sec / remaining_sources)
        return remaining_sec - fallback_reserve * (remaining_sources - 1)
    return remaining_sec / remaining_sources


def _format_hithink_probe_system_log(probe: dict) -> tuple[str, str]:
    status = str(probe.get("hithink_quote_probe") or "skip").strip()
    if status == "ok":
        return "info", "[网络] 同花顺盘中行情探针通过。"

    if status == "skip":
        detail = "未启用"
    elif status == "empty":
        detail = "返回空行情"
    elif status == "deadline":
        detail = "探针超时"
    elif status.startswith("fail:"):
        detail = sanitize_hithink_error(status[len("fail:") :]) or "未知异常"
    else:
        detail = sanitize_hithink_error(status) or "未知异常"

    if bool(probe.get("ok")):
        return "warn", f"[网络] 同花顺未通过，兼容回退可用：{detail}。"
    return "warn", f"[网络] 同花顺未通过，兼容回退不可用：{detail}。"


def _emit_hithink_probe_system_log(probe: dict) -> None:
    level, message = _format_hithink_probe_system_log(probe)
    try:
        from domains.runtime import domain_events as event_bus

        event_bus.sig_system_log.emit(level, message)
    except (AttributeError, RuntimeError, TypeError, ValueError) as exc:
        _log.debug("[网络] 同花顺探针系统日志发送失败: %s", exc)


def _cached_offline_quote_frames(provider, codes: tuple[str, ...]) -> dict:
    cache_lock = getattr(provider, "cache_lock", None)
    if cache_lock is None:
        return {}
    with cache_lock:
        cache_data = getattr(provider, "cache_data", None)
        if not isinstance(cache_data, dict):
            return {}
        return {code: cache_data.get(code) for code in codes if cache_data.get(code) is not None}


def _warehouse_offline_quotes(provider, codes: list[str]) -> dict:
    try:
        warehouse_getter = getattr(provider, "_get_market_data_warehouse", None)
        warehouse = (
            warehouse_getter()
            if callable(warehouse_getter)
            else getattr(provider, "market_data_warehouse", None)
        )
        result = read_latest_quotes(warehouse, codes) if warehouse is not None else None
    except _WAREHOUSE_QUOTE_ERRORS:
        return {}
    if warehouse is None:
        return {}
    if result is None or not result.status.ok or not isinstance(result.data, dict):
        return {}
    try:
        provider._last_market_data_source_status = result.status.to_dict()
    except (AttributeError, RuntimeError, TypeError, ValueError):
        pass
    return result.data


def _missing_offline_quotes(provider, codes: list[str]) -> dict:
    result = _warehouse_offline_quotes(provider, codes)
    missing_codes = [code for code in codes if code not in result]
    if not missing_codes:
        return result
    batch_reader = getattr(provider, "get_data_batch", None)
    if callable(batch_reader):
        frames = batch_reader(missing_codes) or {}
        result.update(build_offline_quotes(missing_codes, frames.get))
        return result
    result.update(build_offline_quotes(missing_codes, provider.get_data))
    return result


class TdxDataProviderRealtimeMixin:
    def is_online(self):
        return not self._offline

    def set_online_mode(self, online=True):
        from domains.runtime import domain_events as event_bus

        if online and self._offline:
            self._offline = False
            _log.info("[网络] ✅ 已切换到联网模式（同花顺实时行情主源）")
            event_bus.sig_network_status_changed.emit(True, "Online")
        elif not online and not self._offline:
            self._offline = True
            _log.info("[网络] 已切换到离线模式")
            event_bus.sig_network_status_changed.emit(False, "Offline")

    def force_reconnect_servers(self):
        """重置盘中实时行情状态，清理主源与回退源的冷却和错误标记。"""
        if self._offline:
            _log.info("[网络] 当前为离线模式，无法重置盘中实时行情连接。")
            return

        _log.info("[网络] 🌐 正在重置同花顺盘中实时行情连接状态...")

        # 清除主线程的 API 以防后续历史联网补全沿用旧连接
        if hasattr(self.thread_local, "api"):
            try:
                self.thread_local.api.disconnect()
            except (AttributeError, OSError, RuntimeError, TypeError) as _e:
                _log.debug(f"[网络] 断开旧 API 连接时异常: {_e}")
            delattr(self.thread_local, "api")
        self._rt_runtime_cooldown_until = 0.0
        self._rt_runtime_consecutive_failures = 0
        self._rt_runtime_last_error = ""
        self._rt_hithink_cooldown_until = 0.0
        self._rt_hithink_last_error = ""
        self._rt_eastmoney_cooldown_until = 0.0
        self._rt_eastmoney_last_error = ""
        self._rt_last_fallback_log_at = 0.0
        self._reset_realtime_runtime(
            "强制刷新同花顺盘中实时行情连接",
            log_warning=False,
            penalize_server=False,
        )

        _log.info("[网络] ✅ 同花顺主源及兼容回退的盘中实时行情状态已重置。")

    def test_network(self, timeout=3, *, return_probe=False):
        """在一个总截止时间内测试实时行情 HTTP 回退链路。"""
        inferred_trade_date = MarketCalendar.today("CN").strftime("%Y-%m-%d")
        timeout_sec = max(0.1, float(timeout or 3))
        started_at = time.monotonic()
        deadline = started_at + timeout_sec
        previous_timeout = float(getattr(self, "_rt_api_call_timeout_sec", 8.0) or 8.0)
        probe = {
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "timeout_sec": timeout_sec,
            "batch_size": int(getattr(self, "_rt_quote_batch_size", RT_QUOTE_BATCH_SIZE) or RT_QUOTE_BATCH_SIZE),
            "dedup_window_sec": float(
                getattr(self, "_rt_runtime_dedup_window_sec", RT_QUOTE_DEDUP_WINDOW_SEC) or RT_QUOTE_DEDUP_WINDOW_SEC
            ),
            "page_probe": "deferred",
            "quote_probe": "skip",
        }
        try:
            ok = False
            quote_failures = []
            probe["hithink_quote_probe"] = "skip"
            probe["eastmoney_quote_probe"] = "skip"
            probe["sina_quote_probe"] = "skip"
            probe["tencent_quote_probe"] = "skip"
            quote_sources = []
            if bool(getattr(self, "_rt_hithink_enabled", False)):
                quote_sources.append(("hithink", self._request_hithink_quote_batch))
            quote_sources.extend(
                (
                    ("eastmoney", self._request_eastmoney_quote_batch),
                    ("sina", self._request_sina_quote_batch),
                    ("tencent", self._request_tencent_quote_batch),
                )
            )
            for source_index, (source_name, requester) in enumerate(quote_sources):
                remaining_sec = deadline - time.monotonic()
                if remaining_sec <= 0:
                    probe[f"{source_name}_quote_probe"] = "deadline"
                    quote_failures.append(f"{source_name}:deadline")
                    break
                remaining_sources = len(quote_sources) - source_index
                source_timeout_sec = _network_probe_source_timeout(
                    remaining_sec,
                    remaining_sources,
                    primary=source_index == 0,
                )
                self._rt_api_call_timeout_sec = source_timeout_sec
                try:
                    quotes = call_with_supported_kwargs(
                        requester,
                        ["000001"],
                        inferred_trade_date,
                        cancellation_token=CancellationToken.with_timeout(source_timeout_sec),
                    )
                    source_ok = bool(quotes and quotes.get("000001"))
                    probe[f"{source_name}_quote_probe"] = "ok" if source_ok else "empty"
                    if source_ok:
                        ok = True
                        break
                except (
                    ConnectionError,
                    KeyError,
                    OSError,
                    RuntimeError,
                    TimeoutError,
                    TypeError,
                    ValueError,
                ) as quote_exc:
                    detail = (
                        sanitize_hithink_error(quote_exc)
                        if source_name == "hithink"
                        else summarize_probe_error(quote_exc)
                    )
                    probe[f"{source_name}_quote_probe"] = f"fail:{detail}"
                    quote_failures.append(f"{source_name}:{detail}")

            probe["quote_probe"] = "ok" if ok else (quote_failures[0] if quote_failures else "empty")
            probe["ok"] = ok
            probe["elapsed_ms"] = round((time.monotonic() - started_at) * 1000.0, 3)
            self._rt_last_network_probe = probe
            _emit_hithink_probe_system_log(probe)
            log_fn = _log.info if ok else _log.warning
            log_fn(
                f"[网络] 盘中行情探针{'通过' if ok else '失败'} "
                f"| page={probe['page_probe']} | quote={probe['quote_probe']} "
                f"| hithink={probe['hithink_quote_probe']} "
                f"| eastmoney={probe['eastmoney_quote_probe']} "
                f"| sina={probe['sina_quote_probe']} "
                f"| tencent={probe['tencent_quote_probe']} "
                f"| batch={probe['batch_size']} | dedup={probe['dedup_window_sec']:.1f}s"
            )
            return dict(probe) if return_probe else ok
        except (ConnectionError, KeyError, OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
            probe["quote_probe"] = f"fail:{summarize_probe_error(exc)}"
            probe["ok"] = False
            self._rt_last_network_probe = probe
            _emit_hithink_probe_system_log(probe)
            _log.warning(
                f"[网络] 盘中行情探针失败 "
                f"| page={probe['page_probe']} | push2={probe['quote_probe']} "
                f"| batch={probe['batch_size']} | dedup={probe['dedup_window_sec']:.1f}s"
            )
            _log.debug(f"[网络] 盘中实时行情连通性测试失败: {exc}")
            return dict(probe) if return_probe else False
        finally:
            self._rt_api_call_timeout_sec = previous_timeout

    def test_network_with_probe(self, timeout=3) -> dict:
        """返回本次实时行情探针的原子快照，供重置流程展示结果。"""
        result = self.test_network(timeout=timeout, return_probe=True)
        return dict(result) if isinstance(result, dict) else {"ok": bool(result)}

    def get_last_network_probe(self) -> dict:
        return dict(getattr(self, "_rt_last_network_probe", {}) or {})

    def get_all_valid_data(self):
        """返回缓存数据的浅拷贝（字典本身是副本，DataFrame 是引用共享）"""
        with self.cache_lock:
            return dict(self.cache_data)

    def _build_offline_quotes(self, codes):
        requested_codes = _normalize_offline_quote_codes(codes)
        if not requested_codes:
            return {}

        frames = _cached_offline_quote_frames(self, requested_codes)
        result = build_offline_quotes(requested_codes, frames.get)
        missing_codes = [code for code in requested_codes if code not in result]
        if missing_codes:
            result.update(_missing_offline_quotes(self, missing_codes))
        return result

    def _ensure_eastmoney_quote_state(self):
        ensure_eastmoney_quote_state(self)

    def _ensure_hithink_quote_state(self):
        ensure_hithink_quote_state(self)

    def _log_quote_fallback(
        self,
        message: str,
        *,
        interval_sec: float = 30.0,
        warning: bool = True,
    ):
        log_quote_fallback(
            self,
            message,
            interval_sec=interval_sec,
            warning=warning,
        )

    def _enter_eastmoney_cooldown(self, reason: str, cooldown_sec: float | None = None):
        enter_eastmoney_cooldown(
            self,
            reason,
            cooldown_sec=cooldown_sec,
            default_cooldown_sec=RT_EASTMONEY_COOLDOWN_SEC,
        )

    def _enter_hithink_cooldown(self, reason: str, cooldown_sec: float | None = None):
        enter_hithink_cooldown(
            self,
            reason,
            cooldown_sec=cooldown_sec,
            default_cooldown_sec=RT_EASTMONEY_COOLDOWN_SEC,
        )

    def _to_eastmoney_secid(self, code: str) -> str:
        return to_eastmoney_secid(code)

    def _request_eastmoney_quote_batch(self, codes, inferred_trade_date: str, *, cancellation_token=None):
        return request_eastmoney_quote_batch(
            self,
            codes,
            inferred_trade_date,
            cancellation_token=cancellation_token,
        )

    def _request_hithink_quote_batch(self, codes, inferred_trade_date: str, *, cancellation_token=None):
        return request_hithink_quote_batch(
            self,
            codes,
            inferred_trade_date,
            cancellation_token=cancellation_token,
        )

    def _request_sina_quote_batch(self, codes, inferred_trade_date: str, *, cancellation_token=None):
        return request_sina_quote_batch(
            self,
            codes,
            inferred_trade_date,
            cancellation_token=cancellation_token,
        )

    def _request_tencent_quote_batch(self, codes, inferred_trade_date: str, *, cancellation_token=None):
        return request_tencent_quote_batch(
            self,
            codes,
            inferred_trade_date,
            cancellation_token=cancellation_token,
        )

    def _fetch_eastmoney_quotes_with_split_retry(
        self,
        codes,
        inferred_trade_date: str,
        min_batch_size: int,
        *,
        cancellation_token=None,
    ):
        return fetch_eastmoney_quotes_with_split_retry(
            self,
            codes,
            inferred_trade_date,
            min_batch_size,
            cancellation_token=cancellation_token,
        )

    def fetch_realtime_quotes_batch(self, codes, _retry_once=True, *, cancellation_token=None):
        """Fetch realtime quotes using the configured batch size."""
        return fetch_realtime_quotes_batch_runtime(
            self,
            codes,
            log=_log,
            batch_size_default=RT_QUOTE_BATCH_SIZE,
            min_batch_size_default=RT_QUOTE_MIN_BATCH_SIZE,
            batch_pause_default=RT_QUOTE_BATCH_PAUSE_SEC,
            cancellation_token=cancellation_token,
        )

    def build_realtime_df(self, code, quote):
        """将盘中图表拼接委托给实时行情服务。"""
        return self._get_realtime_quote_provider().build_realtime_df(code, quote)
