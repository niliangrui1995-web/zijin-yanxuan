# data_provider.py - 数据中台（动态测速池） # 从 vcp_hunter.pyw 提取 TdxDataProvider 类，零逻辑变更
import os
import time
import random
import pickle
import threading
import concurrent.futures
import pandas as pd
from datetime import datetime

from vcp.constants import (
    CACHE_DIR, CACHE_VERSION, MAX_HISTORY_BARS, INCREMENTAL_BARS,
    MARKET_SYNC_WORKERS, DATE_FMT,
)
from vcp.utils import _load_tdx_local_config, read_tdx_day_file

from core.logger import get_logger
_log = get_logger(__name__)


def _run_blocking_call_with_timeout(fn, timeout_sec: float, timeout_message: str):
    """用守护线程包裹阻塞网络调用，超时后快速失败，避免整条链路卡死。"""
    if timeout_sec is None or timeout_sec <= 0:
        return fn()

    done = threading.Event()
    state = {}

    def _runner():
        try:
            state["result"] = fn()
        except Exception as exc:
            state["error"] = exc
        finally:
            done.set()

    worker = threading.Thread(
        target=_runner,
        daemon=True,
        name="tdx-blocking-call-guard",
    )
    worker.start()

    if not done.wait(timeout_sec):
        raise TimeoutError(timeout_message)

    if "error" in state:
        raise state["error"]
    return state.get("result")


