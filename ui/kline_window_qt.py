import numpy as np
import pandas as pd
import pyqtgraph as pg
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QHBoxLayout, QPushButton,
    QGraphicsRectItem
)
import os as _os
from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QFont, QPicture, QPainter
from ui.viewmodels.watchlist_vm import watchlist_vm

# === Custom Candlestick Graphics Item ===
class CandlestickItem(pg.GraphicsObject):
    def __init__(self, data):
        super().__init__()
        self.data = data
        self.generatePicture()

    def generatePicture(self):
        self.picture = QPicture()
        p = QPainter(self.picture)
        
        # Color Config
        up_color = QColor('#E85D5D')    # 红
        down_color = QColor('#3CC68A')  # 绿
        w = 0.3    # Candlestick width multiplier (0-0.5)
        
        for i in range(len(self.data)):
            t, o, c, h, l = self.data[i]
            
            p.setPen(pg.mkPen(up_color if c >= o else down_color))
            # Draw High-Low line (Wick)
            p.drawLine(pg.Point(t, l), pg.Point(t, h))
            
            # Draw Body
            p.setPen(pg.mkPen(up_color if c >= o else down_color))
            p.setBrush(pg.mkBrush(up_color if c >= o else down_color))
            
            if c >= o:
                p.drawRect(pg.QtCore.QRectF(t-w, o, w*2, c-o))
            else:
                p.drawRect(pg.QtCore.QRectF(t-w, c, w*2, o-c))
                
        p.end()

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return pg.QtCore.QRectF(self.picture.boundingRect())


