import os
import time
import datetime
import json
import re
import concurrent.futures
import pandas as pd
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
    QLineEdit, QDialog, QDateEdit, QTextEdit,
    QTableWidgetItem
)
from ui.components.toast_widget import show_toast
from PyQt6.QtCore import Qt, QTimer, QDate
from PyQt6.QtGui import QColor

from vcp.engine import VCPEngine
from core.event_bus import event_bus
from core.logger import get_logger
from core.task_manager import task_manager

log = get_logger(__name__)

class AIDiagPanel(QFrame):
    """
    AI 智能诊断右侧随动面板
    负责处理本群标的单独分析以及全局的一键大模型批量诊断。
    """
    def __init__(self, data_provider, kimi_service, main_window):
        # 传递 main_window 用于局部向下兼容访问 table_sp，直到 watchlist_tab 完全解耦
        super().__init__()
        self.data_provider = data_provider
        self._kimi_service = kimi_service
        self.main_window = main_window 
        self._ai_diag_results = {}
        
        self.setObjectName("moduleCard")
        self.hide() # 默认隐藏
        self._init_ui()
        
        # 加载历史缓存
        QTimer.singleShot(500, self._load_ai_diag_cache)
        
        # 监听来自其他组件的需求事件 (预留)
        # event_bus.sig_open_ai_diag.connect(self.open_ai_diag)

    def _init_ui(self):
        ai_layout = QVBoxLayout(self)
        ai_layout.setContentsMargins(12, 12, 12, 12)
        ai_layout.setSpacing(8)

        ai_header = QHBoxLayout()
        ai_title = QLabel("🤖 AI 深度诊断")
        ai_title.setStyleSheet("font-size: 14px; font-weight: bold; color: #E5E7EB;")
        btn_close_ai = QPushButton("关闭")
        btn_close_ai.setObjectName("iconButton")
        btn_close_ai.setFixedSize(24, 24)
        btn_close_ai.clicked.connect(self.hide)
        
        ai_header.addWidget(ai_title)
        ai_header.addStretch()
        ai_header.addWidget(btn_close_ai)
        ai_layout.addLayout(ai_header)

        input_container = QFrame()
        input_container.setStyleSheet("background-color: #1E293B; border-radius: 6px; padding: 4px;")
        input_layout = QVBoxLayout(input_container)
        input_layout.setContentsMargins(6,6,6,6)
        input_layout.setSpacing(6)
        
        row1 = QHBoxLayout()
        self.code_input = QLineEdit()
        self.code_input.setPlaceholderText("请输入 6 位代码")
        self.code_input.setFixedWidth(120)
        self.code_input.setStyleSheet("background: #0F172A; border: 1px solid rgba(255,255,255,0.1); border-radius: 4px;")
        self.date_input = QDateEdit()
        self.date_input.setCalendarPopup(True)
        self.date_input.setDisplayFormat("yyyy-MM-dd")
        self.date_input.setDate(QDate.currentDate())
        self.date_input.setStyleSheet("background: #0F172A; border: 1px solid rgba(255,255,255,0.1); border-radius: 4px;")
        
        row1.addWidget(QLabel("代码:"))
        row1.addWidget(self.code_input)
        row1.addWidget(QLabel("基准日:"))
        row1.addWidget(self.date_input)
        row1.addStretch()
        input_layout.addLayout(row1)
        
        row2 = QHBoxLayout()
        btn_local = QPushButton("🧪 本地评估")
        btn_ai = QPushButton("🧠 深度扫描")
        btn_local.setObjectName("secondaryButton")
        btn_ai.setObjectName("primaryButton")
        btn_local.clicked.connect(lambda *args: self._handle_diag_start("local"))
        btn_ai.clicked.connect(lambda *args: self._handle_diag_start("kimi"))
        row2.addWidget(btn_local)
        row2.addWidget(btn_ai)
        input_layout.addLayout(row2)
        
        ai_layout.addWidget(input_container)

        self.ai_content = QTextEdit()
        self.ai_content.setReadOnly(True)
        self.ai_content.setStyleSheet("background-color: #0A0C10; color: #C9CDD4; border: 1px solid rgba(255, 255, 255, 0.05); border-radius: 6px; padding: 8px;")
        ai_layout.addWidget(self.ai_content)

    def _merge_and_wrap_ai_diag(self, text):
        if not text or text == '--': return ""
        text = str(text).replace('\n', ' ')
        return text[:25] + "..." if len(text) > 25 else text

    def open_ai_diag(self, preset_code="", auto_start=""):
        if not isinstance(preset_code, str): preset_code = ""
        code = preset_code.strip()

        if not code and hasattr(self.main_window, 'tabs'):
            curr_tab = self.main_window.tabs.currentIndex()
            if curr_tab == 1 and hasattr(self.main_window, 'table_rt') and self.main_window.table_rt.currentItem():
                code = self.main_window.table_rt.item(self.main_window.table_rt.currentRow(), 0).text()
            elif curr_tab == 2 and hasattr(self.main_window, 'table_sp') and self.main_window.table_sp.currentItem():
                code = self.main_window.table_sp.item(self.main_window.table_sp.currentRow(), 0).text()
            elif curr_tab == 0 and hasattr(self.main_window, 'table_scan') and getattr(self.main_window, 'table_scan', None) and self.main_window.table_scan.currentItem():
                code = self.main_window.table_scan.item(self.main_window.table_scan.currentRow(), 1).text()

        if code and len(code) == 6:
            self.code_input.setText(code)

        self.show()
        curr_sizes = self.main_window.right_splitter.sizes()
        if curr_sizes[1] == 0:
            total = sum(curr_sizes)
            self.main_window.right_splitter.setSizes([int(total*0.75), int(total*0.25)])
            
        if auto_start in ('local', 'kimi') and code and len(code) == 6:
            self._handle_diag_start(auto_start)

    def _handle_diag_start(self, mode):
        code = self.code_input.text().strip()
        if not re.match(r'^\d{6}$', code):
            show_toast("请输入正确的 6 位股票代码!", "warning", self)
            self.code_input.setFocus()
            return
            
        diag_date_str = self.date_input.date().toString("yyyy-MM-dd")
        if mode == "local":
            self._run_local_diag_sidebar(code, diag_date_str)
        else:
            self._run_kim_diag_sidebar(code, diag_date_str)

    def _run_local_diag_sidebar(self, code, diag_date=""):
        name = getattr(self.data_provider, 'code2name', {}).get(code, "未知")
        self.ai_content.setHtml(f"<div style='color: #10B981; font-weight: bold;'>🧪 本地技术诊断进行中...</div><div style='color: #64748B;'>计算 {name}({code}) 的形态与指标...</div>")
        
        def do_local_diag():
            try:
                ok, msg = self._get_technical_report_text(code, name, diag_date)
            except Exception as e:
                ok, msg = False, f"本地诊断异常: {e}"
            event_bus.sig_ui_task.emit(lambda: self.ai_content.setHtml(self.ai_content.toHtml() + f"<br><div style='color: #E2E8F0;'>{msg}</div>" if ok else f"<br><div style='color: #EF4444;'>❌ {msg}</div>"))
        task_manager.run_in_background(do_local_diag, task_id="ai_local_diag")

    def _run_kim_diag_sidebar(self, code, diag_date=""):
        name = getattr(self.data_provider, 'code2name', {}).get(code, "未知")
        
        # HTML 进度指示，动态 P1/P6/P8 面板
        self.ai_content.setHtml(f"<div style='color: #8B5CF6; font-weight: bold;'>🧠 AI 深度诊断正在进行中...</div><br><div style='color: #38BDF8;'>[ P1 大数据特征抽取 ] 收录行情切片与基本面池...</div>")
        
        from vcp.utils import _get_kimi_api_key, _load_ai_diag_config
        cfg = _load_ai_diag_config()
        api_key = (cfg.get("kimi_api_key") or "").strip() or _get_kimi_api_key()
        
        def update_progress(step, msg):
            html = self.ai_content.toHtml() + f"<div style='color: #A78BFA; margin-top: 4px;'>[ L{step} 智能流水线 ] {msg}</div>"
            self.ai_content.setHtml(html)

        def do_request():
            import time
            time.sleep(0.6)
            event_bus.sig_ui_task.emit(lambda: update_progress(2, "合并图表结构模型，提纯技术支撑位..."))
            time.sleep(1.2)
            event_bus.sig_ui_task.emit(lambda: update_progress(3, "唤醒 Kimi 云端大语言模型，计算投资研判..."))

            try:
                ok, msg = self._kimi_service.call_kimi_diag(api_key, code, name, diag_date=diag_date)
            except Exception as e:
                ok, msg = False, f"调用网络大模型异常: {e}"
            event_bus.sig_ui_task.emit(lambda: on_done(ok, msg, code))
            
        def on_done(ok, msg, c):
            if ok:
                fmt_msg = msg.replace('\\n', '<br>')
                html = self.ai_content.toHtml() + f"<br><div style='color: #E2E8F0; padding: 6px; border-left: 3px solid #6366F1;'>{fmt_msg}</div><br><div style='color: #10B981; font-weight: bold;'>✅ 诊断数据处理就绪并已同步至内存池。</div>"
                self.ai_content.setHtml(html)
                self._apply_ai_diag_result(c, msg.strip())
            else:
                html = self.ai_content.toHtml() + f"<br><div style='color: #EF4444; font-weight: bold;'>❌ {msg}</div>"
                self.ai_content.setHtml(html)
                
        task_manager.run_in_background(do_request, task_id="ai_kimi_diag")

    def _get_technical_report_text(self, code, name, diag_date=""):
        df = self.data_provider.get_data(code)
        if (df is None or len(df) < 60) and self.data_provider.tdx_vipdoc:
            try:
                local_df = self.data_provider._fetch_from_local_tdx(code)
                if local_df is not None and len(local_df) >= 60:
                    if 'vol' in local_df.columns: local_df.rename(columns={'vol': 'volume'}, inplace=True)
                    df = local_df
            except Exception: pass
            
        if df is None or len(df) < 60:
            return False, "数据样本不足60个交易日,无法生成技术报告."
            
        if diag_date:
            try:
                cutoff = pd.Timestamp(diag_date)
                if df.index.dtype == 'datetime64[ns]' or hasattr(df.index, 'date'):
                    df = df[df.index <= cutoff]
                elif 'date' in df.columns:
                    df = df[pd.to_datetime(df['date']) <= cutoff]
            except Exception: pass
            
        df = VCPEngine.calculate_indicators(df)
        last = df.iloc[-1]
        try:
            actual_date = last.name.strftime('%Y-%m-%d') if hasattr(last.name, 'strftime') else str(df.iloc[-1]['date'])[:10]
        except (ValueError, TypeError): actual_date = ""
        
        close = last['close']
        rsi, macd_hist = last.get('RSI', 50.0), last.get('MACD_Hist', 0.0)
        ma50, bb_up, bb_low = last.get('SMA50', close), last.get('BB_up', close), last.get('BB_low', close)
        
        trend = "多头排列 🚀" if close > ma50 else "震荡/空头 ⚠️"
        rsi_stat = "超买" if rsi > 70 else ("超卖" if rsi < 30 else "中性健康")
        macd_stat = "金叉发散 🟢" if macd_hist > 0 else "死叉/走弱 🔴"
        
        report = f"【本地诊断】 {name} ({code})\n▶ 日期: {actual_date}\n▶ 收盘: {close:.2f}\n▶ 趋势: {trend}\n▶ RSI: {rsi:.2f} ({rsi_stat})\n▶ MACD: {macd_stat}动能: {macd_hist:.3f}\n\n[阻力支撑]\n⬆️ 强阻力: {bb_up:.2f}\n⬇️ 强支撑: {bb_low:.2f}\n🌀 中轴50日: {ma50:.2f}"
        return True, report

    # ===============================
    # 关注池状态更新映射 (向下兼容层待进一步解脱)
    # ===============================
    def _apply_ai_diag_result(self, code, text):
        if not code: return
        self._ai_diag_results[code] = text
        display_text = self._merge_and_wrap_ai_diag(text)
        tip_html = f'<div style="max-width:450px; white-space:pre-wrap;">{text}</div>'
        
        # 通知主界面直接操作 table_sp 更新对应列
        if hasattr(self.main_window, 'table_sp'):
            t = self.main_window.table_sp
            # 兼容 QTableView (MVC)
            if hasattr(t, 'model') and getattr(t, 'model', lambda: None)():
                model = t.model()
                if hasattr(model, 'row_data'):
                    final_list = list(model.row_data)
                    dirty = False
                    for row_data in final_list:
                        if row_data.get('代码', '') == code:
                            if row_data.get('AI结论') != display_text:
                                row_data['AI结论'] = display_text
                                dirty = True
                            break
                    if dirty and hasattr(model, 'update_data'):
                        model.update_data(final_list)
                        
            # 兼容 QTableWidget
            elif hasattr(t, 'rowCount'):
                for row in range(t.rowCount()):
                    item_code = t.item(row, 0)
                    if item_code and item_code.text() == code:
                        ai_item = t.item(row, 9)
                        if ai_item:
                            ai_item.setText(display_text)
                            ai_item.setToolTip(tip_html)
                        else:
                            from PyQt6.QtWidgets import QTableWidgetItem
                            new_item = QTableWidgetItem(display_text)
                            new_item.setToolTip(tip_html)
                            new_item.setForeground(QColor("#C9CDD4"))
                            new_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                            t.setItem(row, 9, new_item)
                        break
                    
        # 直接更新ViewModel缓冲
        from ui.viewmodels.watchlist_vm import watchlist_vm
        if watchlist_vm.is_in_watchlist(code):
            try:
                current_cache = watchlist_vm.get_watchlist_data()
                current_cache[code]["AI诊断"] = text
                watchlist_vm._cache = current_cache
                watchlist_vm._save_data()
            except Exception as e: log.error(f"[AI诊断] 缓存写入异常: {e}")

    def refresh_ai_column_from_cache(self):
        """一次性从内存回填关注池全部数据"""
        if not hasattr(self.main_window, 'table_sp'): return
        t = self.main_window.table_sp
        
        # 兼容 QTableView (MVC)
        if hasattr(t, 'model') and getattr(t, 'model', lambda: None)():
            model = t.model()
            if hasattr(model, 'row_data'):
                final_list = list(model.row_data)
                dirty = False
                for row_data in final_list:
                    code = row_data.get('代码', '')
                    ai_text = self._ai_diag_results.get(code, '')
                    if ai_text:
                        display_text = self._merge_and_wrap_ai_diag(ai_text)
                        if row_data.get('AI结论') != display_text:
                            row_data['AI结论'] = display_text
                            dirty = True
                if dirty and hasattr(model, 'update_data'):
                    model.update_data(final_list)
            return

        if not hasattr(t, 'rowCount'): return
        for row in range(t.rowCount()):
            item_code = t.item(row, 0)
            if not item_code: continue
            code = item_code.text()
            ai_text = self._ai_diag_results.get(code, '')
            if not ai_text: continue
            display_text = self._merge_and_wrap_ai_diag(ai_text)
            tip_html = f'<div style="max-width:450px; white-space:pre-wrap;">{ai_text}</div>'
            ai_item = t.item(row, 9)
            if ai_item:
                ai_item.setText(display_text)
                ai_item.setToolTip(tip_html)
            else:
                new_item = QTableWidgetItem(display_text)
                new_item.setToolTip(tip_html)
                new_item.setForeground(QColor("#C9CDD4"))
                new_item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                t.setItem(row, 9, new_item)

    # ==========================
    # 批量一键大模型扫描
    # ==========================
    def run_special_pool_ai_diag_all(self):
        from ui.viewmodels.watchlist_vm import watchlist_vm
        data_dict = watchlist_vm.get_watchlist_data()
        if not data_dict:
            show_toast("关注池为空", "warning", self)
            return
            
        codes = list(data_dict.keys())
        total = len(codes)
        
        # 使用全局事件让宿主锁定 btn_special_diag
        event_bus.sig_task_progress.emit("ai_diag", 0, str(total))
        
        self._diag_progress = 0
        self._diag_errors = 0
        self._diag_done = False
        self._diag_total = total
        self._diag_logs = []
        
        from vcp.utils import _get_kimi_api_key, _load_ai_diag_config
        cfg = _load_ai_diag_config()
        api_key = (cfg.get("kimi_api_key") or "").strip() or _get_kimi_api_key()
        
        def run_bg():
            self._diag_logs.append(f"[AI诊断] 开始批量诊断关注池 {total} 只标的...")
            def run_one(c):
                name = getattr(self.data_provider, 'code2name', {}).get(c, "未知")
                self._diag_logs.append(f"[AI诊断] -> 请求 Kimi: {name}({c})")
                ok, msg = self._kimi_service.call_kimi_diag(api_key, c, name)
                return c, ok, msg
                
            try:
                with concurrent.futures.ThreadPoolExecutor(max_workers=5) as executor:
                    futures = [executor.submit(run_one, c) for c in codes]
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            c, ok, msg = future.result(timeout=180)
                            c_name = getattr(self.data_provider, 'code2name', {}).get(c, c)
                            if ok and msg:
                                self._diag_logs.append(f"[AI诊断] ✅ {c_name}({c}) 成功")
                                self._ai_diag_results[c] = msg.strip()
                            else:
                                self._diag_errors += 1
                                self._diag_logs.append(f"[AI诊断] ❌ {c_name}({c}) 失败: {msg}")
                        except Exception as e:
                            self._diag_errors += 1
                            self._diag_logs.append(f"[AI诊断] ❌ 任务异常: {e}")
                        self._diag_progress += 1
            except Exception as e:
                self._diag_logs.append(f"[AI诊断] ❌ 致命错误: {e}")
            finally:
                self._diag_done = True
                
        self._diag_poll_timer = QTimer()
        def _poll():
            while self._diag_logs:
                event_bus.sig_system_log.emit("info", self._diag_logs.pop(0))
            event_bus.sig_task_progress.emit("ai_diag", int(self._diag_progress), str(self._diag_total))
            if self._diag_done:
                self._diag_poll_timer.stop()
                self._finish_batch_diag()
        self._diag_poll_timer.timeout.connect(_poll)
        self._diag_poll_timer.start(500)
        
        task_manager.run_in_background(run_bg, task_id="ai_batch_diag")

    def _finish_batch_diag(self):
        event_bus.sig_task_progress.emit("ai_diag", -1, str(self._diag_errors))
        if self._diag_errors > 0:
            event_bus.sig_system_log.emit("warning", f"关注池诊断完成, {self._diag_errors} 只失败")
        else:
            event_bus.sig_system_log.emit("info", "✅ 关注池批量诊断顺利完成")
        self.refresh_ai_column_from_cache()
        self.save_ai_diag_cache()

    def _load_ai_diag_cache(self):
        cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'Cache')
        sp_path = os.path.join(cache_dir, 'ai_diag_special.json')
        if os.path.exists(sp_path):
            try:
                with open(sp_path, 'r', encoding='utf-8') as f: data = json.load(f)
                results = data.get('results') or {}
                for code, val in results.items():
                    if isinstance(val, dict): text, ts = val.get('text', ''), val.get('ts', 0)
                    else: text, ts = str(val), 0
                    if ts > 0 and (time.time() - ts) > 5 * 86400: continue
                    if text: self._ai_diag_results[code] = text
                # 初次启动主动回填
                QTimer.singleShot(2000, self.refresh_ai_column_from_cache)
            except Exception: pass

    def save_ai_diag_cache(self):
        if not self._ai_diag_results: return
        cache_dir = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), 'data', 'Cache')
        try:
            path = os.path.join(cache_dir, 'ai_diag_special.json')
            now_ts = time.time()
            old_ts_map = {}
            if os.path.exists(path):
                with open(path, 'r', encoding='utf-8') as f: old_data = json.load(f)
                for code, val in (old_data.get('results') or {}).items():
                    if isinstance(val, dict) and val.get('ts'): old_ts_map[code] = val['ts']
            results_with_ts = {}
            for code, text in self._ai_diag_results.items():
                if isinstance(text, dict): text = text.get('text', '') or str(text)
                results_with_ts[code] = {'text': str(text), 'ts': old_ts_map.get(code, now_ts)}
            with open(path, 'w', encoding='utf-8') as f:
                json.dump({'saved_at': datetime.datetime.now().isoformat(), 'results': results_with_ts}, f, ensure_ascii=False, indent=2)
        except Exception: pass