class TdxDataProvider:
    def __init__(self, is_trading_day=None, offline=False, offline_mode=None):
        from pytdx.hq import TdxHq_API
        # Backward compatibility: keep accepting legacy keyword `offline_mode`.
        if offline_mode is not None:
            offline = bool(offline_mode)
        self.TdxHq_API = TdxHq_API
        self.cache_file = os.path.join(CACHE_DIR, 'vcp_tdx_cache_adj.pkl')
        self.cache_data = {}
        self.cache_lock = threading.Lock()
        self.thread_local = threading.local()
        # DataLoader 防抖微缓存 (仅对实时行情生效, 生命期 500ms)
        self._rt_quote_cache = {}
        self._rt_quote_time = {}
        self._rt_quote_lock = threading.Lock()
        self._rt_api_call_timeout_sec = 8.0
        self.code2name = {}
        self._offline = offline
        self._is_trading_day = is_trading_day if callable(is_trading_day) else (lambda d=None: datetime.now().weekday() < 5)
        self.tdx_vipdoc = _load_tdx_local_config()
        # 预加载本地 gbbq (股本变迁/除权除息) 数据
        self._local_gbbq = {}  # {code: DataFrame}
        if self.tdx_vipdoc:
            _log.info(f"[启动] 已启用通达信本地K线数据: {self.tdx_vipdoc}")
            self._load_local_gbbq()
        if offline:
            _log.warning("[启动] 离线模式启动：跳过服务器测速，使用本地数据")
            self.server_pool = []
        else:
            _log.info("[启动] 正在启动动态测速池...")
            self.server_pool = self._auto_select_best_servers()

    def _downcast_memory(self):
        """将 cache_data 中所有 float64 列降为 float32，节省约 50% 数值内存

        float32 精度约 7 位有效数字（如 25.360001），
        对于股价（最高不过万元级）完全足够。
        分批处理并释放 GIL，避免阻塞 UI 线程。
        """
        # 幂等保护：Parquet 加载路径和 sync_market_data 会连续调用两次，
        # 第二次数据已经是 float32 无需再遍历
        if getattr(self, '_downcast_done', False):
            return
        import time as _time
        count = 0
        for i, (code, df) in enumerate(list(self.cache_data.items())):
            if df is None:
                continue
            changed = False
            for col in df.columns:
                if df[col].dtype == 'float64':
                    df[col] = df[col].astype('float32')
                    changed = True
            if changed:
                count += 1
            # 每 50 只释放一次 GIL，避免长时间霸占导致 UI 卡顿
            if i % 50 == 0 and i > 0:
                _time.sleep(0)
        self._downcast_done = True
        if count > 0:
            _log.info(f"[内存优化] 已将 {count} 只标的数据降精度 (float64→float32)")


    def _auto_select_best_servers(self):
        """轻量级测速，避免大量并发线程导致 PyQT6 C++ 内存崩溃"""
        candidates = [
            ('119.147.212.81',7709),('124.71.187.122',7709),('106.120.74.86',7709),
            ('122.51.120.217',7709),('121.36.54.217',7709),('124.71.85.110',7709),
            ('114.80.149.19',7709),('114.80.149.22',7709),('114.80.149.84',7709),
            ('115.238.56.198',7709),('115.238.90.165',7709),('117.184.140.156',7709),
            ('119.147.164.60',7709),('123.125.108.23',7709),('123.125.108.24',7709),
            ('180.153.18.17',7709),('180.153.18.170',7709),('180.153.18.171',7709),
            ('218.108.47.69',7709),('218.108.50.178',7709),('218.108.98.244',7709),
            ('218.75.126.9',7709),('218.9.148.108',7709),('221.194.181.176',7709),
        ]
        import random
        import socket
        import time
        random.shuffle(candidates)
        test_list = candidates[:10]  # Just test 10 random servers sequentially
        
        results = []
        for ip, port in test_list:
            try:
                start = time.time()
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                s.connect((ip, port))
                s.close()
                results.append((ip, port, time.time() - start))
            except Exception as _e:
                _log.debug(f"[网络] 测速节点 {ip}:{port} 连接超时/失败: {_e}")
                
        best = sorted(results, key=lambda x: x[2])[:5]
        if not best: best = [(ip, port, 999) for ip, port in candidates[:5]]
        
        _log.info("[OK] 轻量级动态测速完成，最优服务器（延迟 ms）：")
        for i, (ip, port, lat) in enumerate(best, 1):
            _log.info(f"   {i}. {ip}:{port}  -> {lat*1000:.0f}ms")
            
        return [(ip, port) for ip, port, _ in best]

    def _load_local_gbbq(self, force=False):
        """Load local gbbq ex-rights/ex-dividends data into memory."""

        if not self.tdx_vipdoc:
            return
        # gbbq 文件位于通达信安装目录 T0002/hq_cache/gbbq
        tdx_root = os.path.dirname(self.tdx_vipdoc)  # vipdoc 的父目录即为通达信根目录
        gbbq_path = os.path.join(tdx_root, 'T0002', 'hq_cache', 'gbbq')
        if not os.path.exists(gbbq_path):
            _log.info(f"[数据中台] 本地 gbbq 文件不存在: {gbbq_path}")
            return
        gbbq_mtime = os.path.getmtime(gbbq_path)
        
        # pkl 缓存路径
        gbbq_pkl = os.path.join(CACHE_DIR, 'gbbq_parsed.pkl')
        
        # 非强制模式: 优先加载 pkl 缓存(启动时走这条路径,秒加载)
        if not force and os.path.exists(gbbq_pkl):
            try:
                with open(gbbq_pkl, 'rb') as f:
                    cached = pickle.load(f)
                if cached.get('mtime') and cached.get('mtime') != gbbq_mtime:
                    raise ValueError("gbbq 文件已更新，需要重新解析缓存")
                self._local_gbbq = cached['data']
                _log.info(f"[缓存] 已加载本地 gbbq 缓存: {len(self._local_gbbq)} 个代码, {cached.get('records', '?')} 条记录")
                return
            except Exception as _e:
                _log.debug(f"[缓存] gbbq pkl 缓存损坏或版本不匹配，将重新解析: {_e}")
        try:
            from pytdx.reader import GbbqReader
            reader = GbbqReader()
            df = reader.get_df(gbbq_path)
            if df is None or df.empty:
                _log.info("[数据中台] gbbq 文件解析为空")
                return
            # 只保留除权除息记录。
            xdxr = df[df['category'] == 1].copy()
            # 按股票代码分组缓存到内存。
            for code, group in xdxr.groupby('code'):
                self._local_gbbq[str(code)] = group
            _log.info(f"[缓存] 已解析本地 gbbq 原始文件: {len(self._local_gbbq)} 个代码, {len(xdxr)} 条记录")
            # 保存 pkl 缓存，后续启动可直接复用解析结果。
            try:
                os.makedirs(CACHE_DIR, exist_ok=True)
                with open(gbbq_pkl, 'wb') as f:
                    pickle.dump(
                        {'data': self._local_gbbq, 'mtime': gbbq_mtime, 'records': len(xdxr)},
                        f,
                        protocol=4,
                    )
                _log.info(f"[数据中台] gbbq pkl 缓存已保存 -> {gbbq_pkl}")
            except Exception as e:
                _log.error(f"[数据中台] gbbq pkl 缓存保存失败: {e}")
        except Exception as e:
            _log.error(f"[数据中台] gbbq 加载失败(不影响联网复权): {e}")
    def _get_market_code(self, stock_code):
        stock_code = str(stock_code)
        return 1 if stock_code.startswith(('6', '9')) else 0

    def _is_before_930_today(self):
        now = datetime.now()
        return now.hour < 9 or (now.hour == 9 and now.minute < 30)

    def _is_after_1500_today(self):
        return datetime.now().hour >= 15

    def _tdx_day_path(self, code):
        code = str(code).strip()
        if code.startswith(('6', '9')):
            sub = os.path.join('sh', 'lday', f'sh{code}.day')
        else:
            sub = os.path.join('sz', 'lday', f'sz{code}.day')
        return os.path.join(self.tdx_vipdoc, sub)

    def _fetch_from_local_tdx(self, code):
        if not self.tdx_vipdoc:
            return None
        path = self._tdx_day_path(code)
        df = read_tdx_day_file(path)
        # read_tdx_day_file 已直接返回 Pandas DataFrame，无需再做 .to_pandas() 转换
        if df is not None and 'datetime' in df.columns:
            df = df.set_index('datetime')
        
        if df is None or df.empty:
            return None
        if len(df) > MAX_HISTORY_BARS:
            df = df.iloc[-MAX_HISTORY_BARS:]
        # 离线模式下优先使用本地 gbbq 做前复权
        if self._offline or not self.server_pool:
            if self._local_gbbq:
                df = self._apply_forward_adjustment(None, self._get_market_code(code), code, df)
            elif not getattr(self, '_offline_warn_printed', False):
                _log.warning("[警告] 本地 gbbq 缓存不可用，前复权一致性可能下降")
                _log.info("[提示] 请确认通达信目录下存在 T0002/hq_cache/gbbq 文件")
                self._offline_warn_printed = True
        return df

    def _get_thread_api(self):
        if not hasattr(self.thread_local, "api"):
            api = self.TdxHq_API(auto_retry=True, heartbeat=True)
            for ip, port in self.server_pool:
                try:
                    if api.connect(ip, port, time_out=5) and api.get_security_count(0) > 0:
                        break
                except (TimeoutError, OSError, ConnectionError, ValueError) as e:
                    _log.debug(f"[网络] 测速连接节点失败 {ip}:{port} - {e}")
                    continue
                except Exception as e:
                    # pytdx 会抛出自定义异常类型，按网络失败处理并尝试下一个节点
                    _log.debug(f"[网络] 测速连接节点失败 {ip}:{port} - {e}")
                    continue
            self.thread_local.api = api
        return self.thread_local.api

    def _reset_thread_api(self, reason: str = ""):
        if hasattr(self.thread_local, 'api'):
            try:
                self.thread_local.api.disconnect()
            except Exception as _e:
                _log.debug(f"[网络] 断开旧 API 连接时异常: {_e}")
            try:
                delattr(self.thread_local, 'api')
            except Exception:
                pass
        if reason:
            _log.warning(f"[网络] {reason}")

    def _apply_forward_adjustment(self, api, market, code, df):
        """Apply forward adjustment using local gbbq data first, then fall back to online API."""
        try:
            import polars as pl
            xdxr_df = None
            # 优先使用本地 gbbq 数据(无需联网)
            if code in self._local_gbbq:
                local = self._local_gbbq[code]
                xdxr_df = local.copy()
                # gbbq 字段含: datetime(YYYYMMDD整数), hongli_panqianliutong(红利), songgu_qianzongguben(送股)
                xdxr_df['dt'] = pd.to_datetime(xdxr_df['datetime'].astype(str), format='%Y%m%d', errors='coerce').dt.date
                xdxr_df = xdxr_df.dropna(subset=['dt'])
                xdxr_df = xdxr_df.set_index('dt').sort_index(ascending=False)
            elif api is not None:
                # fallback: 联网获取 xdxr_info
                xdxr_data = api.get_xdxr_info(market, code)
                if not xdxr_data:
                    return df
                xdxr_df = pd.DataFrame(xdxr_data)
                xdxr_df = xdxr_df[xdxr_df['category'] == 1]
                if xdxr_df.empty:
                    return df
                xdxr_df['dt'] = pd.to_datetime(xdxr_df[['year', 'month', 'day']].astype(str).agg('-'.join, axis=1)).dt.date
                xdxr_df = xdxr_df.set_index('dt').sort_index(ascending=False)
            else:
                # 既无本地 gbbq 也无 API -> 无法复权
                return df

            if xdxr_df is None or xdxr_df.empty:
                return df

            # 纯 Pandas 复权（不用 Polars），避免多线程并发时 PyArrow C++ 竞态段错误
            work_df = df.reset_index() if df.index.name == 'datetime' else df.copy()

            # 将 datetime 列统一为 date 类型，用于与除权日比较
            if 'datetime' in work_df.columns:
                dt_col = pd.to_datetime(work_df['datetime']).dt.date
            else:
                dt_col = None

            for i in range(len(xdxr_df)):
                row = xdxr_df.iloc[i]
                if 'songgu_qianzongguben' in row.index:
                    # 本地 gbbq 格式
                    sz = float(row.get('songgu_qianzongguben', 0) or 0) / 10.0
                    fh = float(row.get('hongli_panqianliutong', 0) or 0) / 10.0
                else:
                    # API 格式
                    sz = (float(row.get('songgu', 0) or 0) + float(row.get('houzhen', 0) or 0)) / 10.0
                    fh = float(row.get('fenhong', 0) or 0) / 10.0

                dt = xdxr_df.index[i]
                if isinstance(dt, pd.Timestamp):
                    dt = dt.date()
                elif isinstance(dt, str):
                    from datetime import datetime as dt_sys
                    dt = dt_sys.strptime(dt, "%Y-%m-%d").date()

                if dt_col is None:
                    continue

                # 找到除权日之前的所有行，对价格做前复权
                mask = dt_col < dt
                if not mask.any():
                    continue

                for c in ['open', 'high', 'low', 'close']:
                    if c in work_df.columns:
                        work_df.loc[mask, c] = (work_df.loc[mask, c] - fh) / (1 + sz)
                for vc in ['vol', 'volume']:
                    if vc in work_df.columns:
                        work_df.loc[mask, vc] = work_df.loc[mask, vc] * (1 + sz)

            # 返回 Pandas DataFrame（与输入一致）
            if 'datetime' in work_df.columns:
                work_df['datetime'] = pd.to_datetime(work_df['datetime'])
                work_df = work_df.set_index('datetime')
            return work_df
        except Exception as e:
            _log.error(f"[数据中台] 前复权计算异常: {e}", exc_info=True)
            raise ValueError(f"除权除息因子计算失败: {e}") from e

    def _fetch_standard_data(self, api, code, count=MAX_HISTORY_BARS):
        import polars as pl
        market = self._get_market_code(code)
        if self.tdx_vipdoc:
            local_df = self._fetch_from_local_tdx(code)
            if local_df is not None and len(local_df) >= 250:
                try:
                    local_df = self._apply_forward_adjustment(api, market, code, local_df)
                    if 'vol' in local_df.columns:
                        local_df = local_df.rename({'vol': 'volume'})
                    return local_df
                except Exception as e:
                    _log.error(f"[数据中台] 本地 {code} 复权失败，改用网络: {e}")
            elif local_df is not None and len(local_df) < 250:
                _log.info(f"[缓存] 本地日线 {code}: 共 {len(local_df)} 条")
            # 修复: .height 是 Polars 属性，Pandas 无此属性，统一用 len()
            elif self.tdx_vipdoc and (local_df is None or len(local_df) == 0):
                _log.error(f"[数据中台] 本地日线 {code} 异常，改用网络")
        for _ in range(2):
            try:
                data = api.get_security_bars(9, market, code, 0, count)
                if data and len(data) > 0:
                    df = pl.DataFrame(data)
                    # Convert 'datetime' (e.g. "2024-05-12 15:00") to Date
                    if 'datetime' in df.columns:
                        if df.schema['datetime'] != pl.Date:
                            df = df.with_columns(
                                pl.col('datetime').str.strptime(pl.Datetime, "%Y-%m-%d %H:%M", strict=False)
                                .cast(pl.Date)
                            ).sort('datetime', descending=False)
                    # 联网路径也需要转成 Pandas 再传给 _apply_forward_adjustment
                    # 因为 _apply_forward_adjustment 已改为纯 Pandas 实现
                    if hasattr(df, 'to_pandas'):
                        df = df.to_pandas()
                        if 'datetime' in df.columns:
                            df = df.set_index('datetime')
                    df = self._apply_forward_adjustment(api, market, code, df)
                    if 'vol' in df.columns:
                        df = df.rename(columns={'vol': 'volume'})
                    return df
            except ValueError as ve: raise ve
            except Exception as e:
                _log.error(f"[数据中台] 拉取 {code} 历史数据失败: {e}")
        return None

    def get_all_codes(self):
        from core.data_store import DataStore
        if self._offline:
            cached = DataStore().load_json("vcp_code_names")
            if cached:
                _log.info(f"[离线模式] 从名称缓存读取 {len(cached)} 只标的（含股票名称）")
                return cached
            if self.tdx_vipdoc:
                return self._get_codes_from_vipdoc()
            return {}

        api = self._get_thread_api()
        stocks = {}
        for market in [0, 1]:
            count = api.get_security_count(market)
            if not count:
                continue
            for i in range(0, count, 1000):
                batch = api.get_security_list(market, i)
                if batch:
                    for s in batch:
                        code, name = s['code'], s['name']
                        if 'ST' in name: continue
                        if market == 1 and code.startswith(('60', '68')): stocks[code] = name
                        elif market == 0 and code.startswith(('00', '30')): stocks[code] = name
        if stocks:
            try:
                DataStore().save_json("vcp_code_names", stocks)
                _log.info(f"[数据中台] 已保存 {len(stocks)} 只标的名称缓存至 SQLite")
            except Exception as e:
                _log.error(f"[数据中台] 名称缓存保存失败: {e}")
        return stocks

    def _get_codes_from_vipdoc(self):
        stocks = {}
        from core.data_store import DataStore
        name_map = DataStore().load_json("vcp_code_names") or {}
            
        # --- 曾用名/新名 人工热修复映射册 ---
        # 防止因 pytdx 证券列表缓存不及时或本地 JSON 始终未刷新导致的名称滞后
        MANUAL_NAME_ALIASES = {
            '603196': '璞源材料'
        }
            
        for sub, prefix in [('sh/lday', 'sh'), ('sz/lday', 'sz')]:
            lday_dir = os.path.join(self.tdx_vipdoc, sub.replace('/', os.sep))
            if not os.path.isdir(lday_dir):
                continue
            for fname in os.listdir(lday_dir):
                if not fname.endswith('.day'):
                    continue
                code = fname[2:-4]
                # 优先使用缓存名称，无缓存则用代码占位
                display_name = name_map.get(code, code)
                
                # 若命中热修复库，则强行覆写最新名称
                if code in MANUAL_NAME_ALIASES:
                    display_name = MANUAL_NAME_ALIASES[code]
                    
                if prefix == 'sh' and code.startswith(('60', '68')):
                    stocks[code] = display_name
                elif prefix == 'sz' and code.startswith(('00', '30')):
                    stocks[code] = display_name
        has_names = sum(1 for c, n in stocks.items() if c != n)
        _log.info(f"[离线模式] 已从 vipdoc 扫描 {len(stocks)} 只标的（其中 {has_names} 只有名称）")
        return stocks

    def _worker_fetch(self, code, force_refresh, existing_df):
        if self._offline:
            try:
                if self.tdx_vipdoc:
                    local_df = self._fetch_from_local_tdx(code)
                    if local_df is not None and len(local_df) >= 250:
                        # 修复: 兼容 Polars 和 Pandas 两种 rename API
                        if 'vol' in (local_df.columns if hasattr(local_df, 'columns') else []):
                            if hasattr(local_df, 'to_pandas'):
                                local_df = local_df.rename({'vol': 'volume'})
                            else:
                                local_df.rename(columns={'vol': 'volume'}, inplace=True)
                        return code, local_df, "OK"
                    elif local_df is not None:
                        return code, None, "次新股/上市不足250天"
                return code, None, "offline data missing"
            except Exception as e:
                return code, None, f"本地读取异常: {e}"

        time.sleep(random.uniform(0.05, 0.15))
        api = self._get_thread_api()
        try:
            if existing_df is not None and not force_refresh:
                import pandas as pd
                if not isinstance(existing_df, pd.DataFrame):
                    if hasattr(existing_df, 'to_pandas'):
                        existing_df = existing_df.to_pandas()
                        if 'datetime' in existing_df.columns:
                            existing_df = existing_df.set_index('datetime')
                            
                new = self._fetch_standard_data(api, code, count=INCREMENTAL_BARS)
                if new is not None:
                    import polars as pl
                    if isinstance(new, pl.DataFrame):
                        new = new.to_pandas()
                        if 'datetime' in new.columns:
                            new = new.set_index('datetime')

                    last_existing = existing_df.index.max()
                    first_new = new.index.min()
                    gap_days = (first_new - last_existing).days
                    if gap_days > 10:
                        df = self._fetch_standard_data(api, code, count=MAX_HISTORY_BARS)
                        if df is not None:
                            if len(df) >= 250:
                                return code, df, "OK"
                            return code, None, "次新股/上市不足250天"
                        return code, None, "全量下载超时"
                    combined = pd.concat([existing_df, new])
                    return code, combined[~combined.index.duplicated(keep='last')].iloc[-MAX_HISTORY_BARS:], "OK"
                return code, None, "增量下载超时"
            else:
                df = self._fetch_standard_data(api, code, count=MAX_HISTORY_BARS)
                if df is not None:
                    if len(df) >= 250: return code, df, "OK"
                    else: return code, None, "次新股/上市不足250天"
                return code, None, "全量下载超时"
        except ValueError as ve: return code, None, str(ve)
        except Exception as e:
            _log.error(f"[数据中台] {code} 标的数据抓取发生异常: {e}")
            return code, None, "底层结构异常/长期停牌"

    def load_cache_from_disk(self):
        """Load disk cache into memory and return the cache date string.

        优先尝试 Parquet 缓存（体积更小、加载更快），失败时回退 pkl。
        """
        # ---- 优先尝试 Parquet 快速路径 ----
        try:
            from vcp.polars_engine import load_cache_parquet
            result = load_cache_parquet()
            if result is not None:
                loaded_data, last_date = result
                if loaded_data and isinstance(loaded_data, dict):
                    with self.cache_lock:
                        self.cache_data = loaded_data
                    # Parquet 数据在保存前已经过 _downcast_memory 降精度，无需重复执行
                    _log.info(f"\n[数据中台] Parquet 快速加载: {len(self.cache_data)} 只标的 (缓存日期: {last_date})")
                    return last_date
        except ImportError:
            pass  # polars 未安装，回退 pkl
        except Exception as e:
            _log.error(f"[数据中台] Parquet 加载失败，回退 pkl: {e}")
        # ---- 回退: pickle 加载 ----
        if not os.path.exists(self.cache_file):
            return ""
        try:
            file_size = os.path.getsize(self.cache_file)
            if file_size > 500 * 1024 * 1024:
                raise ValueError(f"缓存文件过大({file_size/1024/1024:.1f}MB)，可能已损坏")
            with open(self.cache_file, 'rb') as f:
                pkg = pickle.load(f)
            if not isinstance(pkg, dict) or 'version' not in pkg or 'data' not in pkg:
                raise ValueError("缓存版本不匹配")
            version = pkg.get('version', 0)
            if version != CACHE_VERSION:
                _log.warning(f"\n[缓存] 缓存版本不匹配(version={version})，准备强制重建")
                self.cache_data = {}
                return ""
            loaded_data = pkg.get('data', {})
            if not isinstance(loaded_data, dict):
                raise ValueError("缓存结构异常: data 字段不是字典")
            with self.cache_lock:
                self.cache_data = loaded_data
            self._downcast_memory()
            last_date = pkg.get('date', '')
            _log.info(f"\n[数据中台] pkl 回退加载: {len(self.cache_data)} 只标的 (缓存日期: {last_date})")
            return last_date
        except (pickle.UnpicklingError, ValueError, TypeError, KeyError) as e:
            self.cache_data = {}
            _log.error(f"\n[数据中台] 读取缓存文件失败(格式异常)，已丢弃旧缓存并准备重建。原因: {e}")
            try:
                os.rename(self.cache_file, self.cache_file + '.corrupted')
                _log.error("[缓存] 缓存文件已损坏或无法读取")
            except Exception as _e:
                _log.debug(f"[数据中台] 损坏缓存文件重命名失败: {_e}")
            return ""
        except Exception as e:
            self.cache_data = {}
            _log.error(f"\n[数据中台] 读取缓存文件失败，已丢弃旧缓存并准备重建。原因: {e}", exc_info=True)
            return ""

    def sync_market_data(self, codes, force_refresh=False, progress_callback=None):
        today = datetime.now().strftime(DATE_FMT)
        if not self.cache_data:
            last_date = self.load_cache_from_disk()
        else:
            last_date = today if self.cache_data else ""

        if last_date == today and not force_refresh: return True
        if not force_refresh and self._is_before_930_today() and self._is_trading_day() and last_date:
            _log.info(f"[缓存] 最近一次更新早于 09:30（{last_date}），继续沿用上一交易日快照")
            return True

        total = len(codes)
        # 为什么离线只用 20 线程？50 线程同时持有 DataFrame 内存峰值太高，容易触发 Windows OOM 闪退
        workers = 20 if self._offline else MARKET_SYNC_WORKERS
        _log.info(f"\n[数据中台] 阶段1: 同步日线 -> 目标 {total} 只 | 线程数 {workers} | {'离线本地' if self._offline else ('强制覆盖' if force_refresh else '增量/缓存')}")
        if self.tdx_vipdoc:
            _log.info(f"         数据源: 优先通达信本地 -> {self.tdx_vipdoc}")
        _log.info("         请稍候...")
        completed, audit_log = 0, {}
        start_time = time.time()
        last_log_at = 0
        with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_code = {executor.submit(self._worker_fetch, code, force_refresh, self.cache_data.get(code) if not force_refresh else None): code for code in codes}
            for future in concurrent.futures.as_completed(future_to_code):
                completed += 1
                pct = 100 * (completed / float(total))
                current_step = int(pct / 10) * 10
                should_log = (completed == total) or (current_step > last_log_at)
                
                if should_log:
                    last_log_at = current_step
                    percent = ("{0:.1f}").format(pct)
                    elapsed = time.time() - start_time
                    if elapsed > 2 and completed > 0:
                        rate = completed / elapsed
                        remaining_sec = (total - completed) / rate if rate > 0 else 0
                        eta_msg = f" ETA {int(remaining_sec / 60)} min" if remaining_sec >= 60 else f" ETA {int(remaining_sec)} s"
                    else:
                        eta_msg = ""
                    _log.info(f" -> 同步进度: {percent}% [{completed}/{total}]{eta_msg}")
                    if progress_callback:
                        try:
                            progress_callback(completed, total, eta_msg)
                        except Exception as _e:
                            _log.debug(f"[数据中台] 进度回调异常: {_e}")
                res_code, res_df, status_msg = future.result()
                if res_df is not None:
                    with self.cache_lock:
                        self.cache_data[res_code] = res_df
                else:
                    audit_log.setdefault(status_msg, []).append(res_code)
        failed_count = sum(len(v) for v in audit_log.values())
        _log.error(f"\n[缓存] 阶段1完成：已同步 {len(self.cache_data)} 只标的 | 失败 {failed_count} | 耗时 {time.time()-start_time:.1f}s")
        _log.info(f"{'='*50}\n [内部审计报告] 数据对账单\n{'='*50}")
        _log.info(f" total: {total} | cached: {len(self.cache_data)} | failed: {failed_count}")
        if failed_count > 0:
            for reason, err_codes in sorted(audit_log.items(), key=lambda item: len(item[1]), reverse=True):
                _log.info(f"  - {reason}: {len(err_codes)} 只 (例: {', '.join(err_codes[:5])}...)")
        _log.info(f"{'='*50}")
        # 阶段2: 跳过批量指标预算（按需计算更高效）
        # 原因: 5000 只全量预算耗时 5-10 秒且霸占 GIL 导致 UI 卡顿，
        #       而 evaluate_conditions/precompute_ready_pool 内部已有
        #       'if entangle not in df.columns' 的按需计算兜底逻辑，
        #       实际只有 RPS≥80 的几百只会被真正评估。
        _log.info("[数据中台] 阶段2: 跳过批量指标预算(改为按需计算)，直接进入降精度...")
        self._downcast_memory()

        _log.info("[数据中台] 阶段3: 写入本地缓存(Parquet)...")
        # 主路径写 Parquet（体积更小、加载更快）
        parquet_saved = False
        try:
            from vcp.polars_engine import save_cache_parquet
            save_cache_parquet(self.cache_data, today)
            parquet_saved = True
        except ImportError:
            _log.error("[数据中台] polars 未安装，无法写入 Parquet 缓存")
        except Exception as e:
            _log.error(f"[数据中台] Parquet 写入失败: {e}", exc_info=True)

        # #13: Parquet 保存失败时，fallback 写一份 pkl 作为保底
        if not parquet_saved:
            try:
                import pickle
                # 修复: self._cache_dir 不存在，改用全局常量 CACHE_DIR
                pkl_path = os.path.join(CACHE_DIR, 'cache_data_fallback.pkl')
                with open(pkl_path, 'wb') as f:
                    pickle.dump({'date': today, 'data': self.cache_data}, f, protocol=4)
                _log.warning(f"[数据中台] Parquet 失败,已 fallback 写入 pkl: {pkl_path}")
            except Exception as pkl_e:
                _log.error(f"[数据中台] pkl fallback 也失败: {pkl_e}")
        _log.info(f"[数据中台] 阶段3 完成 -> 缓存已保存 (日期: {today})\n")
        return True

    def get_data(self, code):
        with self.cache_lock:
            df = self.cache_data.get(code)
            if df is not None:
                import pandas as pd
                if not isinstance(df, pd.DataFrame):
                    if hasattr(df, 'to_pandas'):
                        df = df.to_pandas()
                        if 'datetime' in df.columns:
                            df = df.set_index('datetime')
                        self.cache_data[code] = df
            return df

    def get_data_fresh_for_chart(self, code, force_sync=False):
        """Return latest daily bars by combining local cache and online incremental data.

        force_sync=True 时跳过盘前/盘后的缓存短路判断，强制尝试联网补全。
        """
        from vcp.engine import VCPEngine

        existing_df = self.get_data(code)
        if not force_sync and self._is_before_930_today():
            return existing_df
        if (
            not force_sync
            and self._is_after_1500_today()
            and existing_df is not None
            and len(existing_df) > 0
        ):
            try:
                if pd.Timestamp(existing_df.index.max()).date() >= datetime.now().date():
                    return existing_df
            except Exception as _e:
                _log.debug(f"[K线 {code}] 缓存日期检查异常: {_e}")
        api = self._get_thread_api()
        try:
            if existing_df is not None and len(existing_df) >= 250:
                new = self._fetch_standard_data(api, code, count=INCREMENTAL_BARS)
                if new is not None and len(new) > 0:
                    import polars as pl
                    if isinstance(new, pl.DataFrame):
                        new = new.to_pandas()
                        if 'datetime' in new.columns:
                            new = new.set_index('datetime')
                    
                    last_existing = existing_df.index.max()
                    first_new = new.index.min()
                    gap_days = (first_new - last_existing).days
                    if gap_days > 10:
                        full_df = self._fetch_standard_data(api, code, count=MAX_HISTORY_BARS)
                        if full_df is not None and len(full_df) >= 250:
                            full_df = VCPEngine.calculate_indicators(full_df)
                            with self.cache_lock:
                                self.cache_data[code] = full_df
                            return full_df
                    combined = pd.concat([existing_df, new])
                    merged = combined[~combined.index.duplicated(keep='last')].iloc[-MAX_HISTORY_BARS:]
                    merged = VCPEngine.calculate_indicators(merged)
                    with self.cache_lock:
                        self.cache_data[code] = merged
                    return merged
            else:
                full_df = self._fetch_standard_data(api, code, count=MAX_HISTORY_BARS)
                if full_df is not None and len(full_df) >= 250:
                    full_df = VCPEngine.calculate_indicators(full_df)
                    with self.cache_lock:
                        self.cache_data[code] = full_df
                    return full_df
        except (TimeoutError, OSError, ConnectionError) as e:
            _log.error(f"[K线 {code}] 联网补全失败(网络层)，继续使用缓存: {e}")
        except (ValueError, TypeError, KeyError, ArithmeticError) as e:
            _log.error(f"[K线 {code}] 联网补全失败(数据层)，继续使用缓存: {e}")
        return existing_df

    def has_cache(self):
        with self.cache_lock:
            return len(self.cache_data) > 0

    def is_online(self):
        return not self._offline

    def set_online_mode(self, online=True):
        from core.event_bus import event_bus
        if online and self._offline:
            self._offline = False
            if not self.server_pool:
                _log.info("[网络] 正在切换到联网模式，启动测速...")
                self.server_pool = self._auto_select_best_servers()
            _log.info("[网络] ✅ 已切换到联网模式")
            event_bus.sig_network_status_changed.emit(True, "Online")
        elif not online and not self._offline:
            self._offline = True
            _log.info("[网络] 已切换到离线模式")
            event_bus.sig_network_status_changed.emit(False, "Offline")

    def force_reconnect_servers(self):
        """强制重新测速并重置当前所有线程的 API 连接，选取 Top5 最快服务器"""
        if self._offline:
            _log.info("[网络] 当前为离线模式，无法测速。")
            return
            
        _log.info("[网络] 🌐 强制重新联网测速中...")
        new_pool = self._auto_select_best_servers()
        if not new_pool:
            _log.error("[网络] 测速失败，暂保留原有服务器池。")
            return
            
        self.server_pool = new_pool
        
        # 清除主线程的 API 以强制重建，并在下一次请求时重新连接
        if hasattr(self.thread_local, 'api'):
            try:
                self.thread_local.api.disconnect()
            except Exception as _e:
                _log.debug(f"[网络] 断开旧 API 连接时异常: {_e}")
            delattr(self.thread_local, 'api')
            
        _log.info("[网络] ✅ 强制重连成功，已刷新优质节点。")

    def test_network(self, timeout=3):
        """测试是否能连通通达信行情服务器（按序轻量化测试，避免启动期因并发导致内存越界崩溃）"""
        candidates = [
            ('119.147.212.81', 7709), ('124.71.187.122', 7709), ('106.120.74.86', 7709),
            ('122.51.120.217', 7709), ('121.36.54.217', 7709), ('124.71.85.110', 7709),
            ('114.80.149.19', 7709), ('114.80.149.22', 7709), ('115.238.56.198', 7709),
            ('180.153.18.17', 7709), ('218.108.47.69', 7709), ('61.135.142.88', 7709),
        ]
        import random
        import socket
        random.shuffle(candidates)
        # 增加测试覆盖率到 5 个随机节点，并放宽超时为 1.5 秒兜底，解决“网络明明正常却判定不可用”的假阳性反馈
        for ip, port in candidates[:5]:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(1.5)
                s.connect((ip, port))
                s.close()
                return True
            except Exception as _e:
                _log.debug(f"[网络] 测试节点 {ip}:{port} 不可达: {_e}")
        return False

    def get_all_valid_data(self):
        """返回缓存数据的浅拷贝（字典本身是副本，DataFrame 是引用共享）"""
        with self.cache_lock:
            return dict(self.cache_data)

    def _build_offline_quotes(self, codes):
        """离线模式或无服务器节点时，利用内存中既有的最新日线数据充当当天的最新报价字典进行兜底返回"""
        res = {}
        for code in codes:
            hist_df = self.get_data(code)
            if hist_df is not None and len(hist_df) > 0:
                last_row = hist_df.iloc[-1]
                prev_row = hist_df.iloc[-2] if len(hist_df) > 1 else last_row
                last_close = float(prev_row['close']) if len(hist_df) > 1 else float(last_row['open'])
                quote_date = None
                try:
                    quote_date = pd.Timestamp(hist_df.index[-1]).strftime('%Y-%m-%d')
                except Exception:
                    quote_date = None
                res[code] = {
                    'open': float(last_row.get('open', 0)),
                    'high': float(last_row.get('high', 0)),
                    'low': float(last_row.get('low', 0)),
                    'close': float(last_row.get('close', 0)),
                    'volume': float(last_row.get('volume', 0)),
                    'amount': float(last_row.get('amount', 0)),
                    'last_close': last_close,
                    'date': quote_date,
                }
        return res

    def fetch_realtime_quotes_batch(self, codes, _retry_once=True):
        """Fetch realtime quotes in batches of up to 80 symbols."""
        try:
            from core.market_calendar import MarketCalendar
            is_active = MarketCalendar.is_market_active()
        except Exception as _e:
            _log.debug(f"[报价] 市场日历查询失败，默认开市: {_e}")
            is_active = True

        if not is_active:
            # 严格依据交易日历：非交易时间段绝对不走网络，无论断网与否，直接闪回本地最后快照
            return self._build_offline_quotes(codes)

        try:
            from core.market_calendar import MarketCalendar
            latest_trade_date = MarketCalendar.get_latest_trade_date("CN")
            inferred_trade_date = (
                latest_trade_date.strftime('%Y-%m-%d')
                if latest_trade_date is not None
                else datetime.now().strftime('%Y-%m-%d')
            )
        except Exception:
            inferred_trade_date = datetime.now().strftime('%Y-%m-%d')

        # ====== 新增：微秒级 DataLoader 防并发堵塞机制 ======
        now = time.time()
        dedup_codes = []
        result = {}
        
        with self._rt_quote_lock:
            for c in set(codes):
                cached_time = self._rt_quote_time.get(c, 0)
                # 缓冲期 0.5 秒：拦截同一时间窗口内不同 Tab 发起的重复查询
                if now - cached_time < 0.5 and c in self._rt_quote_cache:
                    result[c] = self._rt_quote_cache[c]
                else:
                    dedup_codes.append(c)
                    
        if not dedup_codes:
            return result
        # ===================================================

        # ====== 新增：网络熔断器 (Circuit Breaker) ======
        # 如果之前因为网络挂掉导致连续报错，那么在此期间不要反复发起无用的 TCP 握手（避免 UI 卡死）
        if now < getattr(self, '_circuit_breaker_open_until', 0):
            # 仍在熔断期内，直接快速拒绝 (Fail Fast) 并返回静态快照兜底
            fallback_res = self._build_offline_quotes(dedup_codes)
            result.update(fallback_res)
            return result
        # ===============================================

        # 交易时间段逻辑：如果探测到处于离线模式，必须强行尝试联网
        if self._offline or not self.server_pool:
            _log.warning("[实时报价] 当前为交易时间段但处于离线状态，正在强行尝试联网...")
            self.set_online_mode(True)
            if not self.server_pool:
                self.force_reconnect_servers()

            if self._offline or not self.server_pool:
                _log.error("[实时报价] 交易时间段强制联网失败！请检查本地网络情况。使用本地静态数据临时兜底。")
                fallback_res = self._build_offline_quotes(dedup_codes)
                result.update(fallback_res)
                return result

        api = self._get_thread_api()
        fail_count = 0
        new_fetch = {}
        batch_timeout_sec = float(getattr(self, '_rt_api_call_timeout_sec', 8.0) or 8.0)
        for i in range(0, len(dedup_codes), 80):
            batch = dedup_codes[i:i+80]
            params_list = [(self._get_market_code(c), c) for c in batch]
            try:
                quotes = _run_blocking_call_with_timeout(
                    lambda api_ref=api, payload=params_list: api_ref.get_security_quotes(payload),
                    batch_timeout_sec,
                    f"实时报价批次超时({batch_timeout_sec:.0f}s, {len(batch)}只)",
                )
                if not quotes:
                    fail_count += 1
                    continue
                for q in quotes:
                    code_val = q.get('code', '')
                    if code_val:
                        new_fetch[code_val] = {
                            'open':   q.get('open',  0),
                            'high':   q.get('high',  0),
                            'low':    q.get('low',   0),
                            'close':  q.get('price', 0),
                            'volume': q.get('vol',   0),
                            'amount': q.get('amount', 0),
                            'last_close': q.get('last_close', 0),
                            'date': inferred_trade_date,
                        }
            except TimeoutError as _e:
                _log.warning(f"[实时报价] 批次拉取超时: {_e}")
                fail_count += 1
                self._reset_thread_api("实时报价批次超时，已重建连接")
                api = self._get_thread_api()
                continue
            except Exception as _e:
                _log.debug(f"[实时报价] 批次拉取行情失败: {_e}")
                fail_count += 1
                self._reset_thread_api("实时报价批次异常，已重建连接")
                api = self._get_thread_api()
                continue

        # 将新拉取的数据更新到全局热缓存
        if new_fetch:
            with self._rt_quote_lock:
                fetch_time = time.time()
                for c, q_data in new_fetch.items():
                    self._rt_quote_cache[c] = q_data
                    self._rt_quote_time[c] = fetch_time
            result.update(new_fetch)

        # 批量接口偶发“返回缺码”：优先回退到热缓存，再回退到本地日线，避免部分个股长期不刷新
        missing_codes = [c for c in dedup_codes if c not in result]
        if missing_codes:
            stale_quotes = {}
            with self._rt_quote_lock:
                for c in missing_codes:
                    cached = self._rt_quote_cache.get(c)
                    if cached:
                        q = dict(cached)
                        q.setdefault('date', inferred_trade_date)
                        stale_quotes[c] = q
            if stale_quotes:
                result.update(stale_quotes)
                missing_codes = [c for c in missing_codes if c not in stale_quotes]

        if missing_codes:
            fallback_res = self._build_offline_quotes(missing_codes)
            if fallback_res:
                result.update(fallback_res)

        # 如果部分或全部批次失败进行 API 缓存清理重试
        if not new_fetch and fail_count > 0:
            self._reset_thread_api()
                
            self._circuit_breaker_fails = getattr(self, '_circuit_breaker_fails', 0) + 1
            if getattr(self, '_circuit_breaker_fails', 0) >= 3:
                _log.error("[网络熔断] PyTdx 服务器连续拉取失败达到 3 次，将强行暂停数据刷新 30 秒！")
                self._circuit_breaker_open_until = time.time() + 30
                self._circuit_breaker_fails = 0  # 重置，等待 30 秒后的探针（半开状态）
                
            if _retry_once:
                _log.error("[实时报价] 批次失败，已重建 API 连接并立即重试")
                retry_res = self.fetch_realtime_quotes_batch(dedup_codes, _retry_once=False)
                result.update(retry_res)
                return result
            # 两次都失败后，必须要有强兜底，绝不传空数据给前台！
            _log.error("[实时报价] 全部批次重试失败，已强制调用本地静态快照兜底渲染UI！")
            fallback_res = self._build_offline_quotes(dedup_codes)
            result.update(fallback_res)
            return result
        else:
            # 只要不是重灾区（成功获取到数据），就重置熔断计数器
            self._circuit_breaker_fails = 0

        return result

    def get_realtime_quotes(self, codes):
        """Return a higher-level realtime quote DataFrame for the given symbols."""
        import pandas as pd
        raw_res = self.fetch_realtime_quotes_batch(codes)
        
        data_list = []
        for code in codes:
            if code in raw_res:
                q = raw_res[code]
                cur = float(q.get('close', 0) or 0)
                last_close = float(q.get('last_close', 0) or 0)
                op = float(q.get('open', 0) or 0)
                
                # --- 兜底检查：防御 Pytdx 零值Bug (停牌/未开盘/断流) ---
                if last_close <= 0:
                    hist_df = self.get_data(code)
                    if hist_df is not None and len(hist_df) > 0:
                        last_close = float(hist_df.iloc[-1]['close'])
                        
                if cur <= 0 and last_close > 0:
                    cur = last_close

                # 【修复】涨幅: 使用标准算法 (现价 - 昨收) / 昨收 * 100
                if last_close > 0:
                    pct = ((cur - last_close) / last_close * 100)
                elif op > 0:
                    pct = ((cur - op) / op * 100)
                else:
                    pct = 0
                data_list.append({
                    'code': code, 'current': cur, 'pct_change': pct,
                    'open': op, 'high': q.get('high', cur), 'low': q.get('low', cur)
                })
            # 【修复】移除假数据生成逻辑 — 取不到实时数据的股票直接跳过
                
        if not data_list:
            return pd.DataFrame()
            
        df = pd.DataFrame(data_list)
        df.set_index('code', inplace=True)
        return df

    def build_realtime_df(self, code, quote):
        """Merge a realtime quote into the latest historical bars and return a DataFrame."""
        from vcp.engine import VCPEngine

        hist_df = self.get_data(code)
        if hist_df is None or len(hist_df) < 10: return None
        if quote.get('close', 0) <= 0 or quote.get('open', 0) <= 0: return None

        slice_df = hist_df.iloc[-260:]
        combined = slice_df.copy(deep=True)
        rt_date_str = quote.get('date')
        if rt_date_str:
            rt_date = pd.Timestamp(rt_date_str)
        else:
            try:
                from core.market_calendar import MarketCalendar
                trade_dt = MarketCalendar.get_latest_trade_date()
                if trade_dt:
                    rt_date = pd.Timestamp(trade_dt)
                else:
                    rt_date = pd.Timestamp(datetime.now().date())
            except Exception as _e:
                _log.debug(f"[盘中K线] 获取最近交易日失败: {_e}")
                rt_date = pd.Timestamp(datetime.now().date())

        # 【核心修复】：盘中监控自动进行成交量和成交额“全日预估”
        # 防止早上9点半的微薄成交量拉低MA25，导致系统误判为“缩量假突破”
        now = datetime.now()
        h, m = now.hour, now.minute
        ratio = 1.0
        from core.market_calendar import MarketCalendar
        if MarketCalendar.is_market_active():
            if 9 <= h <= 11:
                passed = (m - 30) if h == 9 else (30 + m if h == 10 else (120 if m > 30 else 90 + m))
            elif 13 <= h < 15:
                passed = 120 + (h - 13) * 60 + m
            elif h == 15:
                passed = 240
            else: # 中午休市
                passed = 120
            passed = max(1, passed)
            ratio = 240.0 / passed
        
        scaled_quote = dict(quote)
        if ratio > 1.05 and 'volume' in scaled_quote:
            scaled_quote['volume'] = float(scaled_quote['volume'] or 0) * ratio
        if ratio > 1.05 and 'amount' in scaled_quote:
            scaled_quote['amount'] = float(scaled_quote['amount'] or 0) * ratio

        if rt_date in slice_df.index:
            cols = [c for c in ['open', 'high', 'low', 'close', 'volume', 'amount'] if c in combined.columns]
            today_row = pd.DataFrame(
                {col: [scaled_quote.get(col, combined.loc[rt_date, col])] for col in cols},
                index=[rt_date]
            )
            combined.update(today_row)
        else:
            # 只有当行情数据返回的确实是一个全新的交易日，才允许增加全新行
            new_row = pd.DataFrame([scaled_quote], index=[rt_date])
            combined = pd.concat([combined, new_row])
            
        if hasattr(combined, "attrs"):
            combined.attrs.pop("vcp_indicators_ready", None)
            combined.attrs.pop("vcp_core_ready", None)
            combined.attrs.pop("vcp_chart_ready", None)
        return VCPEngine.calculate_indicators(combined)