class KLineChartWindow(QWidget):
    def __init__(self, main_window, code, name, data_provider, vcp_data=None, code_list=None, current_idx=0):
        super().__init__()
        self.main_window = main_window
        self.code = code
        self.name = name
        self.data_provider = data_provider
        self.vcp_data = vcp_data or {}
        self.code_list = code_list or []
        self.current_idx = current_idx
        
        # 实时刷新相关状态
        self._rt_timer = None          # 盘中定时刷新定时器
        self._candle_item = None       # 当前K线图元引用（用于增量更新）
        self._vol_up_item = None       # 成交量红柱图元
        self._vol_dn_item = None       # 成交量绿柱图元
        self._ma_lines = {}            # MA 线引用
        self._ma_label = None          # 左上角 MA 标签
        
        self.setWindowTitle(f"{name} ({code}) - K线详情")
        self.resize(1000, 600)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        
        # 设置窗口图标（与主窗口一致）
        from PyQt6.QtGui import QIcon
        icon_path = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), 'bull_icon.ico')
        if _os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))
        
        # Setup modern dark style
        self.setStyleSheet("""
            QWidget { background-color: #0B0B0E; color: #F5F5F7; }
            QLabel { font-weight: bold; font-family: "Microsoft YaHei UI"; }
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        
        # Header Info
        header_layout = QHBoxLayout()
        self.title_lbl = QLabel(f"{name} ({code}) 日线")
        self.title_lbl.setFont(QFont("Microsoft YaHei UI", 16, QFont.Weight.Bold))
        header_layout.addWidget(self.title_lbl)
        
        self.info_lbl = QLabel("正在加载数据...")
        self.info_lbl.setStyleSheet("color: #86868B;")
        header_layout.addWidget(self.info_lbl)
        header_layout.addStretch()
        
        # 上一只/下一只 切换按钮
        nav_btn_style = """
            QPushButton { background-color: transparent; color: #86868B; border: 1px solid #3A3A3C; border-radius: 4px; padding: 5px 12px; font-weight: bold; font-size: 13px; }
            QPushButton:hover { background-color: rgba(255,255,255,0.05); color: #F5F5F7; }
            QPushButton:disabled { color: #3A3A3C; border-color: #2A2A2C; }
        """
        self.btn_prev = QPushButton("◀ 上一只")
        self.btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prev.setStyleSheet(nav_btn_style)
        self.btn_prev.clicked.connect(lambda: self._nav_stock(-1))
        header_layout.addWidget(self.btn_prev)
        
        self.btn_next = QPushButton("下一只 ▶")
        self.btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next.setStyleSheet(nav_btn_style)
        self.btn_next.clicked.connect(lambda: self._nav_stock(1))
        header_layout.addWidget(self.btn_next)
        
        self.btn_fav = QPushButton("☆ 移入关注池")
        self.btn_fav.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fav.setStyleSheet("""
            QPushButton { background-color: transparent; color: #FFD60A; border: 1px solid #FFD60A; border-radius: 4px; padding: 5px 15px; font-weight: bold; }
            QPushButton:hover { background-color: rgba(255, 214, 10, 0.1); }
        """)
        self.btn_fav.clicked.connect(self._toggle_fav)
        header_layout.addWidget(self.btn_fav)
        
        layout.addLayout(header_layout)
        
        # PyQtGraph Graphics Layout Setup (Multi-panels)
        self.layout_widget = pg.GraphicsLayoutWidget(show=True)
        self.layout_widget.setBackground('#0B0B0E')
        layout.addWidget(self.layout_widget)

        # 快捷键 ←/→ 切换上/下一只股票（K线窗口是独立窗口，不与主窗口表格冲突）
        from PyQt6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=lambda: self._nav_stock(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=lambda: self._nav_stock(1))
        self._update_nav_buttons()
        
        # Panel 1: Main K-Line
        self.p1 = self.layout_widget.addPlot(row=0, col=0)
        self.p1.showGrid(x=False, y=True, alpha=0.2)
        self.p1.getAxis("bottom").setStyle(showValues=False) # Hide X labels on top panel
        self.p1.setMouseEnabled(x=False, y=False) # 彻底禁用鼠标缩放拖拽
        
        # Panel 2: Volume
        self.p2 = self.layout_widget.addPlot(row=1, col=0)
        self.p2.setMaximumHeight(100)
        self.p2.showGrid(x=False, y=True, alpha=0.2)
        self.p2.getAxis("bottom").setStyle(showValues=False)
        self.p2.setXLink(self.p1) # Link X-axis zooming
        self.p2.setMouseEnabled(x=False, y=False) # 彻底禁用鼠标缩放拖拽
        
        # Panel 3: MACD
        self.p3 = self.layout_widget.addPlot(row=2, col=0)
        self.p3.setMaximumHeight(120)
        self.p3.showGrid(x=False, y=True, alpha=0.2)
        self.p3.setXLink(self.p1)
        self.p3.setMouseEnabled(x=False, y=False) # 彻底禁用鼠标缩放拖拽
        
        # Crosshair setup (all 3 panels have vertical synced lines)
        self.vLines = [
            pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('#3A3A3C', width=1, style=Qt.PenStyle.DashLine)),
            pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('#3A3A3C', width=1, style=Qt.PenStyle.DashLine)),
            pg.InfiniteLine(angle=90, movable=False, pen=pg.mkPen('#3A3A3C', width=1, style=Qt.PenStyle.DashLine))
        ]
        self.hLines = [
            pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen('#3A3A3C', width=1, style=Qt.PenStyle.DashLine)),
            pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen('#3A3A3C', width=1, style=Qt.PenStyle.DashLine)),
            pg.InfiniteLine(angle=0, movable=False, pen=pg.mkPen('#3A3A3C', width=1, style=Qt.PenStyle.DashLine))
        ]
        
        self.p1.addItem(self.vLines[0], ignoreBounds=True)
        self.p1.addItem(self.hLines[0], ignoreBounds=True)
        self.p2.addItem(self.vLines[1], ignoreBounds=True)
        self.p2.addItem(self.hLines[1], ignoreBounds=True)
        self.p3.addItem(self.vLines[2], ignoreBounds=True)
        self.p3.addItem(self.hLines[2], ignoreBounds=True)
        
        self.proxy1 = pg.SignalProxy(self.p1.scene().sigMouseMoved, rateLimit=60, slot=self.mouseMoved)
        self.proxy2 = pg.SignalProxy(self.p2.scene().sigMouseMoved, rateLimit=60, slot=self.mouseMoved)
        self.proxy3 = pg.SignalProxy(self.p3.scene().sigMouseMoved, rateLimit=60, slot=self.mouseMoved)
        
        self.df = None
        self.time_dict = {}
        
        self._check_fav_status()
        self._load_and_draw()

    def _check_fav_status(self):
        try:
            self.is_fav = watchlist_vm.is_in_watchlist(self.code)
            if self.is_fav:
                self.btn_fav.setText("⭐ 移出关注池")
            else:
                self.btn_fav.setText("☆ 移入关注池")
        except Exception as e:
            print(f"[K线窗口] 检查关注状态失败: {e}")
            self.is_fav = False

    def _toggle_fav(self):
        try:
            watchlist_vm.toggle_stock(self.code, self.name, self.vcp_data)
            self._check_fav_status()
        except Exception as e:
            print(f"[K线窗口] 切换关注状态失败: {e}")
            
    def _load_and_draw(self):
        is_asian = '.' in self.code
        if is_asian:
            self._load_asian_chart()
            return
            
        # 1. 尝试从内存缓存秒开，避免双击卡顿
        df = self.data_provider.get_data(self.code)
        if df is not None and len(df) >= 60:
            self._render_chart_frame(df, loading=True)
        else:
            self.info_lbl.setText("📡 正在从服务器获取完整 K 线数据...")
            
        # 2. 异步拉取最新日线和盘中实时跳动，拼装后重绘
        def _bg_fetch():
            import pandas as pd
            from datetime import datetime
            # 这里的 get_data_fresh_for_chart 包含同步网络IO，必须在 bg_task 里执行
            fresh_df = self.data_provider.get_data_fresh_for_chart(self.code)
            
            now = datetime.now()
            quote_to_apply = None
            
            # 是否需要拉取实时节点？
            # 只要没离线，就拉取。因为即便是周末，也可以拉取到周五最后收盘价来补齐未 F5 的数据。
            if not getattr(self.data_provider, '_offline', False):
                already_has_latest = False
                if fresh_df is not None and not fresh_df.empty:
                    last_dt = pd.Timestamp(fresh_df.index[-1]).date()
                    # 如果当前是交易日且下午 15:05 之后，并且 K 线里已经包含了今天的日期，说明 F5 按过了，不需要再拉了。
                    if now.hour > 15 or (now.hour == 15 and now.minute > 5):
                        if last_dt >= now.date():
                            already_has_latest = True
                            
                is_pre_market = now.weekday() < 5 and (now.hour < 9 or (now.hour == 9 and now.minute < 25))
                is_weekend = now.weekday() >= 5
                
                if not already_has_latest and not is_pre_market and not is_weekend:
                    try:
                        quotes = self.data_provider.fetch_realtime_quotes_batch([self.code])
                        if quotes and self.code in quotes:
                            quote_to_apply = quotes[self.code]
                    except Exception as e:
                        print(f"[K线] 盘中实时拼接失败: {e}")
            return fresh_df, quote_to_apply
            
        def _on_fetch_success(result):
            try:
                if result:
                    fresh_df, quote_to_apply = result
                    if fresh_df is not None:
                        self._render_chart_frame(fresh_df, loading=False)
                        if quote_to_apply is not None:
                            self._refresh_last_bar(quote_to_apply)
            except RuntimeError:
                # The window was closed by the user before the background task finished
                pass
            
        from core.task_manager import task_manager
        task_manager.run_in_background(_bg_fetch, on_success=_on_fetch_success, task_id=f"kline_{self.code}")

    def _render_chart_frame(self, df, loading=False):
        from vcp.engine import VCPEngine
        
        if df is None or len(df) < 60:
            if not loading:
                self.info_lbl.setText("⚠ 数据不足，无法绘图")
            return
            
        if 'MACD' not in df.columns or df['MACD'].isna().all():
            df = VCPEngine.calculate_indicators(df)
            
        if not loading:
            self.info_lbl.setText(f"✅ 数据拉取成功 (缓存行数: {len(df)})")
            
        # 先清除可能存在的旧图元
        self.p1.clear()
        self.p2.clear()
        self.p3.clear()
        self.p1.addItem(self.vLines[0], ignoreBounds=True)
        self.p1.addItem(self.hLines[0], ignoreBounds=True)
        self.p2.addItem(self.vLines[1], ignoreBounds=True)
        self.p2.addItem(self.hLines[1], ignoreBounds=True)
        self.p3.addItem(self.vLines[2], ignoreBounds=True)
        self.p3.addItem(self.hLines[2], ignoreBounds=True)

        # Draw last 250 bars like original design
        self.df = df.iloc[-250:].copy()
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in self.df.columns:
                self.df[col] = self.df[col].ffill().bfill()
        
        # 启动盘中定时刷新（60秒间隔）
        if not loading:
            self._start_rt_timer()
                
        # Data prep for CandlestickItem and Volume
        candle_data = []
        vol_bars_up_x = []
        vol_bars_up_y = []
        vol_bars_dn_x = []
        vol_bars_dn_y = []
        
        x_ticks = []
        for i, (dt, row) in enumerate(self.df.iterrows()):
            o, c, h, l, v = row['open'], row['close'], row['high'], row['low'], row['volume']
            candle_data.append([i, o, c, h, l])
            self.time_dict[i] = dt.strftime('%Y-%m-%d')
            
            if c >= o:
                vol_bars_up_x.append(i)
                vol_bars_up_y.append(v)
            else:
                vol_bars_dn_x.append(i)
                vol_bars_dn_y.append(v)
                
            # Add tick label for every 30 days
            if i % 30 == 0:
                x_ticks.append((i, dt.strftime('%Y-%m')))
                
        # Apply Ticks to the bottom-most axis (p3)
        ticker = self.p3.getAxis("bottom")
        ticker.setTicks([x_ticks])
        
        # Add Candlestick Item (p1)
        item = CandlestickItem(candle_data)
        self.p1.addItem(item)
        self._candle_item = item     # 保存引用，用于增量刷新
        
        # Add MACD Items (p3)
        if 'MACD' in self.df.columns:
            # MACD Lines
            self.p3.plot(range(len(self.df)), self.df['MACD'].values, pen=pg.mkPen('#0A84FF', width=1.2))
            self.p3.plot(range(len(self.df)), self.df['MACD_Signal'].values, pen=pg.mkPen('#FF9F0A', width=1.2))
            
            # MACD Histograms (BarGraph)
            macd_h = self.df['MACD_Hist'].values
            pos_mask = macd_h >= 0
            neg_mask = macd_h < 0
            
            x_range = np.arange(len(self.df))
            bg_pos = pg.BarGraphItem(x=x_range[pos_mask], height=macd_h[pos_mask], width=0.6, brush='#E85D5D', pen=None)
            bg_neg = pg.BarGraphItem(x=x_range[neg_mask], height=macd_h[neg_mask], width=0.6, brush='#3CC68A', pen=None)
            self.p3.addItem(bg_pos)
            self.p3.addItem(bg_neg)
            
        # Add Volume Bars (p2)
        self._vol_up_item = None
        self._vol_dn_item = None
        if vol_bars_up_x:
            v_up = pg.BarGraphItem(x=vol_bars_up_x, height=vol_bars_up_y, width=0.6, brush='#E85D5D', pen=None)
            self.p2.addItem(v_up)
            self._vol_up_item = v_up
        if vol_bars_dn_x:
            v_dn = pg.BarGraphItem(x=vol_bars_dn_x, height=vol_bars_dn_y, width=0.6, brush='#3CC68A', pen=None)
            self.p2.addItem(v_dn)
            self._vol_dn_item = v_dn
        
        # Add MA lines (p1) — 直接从收盘价计算
        closes = self.df['close']
        x_range_ma = list(range(len(self.df)))
        self._ma_config = [
            (10,   '#FFFFFF',  'MA10'),    # 白色
            (20,   '#0A84FF',  'MA20'),    # 蓝色
            (50,   '#FF9F0A',  'MA50'),    # 橙色
            (150,  '#BF5AF2',  'MA150'),   # 紫色
            (200,  '#FF375F',  'MA200'),   # 红色
        ]
        # 预计算各 MA 序列并缓存（供十字光标查询）
        self._ma_series = {}
        for period, color, name in self._ma_config:
            ma = closes.rolling(period).mean()
            self._ma_series[name] = ma.values
            if ma.notna().any():
                self._ma_lines[name] = self.p1.plot(
                    x_range_ma, ma.values,
                    pen=pg.mkPen(color, width=1.5), name=name
                )
        
        # 左上角 MA 均价标签（默认显示最新一根K线的 MA 值）
        self._update_ma_label(len(self.df) - 1)
            
        # Viewbox setup - 强制 100% 并且绝不允许缩放/移动
        # 为两侧保留很小的一点点物理边距，让首尾 K 线不会贴死边框
        padding = 2
        self.p1.setXRange(-padding, len(self.df) + padding, padding=0)
        self.p1.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
        self.p1.setDefaultPadding(0.0)
        
        # Overlay VCP Data
        if self.vcp_data:
            self._draw_vcp_overlays()
        else:
            self.info_lbl.setText(f"数据加载完成: {len(self.df)}根 K线")

    def _draw_vcp_overlays(self):
        # 1. 寻找突破触发日并在 K 线上方打点或画垂直线
        trigger_date = self.vcp_data.get('触发日期', '')
        trigger_idx = -1
        
        # 查找日期对应的 Index 映射
        date_to_idx = {v: k for k, v in self.time_dict.items()}
        
        if trigger_date:
            for i, dt in self.time_dict.items():
                if trigger_date in dt:
                    trigger_idx = i
                    break
                
        if trigger_idx != -1:
            row = self.df.iloc[trigger_idx]
            # 画一条红色虚线提示突破点
            trigger_line = pg.InfiniteLine(pos=trigger_idx, angle=90, pen=pg.mkPen('#F23645', width=1.5, style=Qt.PenStyle.DashLine))
            self.p1.addItem(trigger_line, ignoreBounds=True)
            
            # 在图表最高点放一个文字 "🚀"
            text_item = pg.TextItem(html='<div style="text-align: center"><span style="color: #FFD60A; font-size: 16pt;">⭐</span><br><span style="color: #FFF; font-size: 10pt;">突破</span></div>', anchor=(0.5, 1.2))
            text_item.setPos(trigger_idx, row['high'])
            self.p1.addItem(text_item, ignoreBounds=True)
            
        # 2. 绘制 VCP 收缩区间的箱体与高点连线
        box_high = self.vcp_data.get('区间最高价', 0)
        box_low = self.vcp_data.get('区间最低点', 0)
        
        peak_dates = self.vcp_data.get('_peak_dates', [])
        # Fallback if _peak_dates doesn't exist
        if not peak_dates:
            for key in ['_high1_date', '_high2_date', '_high3_date']:
                if self.vcp_data.get(key): peak_dates.append(self.vcp_data[key])
                
        # 尝试优先从本地 df 在内存中重新跑一次动态算子，确保展现最新的形态变化
        if self.df is not None:
            try:
                from vcp.engine import VCPEngine
                from vcp.models import VCPParams
                params = VCPParams()
                params.rps_threshold = 0 # 图表展示时不卡 RPS，因为这是已选出的池子或者是用户强行查看
                
                # 如果是关注池股票，应用宽松的判定参数
                if hasattr(self, 'is_fav') and self.is_fav:
                    params.amp_threshold = 2.0
                    params.ma_bind_threshold = 0.30
                    params.high_250_threshold = 0.50
                    params.min_amount_20d = 0
                    params.min_history_days = 60
                
                curr_idx = len(self.df) - 1
                curr_dt = self.df.index[curr_idx]
                
                # 严密计算是否符合 VCP 形态，跳过当天的红绿盘判断
                ok, reason, metadata = VCPEngine.evaluate_conditions(
                    self.df, curr_dt, 100, 100, None, params, skip_red_check=True
                )
                
                if ok and metadata:
                    new_box_low = metadata.get('区间最低点', 0)
                    new_box_high = metadata.get('区间最高价', 0)
                    new_peak_dates = metadata.get('_peak_dates', [])
                    
                    if not new_peak_dates:
                        for key in ['_high1_date', '_high2_date', '_high3_date']:
                            if metadata.get(key): new_peak_dates.append(metadata[key])

                    if new_box_high > 0 and len(new_peak_dates) >= 2:
                        box_low = new_box_low
                        box_high = new_box_high
                        peak_dates = new_peak_dates
                        
                        self.vcp_data['区间最低点'] = box_low
                        self.vcp_data['区间最高价'] = box_high
                        self.vcp_data['_peak_dates'] = peak_dates
                else:
                    # 如果这只股票最新数据因为破坏条件（比如深度回调）不符合 VCP 严格计算，我们保留使用老缓存的数据
                    pass
            except Exception as e:
                print(f"K线动态补充计算VCP特征失败: {e}")
                
        if box_high > 0 and box_low > 0 and peak_dates:
            # 过滤并找出高点坐标
            valid_x = []
            for d in peak_dates:
                 d_short = str(d)[:10].replace('-', '')
                 # date_to_idx contains '2026-03-05'
                 for k, v in date_to_idx.items():
                     if k.replace('-', '') == d_short:
                         valid_x.append(v)
                         break
            
            if valid_x:
                 cyan = '#00CED1'
                 x_start = min(valid_x)
                 x_end = trigger_idx if trigger_idx != -1 else len(self.df) - 1
                 
                 # 绘制高点垂直线
                 for x_p in valid_x:
                     v_line = pg.InfiniteLine(pos=x_p, angle=90, pen=pg.mkPen('#FFD700', width=1.0, style=Qt.PenStyle.DashLine))
                     self.p1.addItem(v_line, ignoreBounds=True)
                     
                 # 画箱体矩形框
                 rect = QGraphicsRectItem(x_start, box_low, x_end - x_start, box_high - box_low)
                 rect.setPen(pg.mkPen(None))
                 rect.setBrush(pg.mkBrush(QColor(51, 153, 255, 30))) # 淡蓝透明块
                 self.p1.addItem(rect, ignoreBounds=True)
                 
                 # 画上下沿直线
                 self.p1.plot([x_start, x_end], [box_high, box_high], pen=pg.mkPen(cyan, width=1.5))
                 self.p1.plot([x_start, x_end], [box_low, box_low], pen=pg.mkPen(cyan, width=1.5))
                 
                 # 标注文字
                 label_high = pg.TextItem(f"箱顶: {box_high:.2f}", color=cyan, anchor=(0, 1))
                 label_high.setPos(x_start, box_high)
                 self.p1.addItem(label_high)
                 
                 label_low = pg.TextItem(f"箱底: {box_low:.2f}", color=cyan, anchor=(0, 0))
                 label_low.setPos(x_start, box_low)
                 self.p1.addItem(label_low)

        # 3. (无需显示 VCP 诊断摘要，已被删减)
        
        # 把信息栏默认设置成数据概览
        self.info_lbl.setText(f"数据加载完成: {len(self.df)}根 K线 | 发现 {trigger_date} VCP 买点")

    def _start_rt_timer(self):
        """启动盘中实时刷新定时器（60秒间隔），仅在交易时段运行。"""
        from datetime import datetime
        now = datetime.now()
        is_trading_hours = (now.weekday() < 5 and 
                           ((now.hour == 9 and now.minute >= 25) or 
                            (10 <= now.hour <= 14) or 
                            (now.hour == 15 and now.minute <= 5)))
        
        if not is_trading_hours or self.data_provider._offline:
            return
        
        if self._rt_timer is None:
            self._rt_timer = QTimer(self)
            self._rt_timer.timeout.connect(self._on_rt_timer)
        self._rt_timer.start(60 * 1000)  # 60秒
        print(f"[K线] {self.code} 盘中实时刷新已启动（间隔 60s）")

    def _on_rt_timer(self):
        """定时器回调：拉取最新实时报价，增量更新最后一根K线。"""
        from datetime import datetime
        now = datetime.now()
        
        # 收盘后自动停止定时器
        if now.hour >= 15 and now.minute > 5:
            if self._rt_timer:
                self._rt_timer.stop()
                print(f"[K线] {self.code} 已收盘，停止实时刷新")
            return
        
        try:
            if '.' in self.code:
                import yfinance as yf
                rt_df = yf.Ticker(self.code).history(period="1d", interval="1d")
                if not rt_df.empty:
                    last_row = rt_df.iloc[-1]
                    # last_row.name holds the actual DatetimeIndex
                    rt_date = pd.Timestamp(last_row.name).strftime('%Y-%m-%d')
                    quote = {'date': rt_date, 'open': float(last_row['Open']), 'high': float(last_row['High']), 'low': float(last_row['Low']), 'close': float(last_row['Close']), 'volume': float(last_row.get('Volume', 0))}
                    self._refresh_last_bar(quote)
            else:
                quotes = self.data_provider.fetch_realtime_quotes_batch([self.code])
                if quotes and self.code in quotes:
                    quote = quotes[self.code]
                    self._refresh_last_bar(quote)
        except Exception as e:
            print(f"[K线] {self.code} 实时刷新异常: {e}")

    def _load_asian_chart(self):
        import json, os, pandas as pd
        from ui.tabs.asian_market_tab import JSON_CACHE, GLOBAL_ASIAN_RT_CACHE
        df = None
        if os.path.exists(JSON_CACHE):
            with open(JSON_CACHE, 'r', encoding='utf-8') as f:
                raw = json.load(f)
                stocks = raw.get('stocks', [])
                target_stock = next((s for s in stocks if s.get('ticker') == self.code), None)
                if target_stock:
                    data = target_stock.get('klines', [])
                    if data:
                        df = pd.DataFrame(data)
                        if 'date' in df.columns:
                            df['date'] = pd.to_datetime(df['date'])
                            df.set_index('date', inplace=True)
                        for col in ['open', 'high', 'low', 'close', 'volume']:
                            if col in df.columns:
                                df[col] = df[col].astype(float)
        
        if df is not None:
            self._render_chart_frame(df, loading=False)
            if self.code in GLOBAL_ASIAN_RT_CACHE:
                quote = GLOBAL_ASIAN_RT_CACHE[self.code]
                df_today = quote.get('df_today')
                if df_today is not None and not df_today.empty:
                    last_row = df_today.iloc[-1]
                    rt_date = pd.Timestamp(last_row.name).strftime('%Y-%m-%d')
                    rt_quote = {'date': rt_date, 'open': float(last_row['Open']), 'high': float(last_row['High']), 'low': float(last_row['Low']), 'close': float(last_row['Close']), 'volume': float(last_row.get('Volume', 0))}
                    self._refresh_last_bar(rt_quote)
        else:
            self.info_lbl.setText("⚠ 暂无该亚洲标的历史数据")

    def _refresh_last_bar(self, quote):
        """增量更新最后一根K线的 OHLCV 数据，不全量重绘。"""
        if self.df is None or len(self.df) == 0:
            return
        
        rt_close = float(quote.get('close', 0) or 0)
        rt_open  = float(quote.get('open', 0) or 0)
        rt_high  = float(quote.get('high', 0) or 0)
        rt_low   = float(quote.get('low', 0) or 0)
        rt_vol   = float(quote.get('volume', 0) or 0)
        
        if rt_close <= 0 or rt_open <= 0:
            return
        
        import pandas as pd
        from datetime import datetime
        
        # 提取真实交易日。如果获取不到，兜底用本日历天
        rt_date_str = quote.get('date')
        if rt_date_str:
            rt_date = pd.Timestamp(rt_date_str).date()
        else:
            rt_date = pd.Timestamp(datetime.now().date()).date()
            
        last_idx = len(self.df) - 1
        last_date = pd.Timestamp(self.df.index[-1]).date()
        
        # 判断最后一根K线日期是否与该实时行情的日期重叠
        if last_date >= rt_date:
            # 如果本地 K 线图的最后一天等于(或异常大于)获取到的实际交易日，说明今天这根 K 线已经包含或者无需新建
            # 仅仅需要用最新的 OHLCV 更新最后一根（覆盖它的跳动）
            self.df.iloc[-1, self.df.columns.get_loc('open')] = rt_open
            self.df.iloc[-1, self.df.columns.get_loc('high')] = max(self.df.iloc[-1, self.df.columns.get_loc('high')], rt_high)
            self.df.iloc[-1, self.df.columns.get_loc('low')] = min(self.df.iloc[-1, self.df.columns.get_loc('low')], rt_low)
            self.df.iloc[-1, self.df.columns.get_loc('close')] = rt_close
            if 'volume' in self.df.columns:
                self.df.iloc[-1, self.df.columns.get_loc('volume')] = rt_vol
            
            # 同时修正 time_dict 中的标记（避免覆盖成周末日期）
            self.time_dict[last_idx] = last_date.strftime('%Y-%m-%d')
        else:
            # 说明 rt_date 是一个全新的交易日（比如周一开盘），而且本地最后一根(比如上周五)比它小
            new_row = pd.DataFrame({
                'open': [rt_open], 'high': [rt_high], 'low': [rt_low],
                'close': [rt_close], 'volume': [rt_vol]
            }, index=[pd.Timestamp(rt_date)])
            self.df = pd.concat([self.df, new_row])
            last_idx = len(self.df) - 1
            self.time_dict[last_idx] = rt_date.strftime('%Y-%m-%d')
        
        # === 增量重绘：清除旧图元，重新绘制全部K线 ===
        # （pyqtgraph 的 CandlestickItem 是 QPicture 预渲染的，无法局部更新，
        #   但重绘 250 根K线的性能开销极小，< 5ms）
        if self._candle_item is not None:
            self.p1.removeItem(self._candle_item)
        
        candle_data = []
        vol_up_x, vol_up_y, vol_dn_x, vol_dn_y = [], [], [], []
        for i, (dt, row) in enumerate(self.df.iterrows()):
            o, c, h, l = row['open'], row['close'], row['high'], row['low']
            v = row.get('volume', 0)
            candle_data.append([i, o, c, h, l])
            if c >= o:
                vol_up_x.append(i); vol_up_y.append(v)
            else:
                vol_dn_x.append(i); vol_dn_y.append(v)
        
        new_candle = CandlestickItem(candle_data)
        self.p1.addItem(new_candle)
        self._candle_item = new_candle
        
        # 更新成交量柱子
        if self._vol_up_item is not None:
            self.p2.removeItem(self._vol_up_item)
            self._vol_up_item = None
        if self._vol_dn_item is not None:
            self.p2.removeItem(self._vol_dn_item)
            self._vol_dn_item = None
        if vol_up_x:
            self._vol_up_item = pg.BarGraphItem(x=vol_up_x, height=vol_up_y, width=0.6, brush='#E85D5D', pen=None)
            self.p2.addItem(self._vol_up_item)
        if vol_dn_x:
            self._vol_dn_item = pg.BarGraphItem(x=vol_dn_x, height=vol_dn_y, width=0.6, brush='#3CC68A', pen=None)
            self.p2.addItem(self._vol_dn_item)
        
        # 更新 Y 轴自动范围
        self.p1.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)
        
        # 更新信息栏
        pre_close = rt_open
        if len(self.df) >= 2:
            pre_close = self.df.iloc[-2]['close']
            
        pct = ((rt_close - pre_close) / pre_close * 100) if pre_close > 0 else 0
        color = '#E85D5D' if rt_close >= pre_close else '#3CC68A'
        sign = '+' if rt_close >= pre_close else ''
        now_str = datetime.now().strftime('%H:%M:%S')
        self.info_lbl.setText(
            f"🔴 实时 {now_str} | "
            f"现价: {rt_close:.2f}  "
            f"涨幅: <span style='color:{color}; font-weight:bold;'>{sign}{pct:.2f}%</span>  "
            f"成交量: {rt_vol/10000:.0f}万"
        )

    def closeEvent(self, event):
        """窗口关闭时停止定时器，避免泄漏。"""
        if self._rt_timer is not None:
            self._rt_timer.stop()
            self._rt_timer = None
            print(f"[K线] {self.code} 窗口关闭，定时器已停止")
        super().closeEvent(event)

    def resizeEvent(self, event):
        super().resizeEvent(event)

    def _update_ma_label(self, index):
        """更新左上角 MA 标签为指定索引的均线价格。"""
        if not hasattr(self, '_ma_config') or not hasattr(self, '_ma_series'):
            return
        ma_html_parts = []
        for period, color, name in self._ma_config:
            arr = self._ma_series.get(name)
            if arr is not None and 0 <= index < len(arr):
                val = arr[index]
                if pd.notna(val):
                    ma_html_parts.append(
                        f'<span style="color:{color}; font-size:10pt;">{name}: {val:.2f}</span>'
                    )
        if ma_html_parts:
            html = '&nbsp;&nbsp;'.join(ma_html_parts)
            if self._ma_label is None:
                self._ma_label = pg.TextItem(html=html, anchor=(0, 0))
                self.p1.scene().addItem(self._ma_label)
                self._ma_label.setPos(75, 5)
            else:
                self._ma_label.setHtml(html)

    def mouseMoved(self, evt):
        pos = evt[0]
        # Map back to view coordinates for any of panels
        if self.df is None: return
        
        # Determine panel
        mousePoint = None
        if self.p1.sceneBoundingRect().contains(pos):
            mousePoint = self.p1.vb.mapSceneToView(pos)
        elif self.p2.sceneBoundingRect().contains(pos):
            mousePoint = self.p2.vb.mapSceneToView(pos)
        elif self.p3.sceneBoundingRect().contains(pos):
            mousePoint = self.p3.vb.mapSceneToView(pos)
            
        if mousePoint is not None:
            index = int(mousePoint.x())
            if 0 <= index < len(self.df):
                row = self.df.iloc[index]
                dt_str = self.time_dict.get(index, "")
                o, c, h, l, v = row['open'], row['close'], row['high'], row['low'], row['volume']
                
                if index > 0:
                    pre_close = self.df.iloc[index-1]['close']
                else:
                    pre_close = o
                    
                pct = (c - pre_close) / pre_close * 100 if pre_close > 0 else 0
                
                # Check 涨跌停高亮
                limit_color = ""
                limit_sign = ""
                if pct > 9.0:
                    limit_color = "#FF375F"
                    limit_sign = "🔥涨停🔥 "
                elif pct < -9.0:
                    limit_color = "#30D158"
                    limit_sign = "💦跌停💦 "
                    
                color = "#E85D5D" if c >= pre_close else "#3CC68A"
                sign = "+" if c >= pre_close else ""
                if limit_color: color = limit_color
                
                info_text = (
                    f"日期: {dt_str}  "
                    f"开: {o:.2f}  高: {h:.2f}  低: {l:.2f}  收: {c:.2f}  "
                    f"涨幅: <span style='color:{color}; font-weight:bold;'>{limit_sign}{sign}{pct:.2f}%</span>  "
                    f"成交量: {v/10000:.0f}万"
                )
                self.info_lbl.setText(info_text)
                
                # 动态更新左上角 MA 标签为当前十字光标所在日期的均线价格
                self._update_ma_label(index)
                
                # Update crosshair on all panels
                self.vLines[0].setPos(index)
                self.hLines[0].setPos(mousePoint.y() if self.p1.sceneBoundingRect().contains(pos) else row['close'])
                self.vLines[1].setPos(index)
                if self.p2.sceneBoundingRect().contains(pos): self.hLines[1].setPos(mousePoint.y())
                self.vLines[2].setPos(index)
                if self.p3.sceneBoundingRect().contains(pos): self.hLines[2].setPos(mousePoint.y())

    def _nav_stock(self, delta):
        """切换股票：delta=-1 上一只, +1 下一只（含防抖）"""
        if not self.code_list:
            return
        if getattr(self, '_switching', False):
            return
        new_idx = self.current_idx + delta
        if 0 <= new_idx < len(self.code_list):
            self._switch_to_stock(new_idx)

    def _update_nav_buttons(self):
        """更新上/下一只按钮的可用状态"""
        if not self.code_list:
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            return
        self.btn_prev.setEnabled(self.current_idx > 0)
        self.btn_next.setEnabled(self.current_idx < len(self.code_list) - 1)

    def _switch_to_stock(self, new_idx):
        # 防抖锁
        self._switching = True
        try:
            # 切换股票时停止旧定时器
            if self._rt_timer is not None:
                self._rt_timer.stop()
            
            # 清除图元引用
            self._candle_item = None
            self._vol_up_item = None
            self._vol_dn_item = None
            
            # 取消之前的图元并清除 Layout
            self.p1.clear()
            self.p2.clear()
            self.p3.clear()
            
            # 已移除 VCP 摘要及清理逻辑
            if self._ma_label is not None:
                try:
                    self.p1.scene().removeItem(self._ma_label)
                    self._ma_label = None
                except Exception as e: print(f"[K线图] 异常: {e}")
                
            self.p1.addItem(self.vLines[0], ignoreBounds=True)
            self.p1.addItem(self.hLines[0], ignoreBounds=True)
            self.p2.addItem(self.vLines[1], ignoreBounds=True)
            self.p2.addItem(self.hLines[1], ignoreBounds=True)
            self.p3.addItem(self.vLines[2], ignoreBounds=True)
            self.p3.addItem(self.hLines[2], ignoreBounds=True)
            
            # 重置状态
            item_data = self.code_list[new_idx]
            self.current_idx = new_idx
            self.code = item_data.get('代码', '')
            self.name = item_data.get('名称', '')
            self.vcp_data = item_data
            
            self.setWindowTitle(f"{self.name} ({self.code}) - K线详情")
            self.title_lbl.setText(f"{self.name} ({self.code}) 日线")
            total = len(self.code_list)
            
            # 同步选中在主窗口的表格行
            if self.main_window and hasattr(self.main_window, 'table_scan'):
                self.main_window.table_scan.selectRow(new_idx)
                
            self._check_fav_status()
            self._load_and_draw()
            
            # 若是附加信息则追加索引提示
            curr_text = self.info_lbl.text()
            self.info_lbl.setText(f"[{new_idx+1}/{total}] " + curr_text)
        finally:
            self._switching = False
            self._update_nav_buttons()
