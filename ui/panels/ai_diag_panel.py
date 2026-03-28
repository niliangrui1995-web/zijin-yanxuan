import os
import time
import datetime
import json
import threading
import concurrent.futures
import pandas as pd
from PyQt6.QtWidgets import (
    QFrame, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
    QLineEdit, QMessageBox, QDialog, QDateEdit, QTextEdit,
    QTableWidgetItem
)
from PyQt6.QtCore import Qt, QTimer, QDate
from PyQt6.QtGui import QColor

from vcp.constants import SPECIAL_LATEST_DATA
from vcp.engine import VCPEngine
from core.event_bus import event_bus

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

        self.ai_content = QTextEdit()
        self.ai_content.setReadOnly(True)
        self.ai_content.setStyleSheet("background-color: #0A0C10; color: #C9CDD4; border-radius: 6px; padding: 4px;")
        ai_layout.addWidget(self.ai_content)

    def _merge_and_wrap_ai_diag(self, text):
        if not text or text == '--': return ""
        text = str(text).replace('\n', ' ')
        return text[:25] + "..." if len(text) > 25 else text

    def open_ai_diag(self, preset_code="", auto_start=""):
        if not isinstance(preset_code, str): preset_code = ""
        code = preset_code.strip()

        if not code or not auto_start:
            prefill_code = code
            if not prefill_code and hasattr(self.main_window, 'tabs'):
                curr_tab = self.main_window.tabs.currentIndex()
                if curr_tab == 1 and hasattr(self.main_window, 'table_rt') and self.main_window.table_rt.currentItem():
                    prefill_code = self.main_window.table_rt.item(self.main_window.table_rt.currentRow(), 0).text()
                elif curr_tab == 2 and hasattr(self.main_window, 'table_sp') and self.main_window.table_sp.currentItem():
                    prefill_code = self.main_window.table_sp.item(self.main_window.table_sp.currentRow(), 0).text()
                elif curr_tab == 0 and hasattr(self.main_window, 'table_scan') and self.main_window.table_scan.currentItem():
                    prefill_code = self.main_window.table_scan.item(self.main_window.table_scan.currentRow(), 1).text()

            dialog = QDialog(self)
            dialog.setWindowTitle("AI 诊断 -- 选择标的与检测类型")
            dialog.resize(340, 220)
            dialog.setStyleSheet(self.main_window.styleSheet())
            
            layout = QVBoxLayout(dialog)
            layout.addWidget(QLabel("请输入 6 位股票代码:"))
            input_field = QLineEdit()
            input_field.setPlaceholderText("例如: 000001")
            if prefill_code and len(prefill_code) == 6: input_field.setText(prefill_code)
            layout.addWidget(input_field)

            date_row = QHBoxLayout()
            date_row.addWidget(QLabel("诊断日期:"))
            date_edit = QDateEdit()
            date_edit.setCalendarPopup(True)
            date_edit.setDate(QDate.currentDate())
            date_edit.setDisplayFormat("yyyy-MM-dd")
            date_row.addWidget(date_edit)
            date_row.addStretch()
            layout.addLayout(date_row)

            btn_box = QHBoxLayout()
            btn_local = QPushButton("🧪 本地技术诊断")
            btn_ai = QPushButton("🧠 Kimi 深度诊断")
            btn_local.setObjectName("secondaryButton")
            btn_ai.setObjectName("primaryButton")
            btn_box.addWidget(btn_local)
            btn_box.addWidget(btn_ai)
            layout.addLayout(btn_box)

            diag_date = [None]
            def _handle_local():
                nonlocal auto_start, code
                auto_start, code, diag_date[0] = 'local', input_field.text().strip(), date_edit.date().toString("yyyy-MM-dd")
                dialog.accept()
            def _handle_ai():
                nonlocal auto_start, code
                auto_start, code, diag_date[0] = 'kimi', input_field.text().strip(), date_edit.date().toString("yyyy-MM-dd")
                dialog.accept()

            btn_local.clicked.connect(_handle_local)
            btn_ai.clicked.connect(_handle_ai)

            if dialog.exec() != QDialog.DialogCode.Accepted: return
            if not code or len(code) != 6:
                QMessageBox.warning(self, "输入错误", "请输入正确的 6 位股票代码!")
                return
            diag_date_str = diag_date[0] or datetime.date.today().strftime("%Y-%m-%d")
        else:
            diag_date_str = datetime.date.today().strftime("%Y-%m-%d")

        self.show()
        # 控制主窗口尺寸分布展开
        curr_sizes = self.main_window.right_splitter.sizes()
        if curr_sizes[1] == 0:
            total = sum(curr_sizes)
            self.main_window.right_splitter.setSizes([int(total*0.75), int(total*0.25)])
            
        if auto_start == 'local':
            self._run_local_diag_sidebar(code, diag_date_str)
        else:
            self._run_kim_diag_sidebar(code, diag_date_str)

    def _run_local_diag_sidebar(self, code, diag_date=""):
        name = getattr(self.data_provider, 'code2name', {}).get(code, "未知")
        self.ai_content.setPlainText(f"⏳ 正在极速生成 {name}({code}) 的本地技术诊断模型…\n")
        def do_local_diag():
            try:
                ok, msg = self._get_technical_report_text(code, name, diag_date)
            except Exception as e:
                ok, msg = False, f"本地诊断异常: {e}"
            # 跨线程投递至主UI
            event_bus.sig_ui_task.emit(lambda: self.ai_content.setPlainText(msg if ok else f"❌ {msg}"))
        threading.Thread(target=do_local_diag, daemon=True).start()

    def _run_kim_diag_sidebar(self, code, diag_date=""):
        name = getattr(self.data_provider, 'code2name', {}).get(code, "未知")
        self.ai_content.setPlainText(f"🤖 正在联网抽取 {name}({code}) 投资面与情绪标本…\n(请稍候不要关闭)\n")
        from vcp.utils import _get_kimi_api_key, _load_ai_diag_config
        cfg = _load_ai_diag_config()
        api_key = (cfg.get("kimi_api_key") or "").strip() or _get_kimi_api_key()
        
        def do_request():
            try:
                ok, msg = self._kimi_service.call_kimi_diag(api_key, code, name, diag_date=diag_date)
            except Exception as e:
                ok, msg = False, f"网络异常: {e}"
            event_bus.sig_ui_task.emit(lambda: on_done(ok, msg, code))
            
        def on_done(ok, msg, c):
            if ok:
                self.ai_content.setPlainText(msg + "\n\n✅ 数据已写入对应缓存与单元格映射")
                self._apply_ai_diag_result(c, msg.strip())
            else:
                self.ai_content.setPlainText("❌ " + str(msg))
                
        threading.Thread(target=do_request, daemon=True).start()

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
        except: actual_date = ""
        
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
            for row in range(t.rowCount()):
                item_code = t.item(row, 0)
                if item_code and item_code.text() == code:
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
                    break
                    
        # 直接更新文件缓冲
        if os.path.exists(SPECIAL_LATEST_DATA):
            try:
                with open(SPECIAL_LATEST_DATA, 'r', encoding='utf-8') as f:
                    data_dict = json.load(f)
                if code in data_dict:
                    data_dict[code]["AI诊断"] = text
                    with open(SPECIAL_LATEST_DATA, 'w', encoding='utf-8') as f:
                        json.dump(data_dict, f, ensure_ascii=False, indent=4)
            except Exception as e: print(f"[AI诊断] 缓存写入异常: {e}")

    def refresh_ai_column_from_cache(self):
        """一次性从内存回填关注池全部数据"""
        if not hasattr(self.main_window, 'table_sp'): return
        t = self.main_window.table_sp
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
        data_dict = {}
        if os.path.exists(SPECIAL_LATEST_DATA):
            with open(SPECIAL_LATEST_DATA, 'r', encoding='utf-8') as f: data_dict = json.load(f)
        if not data_dict:
            QMessageBox.information(self, "提示", "关注池为空")
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
        
        threading.Thread(target=run_bg, daemon=True).start()

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
