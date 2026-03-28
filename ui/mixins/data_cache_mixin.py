# -*- coding: utf-8 -*-
"""
ui/mixins/data_cache_mixin.py
数据缓存 Mixin — 从 MainWindowQT 中抽离的数据操作方法集

包含：
- F5 盘后预计算
- 延迟加载缓存
- 智能启动
- RPS 缓存加载
- 盘中监控缓存 保存/加载
"""
import os
import datetime
import pickle

from core.logger import get_logger
from core.event_bus import event_bus
from core.task_manager import task_manager

log = get_logger(__name__)


class DataCacheMixin:
    """
    数据缓存操作 Mixin — 提供给 MainWindowQT 使用。

    依赖宿主提供的属性:
    - self.data_provider: TdxDataProvider
    - self.engine: VCPEngine
    - self._call_in_ui(callback): 线程安全 UI 回调
    - self._sig_f5_done: pyqtSignal(int, float)
    - self.lbl_status, self.lbl_code_count: QLabel
    - self.btn_scan, self.btn_cancel: QPushButton
    - self.table_rt: QTableWidget
    """

    # ================================================================
    # 日期辅助
    # ================================================================
    def _set_default_dates(self):
        today = datetime.date.today().strftime('%Y%m%d')
        self.ent_start.setText(today)
        self.ent_end.setText(today)

    def _set_date_range(self, days, ytd=False):
        today = datetime.date.today()
        ed = today.strftime('%Y%m%d')
        if ytd:
            sd = today.strftime('%Y0101')
        else:
            sd = (today - datetime.timedelta(days=days)).strftime('%Y%m%d')
        self.ent_start.setText(sd)
        self.ent_end.setText(ed)

    # ================================================================
    # F5 盘后预计算
    # ================================================================
    def _action_refresh(self):
        """F5 盘后预计算：重读vipdoc -> 重算指标 -> RPS矩阵 -> 保存缓存"""
        import pandas as pd
        from PyQt6.QtWidgets import QMessageBox

        reply = QMessageBox.question(self, "盘后一键预计算",
            "此操作将执行完整的盘后数据重建流程：\n\n"
            "① 从通达信本地日线(vipdoc)重新读取数据\n"
            "② 重算全市场技术指标(MA/MACD等)\n"
            "③ 预计算全市场RPS排名(120日/250日)\n"
            "④ 保存缓存供次日盘中监控使用\n\n"
            "请确保已在通达信中完成【盘后数据下载】.\n是否执行?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)

        if reply != QMessageBox.StandardButton.Yes:
            return

        self.lbl_status.setText("F5 盘后预计算进行中...")
        self.btn_scan.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self._f5_cancelled = False

        def run_f5():
            import time as _time
            import traceback as _tb
            cache_dir = os.path.join(
                os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
                'data', 'Cache'
            )
            os.makedirs(cache_dir, exist_ok=True)
            total_start = _time.time()

            try:
                log.info("\n" + "=" * 60)
                log.info("[F5] 盘后一键预计算 -- 开始")
                log.info("=" * 60)

                # 阶段0: 重新解析除权除息数据
                log.info("[F5] 阶段0: 重新解析通达信 gbbq 除权除息数据...")
                try:
                    self.data_provider._load_local_gbbq(force=True)
                except Exception as e:
                    log.error(f"[F5] gbbq 解析异常(不影响后续): {e}")

                # 阶段1: 重读本地日线
                log.info("[F5] 阶段1/3: 清空缓存,开始从 vipdoc 重读...")
                try:
                    self.data_provider.cache_data = {}
                    was_online = self.data_provider.is_online()
                    self.data_provider.set_online_mode(False)
                    try:
                        codes_dict = self.data_provider._get_codes_from_vipdoc()
                        log.info(f"[F5] 阶段1/3: 从 vipdoc 扫描到 {len(codes_dict)} 只标的")

                        def _progress(done, total, eta):
                            if total > 0 and done % 500 == 0:
                                log.info(f"[F5] 阶段1/3: 重读本地数据 {done}/{total}")

                        self.data_provider.sync_market_data(
                            codes_dict, force_refresh=True, progress_callback=_progress
                        )
                        self.data_provider.code2name = codes_dict
                    finally:
                        if was_online:
                            self.data_provider.set_online_mode(True)
                    count = len(self.data_provider.cache_data)
                    log.info(f"[F5] 阶段1/3 完成 -- 共加载 {count} 只标的")
                except Exception as e:
                    log.error(f"[F5] ❌ 阶段1 重读本地数据异常: {e}")
                    _tb.print_exc()
                    return

                if getattr(self, '_f5_cancelled', False):
                    log.info("[F5] ⏹ 用户取消")
                    return

                # 阶段2: 预计算 RPS 矩阵
                log.info("[F5] 阶段2/3: 预计算 RPS 矩阵...")
                try:
                    all_data = {
                        c: df for c, df in self.data_provider.cache_data.items()
                        if df is not None and len(df) >= 60
                    }
                    log.info(f"[F5] 阶段2/3: 有效标的 {len(all_data)} 只(>=60根K线)")
                    today_str = datetime.date.today().strftime('%Y%m%d')
                    rps_matrix = self.engine.build_rps_matrix(all_data, today_str, today_str)

                    if rps_matrix:
                        d_str = list(rps_matrix.keys())[-1]
                        d_rps = rps_matrix[d_str]
                        rps120 = pd.Series(d_rps.get('rps120', {}))
                        rps250 = pd.Series(d_rps.get('rps250', {}))
                        rps_pkg = {'date': d_str, 'rps120': rps120, 'rps250': rps250}
                        rps_path = os.path.join(cache_dir, 'vcp_rps_precomputed.pkl')
                        with open(rps_path, 'wb') as f:
                            pickle.dump(rps_pkg, f, protocol=4)
                        self.engine.set_precomputed_rps(d_str, rps120, rps250)
                        valid_count = int(rps120.notna().sum())
                        log.info(f"[F5] 阶段2/3 完成 -- RPS 已计算并保存"
                              f"({valid_count} 只有效排名,基准日 {d_str})")
                    else:
                        log.warning("[F5] ⚠ 阶段2/3: RPS 矩阵计算返回空")
                except Exception as e:
                    log.error(f"[F5] ❌ 阶段2 RPS 计算异常: {e}")
                    _tb.print_exc()

                if getattr(self, '_f5_cancelled', False):
                    log.info("[F5] ⏹ 用户取消")
                    return

                # 阶段2.5: 预计算板块 RPS
                log.info("[F5] 阶段2.5/3: 预计算板块 RPS...")
                try:
                    from vcp.sector import SectorManager
                    from vcp.constants import SECTOR_RPS_CACHE_FILE
                    tdx_root = (
                        os.path.dirname(self.data_provider.tdx_vipdoc)
                        if self.data_provider.tdx_vipdoc else r'D:\\HT'
                    )
                    sm = SectorManager(tdx_root)
                    all_data_f5 = {
                        c: df for c, df in self.data_provider.cache_data.items()
                        if df is not None and len(df) >= 60
                    }
                    sector_date = datetime.date.today().strftime('%Y%m%d')
                    sector_rps = sm.build_sector_rps(all_data_f5, sector_date)
                    sector_pkg = {'date': sector_date, 'sector_rps': sector_rps}
                    with open(SECTOR_RPS_CACHE_FILE, 'wb') as f:
                        pickle.dump(sector_pkg, f, protocol=4)
                    log.info(f"[F5] 阶段2.5/3 完成 -- 板块 RPS ({len(sector_rps)} 个板块)")
                except Exception as e:
                    log.error(f"[F5] ❌ 阶段2.5 板块 RPS 异常: {e}")
                    _tb.print_exc()

                elapsed = _time.time() - total_start
                log.info(f"[F5] ✅ 全部完成 -- 耗时 {elapsed:.1f} 秒")

            except Exception as e:
                log.error(f"[F5] ❌ 预计算过程发生未预期异常: {e}")
                _tb.print_exc()
            finally:
                elapsed = _time.time() - total_start
                count = len(self.data_provider.cache_data) if self.data_provider.cache_data else 0
                log.info(f"[F5] 正在恢复UI状态... (count={count}, elapsed={elapsed:.1f}s)")
                self._sig_f5_done.emit(count, elapsed)

        task_manager.run_in_background(run_f5, task_id="f5_precompute")

    # ================================================================
    # 延迟加载 + 智能启动
    # ================================================================
    def _deferred_data_load(self):
        """延迟加载缓存数据（pkl + RT缓存 + RPS缓存），避免阻塞UI线程"""
        def _load_bg():
            try:
                cache_date = self.data_provider.load_cache_from_disk()
                if cache_date:
                    self._cache_date = cache_date
                    count = len(self.data_provider.cache_data)
                    self._call_in_ui(
                        lambda: self.lbl_code_count.setText(f"标的池: {count}")
                    )
                    self._call_in_ui(lambda: self.lbl_status.setText(
                        f"已加载 {count} 只标的缓存 (日期: {cache_date})"
                    ))
            except Exception as e:
                log.error(f"[启动] 延迟加载缓存异常: {e}")

            # 在UI线程恢复RT缓存
            self._call_in_ui(self._load_rt_cache)

            # 加载 RPS 预计算缓存
            try:
                self._try_load_rps_from_disk()
            except Exception as e:
                log.error(f"[启动] RPS 缓存加载异常: {e}")

            # 通知各 Tab: 缓存数据已就绪，可以回填历史数据
            self._call_in_ui(
                lambda: event_bus.sig_data_updated.emit("cache_loaded", None)
            )

        task_manager.run_in_background(_load_bg, task_id="deferred_load")

    def _smart_startup(self):
        """智能启动：异步检测网络，联网可用则自动切换联网模式"""
        def _check_and_go_online():
            try:
                if self.data_provider.test_network(timeout=3):
                    self.data_provider.set_online_mode(True)
                    self._call_in_ui(lambda: self._update_network_ui(True))
                    log.info("[智能启动] ✅ 网络可用，已自动切换到联网模式")
                    self._call_in_ui(self._auto_start_rt_if_ready)
                else:
                    log.info("[智能启动] 网络不可用，保持离线模式")
            except Exception as e:
                log.error(f"[智能启动] 网络检测异常: {e}")

        task_manager.run_in_background(_check_and_go_online, task_id="smart_startup")

    def _auto_start_rt_if_ready(self):
        """智能启动后自动开启盘中监控（仅在交易时间且数据就绪时）"""
        try:
            from vcp.constants import MARKET_OPEN_AM, MARKET_CLOSE_PM
            now = datetime.datetime.now()
            h, m = now.hour, now.minute
            in_market = (
                (h > MARKET_OPEN_AM[0] or (h == MARKET_OPEN_AM[0] and m >= MARKET_OPEN_AM[1]))
                and (h < MARKET_CLOSE_PM[0] or (h == MARKET_CLOSE_PM[0] and m <= MARKET_CLOSE_PM[1]))
            )
            if not in_market:
                log.info("[智能启动] 非交易时间，跳过盘中自动监控")
                return
            if not self.data_provider.cache_data or len(self.data_provider.cache_data) < 100:
                log.info("[智能启动] 数据不足，跳过盘中自动监控")
                return
            if hasattr(self, 'tab_rt') and hasattr(self.tab_rt, '_toggle_rt_monitor'):
                self.tab_rt._toggle_rt_monitor()
                log.info("[智能启动] ✅ 盘中监控已自动启动")
        except Exception as e:
            log.error(f"[智能启动] 自动监控启动异常: {e}")

    # ================================================================
    # RPS 缓存
    # ================================================================
    def _try_load_rps_from_disk(self):
        """尝试从磁盘加载 F5 预计算的 RPS 缓存"""
        cache_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'data', 'Cache'
        )
        rps_path = os.path.join(cache_dir, 'vcp_rps_precomputed.pkl')
        if not os.path.exists(rps_path):
            return
        try:
            with open(rps_path, 'rb') as f:
                pkg = pickle.load(f)
            cached_date = pkg.get('date', '')
            rps120 = pkg.get('rps120')
            rps250 = pkg.get('rps250')
            if rps120 is None or rps250 is None:
                return
            self.engine.set_precomputed_rps(cached_date, rps120, rps250)
            count = int(rps120.notna().sum()) if hasattr(rps120, 'notna') else 0
            log.info(f"[RPS] ✓ 从磁盘加载预计算RPS(基准日 {cached_date},{count} 只有效排名)")
            self.lbl_status.setText(f"RPS缓存已加载({cached_date},{count}只)")
        except Exception as e:
            log.error(f"[RPS] 磁盘加载失败: {e}")

    # ================================================================
    # 盘中监控缓存（保存/加载）
    # ================================================================
    def _save_rt_cache(self):
        """保存盘中监控当日缓存到 pkl 文件"""
        import re
        from PyQt6.QtWidgets import QTableWidgetItem
        table = self.table_rt
        if table.rowCount() == 0:
            return
        cache_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'data', 'Cache'
        )
        os.makedirs(cache_dir, exist_ok=True)
        try:
            rows = []
            for r in range(table.rowCount()):
                row_vals = []
                for c in range(table.columnCount()):
                    item = table.item(r, c)
                    row_vals.append(item.text() if item else '')
                rows.append(row_vals)
            if rows and rows[0]:
                first_cell = rows[0][0]
                if len(first_cell) > 10 or '(' in first_cell or ',' in first_cell:
                    log.error(f"[盘中缓存] 检测到异常数据,跳过保存")
                    return
            data = {
                'date': datetime.date.today().isoformat(),
                'version': 2,
                'rows': rows,
                'headers': [
                    table.horizontalHeaderItem(c).text()
                    for c in range(table.columnCount())
                ],
            }
            path = os.path.join(
                cache_dir, f"rt_monitor_{datetime.date.today().isoformat()}.pkl"
            )
            with open(path, 'wb') as f:
                pickle.dump(data, f, protocol=4)
            log.info(f"[盘中缓存] 已保存 {len(rows)} 条信号到 {os.path.basename(path)}")

            # 清理超过 10 天的旧缓存
            today = datetime.date.today()
            for fname in os.listdir(cache_dir):
                if fname.startswith('rt_monitor_') and fname.endswith('.pkl'):
                    m = re.search(r'rt_monitor_(\d{4}-\d{2}-\d{2})\.pkl', fname)
                    if m:
                        try:
                            fdate = datetime.datetime.strptime(
                                m.group(1), '%Y-%m-%d'
                            ).date()
                            if (today - fdate).days > 10:
                                os.remove(os.path.join(cache_dir, fname))
                        except Exception:
                            pass
        except Exception as e:
            log.error(f"[盘中缓存] 保存失败: {e}")

    def _load_rt_cache(self):
        """启动时加载最近的盘中监控缓存"""
        from PyQt6.QtWidgets import QTableWidgetItem
        from PyQt6.QtGui import QColor
        from PyQt6.QtCore import Qt
        from ui.components import NumericTableWidgetItem

        cache_dir = os.path.join(
            os.path.dirname(os.path.dirname(os.path.dirname(__file__))),
            'data', 'Cache'
        )
        path = None
        for days_ago in range(10):
            check_date = datetime.date.today() - datetime.timedelta(days=days_ago)
            candidate = os.path.join(
                cache_dir, f"rt_monitor_{check_date.isoformat()}.pkl"
            )
            if os.path.exists(candidate):
                path = candidate
                break
        if not path:
            return
        try:
            with open(path, 'rb') as f:
                data = pickle.load(f)
            raw_rows = data.get('rows', [])
            if not raw_rows:
                return
            cache_date = data.get('date', '?')

            # 自动检测格式
            first = raw_rows[0]
            is_old_format = (
                isinstance(first, (list, tuple)) and len(first) == 2
                and isinstance(first[0], (list, tuple))
                and isinstance(first[1], (list, tuple))
            )

            table = self.table_rt
            if is_old_format:
                table.setSortingEnabled(False)
                table.setRowCount(len(raw_rows))
                for r, (texts, _colors) in enumerate(raw_rows):
                    for c, text in enumerate(texts):
                        if c < table.columnCount():
                            item = NumericTableWidgetItem(str(text)) if c in (3, 4, 5) else QTableWidgetItem(str(text))
                            item.setForeground(QColor("#C9CDD4"))
                            item.setTextAlignment(
                                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                            )
                            table.setItem(r, c, item)
                table.setSortingEnabled(True)
            else:
                table.setSortingEnabled(False)
                table.setRowCount(len(raw_rows))
                for r, row_vals in enumerate(raw_rows):
                    for c, text in enumerate(row_vals):
                        if c < table.columnCount():
                            item = NumericTableWidgetItem(str(text)) if c in (3, 4, 5) else QTableWidgetItem(str(text))
                            item.setForeground(QColor("#C9CDD4"))
                            item.setTextAlignment(
                                Qt.AlignmentFlag.AlignCenter | Qt.AlignmentFlag.AlignVCenter
                            )
                            table.setItem(r, c, item)
                table.setSortingEnabled(True)

            self.lbl_status.setText(
                f"已恢复盘中缓存 ({cache_date}, {len(raw_rows)} 条)"
            )
        except Exception as e:
            log.error(f"[盘中缓存] 加载失败: {e}")
