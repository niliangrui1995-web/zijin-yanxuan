# -*- coding: utf-8 -*-
"""
K 线图窗口 — ECharts 5.5.0 + QWebEngineView 高性能版
替代旧版 PyQtGraph，实现专业级金融图表体验。

核心特性：
- 三面板布局：K线主图 + 成交量 + MACD
- MA5/10/20/50/150/200 均线系统
- VCP 买点信号覆盖层（箱体 + 金星 + 高点连线）
- 盘中 60 秒增量热更新（无闪烁）
- 十字光标 + 顶部工具栏实时联动
"""
import json
import os as _os
import numpy as np
from core.logger import get_logger

log = get_logger(__name__)
import pandas as pd

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QHBoxLayout, QPushButton
)
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtWebEngineWidgets import QWebEngineView

from ui.viewmodels.watchlist_vm import watchlist_vm

# ECharts JS 本地路径（断网也能用）
_ECHARTS_JS_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "assets", "echarts.min.js"
)


def _build_html(title: str, echarts_data: dict, echarts_js_path: str) -> str:
    """
    构建完整的 ECharts HTML 页面。
    为什么把 HTML 模板放在函数里而不是外部文件：因为需要动态嵌入 JSON 数据和 JS 路径，
    放在 Python 里能保证数据注入安全且不依赖额外文件。
    """
    # 将本地 JS 文件路径转成 file:// URL（Windows 路径反斜杠需要处理）
    js_url = QUrl.fromLocalFile(echarts_js_path).toString()
    data_json = json.dumps(echarts_data, ensure_ascii=False)

    return f'''<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <script src="{js_url}"></script>
    <style>
        body {{ margin: 0; padding: 0; background-color: #0A0A0A; color: #D1D4DC; font-family: "Microsoft YaHei", sans-serif; overflow: hidden; }}
        #chart {{ width: 100vw; height: calc(100vh - 35px); margin-top: 35px; }}

        .top-toolbar {{ position: absolute; top: 0; left: 0; right: 0; height: 35px; background: #0A0A0A; border-bottom: 1px solid #222; display: flex; align-items: center; padding: 0 15px; z-index: 100; }}
        .stock-title {{ font-size: 15px; font-weight: bold; color: #FFF; margin-right: 15px; }}
        .info-item {{ font-size: 12px; color: #A0A0A0; margin-right: 8px; }}
        .info-val {{ font-size: 12px; font-weight: bold; margin-left: 2px; }}
        .ma-display {{ margin-left: 5px; font-size: 11px; font-weight: bold; display: flex; gap: 8px; flex-wrap: nowrap; }}
        .ma-display span.ma10 {{ color: #FFFFFF; }}
        .ma-display span.ma20 {{ color: #00A2E8; }}
        .ma-display span.ma50 {{ color: #FF9000; }}
        .ma-display span.ma150 {{ color: #BF5AF2; }}
        .ma-display span.ma200 {{ color: #FF375F; }}
    </style>
</head>
<body>
    <div class="top-toolbar" id="toolbar">
        <div class="stock-title">{title}</div>
        <div class="info-item">日期: <span id="v-date" class="info-val" style="color: #fff">-</span></div>
        <div class="info-item">开: <span id="v-open" class="info-val">-</span></div>
        <div class="info-item">高: <span id="v-high" class="info-val">-</span></div>
        <div class="info-item">低: <span id="v-low" class="info-val">-</span></div>
        <div class="info-item">收: <span id="v-close" class="info-val">-</span></div>
        <div class="info-item">涨幅: <span id="v-pct" class="info-val">-</span></div>
        <div class="info-item">成交量: <span id="v-vol" class="info-val">-</span></div>

        <div class="ma-display">
            <span class="ma10">MA10: <span id="v-ma10">-</span></span>
            <span class="ma20">MA20: <span id="v-ma20">-</span></span>
            <span class="ma50">MA50: <span id="v-ma50">-</span></span>
            <span class="ma150">MA150: <span id="v-ma150">-</span></span>
            <span class="ma200">MA200: <span id="v-ma200">-</span></span>
        </div>
    </div>
    <div id="chart"></div>

    <script>
        const rawData = {data_json};

        const upColor = '#F92855';
        const downColor = '#00FFFF';

        const chart = echarts.init(document.getElementById('chart'));

        // 十字光标联动顶部工具栏
        chart.on('updateAxisPointer', function (event) {{
            const axisInfo = event.axesInfo[0];
            if (axisInfo) {{
                const idx = axisInfo.value;
                if (idx >= 0 && idx < rawData.dates.length) {{
                    const dt = rawData.dates[idx];
                    const kline = rawData.klines[idx];

                    document.getElementById('v-date').innerText = dt;
                    document.getElementById('v-open').innerText = kline[0].toFixed(2);
                    document.getElementById('v-close').innerText = kline[1].toFixed(2);
                    document.getElementById('v-low').innerText = kline[2].toFixed(2);
                    document.getElementById('v-high').innerText = kline[3].toFixed(2);

                    let prevClose = idx > 0 ? rawData.klines[idx-1][1] : kline[0];
                    let pct = ((kline[1] - prevClose) / prevClose * 100);
                    let pctStr = pct >= 0 ? '+' + pct.toFixed(2) + '%' : pct.toFixed(2) + '%';

                    if (pct > 9.0) {{ pctStr = '🔥涨停 ' + pctStr; }}
                    else if (pct < -9.0) {{ pctStr = '💦跌停 ' + pctStr; }}

                    document.getElementById('v-pct').innerText = pctStr;
                    document.getElementById('v-pct').style.color = pct >= 0 ? upColor : downColor;

                    const vol = rawData.vols[idx].value || rawData.vols[idx];
                    document.getElementById('v-vol').innerText = (vol / 10000).toFixed(0) + '万';

                    // 更新 MA 数值显示
                    const maKeys = ['ma10', 'ma20', 'ma50', 'ma150', 'ma200'];
                    for (const key of maKeys) {{
                        const el = document.getElementById('v-' + key);
                        if (el && rawData[key]) {{
                            const val = rawData[key][idx];
                            el.innerText = val !== null && val !== undefined ? val.toFixed(2) : '-';
                        }}
                    }}
                }}
            }}
        }});

        const option = {{
            backgroundColor: '#0A0A0A',
            animation: false,
            tooltip: {{ trigger: 'axis', axisPointer: {{ type: 'cross' }}, showContent: false }},
            axisPointer: {{ link: [{{ xAxisIndex: 'all' }}], label: {{ backgroundColor: '#777' }} }},
            grid: [
                {{ left: '3%', right: '3%', top: '4%', height: '60%' }},
                {{ left: '3%', right: '3%', top: '65%', height: '14%' }},
                {{ left: '3%', right: '3%', top: '82%', height: '15%' }}
            ],
            xAxis: [
                {{ type: 'category', data: rawData.dates, gridIndex: 0, boundaryGap: false, axisLine: {{ onZero: false }}, splitLine: {{ show: true, lineStyle: {{ color: 'rgba(255,255,255,0.05)', type: 'dashed' }} }}, axisLabel: {{ show: false }} }},
                {{ type: 'category', data: rawData.dates, gridIndex: 1, boundaryGap: false, axisLine: {{ onZero: false }}, splitLine: {{ show: true, lineStyle: {{ color: 'rgba(255,255,255,0.05)', type: 'dashed' }} }}, axisLabel: {{ show: false }} }},
                {{ type: 'category', data: rawData.dates, gridIndex: 2, boundaryGap: false, axisLine: {{ onZero: false, lineStyle: {{ color: '#444' }} }}, splitLine: {{ show: true, lineStyle: {{ color: 'rgba(255,255,255,0.05)', type: 'dashed' }} }}, axisLabel: {{ color: '#888' }} }}
            ],
            yAxis: [
                {{ gridIndex: 0, scale: true, splitLine: {{ show: true, lineStyle: {{ color: 'rgba(255,255,255,0.05)', type: 'dashed' }} }}, position: 'right', axisLabel: {{ color: '#888' }} }},
                {{ gridIndex: 1, scale: true, splitLine: {{ show: false }}, position: 'left', axisLabel: {{ color: '#888', formatter: function(val) {{ return val >= 1e8 ? (val/1e8).toFixed(0)+'亿' : val >= 1e4 ? (val/1e4).toFixed(0)+'万' : val; }} }} }},
                {{ gridIndex: 2, scale: true, splitLine: {{ show: false }}, position: 'left', axisLabel: {{ color: '#888' }} }}
            ],
            series: [
                {{
                    name: 'KLine', type: 'candlestick',
                    xAxisIndex: 0, yAxisIndex: 0, data: rawData.klines,
                    itemStyle: {{ color: upColor, color0: downColor, borderColor: upColor, borderColor0: downColor }},
                    markPoint: rawData.vcpMarkers ? {{ data: rawData.vcpMarkers, symbol: 'pin', symbolSize: 1, label: {{ show: true, formatter: '⭐ 突破', color: '#FFD60A', offset: [0, -15] }} }} : null,
                    markLine: rawData.vcpLines ? {{ data: rawData.vcpLines, symbol: 'none', label: {{ show: false }}, lineStyle: {{ color: '#FFD700', type: 'dashed' }} }} : null,
                    markArea: rawData.vcpArea ? {{ data: rawData.vcpArea, itemStyle: {{ color: 'rgba(51, 153, 255, 0.1)' }} }} : null
                }},
                {{ name: 'MA10', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: rawData.ma10, symbol: 'none', lineStyle: {{ color: '#FFFFFF', width: 1.5 }} }},
                {{ name: 'MA20', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: rawData.ma20, symbol: 'none', lineStyle: {{ color: '#00A2E8', width: 1.5 }} }},
                {{ name: 'MA50', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: rawData.ma50, symbol: 'none', lineStyle: {{ color: '#FF9000', width: 1.5 }} }},
                {{ name: 'MA150', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: rawData.ma150, symbol: 'none', lineStyle: {{ color: '#BF5AF2', width: 1 }} }},
                {{ name: 'MA200', type: 'line', xAxisIndex: 0, yAxisIndex: 0, data: rawData.ma200, symbol: 'none', lineStyle: {{ color: '#FF375F', width: 1 }} }},

                {{ name: 'Volume', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: rawData.vols }},
                {{ name: 'VolMA20', type: 'line', xAxisIndex: 1, yAxisIndex: 1, data: rawData.volMa20, symbol: 'none', lineStyle: {{ color: '#FFD700', width: 1.2 }}, z: 10 }},

                {{ name: 'MACD', type: 'bar', xAxisIndex: 2, yAxisIndex: 2, data: rawData.macd }},
                {{ name: 'DIFF', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: rawData.diff, symbol: 'none', lineStyle: {{ color: '#FF9000', width: 1 }} }},
                {{ name: 'DEA', type: 'line', xAxisIndex: 2, yAxisIndex: 2, data: rawData.dea, symbol: 'none', lineStyle: {{ color: '#00A2E8', width: 1 }} }}
            ]
        }};

        chart.setOption(option);
        window.addEventListener('resize', function () {{ chart.resize(); }});

        // 严格禁用鼠标滚轮缩放和拖拽（任务卡明确要求锁死视图）
        document.getElementById('chart').addEventListener('wheel', function(e) {{ e.preventDefault(); }}, {{ passive: false }});
        document.getElementById('chart').addEventListener('mousedown', function(e) {{
            if (e.button === 0) {{ e.stopPropagation(); }}
        }}, true);

        // 默认展示最后一根 K 线的信息
        chart.dispatchAction({{ type: 'showTip', seriesIndex: 0, dataIndex: rawData.dates.length - 1 }});

        // === 盘中增量热更新接口 ===
        window.updateLastBar = function(newQuote) {{
            const lastIdx = rawData.dates.length - 1;
            const lastDt = rawData.dates[lastIdx];
            
            if (newQuote.date && newQuote.date > lastDt) {{
                // 如果出现跨日新增（极罕见），最好交给全量替换或主动刷新，暂时用兜底填充
                rawData.dates.push(newQuote.date);
                rawData.klines.push([newQuote.open, newQuote.close, newQuote.low, newQuote.high]);
                const isUp = newQuote.close >= newQuote.open;
                rawData.vols.push({{ value: newQuote.vol, itemStyle: {{ color: isUp ? upColor : downColor }} }});
                
                chart.setOption({{
                    xAxis: [ {{ data: rawData.dates }}, {{ data: rawData.dates }}, {{ data: rawData.dates }} ],
                    series: [
                        {{ data: rawData.klines }}, {{}}, {{}}, {{}}, {{}}, {{}},
                        {{ data: rawData.vols }}, {{}}
                    ]
                }});
                chart.dispatchAction({{ type: 'showTip', seriesIndex: 0, dataIndex: rawData.dates.length - 1 }});
            }} else {{
                // 同日更新最后一条
                rawData.klines[lastIdx] = [newQuote.open, newQuote.close, newQuote.low, newQuote.high];
                const isUp = newQuote.close >= newQuote.open;
                rawData.vols[lastIdx] = {{ value: newQuote.vol, itemStyle: {{ color: isUp ? upColor : downColor }} }};

                chart.setOption({{
                    series: [
                        {{ data: rawData.klines }},
                        {{}}, {{}}, {{}}, {{}}, {{}},  // 跳过 MA 线
                        {{ data: rawData.vols }},
                        {{}}  // 跳过 VolMA20（盘中不重算均量线）
                    ]
                }}, {{ lazyUpdate: true }});
                chart.dispatchAction({{ type: 'showTip', seriesIndex: 0, dataIndex: lastIdx }});
            }}
        }};

        // === 全量替换接口（切换股票时用） ===
        window.replaceAllData = function(newData) {{
            for (const key of Object.keys(newData)) {{
                rawData[key] = newData[key];
            }}
            // 重建 markPoint / markLine / markArea
            const seriesUpdate = [
                {{
                    data: rawData.klines,
                    markPoint: rawData.vcpMarkers ? {{ data: rawData.vcpMarkers, symbol: 'pin', symbolSize: 1, label: {{ show: true, formatter: '⭐ 突破', color: '#FFD60A', offset: [0, -15] }} }} : null,
                    markLine: rawData.vcpLines ? {{ data: rawData.vcpLines, symbol: 'none', label: {{ show: false }}, lineStyle: {{ color: '#FFD700', type: 'dashed' }} }} : null,
                    markArea: rawData.vcpArea ? {{ data: rawData.vcpArea, itemStyle: {{ color: 'rgba(51, 153, 255, 0.1)' }} }} : null
                }},
                {{ data: rawData.ma10 }},
                {{ data: rawData.ma20 }},
                {{ data: rawData.ma50 }},
                {{ data: rawData.ma150 }},
                {{ data: rawData.ma200 }},
                {{ data: rawData.vols }},
                {{ data: rawData.volMa20 }},
                {{ data: rawData.macd }},
                {{ data: rawData.diff }},
                {{ data: rawData.dea }}
            ];
            chart.setOption({{
                xAxis: [
                    {{ data: rawData.dates }},
                    {{ data: rawData.dates }},
                    {{ data: rawData.dates }}
                ],
                series: seriesUpdate
            }});
            // 更新标题
            document.querySelector('.stock-title').innerText = rawData.title || '';
            chart.dispatchAction({{ type: 'showTip', seriesIndex: 0, dataIndex: rawData.dates.length - 1 }});
        }};
    </script>
</body>
</html>'''


