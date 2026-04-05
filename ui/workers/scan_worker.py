# ui/workers.py - 后台工作线程
# 从 main_window_qt.py 拆分出来的 ScanWorker 和 RtScanWorker
import os
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
        import time as _time
        _total_start = _time.time()
        
        # 为了避免 0% 导致 UI 立即释放按钮状态，改为发 'start'
        self.progress.emit(1, "start")

        try:
            # 1. 重建除权除息数据（强制保证历史计算准确）
            self.progress.emit(5, "正在更新除权除息数据 (gbbq)...")
            _t0 = _time.time()
            self.data_provider._load_local_gbbq(force=True)
            log.info(f"[耗时监控] gbbq加载完成，耗时: {_time.time() - _t0:.2f} 秒")

            self.progress.emit(1, "正在查询数据...")
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
            _t1 = _time.time()
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
                           if pd.notna(v) and (v >= self.params.rps_threshold or d_rps['rps120'].get(k, 0) >= self.params.rps_threshold)]
                
                for idx_code, code in enumerate(targets):
                    # 【休眠释放 GIL】每完成几只后主动释放 CPU
                    # 防止 ScanWorker 把后台 CPU 占满导致主 UI 卡死或盘中监控无响应
                    if idx_code % 20 == 0:
                        _time.sleep(0.001)
                        
                    # === ST 股过滤:ST/*ST 涨跌幅仅 5%,易伪装成 VCP 收缩形态 ===
                    stock_name = self.data_provider.code2name.get(code, '')
                    if 'ST' in stock_name.upper():
                        continue
                    df = self.data_provider.get_data(code)
                    if df is not None:
                        try:
                            # 【并发安全与缓存加速】避免区间扫描的每日历次重复计算指标。
                            # 先在 copy 上计算以保障安全，然后通过 dict 更新覆盖缓存，一劳永逸。
                            if 'entangle' not in df.columns:
                                df = VCPEngine.calculate_indicators(df.copy())
                                self.data_provider.cache_data[code] = df
                            df_safe = df

                            # 【与盘中一致】skip_red_check=True，
                            # 红盘仅是盘中实时判断条件，盘后扫描不应因为
                            # 当日收阴就漏掉形态完好的标的
                            ok, reason, m = self.engine.evaluate_conditions(
                                df_safe, pd.to_datetime(d_str),
                                d_rps['rps120'].get(code, 0),
                                d_rps['rps250'].get(code, 0), None,
                                self.params, skip_red_check=True)
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
            
            log.info(f"[耗时监控] RPS第一阶段扫描完成，耗时: {_time.time() - _t1:.2f} 秒，过滤后剩余 {len(all_results)} 只")
            
            # ---- 二级过滤:与盘中监控对齐的机构+市值筛选 ----
            if all_results:
                self.progress.emit(99, "计算市值...")
                _t2 = _time.time()
                df_res = pd.DataFrame(all_results)
                unique_codes = df_res['代码'].unique().tolist()
                _scan_close = {}
                for c in unique_codes:
                    _cd = self.data_provider.cache_data.get(c)
                    if _cd is not None and not _cd.empty:
                        _scan_close[c] = float(_cd.iloc[-1]['close'])
                        
                # 批量查询市值
                cap_results = VCPEngine.batch_check_market_cap(unique_codes, close_prices=_scan_close)
                
                for res in all_results:
                    c = res['代码']
                    cap = cap_results.get(c)
                    if cap and cap > 0:
                        res['市值'] = f"{cap / 1e8:.0f}亿"
                        res['_cap_raw'] = cap  # 保留原始值用于后续过滤
                    else:
                        res['市值'] = "--"
                        res['_cap_raw'] = 0
                        
                log.info(f"[耗时监控] 批量查询市值完成，耗时: {_time.time() - _t2:.2f} 秒")

            # 因用户要求区间扫描需全面、不漏票，此处取消剔除市值<40亿的盘中监控硬过滤机制
            # 让区间扫描忠于技术形态，展示所有满足 VCP 的股票。
            # 市值计算仍保留，仅为了在界面展示数值（但不剔除）。
            
            # 由于用户要求加快扫描速度，机构过滤对于初始区间扫描过于耗时（需排队查网页），故此处剔除机构筛选逻辑。
            # 如果需要看机构，可以在盘中监控或关注池中再进行查看。


            # 按评分倒序
            if all_results:
                all_results.sort(key=lambda x: x.get('评分', 0), reverse=True)

            log.info(f"[耗时监控] 区间扫描总计耗时: {_time.time() - _total_start:.2f} 秒，最终产生 {len(all_results)} 条结果")

            # 清理内部临时字段
            for r in all_results:
                r.pop('_cap_raw', None)

            # Enrich 热点板块(板块 RPS)
            if all_results:
                self.progress.emit(99, "查询热点板块...")
                try:
                    from vcp.sector import SectorManager
                    tdx_root = os.path.dirname(self.data_provider.tdx_vipdoc) if self.data_provider.tdx_vipdoc else r'D:\\HT'
                    sm = SectorManager.get_instance(tdx_root)
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
