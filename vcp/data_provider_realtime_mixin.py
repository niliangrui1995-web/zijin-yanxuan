import time
import urllib.error
import urllib.request

from core.logger import get_logger
from core.market_calendar import MarketCalendar
from infra.http_safety import urlopen_https
from vcp.data_provider_local import build_offline_quotes
from vcp.data_provider_quotes import (
    coerce_quote_number,
    ensure_eastmoney_quote_state,
    enter_eastmoney_cooldown,
    log_quote_fallback,
    register_eastmoney_success,
    request_eastmoney_quote_batch,
    request_sina_quote_batch,
    request_tencent_quote_batch,
    to_eastmoney_secid,
    to_sina_symbol,
    to_tencent_symbol,
)
from vcp.data_provider_realtime import fetch_eastmoney_quotes_with_split_retry, summarize_probe_error
from vcp.data_provider_realtime import fetch_realtime_quotes_batch as fetch_realtime_quotes_batch_runtime

_log = get_logger(__name__)

RT_QUOTE_BATCH_SIZE = 20
RT_QUOTE_MIN_BATCH_SIZE = 5
RT_QUOTE_BATCH_PAUSE_SEC = 0.12
RT_QUOTE_DEDUP_WINDOW_SEC = 8.5
RT_EASTMONEY_COOLDOWN_SEC = 120.0


