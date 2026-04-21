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
import os as _os

from app.services.scan_runtime_service import calculate_scan_indicators
from app.services.ui_runtime_service import domain_events as event_bus
from core.logger import get_logger
from app.services.ui_runtime_service import MarketCalendar
from app.services.ui_runtime_service import watchlist_vm

log = get_logger(__name__)
import pandas as pd
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtWebEngineWidgets import QWebEngineView
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from ui.kline_chart_payload import (
    build_kline_echarts_payload,
    build_kline_html,
    build_kline_theme_colors,
)
from ui.kline_window_asian import (
    apply_asian_live_quote,
    build_asian_history_df,
    build_asian_rt_quote,
    load_cached_asian_stock,
    schedule_asian_history_backfill,
)
from ui.kline_window_header import (
    apply_header_badges,
    apply_info_styles,
    apply_qt_theme,
    get_cn_target_trade_date,
    refresh_header_context,
    resolve_vcp_context,
    set_header_badge,
)
from ui.kline_window_runtime import (
    load_and_draw,
    normalize_daily_df_index,
    poll_rt_update,
    refresh_last_bar,
)
from ui.theme import theme_manager

# ECharts JS 本地路径（断网也能用）
_ECHARTS_JS_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "assets", "echarts.min.js"
)


def fetch_single_kline(*args, **kwargs):
    # Avoid importing the full app.services facade during K-line window import.
    from app.services.asian_market_service import fetch_single_kline as _fetch_single_kline

    return _fetch_single_kline(*args, **kwargs)


