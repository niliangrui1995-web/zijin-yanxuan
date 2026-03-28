# ui/workers.py - 后台工作线程
# 从 main_window_qt.py 拆分出来的 ScanWorker 和 RtScanWorker
import os
from vcp.constants import SPECIAL_LATEST_DATA
import datetime
import pandas as pd
from PyQt6.QtCore import QThread, pyqtSignal
from vcp.engine import VCPEngine, VCPParams
from core.logger import get_logger

log = get_logger(__name__)


class ScanWorker(QThread):
    progress = pyqtSignal(int, str)
    result_ready = pyqtSignal(list)
    finished_scan = pyqtSignal(bool, str)

    def __init__(self, data_provider, engine, sd, ed, params):
        super().__init__()
        self.data_provider = data_provider
        self.engine = engine
        self.sd = sd
        self.ed = ed
        self.params = params
        self._is_cancelled = False

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        self.progress.emit(0, "正在查询数据...")
        try:
            # 首次运行:需要读取由vipdoc目录结构提取的股票名称和预缓存数据
            if not self.data_provider.cache_data:
                self.progress.emit(0, "首次扫描:读取本地代码表...")
                codes_dict = self.data_provider._get_codes_from_vipdoc()
                
                # 设置一个进度回调映射到信号(占用前 50% 进度条)
                def _sync_cb(done, total, eta):
                    if self._is_cancelled:
                        raise InterruptedError("用户取消")
                    if total > 0 and done % 50 == 0:
                        pct = int((done / total) * 50)
                        self.progress.emit(pct, f"缓存本地日线: {done}/{total} {eta}")
                        
                self.data_provider.sync_market_data(codes_dict, force_refresh=False, progress_callback=_sync_cb)
                self.data_provider.code2name = codes_dict
            elif not hasattr(self.data_provider, 'code2name'):
                self.data_provider.code2name = self.data_provider._get_codes_from_vipdoc()

            if self._is_cancelled:
                self.finished_scan.emit(False, "任务已取消")
                return

            self.progress.emit(50, "计算 RPS 相对强度矩阵...")
            matrix = self.engine.build_rps_matrix(self.data_provider.cache_data, self.sd, self.ed)
            
            if not matrix:
                self.finished_scan.emit(False, "区间无效或无通达信本地数据")
                return

            total_days = len(matrix)
            all_results = []
            
            for i, (d_str, d_rps) in enumerate(matrix.items()):
                if self._is_cancelled:
                    self.finished_scan.emit(False, "任务已取消")
                    return
                
                pct = int(100 * (i+1) / total_days)
                self.progress.emit(pct, f"扫描 {d_str} ({i+1}/{total_days})")
                
                targets = [k for k, v in d_rps['rps250'].items() 
                           if pd.notna(v) and v >= self.params.rps_threshold 
                           and (v >= 90 or v >= d_rps['rps120'].get(k, 0))]
                
                for code in targets:
                    # === ST 股过滤:ST/*ST 涨跌幅仅 5%,易伪装成 VCP 收缩形态 ===
                    stock_name = self.data_provider.code2name.get(code, '')
                    if 'ST' in stock_name.upper():
                        continue
                    df = self.data_provider.get_data(code)
                    if df is not None:
                        try:
                            ok, reason, m = self.engine.evaluate_conditions(
                                df, pd.to_datetime(d_str),
                                d_rps['rps120'].get(code, 0),
                                d_rps['rps250'].get(code, 0), None, self.params)
                            if ok:
                                m.update({
                                    '代码': code,
                                    '名称': self.data_provider.code2name.get(code, ""),
                                    '触发日期': d_str,
                                    '热点板块': "-"
                                })
                                all_results.append(m)
                        except Exception:
                            continue
            
            # Enrich Market Cap
            if all_results:
                self.progress.emit(99, "计算市值...")
                df_res = pd.DataFrame(all_results)
                unique_codes = df_res['代码'].unique().tolist()
                _scan_close = {}
                for c in unique_codes:
                    _cd = self.data_provider.cache_data.get(c)
                    if _cd is not None and not _cd.empty:
                        _scan_close[c] = float(_cd.iloc[-1]['close'])
                cap_results = VCPEngine.batch_check_market_cap(unique_codes, close_prices=_scan_close)
                
                for res in all_results:
                    c = res['代码']
                    if cap_results.get(c):
                        res['市值'] = f"{cap_results[c] / 1e8:.0f}亿"
                    else:
                        res['市值'] = ""

            # Enrich 热点板块(板块 RPS)
            if all_results:
                self.progress.emit(99, "查询热点板块...")
                try:
                    from vcp.sector import SectorManager
                    tdx_root = os.path.dirname(self.data_provider.tdx_vipdoc) if self.data_provider.tdx_vipdoc else r'D:\HT'
                    sm = SectorManager(tdx_root)
                    # 取最后一个扫描日作为板块 RPS 基准日
                    last_date = all_results[-1].get('触发日期', '')
                    if last_date:
                        sector_rps = sm.build_sector_rps(self.data_provider.cache_data, last_date)
                        for res in all_results:
                            code = res['代码']
                            passed, info_str, _ = sm.check_sector_rps(code, sector_rps, threshold=0)
                            res['热点板块'] = info_str if info_str else "-"
                except Exception as e:
                    log.error(f"[板块查询] 异常: {e}")
            
            self.result_ready.emit(all_results)
            self.finished_scan.emit(True, f"扫描完成,捕获 {len(all_results)} 条信号")

        except InterruptedError:
            self.finished_scan.emit(False, "任务已取消")
        except Exception as e:
            self.finished_scan.emit(False, f"扫描异常: {str(e)}")

