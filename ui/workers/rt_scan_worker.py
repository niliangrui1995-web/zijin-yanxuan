# ui/workers.py - 后台工作线程
# 从 main_window_qt.py 拆分出来的 ScanWorker 和 RtScanWorker
import os
import math
import datetime
from PyQt6.QtCore import QThread, pyqtSignal
from vcp.engine import VCPEngine, VCPParams
from core.logger import get_logger
from core.market_calendar import MarketCalendar
from core.sector_rps_helper import enrich_hot_sector_rows, load_sector_rps_snapshot

log = get_logger(__name__)

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
        self._zbg_cache = {}          # 总股本缓存 {code: zongguben}，用于按最新现价动态重算市值

    def stop(self):
        self._is_running = False

    def _cleanup_caches(self):
        """线程退出时释放内存。
        为什么不在 stop() 里做？stop() 是 UI 线程调用的，
        而 _run_one_round() 可能正在 worker 线程中访问这些缓存。
        在同一线程的 run() 退出后调用，彻底避免竞态。
        """
        import gc as _gc
        self._ready_pool = None
        self._rps120 = None
        self._rps250 = None
        self._all_data = None
        self._signal_details = {}
        self._special_details = {}
        self._sector_rps = None
        self._sector_manager = None
        self._cap_cache = {}
        self._zbg_cache = {}
        self._seen_signals = set()
        _gc.collect()
        log.info("[监控] 线程结束，已释放全部缓存")

    def run(self):
        import time as _time
        import numpy as np

        while self._is_running:
            self._scan_count += 1
            t0 = _time.time()
            try:
                self._run_one_round(np)
            except InterruptedError:
                if not self._is_running:
                    self._cleanup_caches()
                    return
            except Exception as e:
                self.progress.emit(f"盘中扫描异常: {e}")
                log.error(f"[盘中监控] 第{self._scan_count}轮扫描异常: {e}", exc_info=True)

            elapsed = _time.time() - t0
            # 每轮结束后主动回收 Polars 转换等产生的临时对象
            import gc as _gc
            _gc.collect()
            # stop() 可能在本轮执行中被调用：此时直接退出
            if not self._is_running:
                self._cleanup_caches()
                return
            self.progress.emit(f"第{self._scan_count}轮完成(耗时 {elapsed:.1f}s),等待下轮...")

            # 等待间隔(可被中断)
            for _ in range(int(self.interval * 10)):
                if not self._is_running:
                    self._cleanup_caches()
                    return
                _time.sleep(0.1)

    def _run_one_round(self, np):
        import time as _time

        # ===== 阶段1: 确保有历史数据 =====
        if self._all_data is None:
            self.progress.emit("加载历史日线数据...")
            cache_snapshot = self.data_provider.get_all_valid_data()
            self._all_data = {c: df for c, df in cache_snapshot.items()
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
                    log.info(f"[盘中] 加载预计算 RPS (基准日 {precomputed_bundle.get('date', '?')})")
            if self._rps120 is None or self._rps250 is None:
                # 兜底现算 RPS（忘记跑 F5 时自动补算）— 使用已有的 Polars 引擎
                try:
                    trade_dt = MarketCalendar.get_latest_trade_date()
                    if trade_dt:
                        today_str = trade_dt.strftime('%Y%m%d')
                    else:
                        today_str = datetime.date.today().strftime('%Y%m%d')
                except Exception as _e:
                    log.debug(f"[盘中] 获取最近交易日失败: {_e}")
                    today_str = datetime.date.today().strftime('%Y%m%d')
                try:
                    import gc
                    from vcp.polars_engine import build_rps_matrix_pl
                    rps_matrix = build_rps_matrix_pl(self._all_data, today_str, today_str)
                    if rps_matrix:
                        d_str = list(rps_matrix.keys())[-1]
                        d_rps = rps_matrix[d_str]
                        self._rps120 = d_rps.get('rps120', {})
                        self._rps250 = d_rps.get('rps250', {})
                        valid_count = sum(1 for v in self._rps120.values() if v == v)

                        # 现算完成后立即释放巨型中间矩阵，避免内存峰值引发 OOM 闪退
                        del d_rps, rps_matrix
                        gc.collect()

                        log.info(f"[盘中] 现算 RPS 完成 ({valid_count} 只)，已释放中间矩阵")

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
                            log.debug(f"[盘中] RPS 已保存磁盘 ({today_str})")
                        except Exception as e:
                            log.error(f"[盘中] RPS 磁盘保存失败: {e}")
                    else:
                        self.progress.emit("❌ RPS 计算失败:无价格数据")
                        return
                except Exception as e:
                    log.error(f"[盘中] 兜底 RPS 计算异常: {e}")
                    self.progress.emit("❌ RPS 计算失败")
                    return


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
                code2name=self.data_provider.code2name,
                progress_callback=lambda msg: self.progress.emit(msg),
                cancelled_checker=lambda: not self._is_running)

            # 【缓存同步】将盘中算出的带有技术指标的 DataFrame 同步回全局字典
            # 防止取消监控后再次进入盘中监控或区间扫描时发生 80 秒的重复初次计算
            for _code, _df in self._all_data.items():
                if 'entangle' in _df.columns:
                    _orig = self.data_provider.cache_data.get(_code)
                    if _orig is not None and 'entangle' not in _orig.columns:
                        with self.data_provider.cache_lock:
                            self.data_provider.cache_data[_code] = _df

            if self._ready_pool is not None:
                old_codes = set(self._ready_pool.keys())
                new_codes = set(new_pool.keys())
                added = new_codes - old_codes
                removed = old_codes - new_codes
                if added or removed:
                    log.info(f"[待突破池] 刷新: +{len(added)} 新增 / -{len(removed)} 剔除 (形态恶化)")
            self._ready_pool = new_pool
            log.info(f"[待突破池] {label}: {len(self._ready_pool)} 只 ({_time.time()-t0:.1f}s)")

        pool_size = len(self._ready_pool)
        self.scan_count.emit(self._scan_count, pool_size)

        # ===== 阶段4: 拉取实时报价 =====
        codes_to_fetch = list(self._ready_pool.keys())
        # 加入关注池代码(即使不在待突破池中)
        from ui.viewmodels.watchlist_vm import watchlist_vm
        special_codes = set(watchlist_vm.get_all_codes())
        
        # 【修复 BUG】清理已剔除出待突破池的历史爆破信号，防止"诈尸"
        stale_signals = [c for c in self._signal_details if c not in self._ready_pool]
        for c in stale_signals:
            del self._signal_details[c]

        # 【#4 修复内存泄漏】同步清理 _seen_signals 中不在新池的 stale key
        # 避免盘中连续运行一整天时 set 无限增长
        self._seen_signals = {
            (code, status) for code, status in self._seen_signals
            if code in self._ready_pool
        }

        # 【修复 BUG】清理已经移出关注池的历史缓存明细，避免断线重连或盘中监控时“诈尸”显示
        stale_keys = [c for c in self._special_details if c not in special_codes]
        for c in stale_keys:
            del self._special_details[c]

        for sc in special_codes:
            if sc not in codes_to_fetch:
                codes_to_fetch.append(sc)

        self.progress.emit(f"第{self._scan_count}轮:拉取 {len(codes_to_fetch)} 只报价...")
        quotes = self.data_provider.fetch_realtime_quotes_batch(codes_to_fetch)
        if not quotes:
            self.progress.emit("实时报价获取失败")
            return

        # 市值动态重算基础：增量缓存总股本，后续每轮按实时现价计算
        codes_need_zbg = [c for c in quotes.keys() if c not in self._zbg_cache]
        if codes_need_zbg:
            try:
                finance_data = VCPEngine.batch_get_finance_info(codes_need_zbg)
                for c in codes_need_zbg:
                    info = finance_data.get(c, {}) if isinstance(finance_data, dict) else {}
                    zbg = float(info.get('zongguben', 0) or 0)
                    if zbg > 0:
                        self._zbg_cache[c] = zbg
            except Exception as _e:
                log.debug(f"[盘中] 总股本缓存刷新失败: {_e}")

        def _format_dynamic_cap(code: str, rt_price: float, fallback: str = "") -> str:
            zbg = float(self._zbg_cache.get(code, 0) or 0)
            if zbg > 0 and rt_price > 0:
                return f"{(zbg * rt_price) / 1e8:.0f}亿"
            return fallback or ""

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
            
            # --- 兜底检查：防御 Pytdx 服务器返回零值 (停牌/断流) ---
            if last_close <= 0:
                hist_df = self.data_provider.get_data(code)
                if hist_df is not None and len(hist_df) > 0:
                    last_close = float(hist_df.iloc[-2]['close']) if len(hist_df) > 1 else float(hist_df.iloc[-1]['close'])
                    
            if rt_close <= 0 and last_close > 0:
                rt_close = last_close
                quote['close'] = rt_close  # 回写防御后续使用 quote['close']

            if last_close > 0 and rt_close > 0:
                pct = ((rt_close / last_close) - 1) * 100
            else:
                pct = 0

            # ------ 关注池股票:完整 evaluate ------
            if is_special:
                has_rps = not (math.isnan(r120) or math.isnan(r250))
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
                cap = _format_dynamic_cap(code, rt_close, pool_entry.get('market_cap', '') if pool_entry else '')
                sector = pool_entry.get('sector_info', '--') if pool_entry else '--'
                sig = {
                    '时间': datetime.datetime.now().strftime('%H:%M'), '代码': code,
                    '名称': self.data_provider.code2name.get(code, code),
                    '现价': f"{quote['close']:.2f}", '涨幅%': f"{pct:+.2f}%",
                    '评分': m.get('评分', '--'), 'RPS强度': rps_display,
                    '市值': cap, '突破状态': status, '热点板块': sector,
                    '区间振幅': m.get('区间振幅', ''),
                    '_is_special': True,   # 标记为关注池股票,盘中监控表格不展示
                }
                if isinstance(m, dict):
                    for k, v in m.items():
                        if k not in sig and v is not None:
                            sig[k] = v
                new_signals.append(sig)
                self._special_details[code] = sig   # 关注池独立存储
                # 不 continue ---- 关注池股票继续走下方 rt_quick_check,
                # 如果也满足盘中突破条件,同样会出现在盘中监控表格中

            # ------ 所有股票(含关注池)统一走待突破池轻量检测 ------
            if math.isnan(r120) or math.isnan(r250):
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
                stock_name = self.data_provider.code2name.get(code, '')
                log.info(f"[盘中] 🔥 {code} {stock_name} | {quote['close']:.2f} | {pct:+.2f}% | {breakout_status}")
                # #15: 桌面通知 + 声音提醒，让用户即使不在终端也能感知
                try:
                    from ui.components.notification_service import notify_breakout
                    notify_breakout(code, stock_name, f"{pct:+.2f}% {breakout_status}")
                except Exception as _e:
                    log.debug(f"[盘中] 桌面通知发送失败: {_e}")  # 通知失败不应影响核心扫描逻辑

            # 构建信号时,优先使用 pool_entry 中的板块/市值,
            # 若为空则保留上一轮已经通过阶段6补全的旧值(防止覆盖)
            prev_sig = self._signal_details.get(code, {})
            _sector = pool_entry.get('sector_info', '') or prev_sig.get('热点板块', '')
            _cap_fallback = pool_entry.get('market_cap', '') or prev_sig.get('市值', '')
            _cap = _format_dynamic_cap(code, rt_close, _cap_fallback)
            sig = {
                '时间': datetime.datetime.now().strftime('%H:%M'), '代码': code,
                '名称': self.data_provider.code2name.get(code, code),
                '现价': f"{quote['close']:.2f}", '涨幅%': f"{pct:+.2f}%",
                '评分': score, 'RPS强度': pool_entry.get('rps_str', f"{r120:.0f}/{r250:.0f}"),
                '市值': _cap,
                '突破状态': f"✓ {breakout_status}",
                '热点板块': _sector,
                '区间振幅': m_meta.get('区间振幅', ''),
            }
            if isinstance(m_meta, dict):
                for k, v in m_meta.items():
                    if k not in sig and v is not None:
                        sig[k] = v
            new_signals.append(sig)
            self._signal_details[code] = sig  # 盘中监控独立存储

        # ===== 阶段6: 补全市值与热点板块(首轮构建缓存,后续复用) =====
        # 合并盘中监控 + 关注池信号（整轮共用，避免重复构建）
        _all_sigs = {**self._signal_details, **self._special_details}
        # 市值按最新现价动态刷新（优先于历史缓存）
        for sig in _all_sigs.values():
            code = sig.get('代码', '')
            q = quotes.get(code, {}) if code else {}
            rt_price = float(q.get('close', 0) or 0)
            if rt_price <= 0:
                try:
                    rt_price = float(str(sig.get('现价', '')).replace(',', ''))
                except (ValueError, TypeError):
                    rt_price = 0
            cap_val = _format_dynamic_cap(code, rt_price, sig.get('市值', ''))
            if cap_val:
                sig['市值'] = cap_val

        codes_need_cap = [sig['代码'] for sig in _all_sigs.values()
                          if not sig.get('市值') and sig['代码'] not in self._cap_cache]
        if codes_need_cap:
            try:
                self.progress.emit(f"补全 {len(codes_need_cap)} 只市值...")
                close_prices = {}
                for c in codes_need_cap:
                    q = quotes.get(c)
                    rt_price = float(q.get('close', 0) or 0) if q else 0
                    
                    if rt_price <= 0:
                        hist = self.data_provider.get_data(c)
                        if hist is not None and len(hist) > 0:
                            rt_price = float(hist.iloc[-1]['close'])
                            
                    close_prices[c] = rt_price
                cap_results = VCPEngine.batch_check_market_cap(codes_need_cap, close_prices=close_prices)
                for c in codes_need_cap:
                    cap = cap_results.get(c)
                    if cap and cap > 0:
                        self._cap_cache[c] = f"{cap / 1e8:.0f}亿"
                    else:
                        self._cap_cache[c] = ''
            except Exception as e:
                log.error(f"[盘中] 市值补全异常: {e}")

        latest_trade_date = MarketCalendar.get_latest_trade_date().strftime("%Y%m%d")

        # 构建板块管理器(仅首轮,后续复用)
        if self._sector_manager is None:
            try:
                self._sector_manager, self._sector_rps, _, source = load_sector_rps_snapshot(
                    self.data_provider,
                    self._all_data,
                    target_date=latest_trade_date,
                    logger=log,
                )
                if self._sector_manager and self._sector_rps:
                    log.info(f"[盘中] 热点板块补全就绪 ({source})")
                else:
                    self._sector_manager = False
                    self._sector_rps = {}
            except Exception as e:
                log.error(f"[盘中] 板块管理器创建异常: {e}")
                self._sector_manager = False  # 标记为失败,不再重试

        # 补全空板块信息（复用上方 _all_sigs，不再重复构建）
        if self._sector_manager and self._sector_rps:
            for sig in _all_sigs.values():
                code = sig['代码']
                # 补全市值
                if not sig.get('市值') and code in self._cap_cache:
                    sig['市值'] = self._cap_cache[code]
            enrich_hot_sector_rows(
                _all_sigs.values(),
                self._sector_manager,
                self._sector_rps,
                logger=log,
            )
        else:
            # 仅补全市值(板块管理器不可用时)
            for sig in _all_sigs.values():
                if not sig.get('市值') and sig['代码'] in self._cap_cache:
                    sig['市值'] = self._cap_cache[sig['代码']]
                sig['热点板块'] = sig.get('热点板块') or '--'

        # 合并盘中监控信号 + 关注池信号(两者独立存储,互不干扰)
        all_signals = list(self._signal_details.values()) + list(self._special_details.values())

        log.info(f"[盘中] 第{self._scan_count}轮 | 池 {pool_size} | 报价 {len(quotes)} | 新信号 {len(new_signals)} | 累计 {len(all_signals)}")
        self.rt_result_ready.emit(all_signals)
