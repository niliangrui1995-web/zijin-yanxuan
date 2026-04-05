# data_provider.py - 数据中台（动态测速池） # 从 vcp_hunter.pyw 提取 TdxDataProvider 类，零逻辑变更
import os
import time
import socket
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

class TdxDataProvider:
    def __init__(self, is_trading_day=None, offline=False):
        from pytdx.hq import TdxHq_API
        self.TdxHq_API = TdxHq_API
        self.cache_file = os.path.join(CACHE_DIR, 'vcp_tdx_cache_adj.pkl')
        self.cache_data = {}
        self.cache_lock = threading.Lock()
        self.thread_local = threading.local()
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
        if count > 0:
            from core.logger import get_logger
            get_logger(__name__).info(f"[内存优化] 已将 {count} 只标的数据降精度 (float64→float32)")


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
        import random, socket, time
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
            except Exception:
                pass
                
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
            except Exception:
                pass  # 缓存损坏, 回退到重新解析        
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
                except Exception as e:
                    _log.debug(f"[网络] 测速连接节点失败 {ip}:{port} - {e}")
                    continue
            self.thread_local.api = api
        return self.thread_local.api

    def _apply_forward_adjustment(self, api, market, code, df):
        """Apply forward adjustment using local gbbq data first, then fall back to online API."""
        try:
            xdxr_df = None
            # 优先使用本地 gbbq 数据(无需联网)
            if code in self._local_gbbq:
                local = self._local_gbbq[code]
                xdxr_df = local.copy()
                # gbbq 字段含: datetime(YYYYMMDD整数), hongli_panqianliutong(红利), songgu_qianzongguben(送股)
                xdxr_df['dt'] = pd.to_datetime(xdxr_df['datetime'].astype(str), format='%Y%m%d', errors='coerce')
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
                xdxr_df['dt'] = pd.to_datetime(xdxr_df[['year', 'month', 'day']].astype(str).agg('-'.join, axis=1))
                xdxr_df = xdxr_df.set_index('dt').sort_index(ascending=False)
            else:
                # 既无本地 gbbq 也无 API -> 无法复权
                return df

            if xdxr_df is None or xdxr_df.empty:
                return df

            adj_df = df.copy()
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
                mask = adj_df.index < dt
                if not mask.any():
                    continue
                for col in ['open', 'high', 'low', 'close']:
                    adj_df.loc[mask, col] = (adj_df.loc[mask, col] - fh) / (1 + sz)
                if 'vol' in adj_df.columns:
                    adj_df.loc[mask, 'vol'] *= (1 + sz)
                if 'volume' in adj_df.columns:
                    adj_df.loc[mask, 'volume'] *= (1 + sz)
            return adj_df
        except Exception as e:
            import traceback
            _log.error(f"[数据中台] 前复权计算异常: {e}")
            traceback.print_exc()
            raise ValueError(f"除权除息因子计算失败: {e}") from e

    def _fetch_standard_data(self, api, code, count=MAX_HISTORY_BARS):
        market = self._get_market_code(code)
        if self.tdx_vipdoc:
            local_df = self._fetch_from_local_tdx(code)
            if local_df is not None and len(local_df) >= 250:
                try:
                    local_df = self._apply_forward_adjustment(api, market, code, local_df)
                    if 'vol' in local_df.columns:
                        local_df.rename(columns={'vol': 'volume'}, inplace=True)
                    return local_df
                except Exception as e:
                    _log.error(f"[数据中台] 本地 {code} 复权失败，改用网络: {e}")
            elif local_df is not None and len(local_df) < 250:
                _log.info(f"[缓存] 本地日线 {code}: 共 {len(local_df)} 条")
            elif self.tdx_vipdoc and (local_df is None or local_df.empty):
                _log.error(f"[数据中台] 本地日线 {code} 异常，改用网络")
        for _ in range(2):
            try:
                data = api.get_security_bars(9, market, code, 0, count)
                if data and len(data) > 0:
                    df = pd.DataFrame(data)
                    df['datetime'] = pd.to_datetime(df['datetime']).dt.normalize()
                    df.set_index('datetime', inplace=True)
                    df = df.sort_index(ascending=True)
                    df = self._apply_forward_adjustment(api, market, code, df)
                    if 'vol' in df.columns: df.rename(columns={'vol': 'volume'}, inplace=True)
                    return df
            except ValueError as ve: raise ve
            except Exception as e:
                _log.error(f"[数据中台] 拉取 {code} 历史数据失败: {e}")
                pass
        return None

    def get_all_codes(self):
        import json as _json
        _name_cache_file = os.path.join(CACHE_DIR, 'vcp_code_names.json')

        if self._offline:
            if os.path.exists(_name_cache_file):
                try:
                    with open(_name_cache_file, 'r', encoding='utf-8') as f:
                        cached = _json.load(f)
                    if cached:
                        _log.info(f"[离线模式] 从名称缓存读取 {len(cached)} 只标的（含股票名称）")
                        return cached
                except Exception as e:
                    _log.error(f"[离线模式] 名称缓存读取失败: {e}")
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
                with open(_name_cache_file, 'w', encoding='utf-8') as f:
                    _json.dump(stocks, f, ensure_ascii=False)
                _log.info(f"[数据中台] 已保存 {len(stocks)} 只标的名称缓存 -> {_name_cache_file}")
            except Exception as e:
                _log.error(f"[数据中台] 名称缓存保存失败: {e}")
        return stocks

    def _get_codes_from_vipdoc(self):
        stocks = {}
        # Bug-2 修复：优先从 JSON 名称缓存读取股票名称（联网时已保存过一份）
        import json as _json
        _name_cache_file = os.path.join(CACHE_DIR, 'vcp_code_names.json')
        name_map = {}
        try:
            if os.path.exists(_name_cache_file):
                with open(_name_cache_file, 'r', encoding='utf-8') as f:
                    name_map = _json.load(f) or {}
        except Exception:
            pass
            
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
                        if 'vol' in local_df.columns:
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
                new = self._fetch_standard_data(api, code, count=INCREMENTAL_BARS)
                if new is not None:
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
            except Exception:
                pass
            return ""
        except Exception as e:
            self.cache_data = {}
            _log.error(f"\n[数据中台] 读取缓存文件失败，已丢弃旧缓存并准备重建。原因: {e}")
            import traceback
            traceback.print_exc()
            return ""

    def sync_market_data(self, codes, force_refresh=False, progress_callback=None):
        # 延迟导入避免循环引用
        from vcp.engine import VCPEngine

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
        workers = 50 if self._offline else MARKET_SYNC_WORKERS
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
                now_ts = time.time()
                pct = 100 * (completed / float(total))
                should_log = (completed == total or completed % 25 == 0 or int(pct) > last_log_at) and (now_ts - start_time > 2 or completed <= 25 or completed == total)
                if should_log:
                    last_log_at = int(pct)
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
                        except Exception:
                            pass
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
        _log.info(f"[数据中台] 阶段2: 跳过批量指标预算(改为按需计算)，直接进入降精度...")
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
            _log.error(f"[数据中台] Parquet 写入失败: {e}")
            import traceback
            traceback.print_exc()

        # #13: Parquet 保存失败时，fallback 写一份 pkl 作为保底
        if not parquet_saved:
            try:
                import pickle
                pkl_path = os.path.join(self._cache_dir, 'cache_data_fallback.pkl')
                with open(pkl_path, 'wb') as f:
                    pickle.dump({'date': today, 'data': self.cache_data}, f, protocol=4)
                _log.warning(f"[数据中台] Parquet 失败,已 fallback 写入 pkl: {pkl_path}")
            except Exception as pkl_e:
                _log.error(f"[数据中台] pkl fallback 也失败: {pkl_e}")
        _log.info(f"[数据中台] 阶段3 完成 -> 缓存已保存 (日期: {today})\n")
        return True

    def get_data(self, code):
        with self.cache_lock:
            return self.cache_data.get(code)

    def get_data_fresh_for_chart(self, code):
        """Return latest daily bars by combining local cache and online incremental data."""
        from vcp.engine import VCPEngine

        existing_df = self.get_data(code)
        if self._is_before_930_today():
            return existing_df
        if self._is_after_1500_today() and existing_df is not None and len(existing_df) > 0:
            try:
                if pd.Timestamp(existing_df.index.max()).date() >= datetime.now().date():
                    return existing_df
            except Exception:
                pass
        api = self._get_thread_api()
        try:
            if existing_df is not None and len(existing_df) >= 250:
                new = self._fetch_standard_data(api, code, count=INCREMENTAL_BARS)
                if new is not None and len(new) > 0:
                    last_existing = existing_df.index.max()
                    first_new = new.index.min()
                    gap_days = (first_new - last_existing).days
                    if gap_days > 10:
                        full_df = self._fetch_standard_data(api, code, count=MAX_HISTORY_BARS)
                        if full_df is not None and len(full_df) >= 250:
                            VCPEngine.calculate_indicators(full_df)
                            with self.cache_lock:
                                self.cache_data[code] = full_df
                            return full_df
                    combined = pd.concat([existing_df, new])
                    merged = combined[~combined.index.duplicated(keep='last')].iloc[-MAX_HISTORY_BARS:]
                    VCPEngine.calculate_indicators(merged)
                    with self.cache_lock:
                        self.cache_data[code] = merged
                    return merged
            else:
                full_df = self._fetch_standard_data(api, code, count=MAX_HISTORY_BARS)
                if full_df is not None and len(full_df) >= 250:
                    VCPEngine.calculate_indicators(full_df)
                    with self.cache_lock:
                        self.cache_data[code] = full_df
                    return full_df
        except Exception as e:
            _log.error(f"[K线 {code}] 联网补全失败，继续使用缓存: {e}")
        return existing_df

    def has_cache(self):
        with self.cache_lock:
            return len(self.cache_data) > 0

    def is_online(self):
        return not self._offline

    def set_online_mode(self, online=True):
        if online and self._offline:
            self._offline = False
            if not self.server_pool:
                _log.info("[网络] 正在切换到联网模式，启动测速...")
                self.server_pool = self._auto_select_best_servers()
            _log.info("[网络] ✅ 已切换到联网模式")
        elif not online and not self._offline:
            self._offline = True
            _log.info("[网络] 已切换到离线模式")

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
            except Exception:
                pass
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
        for ip, port in candidates[:3]:
            try:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.settimeout(0.5)
                s.connect((ip, port))
                s.close()
                return True
            except Exception:
                pass
        return False

    def get_all_valid_data(self):
        """返回缓存数据的浅拷贝（字典本身是副本，DataFrame 是引用共享）"""
        with self.cache_lock:
            return dict(self.cache_data)

    def fetch_realtime_quotes_batch(self, codes, _retry_once=True):
        """Fetch realtime quotes in batches of up to 80 symbols."""
        if self._offline or not self.server_pool:
            _log.info("[实时报价] 离线模式或无服务器，无法拉取实时报价")
            return {}
        api = self._get_thread_api()
        result = {}
        fail_count = 0
        code_list = list(codes)
        for i in range(0, len(code_list), 80):
            batch = code_list[i:i+80]
            params_list = [(self._get_market_code(c), c) for c in batch]
            try:
                quotes = api.get_security_quotes(params_list)
                if not quotes:
                    fail_count += 1
                    continue
                for q in quotes:
                    code = q.get('code', '')
                    if code:
                        result[code] = {
                            'open':   q.get('open',  0),
                            'high':   q.get('high',  0),
                            'low':    q.get('low',   0),
                            'close':  q.get('price', 0),
                            'volume': q.get('vol',   0),
                            'amount': q.get('amount', 0),
                            'last_close': q.get('last_close', 0),
                        }
            except Exception:
                fail_count += 1
                continue
        # 如果首轮批次全部失败，清理线程 API 并立即重试一次，避免刚切换联网模式时首笔空结果。
        if not result and fail_count > 0:
            if hasattr(self.thread_local, 'api'):
                try:
                    self.thread_local.api.disconnect()
                except Exception:
                    pass
                delattr(self.thread_local, 'api')
            if _retry_once:
                _log.error("[实时报价] 首轮批次失败，已重建 API 连接并立即重试")
                return self.fetch_realtime_quotes_batch(codes, _retry_once=False)
            _log.error("[实时报价] 全部批次失败，已清除 API 缓存，下次将重新连接")
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
                    hist_df = self.cache_data.get(code)
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
        today = pd.Timestamp(datetime.now().date())
        if today in slice_df.index:
            cols = [c for c in ['open', 'high', 'low', 'close', 'volume'] if c in combined.columns]
            today_row = pd.DataFrame(
                {col: [quote.get(col, combined.loc[today, col])] for col in cols},
                index=[today]
            )
            combined.update(today_row)
        else:
            new_row = pd.DataFrame([quote], index=[today])
            combined = pd.concat([combined, new_row])
        if hasattr(combined, "attrs"):
            combined.attrs.pop("vcp_indicators_ready", None)
            combined.attrs.pop("vcp_core_ready", None)
            combined.attrs.pop("vcp_chart_ready", None)
        return VCPEngine.calculate_indicators(combined)