class RtScanWorker(QThread):
    """盘中监控核心工作线程:
    首轮: 加载RPS -> 构建待突破池 -> 拉取实时报价 -> VCP突破检测 -> 发射信号
    后续轮: 复用待突破池 -> 拉取实时报价 -> rt_quick_check轻量检测 -> 发射信号
    """
    rt_result_ready = pyqtSignal(list)   # 每轮完整信号列表
    progress = pyqtSignal(str)           # 状态文字
    scan_count = pyqtSignal(int, int)    # (轮次, 待突破池大小)

    def __init__(self, data_provider, engine, interval=300, rps_threshold=80):
        super().__init__()
        self.data_provider = data_provider
        self.engine = engine
        self.interval = interval
        self.rps_threshold = rps_threshold
        self._is_running = True
        # 缓存(跨轮复用)
        self._ready_pool = None       # 待突破池 {code: entry}
        self._rps120 = None           # RPS120 Series
        self._rps250 = None           # RPS250 Series
        self._all_data = None         # 历史日线数据
        self._scan_count = 0
        self._pool_refresh_interval = 10   # 每 10 轮重建待突破池（动态剔除形态恶化标的）
        self._seen_signals = set()    # 信号去重 (code, status)
        self._signal_details = {}     # 盘中监控信号详情(仅待突破池红盘触发)
        self._special_details = {}    # 关注池实时数据(独立存储,不影响盘中监控)
        self._sector_manager = None   # 板块管理器(首轮创建后缓存)
        self._sector_rps = None       # 板块 RPS 字典(首轮计算后缓存)
        self._cap_cache = {}          # 市值缓存 {code: '71亿'} 跨轮复用

    def stop(self):
        self._is_running = False

    def run(self):
        import time as _time
        import numpy as np

        while self._is_running:
            self._scan_count += 1
            t0 = _time.time()
            try:
                self._run_one_round(np)
            except Exception as e:
                self.progress.emit(f"盘中扫描异常: {e}")
                import traceback
                traceback.print_exc()

            elapsed = _time.time() - t0
            self.progress.emit(f"第{self._scan_count}轮完成(耗时 {elapsed:.1f}s),等待下轮...")

            # 等待间隔(可被中断)
            for _ in range(int(self.interval * 10)):
                if not self._is_running:
                    return
                _time.sleep(0.1)

    def _run_one_round(self, np):
        import time as _time

        # ===== 阶段1: 确保有历史数据 =====
        if self._all_data is None:
            self.progress.emit("加载历史日线数据...")
            self._all_data = {c: df for c, df in self.data_provider.cache_data.items()
                              if df is not None and len(df) >= 60}
            if not self._all_data:
                self.progress.emit("❌ 无历史数据,请先执行 F5 或扫描")
                return

        # ===== 阶段2: 计算/复用 RPS =====
        if self._rps120 is None or self._rps250 is None:
            self.progress.emit("计算全市场 RPS 排名...")
            precomputed_bundle = self.engine.get_precomputed_rps()
            if precomputed_bundle:
                self._rps120 = precomputed_bundle.get('rps120')
                self._rps250 = precomputed_bundle.get('rps250')
                if self._rps120 is not None and self._rps250 is not None:
                    log.info(f"[盘中] 使用预计算 RPS(基准日 {precomputed_bundle.get('date', '?')})")
            if self._rps120 is None or self._rps250 is None:
                # 兜底现算 RPS(忘记跑 F5 时自动补算,结果保存磁盘覆盖旧缓存)
                today_str = datetime.date.today().strftime('%Y%m%d')
                min_start = pd.Timestamp(today_str) - pd.Timedelta(days=500)
                prices = VCPEngine._build_prices_matrix(self._all_data, min_start, None)
                if prices.empty:
                    self.progress.emit("❌ RPS 计算失败:无价格数据")
                    return
                rps120 = prices.pct_change(120).rank(axis=1, pct=True) * 100
                rps250 = prices.pct_change(250).rank(axis=1, pct=True) * 100
                self._rps120 = rps120.iloc[-1]
                self._rps250 = rps250.iloc[-1]
                valid_count = int(self._rps120.notna().sum())
                log.info(f"[盘中] 现算 RPS 完成({valid_count} 只有效排名)")

                # 保存到磁盘(与 F5 格式一致,下次启动可直接加载)
                try:
                    import pickle
                    from vcp.constants import CACHE_DIR
                    cache_dir = CACHE_DIR
                    rps_pkg = {'date': today_str, 'rps120': self._rps120, 'rps250': self._rps250}
                    rps_path = os.path.join(cache_dir, 'vcp_rps_precomputed.pkl')
                    with open(rps_path, 'wb') as f:
                        pickle.dump(rps_pkg, f, protocol=4)
                    self.engine.set_precomputed_rps(today_str, self._rps120, self._rps250)
                    log.info(f"[盘中] 现算 RPS 已保存磁盘(基准日 {today_str},{valid_count} 只)")
                except Exception as e:
                    log.error(f"[盘中] RPS 磁盘保存失败: {e}")

        # ===== 阶段3: 构建/刷新待突破池 =====
        need_rebuild = (self._ready_pool is None or
                        self._scan_count % self._pool_refresh_interval == 1)
        if need_rebuild:
            label = "首轮构建" if self._ready_pool is None else f"第{self._scan_count}轮刷新"
            self.progress.emit(f"{label}待突破池...")
            t0 = _time.time()
            rt_params = VCPParams(
                rps_threshold=self.rps_threshold,
                amp_threshold=0.45,
                ma_bind_threshold=0.05,
                high_250_threshold=0.10,
                min_amount_20d=8e7,
                min_history_days=250,
            )
            new_pool = VCPEngine.precompute_ready_pool(
                self._all_data, self._rps120, self._rps250, rt_params,
                code2name=self.data_provider.code2name)
            if self._ready_pool is not None:
                old_codes = set(self._ready_pool.keys())
                new_codes = set(new_pool.keys())
                added = new_codes - old_codes
                removed = old_codes - new_codes
                if added or removed:
                    log.info(f"[待突破池] 刷新: +{len(added)} 新增 / -{len(removed)} 剔除 (形态恶化)")
            self._ready_pool = new_pool
            log.info(f"[待突破池] {label}完成: {len(self._ready_pool)} 只候选(耗时 {_time.time()-t0:.1f}s)")

        pool_size = len(self._ready_pool)
        self.scan_count.emit(self._scan_count, pool_size)

        # ===== 阶段4: 拉取实时报价 =====
        codes_to_fetch = list(self._ready_pool.keys())
        # 加入关注池代码(即使不在待突破池中)
        import json
        special_codes = set()
        special_path = SPECIAL_LATEST_DATA
        if os.path.exists(special_path):
            try:
                with open(special_path, 'r', encoding='utf-8') as f:
                    special_codes = set(json.load(f).keys())
            except Exception:
                pass
        for sc in special_codes:
            if sc not in codes_to_fetch:
                codes_to_fetch.append(sc)

        self.progress.emit(f"第{self._scan_count}轮:拉取 {len(codes_to_fetch)} 只报价...")
        quotes = self.data_provider.fetch_realtime_quotes_batch(codes_to_fetch)
        if not quotes:
            self.progress.emit("实时报价获取失败")
            return

        # ===== 阶段5: 突破检测 =====
        new_signals = []
        for code, quote in quotes.items():
            is_special = code in special_codes
            pool_entry = self._ready_pool.get(code)
            r120 = self._rps120.get(code, float('nan'))
            r250 = self._rps250.get(code, float('nan'))

            # 涨幅计算:优先使用 pytdx 返回的昨收价,精确可靠
            last_close = float(quote.get('last_close', 0) or 0)
            rt_close = float(quote.get('close', 0) or 0)
            if last_close > 0 and rt_close > 0:
                pct = ((rt_close / last_close) - 1) * 100
            else:
                # 兜底:用历史缓存最后一根K线
                hist_df = self._all_data.get(code)
                if hist_df is not None and len(hist_df) > 0:
                    prev_close = float(hist_df.iloc[-1]['close'])
                    pct = ((rt_close / prev_close) - 1) * 100 if prev_close > 0 else 0
                else:
                    pct = 0

            # ------ 关注池股票:完整 evaluate ------
            if is_special:
                has_rps = not (pd.isna(r120) or pd.isna(r250))
                rps_display = f"{r120:.0f}/{r250:.0f}" if has_rps else "--/--"
                special_params = VCPParams(rps_threshold=0, amp_threshold=2.0, ma_bind_threshold=0.30,
                                          high_250_threshold=0.50, min_amount_20d=0, min_history_days=60)
                rt_df = self.data_provider.build_realtime_df(code, quote)
                if has_rps and rt_df is not None and len(rt_df) >= 60:
                    eval_day = rt_df.index[-1]
                    ok, reason, m = self.engine.evaluate_conditions(rt_df, eval_day, float(r120), float(r250), None, special_params)
                    if ok:
                        status = "✓ 触发买入 . " + m.get('突破状态', '突破')
                    else:
                        status = f"○ 未触发 . 跟踪 ({reason.split(' | ')[0]})"
                        m = {'评分': '--', 'RPS强度': rps_display}
                else:
                    # 兜底:即使无法构造实时DF,也显示基本信息(不静默消失)
                    status = "○ 跟踪中"
                    m = {'评分': '--', 'RPS强度': rps_display}
                cap = pool_entry.get('market_cap', '') if pool_entry else ''
                sector = pool_entry.get('sector_info', '--') if pool_entry else '--'
                sig = {
                    '时间': datetime.datetime.now().strftime('%H:%M'), '代码': code,
                    '名称': self.data_provider.code2name.get(code, code),
                    '现价': f"{quote['close']:.2f}", '涨幅%': f"{pct:+.2f}%",
                    '评分': m.get('评分', '--'), 'RPS强度': rps_display,
                    '市值': cap, '突破状态': status, '热点板块': sector,
                    'AI诊断': '', '区间振幅': m.get('区间振幅', ''),
                    '_is_special': True,   # 标记为关注池股票,盘中监控表格不展示
                }
                new_signals.append(sig)
                self._special_details[code] = sig   # 关注池独立存储
                # 不 continue ---- 关注池股票继续走下方 rt_quick_check,
                # 如果也满足盘中突破条件,同样会出现在盘中监控表格中

            # ------ 所有股票(含关注池)统一走待突破池轻量检测 ------
            if pd.isna(r120) or pd.isna(r250):
                continue
            if pool_entry is None:
                continue  # 不在待突破池

            ok, breakout_status, score = VCPEngine.rt_quick_check(quote, pool_entry)
            if not ok:
                continue  # 非红盘 -- 盘中监控不展示

            m_meta = pool_entry.get('meta', {})
            # 去重仅控制日志打印,不跳过信号更新(确保实时数据每轮刷新)
            sig_key = (code, breakout_status)
            if sig_key not in self._seen_signals:
                self._seen_signals.add(sig_key)
                log.info(f"  🔥 新信号! {code} {self.data_provider.code2name.get(code, '')} "
                      f"| 现价 {quote['close']:.2f} | {pct:+.2f}% | {breakout_status}")

            # 构建信号时,优先使用 pool_entry 中的板块/市值,
            # 若为空则保留上一轮已经通过阶段6补全的旧值(防止覆盖)
            prev_sig = self._signal_details.get(code, {})
            _sector = pool_entry.get('sector_info', '') or prev_sig.get('热点板块', '')
            _cap = pool_entry.get('market_cap', '') or prev_sig.get('市值', '')
            sig = {
                '时间': datetime.datetime.now().strftime('%H:%M'), '代码': code,
                '名称': self.data_provider.code2name.get(code, code),
                '现价': f"{quote['close']:.2f}", '涨幅%': f"{pct:+.2f}%",
                '评分': score, 'RPS强度': pool_entry.get('rps_str', f"{r120:.0f}/{r250:.0f}"),
                '市值': _cap,
                '突破状态': f"✓ {breakout_status}",
                '热点板块': _sector,
                'AI诊断': '', '区间振幅': m_meta.get('区间振幅', ''),
            }
            new_signals.append(sig)
            self._signal_details[code] = sig  # 盘中监控独立存储

        # ===== 阶段6: 补全市值与热点板块(首轮构建缓存,后续复用) =====
        # 收集需要补全市值的代码(盘中监控 + 关注池合并)
        _all_sigs = {**self._signal_details, **self._special_details}
        codes_need_cap = [sig['代码'] for sig in _all_sigs.values()
                          if not sig.get('市值') and sig['代码'] not in self._cap_cache]
        if codes_need_cap:
            try:
                self.progress.emit(f"补全 {len(codes_need_cap)} 只市值...")
                close_prices = {}
                for c in codes_need_cap:
                    q = quotes.get(c)
                    if q:
                        close_prices[c] = float(q.get('close', 0) or 0)
                    else:
                        hist = self._all_data.get(c)
                        if hist is not None and len(hist) > 0:
                            close_prices[c] = float(hist.iloc[-1]['close'])
                cap_results = VCPEngine.batch_check_market_cap(codes_need_cap, close_prices=close_prices)
                for c in codes_need_cap:
                    cap = cap_results.get(c)
                    if cap and cap > 0:
                        self._cap_cache[c] = f"{cap / 1e8:.0f}亿"
                    else:
                        self._cap_cache[c] = ''
            except Exception as e:
                log.error(f"[盘中] 市值补全异常: {e}")

        # 构建板块管理器(仅首轮,后续复用)
        if self._sector_manager is None:
            try:
                from vcp.sector import SectorManager
                import pickle as _pkl
                tdx_root = os.path.dirname(self.data_provider.tdx_vipdoc) if self.data_provider.tdx_vipdoc else r'D:\\HT'
                self._sector_manager = SectorManager(tdx_root)

                # 优先从磁盘加载板块 RPS 缓存(F5 或上次盘中监控保存的)
                from vcp.constants import SECTOR_RPS_CACHE_FILE
                _loaded = False
                if os.path.exists(SECTOR_RPS_CACHE_FILE):
                    try:
                        with open(SECTOR_RPS_CACHE_FILE, 'rb') as _f:
                            _pkg = _pkl.load(_f)
                        self._sector_rps = _pkg.get('sector_rps', {})
                        _cached_date = _pkg.get('date', '?')
                        if self._sector_rps:
                            _loaded = True
                            log.info(f"[盘中] 板块 RPS 从磁盘加载(基准日 {_cached_date},{len(self._sector_rps)} 个板块)")
                    except Exception:
                        pass

                # 磁盘无缓存则现算 + 保存
                if not _loaded:
                    today_str = datetime.date.today().strftime('%Y%m%d')
                    self._sector_rps = self._sector_manager.build_sector_rps(
                        self._all_data, today_str)
                    log.info(f"[盘中] 板块 RPS 现算完成: {len(self._sector_rps)} 个板块")
                    try:
                        _pkg = {'date': today_str, 'sector_rps': self._sector_rps}
                        with open(SECTOR_RPS_CACHE_FILE, 'wb') as _f:
                            _pkl.dump(_pkg, _f, protocol=4)
                        log.info("[盘中] 板块 RPS 已保存磁盘")
                    except Exception as _e:
                        log.error(f"[盘中] 板块 RPS 磁盘保存失败: {_e}")
            except Exception as e:
                log.error(f"[盘中] 板块管理器创建异常: {e}")
                self._sector_manager = False  # 标记为失败,不再重试

        # 补全空板块信息(盘中监控 + 关注池)
        _all_sigs = {**self._signal_details, **self._special_details}
        if self._sector_manager and self._sector_rps:
            for sig in _all_sigs.values():
                code = sig['代码']
                # 补全市值
                if not sig.get('市值') and code in self._cap_cache:
                    sig['市值'] = self._cap_cache[code]
                # 补全热点板块
                if not sig.get('热点板块') or sig['热点板块'] in ('', '--', '-'):
                    try:
                        _, info_str, _ = self._sector_manager.check_sector_rps(
                            code, self._sector_rps, threshold=0)
                        sig['热点板块'] = info_str if info_str else '--'
                    except Exception:
                        pass
        else:
            # 仅补全市值(板块管理器不可用时)
            for sig in _all_sigs.values():
                if not sig.get('市值') and sig['代码'] in self._cap_cache:
                    sig['市值'] = self._cap_cache[sig['代码']]

        # 合并盘中监控信号 + 关注池信号(两者独立存储,互不干扰)
        all_signals = list(self._signal_details.values()) + list(self._special_details.values())

        log.info(f"[盘中] 第{self._scan_count}轮 | 待突破池 {pool_size} | 报价 {len(quotes)} | "
              f"本轮新信号 {len(new_signals)} | 累计 {len(all_signals)}")
        self.rt_result_ready.emit(all_signals)