class KLineChartWindow(QWidget):
    """ECharts 驱动的 K 线图窗口"""

    def __init__(self, main_window, code, name, data_provider, vcp_data=None, code_list=None, current_idx=0):
        super().__init__()
        self.main_window = main_window
        self.code = code
        self.name = name
        self.data_provider = data_provider
        self.vcp_data = vcp_data or {}
        self.code_list = code_list or []
        self.current_idx = current_idx

        # 盘中实时刷新定时器
        self._rt_timer = None
        # 缓存当前展示的 DataFrame（用于增量更新）
        self.df = None
        self.time_dict = {}

        self.setWindowTitle(f"{name} ({code}) - K线详情")
        self.resize(1100, 680)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)

        # 窗口图标
        from PyQt6.QtGui import QIcon
        icon_path = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), 'bull_icon.ico')
        if _os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # 深色主题
        self.setStyleSheet("""
            QWidget { background-color: #0B0B0E; color: #F5F5F7; }
            QLabel { font-weight: bold; font-family: "Microsoft YaHei UI"; }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        # === 顶部 PyQt 原生控制栏（快捷键、按钮等都挂在这里） ===
        header_widget = QWidget()
        header_widget.setFixedHeight(40)
        header_widget.setStyleSheet("background-color: #0B0B0E; border-bottom: 1px solid #222;")
        header_layout = QHBoxLayout(header_widget)
        header_layout.setContentsMargins(10, 0, 10, 0)

        # 不再显示股票名称（ECharts 内部工具栏已有），只保留状态信息
        self.info_lbl = QLabel("正在加载数据...")
        self.info_lbl.setStyleSheet("color: #86868B; font-size: 12px;")
        header_layout.addWidget(self.info_lbl)
        header_layout.addStretch()

        # 导航按钮
        nav_btn_style = """
            QPushButton { background-color: transparent; color: #86868B; border: 1px solid #3A3A3C; border-radius: 4px; padding: 4px 10px; font-weight: bold; font-size: 12px; }
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
            QPushButton { background-color: transparent; color: #FFD60A; border: 1px solid #FFD60A; border-radius: 4px; padding: 4px 12px; font-weight: bold; font-size: 12px; }
            QPushButton:hover { background-color: rgba(255, 214, 10, 0.1); }
        """)
        self.btn_fav.clicked.connect(self._toggle_fav)
        header_layout.addWidget(self.btn_fav)

        layout.addWidget(header_widget)

        # === ECharts WebEngine 主图区域 ===
        self.browser = QWebEngineView()
        self.browser.setStyleSheet("background-color: #0A0A0A;")
        layout.addWidget(self.browser)

        # 快捷键 ←/→ 切换上/下一只股票
        from PyQt6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=lambda: self._nav_stock(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=lambda: self._nav_stock(1))
        self._update_nav_buttons()

        self._check_fav_status()
        self._load_and_draw()

    # ======================== 关注池 ========================
    def _check_fav_status(self):
        try:
            self.is_fav = watchlist_vm.is_in_watchlist(self.code)
            self.btn_fav.setText("⭐ 移出关注池" if self.is_fav else "☆ 移入关注池")
        except Exception as e:
            print(f"[K线窗口] 检查关注状态失败: {e}")
            self.is_fav = False

    def _toggle_fav(self):
        try:
            watchlist_vm.toggle_stock(self.code, self.name, self.vcp_data)
            self._check_fav_status()
        except Exception as e:
            print(f"[K线窗口] 切换关注状态失败: {e}")

    # ======================== 数据加载 ========================
    def _load_and_draw(self):
        """异步加载 K 线数据并渲染 ECharts"""
        is_asian = '.' in self.code
        if is_asian:
            self._load_asian_chart()
            return

        # 1. 尝试从内存缓存秒开
        df = self.data_provider.get_data(self.code)
        if df is not None and len(df) >= 60:
            self._render_chart(df, loading=True)
        else:
            self.info_lbl.setText("📡 正在获取完整 K 线数据...")

        # 2. 异步拉取最新日线 + 盘中实时
        def _bg_fetch():
            from datetime import datetime
            fresh_df = self.data_provider.get_data_fresh_for_chart(self.code)

            now = datetime.now()
            quote_to_apply = None

            if not getattr(self.data_provider, '_offline', False):
                already_has_latest = False
                if fresh_df is not None and not fresh_df.empty:
                    last_dt = pd.Timestamp(fresh_df.index[-1]).date()
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
                        # 在渲染前将今日最新报价合并到 df，这样算出来的 MA 和 MACD 对最后一根 K 也是准确的
                        if quote_to_apply is not None:
                            from datetime import datetime
                            # 计算正确的最新日期
                            today_date = datetime.now().date()
                            
                            rt_open = float(quote_to_apply.get('open', 0) or 0)
                            if rt_open > 0:
                                rt_close = float(quote_to_apply.get('close', 0) or 0)
                                rt_high = float(quote_to_apply.get('high', 0) or 0)
                                rt_low = float(quote_to_apply.get('low', 0) or 0)
                                rt_vol = float(quote_to_apply.get('volume', 0) or 0)
                                
                                last_date = pd.Timestamp(fresh_df.index[-1]).date()
                                
                                # 如果是同一天，就覆盖最后一行
                                if last_date >= today_date:
                                    fresh_df.iloc[-1, fresh_df.columns.get_loc('open')] = rt_open
                                    fresh_df.iloc[-1, fresh_df.columns.get_loc('high')] = max(float(fresh_df.iloc[-1]['high']), rt_high)
                                    fresh_df.iloc[-1, fresh_df.columns.get_loc('low')] = min(float(fresh_df.iloc[-1]['low']), rt_low)
                                    fresh_df.iloc[-1, fresh_df.columns.get_loc('close')] = rt_close
                                    if 'volume' in fresh_df.columns:
                                        fresh_df.iloc[-1, fresh_df.columns.get_loc('volume')] = rt_vol
                                else:
                                    # 如果是新交易日，追加一行
                                    new_row = pd.DataFrame({
                                        'open': [rt_open], 'high': [rt_high], 'low': [rt_low],
                                        'close': [rt_close], 'volume': [rt_vol]
                                    }, index=[pd.Timestamp(today_date)])
                                    fresh_df = pd.concat([fresh_df, new_row])
                                    
                        # 现在包含最新的K线，统一交给 _render_chart 去计算所有指标
                        self._render_chart(fresh_df, loading=False)
            except RuntimeError:
                # 窗口已被用户关闭
                pass

        from core.task_manager import task_manager
        task_manager.run_in_background(_bg_fetch, on_success=_on_fetch_success, task_id=f"kline_{self.code}")

    def _load_asian_chart(self):
        """加载亚洲市场（yfinance 缓存）的 K 线数据"""
        import json as json_mod
        from ui.tabs.asian_market_tab import JSON_CACHE, GLOBAL_ASIAN_RT_CACHE

        df = None
        if _os.path.exists(JSON_CACHE):
            with open(JSON_CACHE, 'r', encoding='utf-8') as f:
                raw = json_mod.load(f)
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
            if self.code in GLOBAL_ASIAN_RT_CACHE:
                quote = GLOBAL_ASIAN_RT_CACHE[self.code]
                df_today = quote.get('df_today')
                if df_today is not None and not df_today.empty:
                    # df_today 是雅虎 history(period='2mo') 的完整 DataFrame
                    idx = pd.to_datetime(df_today.index).normalize()
                    if idx.tz is not None:
                        idx = idx.tz_localize(None)
                    rt_df = pd.DataFrame(index=idx)
                    rt_df['open'] = df_today['Open'].values.astype(float)
                    rt_df['high'] = df_today['High'].values.astype(float)
                    rt_df['low'] = df_today['Low'].values.astype(float)
                    rt_df['close'] = df_today['Close'].values.astype(float)
                    if 'Volume' in df_today.columns:
                        rt_df['volume'] = df_today['Volume'].values.astype(float)
                    else:
                        rt_df['volume'] = 0.0
                    
                    # 关键修复：combine_first/update 会因日期格式/时区微妙差异静默失败
                    # 改为暴力覆盖：统一 normalize 后，删掉 df 中与 rt_df 重叠的行，再拼接
                    df.index = pd.to_datetime(df.index).normalize()
                    overlap_mask = df.index.isin(rt_df.index)
                    df = pd.concat([df[~overlap_mask], rt_df]).sort_index()
                    df = df[~df.index.duplicated(keep='last')]
                
                # --- 用 fast_info 实时 OHLC 强刷最后一根 K 线 ---
                from datetime import datetime
                from ui.tabs.asian_market_tab import get_market_status
                
                market_suffix = "TW"
                if '.' in self.code:
                    market_suffix = self.code.split('.')[-1]
                    
                status = get_market_status(market_suffix)
                today_date = datetime.now().date()
                last_date = pd.Timestamp(df.index[-1]).date()
                
                rt_close = quote.get('close')
                rt_open = quote.get('open')
                rt_high = quote.get('high')
                rt_low = quote.get('low')
                
                if rt_close is not None:
                    if last_date >= today_date:
                        # 今天的行已存在，用 fast_info 强刷全部 OHLC（包括开盘价！）
                        if rt_open:
                            df.iloc[-1, df.columns.get_loc('open')] = float(rt_open)
                        if rt_high:
                            df.iloc[-1, df.columns.get_loc('high')] = max(float(df.iloc[-1]['high']), float(rt_high))
                        if rt_low:
                            df.iloc[-1, df.columns.get_loc('low')] = min(float(df.iloc[-1]['low']), float(rt_low))
                        df.iloc[-1, df.columns.get_loc('close')] = float(rt_close)
                    elif "休市" not in status:
                        # 交易日但 history 没给今天的行，强行造一根
                        sim_open = float(rt_open or rt_close)
                        sim_high = float(rt_high or max(sim_open, float(rt_close)))
                        sim_low = float(rt_low or min(sim_open, float(rt_close)))
                        
                        new_row = pd.DataFrame({
                            'open': [sim_open], 
                            'high': [sim_high], 
                            'low': [sim_low],
                            'close': [float(rt_close)], 
                            'volume': [0.0]
                        }, index=[pd.Timestamp(today_date)])
                        df = pd.concat([df, new_row])


            # 统一交由 _render_chart() 去计算指标并生成完整 ECharts，此时必然包含最新日期
            self._render_chart(df, loading=False)
        else:
            self.info_lbl.setText("⚠ 暂无该亚洲标的历史数据")

    # ======================== 图表渲染 ========================
    def _render_chart(self, df, loading=False):
        """将 DataFrame 转换成 ECharts 数据格式并渲染到 WebEngine"""
        from vcp.engine import VCPEngine

        # 兼容 Polars DataFrame
        if not hasattr(df, 'iloc'):
            df = df.to_pandas()

        # 确保有 DatetimeIndex
        if 'date' in df.columns and not isinstance(df.index, pd.DatetimeIndex):
            df['date'] = pd.to_datetime(df['date'].astype(str))
            df.set_index('date', inplace=True)

        if df is None or len(df) < 60:
            if not loading:
                self.info_lbl.setText("⚠ 数据不足，无法绘图")
            return

        # 确保有 MACD 指标
        if 'MACD' not in df.columns or df['MACD'].isna().all():
            df = VCPEngine.calculate_indicators(df)

        if not loading:
            self.info_lbl.setText(f"✅ 数据拉取成功 (缓存行数: {len(df)})")

        # 截取最后 250 根 K 线
        self.df = df.iloc[-250:].copy()
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in self.df.columns:
                self.df[col] = self.df[col].ffill().bfill()

        # 构建 ECharts 数据
        echarts_data = self._build_echarts_data()

        # 判断是首次加载还是切换股票
        if not loading and not hasattr(self, '_first_render_done'):
            # 首次完整渲染（替换 loading 占位）
            pass

        # 渲染 HTML 到 WebEngine
        html_content = _build_html(
            title=f"{self.name} ({self.code}) 日线",
            echarts_data=echarts_data,
            echarts_js_path=_ECHARTS_JS_PATH
        )

        # 用 baseUrl 确保本地 file:// 引用正常
        base_url = QUrl.fromLocalFile(
            _os.path.dirname(_os.path.abspath(_ECHARTS_JS_PATH)) + "/"
        )
        self.browser.setHtml(html_content, base_url)
        self._first_render_done = True

        # 启动盘中定时器
        if not loading:
            self._start_rt_timer()

    def _build_echarts_data(self) -> dict:
        """将当前 self.df 转换为 ECharts 所需的 JSON 数据结构"""
        dates = []
        klines = []
        vols = []
        self.time_dict = {}

        up_color = '#F92855'
        down_color = '#00FFFF'

        for i, (dt, row) in enumerate(self.df.iterrows()):
            o = float(row['open'])
            c = float(row['close'])
            h = float(row['high'])
            l = float(row['low'])
            v = float(row.get('volume', 0))

            date_str = dt.strftime('%Y-%m-%d')
            dates.append(date_str)
            self.time_dict[i] = date_str

            # ECharts candlestick 格式：[open, close, low, high]
            klines.append([o, c, l, h])

            is_up = c >= o
            vols.append({
                "value": v,
                "itemStyle": {"color": up_color if is_up else down_color}
            })

        # 计算 MA 线
        closes = self.df['close'].values
        ma_config = [
            (10, 'ma10'),
            (20, 'ma20'),
            (50, 'ma50'),
            (150, 'ma150'),
            (200, 'ma200'),
        ]
        ma_data = {}
        for period, key in ma_config:
            if len(closes) >= period:
                ma = pd.Series(closes).rolling(period).mean()
                # 将 NaN 转为 None（JSON 中的 null），ECharts 会自动断线
                ma_data[key] = [round(v, 2) if not np.isnan(v) else None for v in ma.values]
            else:
                ma_data[key] = [None] * len(closes)

        # MACD 数据
        macd_bars = []
        diff_line = []
        dea_line = []

        if 'MACD' in self.df.columns:
            for i, (_, row) in enumerate(self.df.iterrows()):
                macd_val = float(row.get('MACD', 0) or 0)
                signal_val = float(row.get('MACD_Signal', 0) or 0)
                hist_val = float(row.get('MACD_Hist', 0) or 0)

                macd_bars.append({
                    "value": hist_val,
                    "itemStyle": {"color": up_color if hist_val >= 0 else down_color}
                })
                diff_line.append(round(macd_val, 4))
                dea_line.append(round(signal_val, 4))

        # 计算 Volume MA20 均量线
        vol_values = [v["value"] if isinstance(v, dict) else v for v in vols]
        vol_ma20 = pd.Series(vol_values).rolling(20).mean()
        vol_ma20_data = [round(v, 0) if not np.isnan(v) else None for v in vol_ma20.values]

        result = {
            "title": f"{self.name} ({self.code}) 日线",
            "dates": dates,
            "klines": klines,
            "vols": vols,
            "volMa20": vol_ma20_data,
            "macd": macd_bars,
            "diff": diff_line,
            "dea": dea_line,
            **ma_data,
            "vcpMarkers": None,
            "vcpLines": None,
            "vcpArea": None,
        }

        # 叠加 VCP 信号
        if self.vcp_data:
            self._inject_vcp_overlays(result, dates)

        return result

    def _inject_vcp_overlays(self, data: dict, dates: list):
        """将 VCP 买点信号注入 ECharts 的 markPoint / markLine / markArea"""
        trigger_date = self.vcp_data.get('触发日期', '')
        trigger_idx = -1

        date_to_idx = {d: i for i, d in enumerate(dates)}

        if trigger_date:
            for d, idx in date_to_idx.items():
                if trigger_date in d:
                    trigger_idx = idx
                    break

        # 金星突破标记
        if trigger_idx != -1:
            kline = data["klines"][trigger_idx]
            data["vcpMarkers"] = [{
                "coord": [trigger_idx, kline[3]],  # kline[3] = high
                "symbol": "pin",
                "symbolSize": 40,
                "label": {"show": True, "formatter": "⭐ 突破", "color": "#FFD60A", "fontSize": 11}
            }]

        # 箱体与高点连线
        box_high = self.vcp_data.get('区间最高价', 0)
        box_low = self.vcp_data.get('区间最低点', 0)

        peak_dates = self.vcp_data.get('_peak_dates', [])
        if not peak_dates:
            for key in ['_high1_date', '_high2_date', '_high3_date']:
                if self.vcp_data.get(key):
                    peak_dates.append(self.vcp_data[key])

        if box_high > 0 and box_low > 0 and peak_dates:
            valid_indices = []
            for d in peak_dates:
                d_short = str(d)[:10]
                if d_short in date_to_idx:
                    valid_indices.append(date_to_idx[d_short])
                else:
                    # 尝试去掉横杠匹配
                    d_no_dash = d_short.replace('-', '')
                    for date_key, idx in date_to_idx.items():
                        if date_key.replace('-', '') == d_no_dash:
                            valid_indices.append(idx)
                            break

            if valid_indices:
                x_start = min(valid_indices)
                x_end = trigger_idx if trigger_idx != -1 else len(dates) - 1

                # 箱体区域
                data["vcpArea"] = [[
                    {"xAxis": dates[x_start], "yAxis": box_low},
                    {"xAxis": dates[x_end], "yAxis": box_high}
                ]]

                # 高点垂直虚线
                vcp_lines = []
                for xi in valid_indices:
                    vcp_lines.append([
                        {"xAxis": dates[xi], "yAxis": box_low},
                        {"xAxis": dates[xi], "yAxis": box_high}
                    ])
                data["vcpLines"] = vcp_lines

    # ======================== 盘中增量更新 ========================
    def _start_rt_timer(self):
        """启动盘中实时刷新定时器（60秒间隔），只在交易时段运行"""
        from datetime import datetime
        now = datetime.now()
        is_trading_hours = (now.weekday() < 5 and
                           ((now.hour == 9 and now.minute >= 25) or
                            (10 <= now.hour <= 14) or
                            (now.hour == 15 and now.minute <= 5)))

        if not is_trading_hours or getattr(self.data_provider, '_offline', False):
            return

        if self._rt_timer is None:
            self._rt_timer = QTimer(self)
            self._rt_timer.timeout.connect(self._on_rt_timer)
        self._rt_timer.start(60 * 1000)
        print(f"[K线] {self.code} 盘中实时刷新已启动（间隔 60s）")

    def _on_rt_timer(self):
        """定时器回调：拉取最新实时报价，通过 JS 增量更新最后一根 K 线"""
        from datetime import datetime
        now = datetime.now()

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
                    rt_date = pd.Timestamp(last_row.name).strftime('%Y-%m-%d')
                    quote = {
                        'date': rt_date,
                        'open': float(last_row['Open']),
                        'high': float(last_row['High']),
                        'low': float(last_row['Low']),
                        'close': float(last_row['Close']),
                        'volume': float(last_row.get('Volume', 0))
                    }
                    self._refresh_last_bar(quote)
            else:
                quotes = self.data_provider.fetch_realtime_quotes_batch([self.code])
                if quotes and self.code in quotes:
                    self._refresh_last_bar(quotes[self.code])
        except Exception as e:
            print(f"[K线] {self.code} 实时刷新异常: {e}")

    def _refresh_last_bar(self, quote):
        """通过 JS 注入实现无闪烁增量更新最后一根 K 线"""
        if self.df is None or len(self.df) == 0:
            return

        rt_close = float(quote.get('close', 0) or 0)
        rt_open = float(quote.get('open', 0) or 0)
        rt_high = float(quote.get('high', 0) or 0)
        rt_low = float(quote.get('low', 0) or 0)
        rt_vol = float(quote.get('volume', 0) or 0)

        if rt_close <= 0 or rt_open <= 0:
            return

        from datetime import datetime

        rt_date_str = quote.get('date')
        last_date = pd.Timestamp(self.df.index[-1]).date()

        if rt_date_str:
            rt_date = pd.Timestamp(rt_date_str).date()
        else:
            from core.market_calendar import MarketCalendar
            today_date = datetime.now().date()
            is_trade = True
            try:
                trade_dates = MarketCalendar._trade_dates
                if trade_dates is None:
                    trade_dates = MarketCalendar.load_trade_dates()
                if trade_dates:
                    is_trade = today_date.strftime("%Y-%m-%d") in trade_dates
                else:
                    is_trade = today_date.weekday() < 5
            except Exception as _e:
                log.debug(f"[K线] 交易日历查询失败，按周末判断: {_e}")
                is_trade = today_date.weekday() < 5

            rt_date = pd.Timestamp(today_date).date() if is_trade else last_date

        # 更新本地 df 缓存
        if last_date >= rt_date:
            # 覆盖更新最后一根
            self.df.iloc[-1, self.df.columns.get_loc('open')] = rt_open
            self.df.iloc[-1, self.df.columns.get_loc('high')] = max(
                self.df.iloc[-1, self.df.columns.get_loc('high')], rt_high
            )
            self.df.iloc[-1, self.df.columns.get_loc('low')] = min(
                self.df.iloc[-1, self.df.columns.get_loc('low')], rt_low
            )
            self.df.iloc[-1, self.df.columns.get_loc('close')] = rt_close
            if 'volume' in self.df.columns:
                self.df.iloc[-1, self.df.columns.get_loc('volume')] = rt_vol
        else:
            # 新增一根 K 线
            new_row = pd.DataFrame({
                'open': [rt_open], 'high': [rt_high], 'low': [rt_low],
                'close': [rt_close], 'volume': [rt_vol]
            }, index=[pd.Timestamp(rt_date)])
            self.df = pd.concat([self.df, new_row])

        # 通过 JS 精准更新最后一根 Bar（无闪烁，不全量重绘）
        rt_json = json.dumps({
            'date': pd.Timestamp(rt_date).strftime('%Y-%m-%d'),
            'open': rt_open,
            'high': float(self.df.iloc[-1]['high']),
            'low': float(self.df.iloc[-1]['low']),
            'close': rt_close,
            'vol': rt_vol
        })
        self.browser.page().runJavaScript(f"window.updateLastBar({rt_json})")

        # 更新 PyQt 原生信息栏
        pre_close = rt_open
        if len(self.df) >= 2:
            pre_close = float(self.df.iloc[-2]['close'])

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

    # ======================== 导航 ========================
    def _nav_stock(self, delta):
        """切换股票：delta=-1 上一只, +1 下一只"""
        if not self.code_list:
            return
        if getattr(self, '_switching', False):
            return
        new_idx = self.current_idx + delta
        if 0 <= new_idx < len(self.code_list):
            self._switch_to_stock(new_idx)

    def _update_nav_buttons(self):
        if not self.code_list:
            self.btn_prev.setEnabled(False)
            self.btn_next.setEnabled(False)
            return
        self.btn_prev.setEnabled(self.current_idx > 0)
        self.btn_next.setEnabled(self.current_idx < len(self.code_list) - 1)

    def _switch_to_stock(self, new_idx):
        """切换到指定索引的股票"""
        self._switching = True
        try:
            # 停止旧定时器
            if self._rt_timer is not None:
                self._rt_timer.stop()

            # 重置状态
            item_data = self.code_list[new_idx]
            self.current_idx = new_idx
            self.code = item_data.get('代码', '')
            self.name = item_data.get('名称', '')
            self.vcp_data = item_data

            self.setWindowTitle(f"{self.name} ({self.code}) - K线详情")
            total = len(self.code_list)

            # 同步选中主窗口表格行
            if self.main_window and hasattr(self.main_window, 'table_scan'):
                self.main_window.table_scan.selectRow(new_idx)

            self._check_fav_status()
            self._load_and_draw()

            curr_text = self.info_lbl.text()
            self.info_lbl.setText(f"[{new_idx+1}/{total}] " + curr_text)
        finally:
            self._switching = False
            self._update_nav_buttons()

    # ======================== 资源释放 ========================
    def closeEvent(self, event):
        """窗口关闭时彻底释放 WebEngine 资源，防止内存泄漏"""
        # 停止定时器
        if self._rt_timer is not None:
            self._rt_timer.stop()
            self._rt_timer = None

        self.time_dict.clear()
        self.df = None

        # 释放 WebEngine（先导航到空白页释放 Chromium 渲染进程）
        try:
            self.browser.setUrl(QUrl("about:blank"))
            self.browser.deleteLater()
        except Exception as _e:
            log.debug(f"[K线] WebEngine 释放异常: {_e}")

        print(f"[K线] {self.code} 窗口关闭，WebEngine 资源已释放")
        super().closeEvent(event)
