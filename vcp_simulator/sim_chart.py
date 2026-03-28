"""
SimulatorChartWidget — pyqtgraph K线渲染控件（完整版）
功能：K线+成交量+MACD+MA均线+VCP结构矩形+峰位虚线+R标签
      +Fib回撤线+买卖三角标记+持仓成本线+Pivot线+十字光标OHLC
"""
import numpy as np
import pandas as pd
import pyqtgraph as pg
from PyQt6.QtWidgets import QWidget, QVBoxLayout, QLabel
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QColor, QPicture, QPainter


# ---------- K 线图形对象 ----------
class CandlestickItem(pg.GraphicsObject):
    """高性能批量绘制 K 线蜡烛体"""
    def __init__(self, data):
        super().__init__()
        self.data = data  # [(x, open, close, high, low), ...]
        self.generatePicture()

    def generatePicture(self):
        self.picture = QPicture()
        p = QPainter(self.picture)
        up_color = QColor('#FF4757')    # 红涨 — 更鲜艳
        down_color = QColor('#26DE81')  # 绿跌 — 更鲜艳
        w = 0.3  # 蜡烛体半宽

        for i in range(len(self.data)):
            t, o, c, h, l = self.data[i]
            color = up_color if c >= o else down_color

            # 上下影线
            p.setPen(pg.mkPen(color))
            p.drawLine(pg.Point(float(t), float(l)), pg.Point(float(t), float(h)))

            # 蜡烛实体
            p.setBrush(pg.mkBrush(color))
            if c >= o:
                body_h = max(c - o, 0.001)  # 防止 0 高度
                p.drawRect(pg.QtCore.QRectF(t - w, o, w * 2, body_h))
            else:
                body_h = max(o - c, 0.001)
                p.drawRect(pg.QtCore.QRectF(t - w, c, w * 2, body_h))

        p.end()

    def paint(self, p, *args):
        p.drawPicture(0, 0, self.picture)

    def boundingRect(self):
        return pg.QtCore.QRectF(self.picture.boundingRect())