class KLineChartWindow(QWidget):
    """ECharts 驱动的 K 线图窗口"""

    def __init__(self, main_window, code, name, data_provider, vcp_data=None, code_list=None, current_idx=0):
        super().__init__()
        self.main_window = main_window
        self.code = code
        self.name = name
        self.data_provider = data_provider
        self._log = log
        self.vcp_data = self._resolve_vcp_context(code, name, vcp_data or {})
        self.code_list = code_list or []
        self.current_idx = current_idx

        # 盘中实时刷新定时器
        self._rt_timer = None
        # 缓存当前展示的 DataFrame（用于增量更新）
        self.df = None

        self.setWindowTitle(f"{name} ({code}) - K线图")
        self.setWindowFlags(self.windowFlags() | Qt.WindowType.FramelessWindowHint)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.resize(1100, 680)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        event_bus.sig_rt_quotes.connect(self._on_global_rt_quotes)

        # 窗口图标
        from PyQt6.QtGui import QIcon
        icon_path = _os.path.join(_os.path.dirname(_os.path.dirname(__file__)), 'bull_icon.ico')
        if _os.path.exists(icon_path):
            self.setWindowIcon(QIcon(icon_path))

        # 外层圆角防锯齿容器
        from PyQt6.QtWidgets import QFrame, QToolButton
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.container = QFrame()
        self.container.setObjectName("klineContainer")
        container_layout = QVBoxLayout(self.container)
        container_layout.setContentsMargins(1, 1, 1, 1)
        container_layout.setSpacing(0)

        # 自定义拖拽标题栏
        from ui.components.shared_title_bar import DraggableTitleBar
        self.title_bar = DraggableTitleBar(self)
        self.title_bar.setFixedHeight(36)
        tb_layout = QHBoxLayout(self.title_bar)
        tb_layout.setContentsMargins(14, 0, 8, 0)

        self.title_lbl = QLabel("K线图")
        tb_layout.addWidget(self.title_lbl)
        tb_layout.addStretch()

        self.btn_close = QToolButton()
        self.btn_close.setText("✕")
        self.btn_close.setFixedSize(32, 28)
        self.btn_close.setToolTip("关闭 K 线窗口")
        self.btn_close.setAccessibleName("关闭 K 线窗口")
        self.btn_close.clicked.connect(self.close)
        tb_layout.addWidget(self.btn_close)

        container_layout.addWidget(self.title_bar)

        # === 顶部主信息区 ===
        self.header_widget = QWidget()
        self.header_widget.setFixedHeight(72)
        header_layout = QHBoxLayout(self.header_widget)
        header_layout.setContentsMargins(14, 10, 14, 10)
        header_layout.setSpacing(12)

        left_group = QVBoxLayout()
        left_group.setContentsMargins(0, 0, 0, 0)
        left_group.setSpacing(4)

        identity_layout = QHBoxLayout()
        identity_layout.setContentsMargins(0, 0, 0, 0)
        identity_layout.setSpacing(8)
        self.identity_lbl = QLabel()
        identity_layout.addWidget(self.identity_lbl)
        self.market_badge_lbl = QLabel()
        self.market_badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        identity_layout.addWidget(self.market_badge_lbl)
        self.session_badge_lbl = QLabel()
        self.session_badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        identity_layout.addWidget(self.session_badge_lbl)
        self.feed_badge_lbl = QLabel()
        self.feed_badge_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        identity_layout.addWidget(self.feed_badge_lbl)
        identity_layout.addStretch()
        left_group.addLayout(identity_layout)

        self.info_lbl = QLabel("正在准备图表...")
        self.info_lbl.setMinimumHeight(24)
        self.info_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
        left_group.addWidget(self.info_lbl)
        header_layout.addLayout(left_group, 1)

        right_group = QHBoxLayout()
        right_group.setContentsMargins(0, 0, 0, 0)
        right_group.setSpacing(8)

        self.btn_prev = QPushButton("上一只")
        self.btn_prev.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prev.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.btn_prev.setToolTip("查看当前列表中的上一只股票")
        self.btn_prev.setAccessibleName("上一只股票")
        self.btn_prev.setAccessibleDescription("切换到当前列表中的上一只股票")
        self.btn_prev.clicked.connect(lambda: self._nav_stock(-1))
        right_group.addWidget(self.btn_prev)

        self.nav_index_lbl = QLabel("-- / --")
        self.nav_index_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_group.addWidget(self.nav_index_lbl)

        self.btn_next = QPushButton("下一只")
        self.btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.btn_next.setToolTip("查看当前列表中的下一只股票")
        self.btn_next.setAccessibleName("下一只股票")
        self.btn_next.setAccessibleDescription("切换到当前列表中的下一只股票")
        self.btn_next.clicked.connect(lambda: self._nav_stock(1))
        right_group.addWidget(self.btn_next)

        self.btn_fav = QPushButton("加入关注")
        self.btn_fav.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fav.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.btn_fav.setToolTip("将当前股票加入或移出关注池")
        self.btn_fav.setAccessibleName("切换关注状态")
        self.btn_fav.setAccessibleDescription("将当前股票加入或移出关注池")
        self.btn_fav.clicked.connect(self._toggle_fav)
        right_group.addWidget(self.btn_fav)

        header_layout.addLayout(right_group)

        container_layout.addWidget(self.header_widget)

        # === VCP 摘要带 ===
        self.summary_widget = QWidget()
        self.summary_widget.setFixedHeight(86)
        summary_layout = QHBoxLayout(self.summary_widget)
        summary_layout.setContentsMargins(12, 6, 12, 8)
        summary_layout.setSpacing(8)

        self.summary_cards = []
        self._summary_key_color = ""
        self._summary_value_color = ""
        self._summary_highlight_color = ""
        for _ in range(3):
            card = QFrame()
            card.setObjectName("klineSummaryCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(10, 8, 10, 8)
            card_layout.setSpacing(4)

            title_lbl = QLabel("--")
            title_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            card_layout.addWidget(title_lbl)

            value_labels = []
            for _row_idx in range(2):
                label = QLabel("--")
                label.setMinimumHeight(20)
                label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                label.setTextFormat(Qt.TextFormat.RichText)
                value_labels.append(label)
                card_layout.addWidget(label)

            summary_layout.addWidget(card, 1)
            self.summary_cards.append({
                "frame": card,
                "title": title_lbl,
                "labels": value_labels,
            })

        container_layout.addWidget(self.summary_widget)

        # === ECharts WebEngine 主图区域 ===
        self.browser = QWebEngineView()
        container_layout.addWidget(self.browser)

        main_layout.addWidget(self.container)

        # 快捷键 ←/→ 切换上/下一只股票
        from PyQt6.QtGui import QKeySequence, QShortcut
        QShortcut(QKeySequence(Qt.Key.Key_Left), self, activated=lambda: self._nav_stock(-1))
        QShortcut(QKeySequence(Qt.Key.Key_Right), self, activated=lambda: self._nav_stock(1))
        self._update_nav_buttons()

        # 初始化主题样式（必须在所有控件创建完成后调用）
        self._apply_qt_theme()

        self._check_fav_status()
        self._refresh_header_context()
        QTimer.singleShot(0, self._refresh_header_context)
        self._load_and_draw()

        # 监听全局主题切换 → 重新渲染 K 线图
        theme_manager.sig_theme_changed.connect(self._on_theme_changed)

    # ======================== 关注池 ========================
    def _check_fav_status(self):
        try:
            self.is_fav = watchlist_vm.is_in_watchlist(self.code)
            self.btn_fav.setText("已关注" if self.is_fav else "加入关注")
            self.btn_fav.setProperty("watching", bool(self.is_fav))
            self.btn_fav.style().unpolish(self.btn_fav)
            self.btn_fav.style().polish(self.btn_fav)
            self.btn_fav.update()
            if hasattr(self, "summary_cards"):
                self._refresh_header_context()
        except (AttributeError, RuntimeError, TypeError, ValueError) as e:
            log.debug(f"[K线] 检查关注状态失败: {e}")
            self.is_fav = False
            self.btn_fav.setProperty("watching", False)

    def _toggle_fav(self):
        try:
            watchlist_vm.toggle_stock(self.code, self.name, self.vcp_data)
            self._check_fav_status()
        except (AttributeError, RuntimeError, TypeError, ValueError) as e:
            log.debug(f"[K线] 切换关注状态失败: {e}")

    def _set_status_message(self, text: str, tone: str = "info"):
        self._info_tone = tone
        if hasattr(self, "info_lbl"):
            self.info_lbl.setText(str(text or "").strip())
        if hasattr(self, "info_lbl"):
            self._apply_info_styles()
        if hasattr(self, "feed_badge_lbl"):
            self._apply_header_badges()

    def _set_header_badge(self, label: QLabel, text: str, tone_name: str):
        set_header_badge(self, label, text, tone_name)

    def _apply_header_badges(self):
        apply_header_badges(self)

    def _refresh_header_context(self):
        refresh_header_context(self)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if hasattr(self, "summary_cards"):
            self._refresh_header_context()

    def _resolve_vcp_context(self, code: str, name: str, item_data: dict = None) -> dict:
        return resolve_vcp_context(self, code, name, item_data)

    # ======================== 主题切换 ========================
    def _on_theme_changed(self, _theme_name: str):
        """主题切换时重新渲染整个 K 线图——换衣服，不是染色"""
        self._apply_qt_theme()
        if self.df is not None and len(self.df) > 0:
            self._render_chart(self.df, loading=False)

    def _apply_qt_theme(self):
        apply_qt_theme(self)

    def _apply_info_styles(self, widget_text: str | None = None, info_color: str | None = None, is_dark: bool | None = None):
        apply_info_styles(
            self,
            widget_text=widget_text,
            info_color=info_color,
            is_dark=is_dark,
        )

    def _get_market(self) -> str:
        return MarketCalendar.infer_market(self.code)

    def _get_cn_target_trade_date(self):
        return get_cn_target_trade_date()

    def _build_asian_rt_quote(self):
        from ui.tabs.asian_market_tab import GLOBAL_ASIAN_RT_CACHE

        market = self._get_market()
        latest_trade_date = MarketCalendar.get_latest_trade_date(market)
        quote = GLOBAL_ASIAN_RT_CACHE.get(self.code) or {}
        return build_asian_rt_quote(
            self.code,
            quote,
            market=market,
            latest_trade_date=latest_trade_date,
        )

    def _normalize_daily_df_index(self, df):
        return normalize_daily_df_index(df, logger=self._log)

    # ======================== 数据加载 ========================
    def _load_and_draw(self):
        load_and_draw(self)

    def _load_asian_chart(self):
        """加载亚洲市场（yfinance 缓存）的 K 线数据"""
        from ui.tabs.asian_market_tab import GLOBAL_ASIAN_RT_CACHE, JSON_CACHE
        from ui.tabs.asian_market_workers import fetch_asian_realtime_quote, is_cf_proxy_enabled

        df = None
        target_stock = load_cached_asian_stock(JSON_CACHE, self.code)
        if target_stock:
            df = build_asian_history_df(
                target_stock,
                vcp_data=self.vcp_data,
                refresh_header_context=self._refresh_header_context,
                normalize_daily_df_index=self._normalize_daily_df_index,
            )

        if df is None:
            from app.services.ui_runtime_service import background_job_runner as task_manager
            schedule_asian_history_backfill(
                self,
                task_manager=task_manager,
                fetch_single_kline=fetch_single_kline,
            )
            return

        if df is not None:
            market = self._get_market()
            quote = GLOBAL_ASIAN_RT_CACHE.get(self.code)
            latest_trade_date = MarketCalendar.get_latest_trade_date(market)
            if quote is None and latest_trade_date is not None and not df.empty:
                try:
                    last_date = pd.Timestamp(df.index[-1]).date()
                except (IndexError, TypeError, ValueError):
                    last_date = None
                if last_date is None or last_date < latest_trade_date:
                    try:
                        quote = fetch_asian_realtime_quote(self.code, use_cf_proxy=is_cf_proxy_enabled())
                    except (AttributeError, ImportError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                        self._log.warning(f"[K绾縘 {self.code} 鐩樺悗琛ヨ冻浜氭床鎶ヤ环澶辫触: {exc}")
                    if quote:
                        GLOBAL_ASIAN_RT_CACHE[self.code] = quote

            if quote is not None:
                df = apply_asian_live_quote(
                    df,
                    quote,
                    market=market,
                )

            # 统一交由 _render_chart() 去计算指标并生成完整 ECharts，此时必然包含最新日期
            self._render_chart(df, loading=False)
        else:
            self._set_status_message("当前标的暂无历史日线数据", tone="warning")

    # ======================== 图表渲染 ========================
    def _render_chart(self, df, loading=False):
        """将 DataFrame 转换成 ECharts 数据格式并渲染到 WebEngine"""
        # 兼容 Polars DataFrame
        if not hasattr(df, 'iloc'):
            df = df.to_pandas()

        # 确保有 DatetimeIndex
        if 'date' in df.columns and not isinstance(df.index, pd.DatetimeIndex):
            df['date'] = pd.to_datetime(df['date'].astype(str))
            df.set_index('date', inplace=True)
        df = self._normalize_daily_df_index(df)

        if df is None or len(df) < 5:
            if not loading:
                self._set_status_message("历史数据不足，暂无法绘图", tone="warning")
            return

        # 始终要求重算完整指标，避免合并了今天盘中的新K线后由于缺少最新日期的MACD导致JS渲染因含有NaN而雪崩不画K线
        df = calculate_scan_indicators(df)

        if loading:
            self._set_status_message(f"已载入本地缓存 · {len(df)} 条日线", tone="info")
        else:
            self._set_status_message(f"图表已更新 · {len(df)} 条日线", tone="success")

        # 截取最后 250 根 K 线
        self.df = df.iloc[-250:].copy()
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in self.df.columns:
                self.df[col] = self.df[col].ffill().bfill()

        # 构建 ECharts 数据
        echarts_data = build_kline_echarts_payload(
            self.df,
            code=self.code,
            name=self.name,
            vcp_data=self.vcp_data,
        )

        # 判断是首次加载还是切换股票
        if not loading and not hasattr(self, '_first_render_done'):
            # 首次完整渲染（替换 loading 占位）
            pass

        # 渲染 HTML 到 WebEngine
        html_content = build_kline_html(
            title=f"{self.name} ({self.code}) 日线",
            echarts_data=echarts_data,
            echarts_js_path=_ECHARTS_JS_PATH,
            theme_colors=build_kline_theme_colors()
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

    # ======================== 盘中增量更新 ========================
    def _start_rt_timer(self):
        """启动盘中实时刷新定时器（60秒间隔），只在交易时段运行"""
        market = self._get_market()
        if not MarketCalendar.is_quote_refresh_time(market):
            if self._rt_timer is not None:
                self._rt_timer.stop()
            return
        if market == "CN":
            if self._rt_timer is not None:
                self._rt_timer.stop()
            return

        if self._rt_timer is None:
            self._rt_timer = QTimer(self)
            self._rt_timer.timeout.connect(self._on_rt_timer)
        self._rt_timer.start(60 * 1000)
        log.debug(f"[K线] {self.code} 实时刷新已启动 (60s)")

    def _on_global_rt_quotes(self, quotes: dict):
        if self._get_market() != "CN":
            return
        if not quotes or self.code not in quotes:
            return
        try:
            self._refresh_last_bar(quotes[self.code])
        except (AttributeError, RuntimeError, TypeError, ValueError) as e:
            log.debug(f"[K线] 全局实时行情刷新失败: {e}")

    def _on_rt_timer(self):
        poll_rt_update(self)

    def _refresh_last_bar(self, quote):
        refresh_last_bar(self, quote)

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
            self.vcp_data = self._resolve_vcp_context(self.code, self.name, item_data)

            title = f"{self.name} ({self.code}) - K线图"
            self.setWindowTitle(title)
            self._refresh_header_context()

            # 同步选中主窗口表格行
            workspace = getattr(self.main_window, "_workspace", None) if self.main_window else None
            if workspace is not None:
                preferred_tab_index = item_data.get("__source_tab_index")
                if not isinstance(preferred_tab_index, int):
                    preferred_tab_index = None
                workspace.select_code_row(self.code, preferred_tab_index=preferred_tab_index)

            self._check_fav_status()
            self._load_and_draw()
        finally:
            self._switching = False
            self._update_nav_buttons()

    # ======================== 资源释放 ========================
    def closeEvent(self, event):
        """窗口关闭时彻底释放 WebEngine 资源，防止内存泄漏"""
        # 断开主题切换信号，防止信号调用已销毁的窗口
        try:
            theme_manager.sig_theme_changed.disconnect(self._on_theme_changed)
        except TypeError:
            pass
        try:
            event_bus.sig_rt_quotes.disconnect(self._on_global_rt_quotes)
        except TypeError:
            pass

        # 停止定时器
        if self._rt_timer is not None:
            self._rt_timer.stop()
            self._rt_timer = None

        self.df = None

        # 释放 WebEngine（先导航到空白页释放 Chromium 渲染进程）
        try:
            self.browser.setUrl(QUrl("about:blank"))
            self.browser.deleteLater()
        except (AttributeError, RuntimeError, TypeError) as _e:
            log.debug(f"[K线] WebEngine 释放异常: {_e}")

        log.debug(f"[K线] {self.code} 窗口关闭")
        super().closeEvent(event)