class TdxDataProviderRealtimeMixin:
    def is_online(self):
        return not self._offline

    def set_online_mode(self, online=True):
        from domains.runtime import domain_events as event_bus

        if online and self._offline:
            self._offline = False
            _log.info("[网络] ✅ 已切换到联网模式（东方财富实时行情）")
            event_bus.sig_network_status_changed.emit(True, "Online")
        elif not online and not self._offline:
            self._offline = True
            _log.info("[网络] 已切换到离线模式")
            event_bus.sig_network_status_changed.emit(False, "Offline")

    def force_reconnect_servers(self):
        """重置东方财富实时行情状态，清理冷却与错误标记。"""
        if self._offline:
            _log.info("[网络] 当前为离线模式，无法重置东方财富实时行情连接。")
            return

        _log.info("[网络] 🌐 正在重置东方财富实时行情连接状态...")

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
        self._reset_realtime_runtime(
            "强制刷新东方财富实时行情连接",
            log_warning=False,
            penalize_server=False,
        )

        _log.info("[网络] ✅ 东方财富实时行情状态已重置。")

    def test_network(self, timeout=3):
        """测试东方财富实时行情 HTTP 链路是否可用。"""
        inferred_trade_date = MarketCalendar.today("CN").strftime("%Y-%m-%d")
        timeout_sec = float(timeout or 3)
        previous_timeout = float(getattr(self, "_rt_api_call_timeout_sec", 8.0) or 8.0)
        probe = {
            "checked_at": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
            "timeout_sec": timeout_sec,
            "batch_size": int(getattr(self, "_rt_quote_batch_size", RT_QUOTE_BATCH_SIZE) or RT_QUOTE_BATCH_SIZE),
            "dedup_window_sec": float(
                getattr(self, "_rt_runtime_dedup_window_sec", RT_QUOTE_DEDUP_WINDOW_SEC) or RT_QUOTE_DEDUP_WINDOW_SEC
            ),
            "page_probe": "skip",
            "quote_probe": "skip",
        }
        self._rt_api_call_timeout_sec = timeout_sec
        try:
            try:
                req = urllib.request.Request(
                    "https://quote.eastmoney.com/center/gridlist.html#hs_a_board",
                    headers={
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
                        "Referer": "https://quote.eastmoney.com/",
                        "Connection": "close",
                    },
                )
                resp = urlopen_https(req, timeout=timeout_sec)
                try:
                    payload = resp.read(256)
                finally:
                    try:
                        resp.close()
                    except (AttributeError, OSError, RuntimeError, TypeError):
                        pass
                probe["page_probe"] = "ok" if payload else "empty"
            except (ConnectionError, OSError, TimeoutError, urllib.error.URLError, ValueError) as page_exc:
                probe["page_probe"] = f"fail:{summarize_probe_error(page_exc)}"

            ok = False
            quote_failures = []
            probe["eastmoney_quote_probe"] = "skip"
            probe["sina_quote_probe"] = "skip"
            probe["tencent_quote_probe"] = "skip"
            for source_name, requester in (
                ("eastmoney", self._request_eastmoney_quote_batch),
                ("sina", self._request_sina_quote_batch),
                ("tencent", self._request_tencent_quote_batch),
            ):
                try:
                    quotes = requester(["000001"], inferred_trade_date)
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
                    detail = summarize_probe_error(quote_exc)
                    probe[f"{source_name}_quote_probe"] = f"fail:{detail}"
                    quote_failures.append(f"{source_name}:{detail}")

            probe["quote_probe"] = "ok" if ok else (quote_failures[0] if quote_failures else "empty")
            probe["ok"] = ok
            self._rt_last_network_probe = probe
            log_fn = _log.info if ok else _log.warning
            log_fn(
                f"[网络] 东方财富探针{'通过' if ok else '失败'} "
                f"| page={probe['page_probe']} | quote={probe['quote_probe']} "
                f"| eastmoney={probe['eastmoney_quote_probe']} "
                f"| sina={probe['sina_quote_probe']} "
                f"| tencent={probe['tencent_quote_probe']} "
                f"| batch={probe['batch_size']} | dedup={probe['dedup_window_sec']:.1f}s"
            )
            return ok
        except (ConnectionError, KeyError, OSError, RuntimeError, TimeoutError, TypeError, ValueError) as exc:
            probe["quote_probe"] = f"fail:{summarize_probe_error(exc)}"
            probe["ok"] = False
            self._rt_last_network_probe = probe
            _log.warning(
                f"[网络] 东方财富探针失败 "
                f"| page={probe['page_probe']} | push2={probe['quote_probe']} "
                f"| batch={probe['batch_size']} | dedup={probe['dedup_window_sec']:.1f}s"
            )
            _log.debug(f"[网络] 东方财富实时行情连通性测试失败: {exc}")
            return False
        finally:
            self._rt_api_call_timeout_sec = previous_timeout

    def get_last_network_probe(self) -> dict:
        return dict(getattr(self, "_rt_last_network_probe", {}) or {})

    def get_all_valid_data(self):
        """返回缓存数据的浅拷贝（字典本身是副本，DataFrame 是引用共享）"""
        with self.cache_lock:
            return dict(self.cache_data)

    def _build_offline_quotes(self, codes):
        return build_offline_quotes(codes, self.get_data)

    def _ensure_eastmoney_quote_state(self):
        ensure_eastmoney_quote_state(self)

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

    def _register_eastmoney_success(self):
        register_eastmoney_success(self)

    def _to_eastmoney_secid(self, code: str) -> str:
        return to_eastmoney_secid(code)

    @staticmethod
    def _to_sina_symbol(code: str) -> str:
        return to_sina_symbol(code)

    @staticmethod
    def _to_tencent_symbol(code: str) -> str:
        return to_tencent_symbol(code)

    @staticmethod
    def _coerce_quote_number(value) -> float:
        return coerce_quote_number(value)

    def _request_eastmoney_quote_batch(self, codes, inferred_trade_date: str):
        return request_eastmoney_quote_batch(self, codes, inferred_trade_date)

    def _request_sina_quote_batch(self, codes, inferred_trade_date: str):
        return request_sina_quote_batch(self, codes, inferred_trade_date)

    def _request_tencent_quote_batch(self, codes, inferred_trade_date: str):
        return request_tencent_quote_batch(self, codes, inferred_trade_date)

    def _fetch_eastmoney_quotes_with_split_retry(
        self,
        codes,
        inferred_trade_date: str,
        min_batch_size: int,
    ):
        return fetch_eastmoney_quotes_with_split_retry(
            self,
            codes,
            inferred_trade_date,
            min_batch_size,
        )

    def fetch_realtime_quotes_batch(self, codes, _retry_once=True):
        """Fetch realtime quotes using the configured batch size."""
        return fetch_realtime_quotes_batch_runtime(
            self,
            codes,
            log=_log,
            batch_size_default=RT_QUOTE_BATCH_SIZE,
            min_batch_size_default=RT_QUOTE_MIN_BATCH_SIZE,
            batch_pause_default=RT_QUOTE_BATCH_PAUSE_SEC,
        )

    def build_realtime_df(self, code, quote):
        """将盘中图表拼接委托给实时行情服务。"""
        return self._get_realtime_quote_provider().build_realtime_df(code, quote)