class SimulatorChartWidget(QWidget):
    """完整版模拟器图表控件：K线+成交量+MACD 三面板"""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: #080A0F;")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # OHLC 信息标签
        self.ohlc_label = QLabel("")
        self.ohlc_label.setStyleSheet("""
            color: #8B92A5; font-size: 11px; padding: 4px 10px;
            background: rgba(13, 15, 20, 0.85);
            font-family: 'Consolas', 'Courier New', monospace;
            border-bottom: 1px solid rgba(255,255,255,0.04);
        """)
        layout.addWidget(self.ohlc_label)

        # pyqtgraph 布局
        self.layout_widget = pg.GraphicsLayoutWidget(show=True)
        self.layout_widget.setBackground('#080A0F')
        layout.addWidget(self.layout_widget)

        # 面板1: K线 (占比 4)
        self.p1 = self.layout_widget.addPlot(row=0, col=0)
        self.p1.showGrid(x=False, y=True, alpha=0.15)
        self.p1.getAxis("bottom").setStyle(showValues=False)
        self.p1.setMouseEnabled(x=False, y=False)

        # 面板2: 成交量 (占比 1)
        self.p2 = self.layout_widget.addPlot(row=1, col=0)
        self.p2.setMaximumHeight(80)
        self.p2.showGrid(x=False, y=True, alpha=0.1)
        self.p2.getAxis("bottom").setStyle(showValues=False)
        self.p2.setXLink(self.p1)
        self.p2.setMouseEnabled(x=False, y=False)

        # 面板3: MACD (占比 1.2)
        self.p3 = self.layout_widget.addPlot(row=2, col=0)
        self.p3.setMaximumHeight(90)
        self.p3.showGrid(x=False, y=True, alpha=0.1)
        self.p3.setXLink(self.p1)
        self.p3.setMouseEnabled(x=False, y=False)

        # 十字光标
        dash_pen = pg.mkPen(QColor(100, 116, 139, 100), width=1, style=Qt.PenStyle.DashLine)
        self.vLine1 = pg.InfiniteLine(angle=90, movable=False, pen=dash_pen)
        self.hLine1 = pg.InfiniteLine(angle=0, movable=False, pen=dash_pen)
        self.vLine2 = pg.InfiniteLine(angle=90, movable=False, pen=dash_pen)
        self.vLine3 = pg.InfiniteLine(angle=90, movable=False, pen=dash_pen)

        for item, plot in [(self.vLine1, self.p1), (self.hLine1, self.p1),
                           (self.vLine2, self.p2), (self.vLine3, self.p3)]:
            plot.addItem(item, ignoreBounds=True)

        # 鼠标事件代理
        self.proxy1 = pg.SignalProxy(self.p1.scene().sigMouseMoved, rateLimit=60, slot=self.mouseMoved)

        # 内部状态
        self.df = None
        self.start_loc = 0
        self.time_dict = {}

    def update_chart(self, full_df, current_loc, trigger_loc=None, entry_loc=None, entry_price=None,
                     pivot=0.0, trades=None, current_code=None,
                     signal_details=None, show_full_future=False,
                     blind_mode=False):
        """
        核心绘图方法。
        trades: list[TradeRecord] — 该股票上的所有已完成交易
        signal_details: dict — VCP 信号详情 (包含 _peak_dates, 区间最高价 等)
        show_full_future: 是否展示全部未来走势（上帝视角）
        """
        self.p1.clear()
        self.p2.clear()
        self.p3.clear()

        # 重新添加十字光标
        for item, plot in [(self.vLine1, self.p1), (self.hLine1, self.p1),
                           (self.vLine2, self.p2), (self.vLine3, self.p3)]:
            plot.addItem(item, ignoreBounds=True)

        if full_df is None or len(full_df) == 0:
            return

        # 计算显示窗口
        m = signal_details or {}
        total_len = m.get('_hit_base', 90) + m.get('_hit_E', 10)

        # 获取 trigger_loc（优先使用外部传入值，否则回退到 current_loc）
        if trigger_loc is None:
            trigger_loc = current_loc

        if show_full_future:
            end = len(full_df) - 1
            start = max(0, trigger_loc - total_len - 150)
            if end - start > 400:
                start = max(0, end - 400)
        else:
            end = current_loc
            start = max(0, trigger_loc - total_len - 150)

        self.start_loc = start
        self.df = full_df.iloc[start:end + 1].copy()

        if len(self.df) == 0:
            return

        # 前向填充 NaN
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in self.df.columns:
                self.df[col] = self.df[col].ffill().bfill()

        n = len(self.df)

        # ======== 1. K 线蜡烛 ========
        candle_data = []
        vol_up_x, vol_up_y = [], []
        vol_dn_x, vol_dn_y = [], []
        x_ticks = []
        self.time_dict.clear()

        for i, (dt, row) in enumerate(self.df.iterrows()):
            o, c, h, l = row['open'], row['close'], row['high'], row['low']
            v = row.get('volume', 0)
            candle_data.append([i, o, c, h, l])
            self.time_dict[i] = dt.strftime('%Y-%m-%d')

            if c >= o:
                vol_up_x.append(i)
                vol_up_y.append(v)
            else:
                vol_dn_x.append(i)
                vol_dn_y.append(v)

            if i % 30 == 0:
                label = "****" if blind_mode else dt.strftime('%Y-%m')
                x_ticks.append((i, label))

        self.p3.getAxis("bottom").setTicks([x_ticks])
        self.p3.getAxis("bottom").setStyle(showValues=True)

        candle_item = CandlestickItem(candle_data)
        self.p1.addItem(candle_item)

        # ======== 2. 成交量柱 ========
        if vol_up_x:
            self.p2.addItem(pg.BarGraphItem(x=vol_up_x, height=vol_up_y, width=0.6, brush='#F23645', pen=None))
        if vol_dn_x:
            self.p2.addItem(pg.BarGraphItem(x=vol_dn_x, height=vol_dn_y, width=0.6, brush='#30D158', pen=None))

        # ======== 3. MA 均线 ========
        closes = self.df['close']
        for period, color, name in [(10, '#FFFFFF', 'MA10'), (20, '#0A84FF', 'MA20'),
                                     (50, '#FF9F0A', 'MA50'), (150, '#BF5AF2', 'MA150'),
                                     (200, '#FF375F', 'MA200')]:
            ma = closes.rolling(period).mean()
            if ma.notna().any():
                xs = list(range(n))
                self.p1.plot(xs, ma.values, pen=pg.mkPen(color, width=1.2), name=name)

        # ======== 4. MACD 副图 ========
        if 'MACD' in self.df.columns and not self.df['MACD'].isna().all():
            xs = list(range(n))
            # MACD 线
            self.p3.plot(xs, self.df['MACD'].values, pen=pg.mkPen('#BF5AF2', width=1))
            # 信号线
            if 'MACD_Signal' in self.df.columns:
                self.p3.plot(xs, self.df['MACD_Signal'].values, pen=pg.mkPen('#FF9F0A', width=1))
            # 柱状图
            if 'MACD_Hist' in self.df.columns:
                hist = self.df['MACD_Hist'].values
                hist_up_x = [i for i in range(n) if not np.isnan(hist[i]) and hist[i] >= 0]
                hist_up_y = [hist[i] for i in hist_up_x]
                hist_dn_x = [i for i in range(n) if not np.isnan(hist[i]) and hist[i] < 0]
                hist_dn_y = [hist[i] for i in hist_dn_x]
                if hist_up_x:
                    self.p3.addItem(pg.BarGraphItem(x=hist_up_x, height=hist_up_y, width=0.6, brush='#8B0000', pen=None))
                if hist_dn_x:
                    self.p3.addItem(pg.BarGraphItem(x=hist_dn_x, height=hist_dn_y, width=0.6, brush='#1A5276', pen=None))

        # ======== 5. Fibonacci 回撤线 ========
        high_val = self.df['high'].max()
        low_val = self.df['low'].min()
        diff = high_val - low_val
        if diff > 0:
            fib_pen = pg.mkPen('#555555', width=0.8, style=Qt.PenStyle.DotLine)
            for ratio, label in [(0.382, 'Fib 38.2%'), (0.5, 'Fib 50.0%'), (0.618, 'Fib 61.8%')]:
                y = high_val - diff * ratio
                self.p1.plot([0, n], [y, y], pen=fib_pen)
                txt = pg.TextItem(label, color='#555555', anchor=(1, 1))
                txt.setPos(n - 1, y)
                self.p1.addItem(txt)

        # ======== 6. Pivot (箱顶) 线 ========
        if pivot and pivot > 0:
            self.p1.plot([0, n], [pivot, pivot],
                         pen=pg.mkPen('#00CED1', width=1.0, style=Qt.PenStyle.DashLine))
            txt = pg.TextItem(f"箱顶: {pivot:.2f}", color='#00CED1', anchor=(0, 1))
            txt.setPos(0, pivot)
            self.p1.addItem(txt)

        # ======== 7. VCP 结构矩形 + 峰位虚线 + R标签 ========
        box_high = float(m.get('区间最高价', 0))
        box_low = float(m.get('区间最低点', 0))
        peak_dates = m.get('_peak_dates') or [m.get('_high1_date'), m.get('_high2_date'), m.get('_high3_date')]
        peak_dates = [pd.to_datetime(d) for d in peak_dates if d]

        if peak_dates and box_high > 0 and box_low > 0:
            try:
                plot_start = self.df.index[0]
                plot_end = self.df.index[-1]
                valid_peaks = [d for d in peak_dates if plot_start <= d <= plot_end]
                if valid_peaks:
                    idxer = self.df.index.get_indexer(valid_peaks, method='nearest')
                    x_plots = [max(0, min(int(idx), n - 1)) for idx in idxer]
                    x_s, x_e = x_plots[0], x_plots[-1]

                    # 半透明蓝色矩形
                    rect = pg.LinearRegionItem(values=(x_s, x_e), orientation='vertical',
                                                brush=pg.mkBrush(51, 153, 255, 30), movable=False)
                    rect.setZValue(-10)
                    self.p1.addItem(rect)

                    # 青色横线：箱顶、箱底
                    cyan_pen = pg.mkPen('#00CED1', width=1.5)
                    self.p1.plot([x_s, x_e], [box_high, box_high], pen=cyan_pen)
                    self.p1.plot([x_s, x_e], [box_low, box_low], pen=cyan_pen)

                    # 箱底标注
                    txt_lo = pg.TextItem(f"箱底: {box_low:.2f}", color='#9CA3AF', anchor=(0, 0))
                    txt_lo.setPos(x_s, box_low)
                    self.p1.addItem(txt_lo)

                    # 峰位黄色虚线 + R 标签
                    peak_pen = pg.mkPen('#FFD700', width=1.2, style=Qt.PenStyle.DashLine)
                    for xp in x_plots:
                        line = pg.InfiniteLine(pos=xp, angle=90, pen=peak_pen, movable=False)
                        self.p1.addItem(line)

                    for i in range(len(x_plots) - 1):
                        x_center = (x_plots[i] + x_plots[i + 1]) / 2
                        r_txt = pg.TextItem(f'R{i+1}', color='#FFD700', anchor=(0.5, 1))
                        r_txt.setPos(x_center, box_low)
                        self.p1.addItem(r_txt)

                    # 振幅标注
                    amp_pct = (box_high - box_low) / box_low * 100 if box_low > 0 else 0
                    amp_txt = pg.TextItem(f"振幅 {amp_pct:.1f}%", color='#00CED1', anchor=(0, 0))
                    amp_txt.setPos(x_s, box_low * 0.995)
                    self.p1.addItem(amp_txt)

                    # 成交量面板：买入区橙色高亮
                    if x_plots:
                        vol_highlight = pg.LinearRegionItem(values=(x_plots[-1], n),
                                                            orientation='vertical',
                                                            brush=pg.mkBrush(255, 179, 102, 40),
                                                            movable=False)
                        vol_highlight.setZValue(-10)
                        self.p2.addItem(vol_highlight)
            except Exception:
                pass

        # ======== 8. 红色触发点三角 ========
        trigger_rel = trigger_loc - start
        if 0 <= trigger_rel < n:
            trigger_y = self.df.iloc[trigger_rel]['low'] * 0.96
            scatter = pg.ScatterPlotItem([trigger_rel], [trigger_y], symbol='t',
                                          size=18, brush='#FF5252', pen=None)
            self.p1.addItem(scatter)

        # ======== 9. 买卖历史三角标记 ========
        if trades and current_code:
            buy_xs, buy_ys = [], []
            sell_xs, sell_ys = [], []
            for t in trades:
                if t.code != current_code:
                    continue
                if t.entry_loc >= 0:
                    idx_b = t.entry_loc - start
                    if 0 <= idx_b < n:
                        buy_xs.append(idx_b)
                        buy_ys.append(self.df.iloc[idx_b]['low'] * 0.94)
                if t.exit_loc >= 0:
                    idx_s = t.exit_loc - start
                    if 0 <= idx_s < n:
                        sell_xs.append(idx_s)
                        sell_ys.append(self.df.iloc[idx_s]['high'] * 1.06)
            if buy_xs:
                self.p1.addItem(pg.ScatterPlotItem(buy_xs, buy_ys, symbol='t',
                                                    size=18, brush='#FFD600', pen=None))
            if sell_xs:
                self.p1.addItem(pg.ScatterPlotItem(sell_xs, sell_ys, symbol='t3',
                                                    size=18, brush='#00E676', pen=None))

        # ======== 10. 当前持仓成本线 ========
        if entry_loc is not None and entry_price is not None:
            rel_entry = entry_loc - start
            if 0 <= rel_entry < n:
                # 黄色买入三角
                self.p1.addItem(pg.ScatterPlotItem([rel_entry],
                    [self.df.iloc[rel_entry]['low'] * 0.94],
                    symbol='t', size=18, brush='#FFD600', pen=None))
                # 成本虚线
                self.p1.plot([rel_entry, n], [entry_price, entry_price],
                             pen=pg.mkPen('#FFD600', width=1.0, style=Qt.PenStyle.DashLine))
                cost_txt = pg.TextItem(f"成本: {entry_price:.2f}", color='#FFD600', anchor=(0, 1))
                cost_txt.setPos(rel_entry, entry_price)
                self.p1.addItem(cost_txt)

        # ======== 坐标范围 ========
        padding = 2
        self.p1.setXRange(-padding, n + padding, padding=0)
        self.p1.enableAutoRange(axis=pg.ViewBox.YAxis, enable=True)

    def mouseMoved(self, evt):
        """十字光标 + OHLC 信息面板"""
        pos = evt[0]
        if self.df is None:
            return

        mouse_point = None
        if self.p1.sceneBoundingRect().contains(pos):
            mouse_point = self.p1.vb.mapSceneToView(pos)
        elif self.p2.sceneBoundingRect().contains(pos):
            mouse_point = self.p2.vb.mapSceneToView(pos)
        elif self.p3.sceneBoundingRect().contains(pos):
            mouse_point = self.p3.vb.mapSceneToView(pos)

        if mouse_point is not None:
            index = int(mouse_point.x())
            if 0 <= index < len(self.df):
                row = self.df.iloc[index]
                dt_str = self.time_dict.get(index, "")

                # 更新十字光标
                self.vLine1.setPos(index)
                self.vLine2.setPos(index)
                self.vLine3.setPos(index)
                if self.p1.sceneBoundingRect().contains(pos):
                    self.hLine1.setPos(mouse_point.y())

                # 更新 OHLC 标签
                o, h, l, c = row['open'], row['high'], row['low'], row['close']
                chg = (c / row['open'] - 1) * 100 if row['open'] > 0 else 0
                v = row.get('volume', 0)
                self.ohlc_label.setText(
                    f"  {dt_str}  |  O: {o:.2f}  H: {h:.2f}  L: {l:.2f}  C: {c:.2f}  "
                    f"涨跌: {chg:+.2f}%  成交量: {v/10000:.0f}万手"
                )
