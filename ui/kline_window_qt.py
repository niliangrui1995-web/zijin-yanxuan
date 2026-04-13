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
import html
import json
import os as _os
from core.logger import get_logger
from core.market_calendar import MarketCalendar

log = get_logger(__name__)
import pandas as pd

from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QLabel, QHBoxLayout, QPushButton
)
from PyQt6.QtCore import Qt, QTimer, QUrl
from PyQt6.QtWebEngineWidgets import QWebEngineView

from ui.viewmodels.watchlist_vm import watchlist_vm
from ui.theme import theme_manager
from ui.kline_chart_payload import (
    build_kline_echarts_payload,
    build_kline_html,
    build_kline_summary_items,
    build_kline_theme_colors,
    build_kline_window_palette,
    format_kline_market_badge,
    resolve_kline_vcp_context,
)
from ui.theme_tokens import build_ui_tokens, get_state_tone

# ECharts JS 本地路径（断网也能用）
_ECHARTS_JS_PATH = _os.path.join(
    _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))),
    "assets", "echarts.min.js"
)


class KLineChartWindow(QWidget):
    """ECharts 驱动的 K 线图窗口"""

    def __init__(self, main_window, code, name, data_provider, vcp_data=None, code_list=None, current_idx=0):
        super().__init__()
        self.main_window = main_window
        self.code = code
        self.name = name
        self.data_provider = data_provider
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
        from ui.main_window_qt import DraggableTitleBar
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
        self.btn_prev.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_prev.clicked.connect(lambda: self._nav_stock(-1))
        right_group.addWidget(self.btn_prev)

        self.nav_index_lbl = QLabel("-- / --")
        self.nav_index_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        right_group.addWidget(self.nav_index_lbl)

        self.btn_next = QPushButton("下一只")
        self.btn_next.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_next.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_next.clicked.connect(lambda: self._nav_stock(1))
        right_group.addWidget(self.btn_next)

        self.btn_fav = QPushButton("加入关注")
        self.btn_fav.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fav.setFocusPolicy(Qt.FocusPolicy.NoFocus)
        self.btn_fav.clicked.connect(self._toggle_fav)
        right_group.addWidget(self.btn_fav)

        header_layout.addLayout(right_group)

        container_layout.addWidget(self.header_widget)

        # === VCP 摘要带 ===
        self.summary_widget = QWidget()
        self.summary_widget.setFixedHeight(92)
        summary_layout = QHBoxLayout(self.summary_widget)
        summary_layout.setContentsMargins(14, 8, 14, 10)
        summary_layout.setSpacing(10)

        self.summary_cards = []
        self.summary_labels = {}
        self._summary_key_color = ""
        self._summary_value_color = ""
        self._summary_highlight_color = ""
        summary_groups = (
            ("形态概览", ("形态", "触发")),
            ("区间结构", ("区间", "振幅")),
            ("强度跟踪", ("RPS", "关注")),
        )
        for title, keys in summary_groups:
            card = QFrame()
            card.setObjectName("klineSummaryCard")
            card_layout = QVBoxLayout(card)
            card_layout.setContentsMargins(12, 10, 12, 10)
            card_layout.setSpacing(6)

            title_lbl = QLabel(title)
            title_lbl.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
            card_layout.addWidget(title_lbl)

            value_labels = []
            for key in keys:
                label = QLabel("--")
                label.setMinimumHeight(22)
                label.setAlignment(Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft)
                label.setTextFormat(Qt.TextFormat.RichText)
                self.summary_labels[key] = label
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
            if hasattr(self, "summary_labels"):
                self._refresh_header_context()
        except Exception as e:
            log.debug(f"[K线] 检查关注状态失败: {e}")
            self.is_fav = False
            self.btn_fav.setProperty("watching", False)

    def _toggle_fav(self):
        try:
            watchlist_vm.toggle_stock(self.code, self.name, self.vcp_data)
            self._check_fav_status()
        except Exception as e:
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
        tokens = build_ui_tokens(theme_manager.current_theme)
        tone = get_state_tone(tone_name, theme_manager.current_theme)
        label.setText(text)
        label.setStyleSheet(
            f"background-color: {tone['bg']}; color: {tone['fg']}; border: 1px solid {tone['border']};"
            f" border-radius: {tokens['radius']['pill']}px; padding: 1px 9px;"
            f" min-height: {tokens['shell']['status_pill_min_height']}px;"
            f" font-size: {tokens['font']['size_xs']}px; font-weight: {tokens['font']['weight_semibold']};"
        )

    def _apply_header_badges(self):
        market = self._get_market()
        is_offline = bool(getattr(self.data_provider, "_offline", False))
        info_tone = getattr(self, "_info_tone", "info")

        if is_offline and market == "CN":
            feed_text, feed_tone = "本地缓存", "stale"
            session_text, session_tone = "离线", "stale"
        else:
            if info_tone == "realtime":
                feed_text, feed_tone = "实时链路", "realtime"
            elif info_tone == "loading":
                feed_text, feed_tone = "同步中", "focus"
            elif info_tone == "success":
                feed_text, feed_tone = "已同步", "success"
            else:
                feed_text, feed_tone = "日线工作区", "info"

            if MarketCalendar.is_market_active(market):
                session_text, session_tone = "盘中", "realtime"
            else:
                session_text, session_tone = "收盘", "neutral"

        self._set_header_badge(self.session_badge_lbl, session_text, session_tone)
        self._set_header_badge(self.feed_badge_lbl, feed_text, feed_tone)

    def _refresh_header_context(self):
        market_badge = format_kline_market_badge(self.code)
        if hasattr(self, "identity_lbl"):
            self.identity_lbl.setText(f"{self.name}  {self.code}")
        if hasattr(self, "market_badge_lbl"):
            self.market_badge_lbl.setText(market_badge)
        if hasattr(self, "nav_index_lbl"):
            total = len(self.code_list)
            self.nav_index_lbl.setText(
                f"{self.current_idx + 1} / {total}" if total else "单票"
            )
        if hasattr(self, "feed_badge_lbl"):
            self._apply_header_badges()
        if hasattr(self, "summary_labels"):
            summary = build_kline_summary_items(self.vcp_data, getattr(self, "is_fav", False))
            for key, label in self.summary_labels.items():
                value = html.escape(str(summary.get(key, "--")))
                value_color = self._summary_highlight_color if key == "关注" and value == "已关注" else self._summary_value_color
                label.setText(
                    f"<span style='color:{self._summary_key_color};'>{key}</span>"
                    f"&nbsp;&nbsp;<span style='color:{value_color}; font-weight:600;'>{value}</span>"
                )

    def _resolve_vcp_context(self, code: str, name: str, item_data: dict = None) -> dict:
        try:
            watchlist_entry = watchlist_vm.get_watchlist_data().get(code, {})
        except Exception as e:
            log.debug(f"[K线] 读取关注池上下文失败: {e}")
            watchlist_entry = {}

        scan_results = []
        try:
            tab_scan = getattr(self.main_window, "tab_scan", None)
            scan_results = getattr(tab_scan, "_current_results", []) or []
        except Exception as e:
            log.debug(f"[K线] 读取扫描上下文失败: {e}")

        return resolve_kline_vcp_context(
            code=code,
            name=name,
            item_data=item_data,
            watchlist_entry=watchlist_entry,
            scan_results=scan_results,
        )

    # ======================== 主题切换 ========================
    def _on_theme_changed(self, _theme_name: str):
        """主题切换时重新渲染整个 K 线图——换衣服，不是染色"""
        self._apply_qt_theme()
        if self.df is not None and len(self.df) > 0:
            self._render_chart(self.df, loading=False)

    def _apply_qt_theme(self):
        """根据当前主题刷新 PyQt 原生层样式（窗口容器、按钮、信息栏）。
        为什么暗色主题硬编码不读 token？K 线窗口在墨渊下使用独有配色
        （#0B0B0E 而非全局 #0F1117），直接写死保证零变化。
        """
        t = theme_manager.current_theme
        tokens = build_ui_tokens(t)
        is_dark = tokens["is_dark"]
        palette = build_kline_window_palette(t, is_dark)
        widget_bg = palette["widget_bg"]
        widget_text = palette["widget_text"]
        toolbar_bg = palette["toolbar_bg"]
        toolbar_border = palette["toolbar_border"]
        summary_bg = palette["summary_bg"]
        info_color = palette["info_color"]
        btn_border = palette["btn_border"]
        btn_hover_bg = palette["btn_hover_bg"]
        btn_hover_text = palette["btn_hover_text"]
        btn_disabled_text = palette["btn_disabled_text"]
        btn_disabled_border = palette["btn_disabled_border"]
        chart_bg = palette["chart_bg"]
        nav_bg = palette["nav_bg"]
        badge_bg = palette["badge_bg"]
        badge_fg = palette["badge_fg"]
        summary_border = palette["summary_border"]
        radius = tokens["radius"]
        font = tokens["font"]
        control = tokens["control"]
        neutral_tone = tokens["state"]["neutral"]
        action_height = control["button_height"]

        self.setStyleSheet(f"""
            QWidget {{ background-color: {widget_bg}; color: {widget_text}; }}
            QLabel {{ font-family: {font['family']}; }}
        """)

        # 外层圆角防锯齿容器
        self.container.setStyleSheet(f"""
            QFrame#klineContainer {{
                background-color: {widget_bg};
                border: 1px solid {toolbar_border};
                border-radius: {radius['md']}px;
            }}
        """)

        # 自定义拖拽标题栏样式
        self.title_bar.setStyleSheet(f"""
            QWidget {{
                background-color: {toolbar_bg};
                border-top-left-radius: {radius['md']}px;
                border-top-right-radius: {radius['md']}px;
                border-bottom: none;
            }}
        """)
        self.title_lbl.setStyleSheet(
            f"color: {widget_text}; font-weight: {font['weight_bold']}; font-size: {font['size_md']}px;"
        )

        # 关闭按钮鼠标悬浮效果
        self.btn_close.setStyleSheet(f"""
            QToolButton {{
                background: transparent;
                border: none;
                color: {info_color};
            }}
            QToolButton:hover {{
                background-color: #E81123;
                color: white;
                border-radius: {radius['xs']}px;
            }}
        """)

        self.header_widget.setStyleSheet(
            f"background-color: {toolbar_bg}; border-bottom: 1px solid {toolbar_border};"
        )
        self.identity_lbl.setStyleSheet(
            f"color: {widget_text}; font-weight: {font['weight_bold']}; font-size: {font['size_lg']}px;"
        )
        self.market_badge_lbl.setStyleSheet(
            f"background-color: {badge_bg}; color: {badge_fg}; border: 1px solid {badge_bg};"
            f"border-radius: {radius['pill']}px; padding: 1px 9px;"
            f" font-size: {font['size_xs']}px; font-weight: {font['weight_semibold']};"
        )
        self.btn_prev.setFixedHeight(action_height)
        self.btn_next.setFixedHeight(action_height)
        self.btn_fav.setFixedHeight(action_height)
        self.nav_index_lbl.setFixedHeight(action_height)
        self.nav_index_lbl.setMinimumWidth(72)
        self.nav_index_lbl.setStyleSheet(
            f"background-color: {neutral_tone['bg']}; color: {info_color}; border: 1px solid {btn_border};"
            f"border-radius: {radius['pill']}px; padding: 0 10px; font-size: {font['size_xs']}px;"
            f" font-weight: {font['weight_semibold']}; font-family: {font['mono_family']};"
        )

        nav_style = f"""
            QPushButton {{
                background-color: {neutral_tone['bg']};
                color: {info_color};
                border: 1px solid {btn_border};
                border-radius: {radius['md']}px;
                padding: 0 12px;
                font-weight: {font['weight_semibold']};
                font-size: {font['size_sm']}px;
            }}
            QPushButton:hover {{ background-color: {btn_hover_bg}; color: {btn_hover_text}; }}
            QPushButton:disabled {{ color: {btn_disabled_text}; border-color: {btn_disabled_border}; }}
        """
        self.btn_prev.setStyleSheet(nav_style)
        self.btn_next.setStyleSheet(nav_style)

        vcp_star = t.get('KLINE_VCP_STAR', '#FFD60A')
        fav_hover = 'rgba(255, 214, 10, 0.1)' if is_dark else 'rgba(217, 119, 6, 0.1)'
        fav_active_bg = '#FACC15' if not is_dark else '#FFD60A'
        fav_active_text = '#2B1900' if not is_dark else '#201300'
        fav_active_hover = '#FDE047' if not is_dark else '#FFE083'
        self.btn_fav.setProperty("watching", bool(getattr(self, "is_fav", False)))
        self.btn_fav.setStyleSheet(f"""
            QPushButton[watching="false"] {{
                background-color: {neutral_tone['bg']};
                color: {vcp_star};
                border: 1px solid {vcp_star};
                border-radius: {radius['md']}px;
                padding: 0 12px;
                font-weight: {font['weight_semibold']};
                font-size: {font['size_sm']}px;
            }}
            QPushButton[watching="false"]:hover {{ background-color: {fav_hover}; }}
            QPushButton[watching="true"] {{
                background-color: {fav_active_bg};
                color: {fav_active_text};
                border: 1px solid {fav_active_bg};
                border-radius: {radius['md']}px;
                padding: 0 12px;
                font-weight: {font['weight_semibold']};
                font-size: {font['size_sm']}px;
            }}
            QPushButton[watching="true"]:hover {{
                background-color: {fav_active_hover};
                border: 1px solid {fav_active_hover};
            }}
        """)
        self._apply_header_badges()

        self.summary_widget.setStyleSheet(
            f"background-color: {summary_bg}; border-bottom: 1px solid {summary_border};"
        )
        self._summary_key_color = t["TEXT_MUTED"]
        self._summary_value_color = widget_text
        self._summary_highlight_color = vcp_star
        for card in self.summary_cards:
            card["frame"].setStyleSheet(
                f"""
                QFrame#klineSummaryCard {{
                    background-color: {nav_bg};
                    border: 1px solid {summary_border};
                    border-radius: {radius['lg']}px;
                }}
                """
            )
            card["title"].setStyleSheet(
                f"color: {t['TEXT_MUTED']}; font-size: {font['size_xs']}px;"
                f" font-weight: {font['weight_medium']}; letter-spacing: 0.2px;"
            )
            for label in card["labels"]:
                label.setStyleSheet(
                    f"font-size: {font['size_sm']}px; font-weight: {font['weight_medium']};"
                )

        self._apply_info_styles(widget_text=widget_text, info_color=info_color, is_dark=is_dark)
        self.browser.setStyleSheet(f"background-color: {chart_bg};")

    def _apply_info_styles(self, widget_text: str | None = None, info_color: str | None = None, is_dark: bool | None = None):
        t = theme_manager.current_theme
        if widget_text is None or info_color is None or is_dark is None:
            widget_text = "#F5F7FA" if theme_manager.is_dark() else t["TEXT_PRIMARY"]
            info_color = "#8B98A8" if theme_manager.is_dark() else t["TEXT_MUTED"]
            is_dark = theme_manager.is_dark()

        tone = getattr(self, "_info_tone", "info")
        tokens = build_ui_tokens(t)
        state_tone = get_state_tone("info" if tone == "realtime" else tone, t)
        border_color = state_tone["border"] if tone != "info" else ("rgba(148, 163, 184, 0.10)" if is_dark else t["BORDER_SUBTLE"])
        fg_color = widget_text if tone == "info" else state_tone["fg"]
        bg_color = t["BRAND_SUBTLE"] if tone == "info" else state_tone["bg"]
        self.info_lbl.setStyleSheet(
            f"background-color: {bg_color}; color: {fg_color}; border: 1px solid {border_color};"
            f"border-radius: {tokens['radius']['pill']}px; padding: 5px 10px;"
            f" font-size: {tokens['font']['size_sm']}px; font-weight: {tokens['font']['weight_semibold']};"
        )

    def _get_market(self) -> str:
        return MarketCalendar.infer_market(self.code)

    def _get_cn_target_trade_date(self):
        """CN 专用：盘前按上一交易日，盘中/盘后按当日交易日。"""
        from datetime import timedelta

        now_cn = MarketCalendar._get_market_now("CN")
        today = now_cn.date()
        latest = MarketCalendar.get_latest_trade_date("CN", ref_date=today)
        if latest is None:
            return None

        if (not MarketCalendar.is_trade_day(today, market="CN")):
            return latest

        hhmm = now_cn.hour * 100 + now_cn.minute
        if hhmm < 915:
            return MarketCalendar.get_latest_trade_date("CN", ref_date=today - timedelta(days=1))

        return latest

    def _build_asian_rt_quote(self):
        from ui.tabs.asian_market_tab import GLOBAL_ASIAN_RT_CACHE

        market = self._get_market()
        latest_trade_date = MarketCalendar.get_latest_trade_date(market)
        quote = GLOBAL_ASIAN_RT_CACHE.get(self.code) or {}
        if latest_trade_date is None or not quote:
            return None

        df_today = quote.get('df_today')
        if df_today is not None and not df_today.empty:
            try:
                last_row = df_today.iloc[-1]
                last_dt = pd.Timestamp(last_row.name)
                if last_dt.tzinfo is not None:
                    last_dt = last_dt.tz_localize(None)
                if last_dt.date() == latest_trade_date:
                    return {
                        'date': latest_trade_date.strftime('%Y-%m-%d'),
                        'open': float(last_row.get('Open', 0) or 0),
                        'high': float(last_row.get('High', 0) or 0),
                        'low': float(last_row.get('Low', 0) or 0),
                        'close': float(last_row.get('Close', 0) or 0),
                        'volume': float(last_row.get('Volume', 0) or 0),
                    }
            except Exception as _e:
                log.debug(f"[K线] 组装亚洲实时 df_today 失败: {_e}")

        rt_close = float(quote.get('close', 0) or 0)
        rt_open = float(quote.get('open', rt_close) or 0)
        if rt_close <= 0 or rt_open <= 0:
            return None
        quote_trade_date = None
        raw_quote_date = quote.get('date')
        if raw_quote_date:
            try:
                quote_trade_date = pd.Timestamp(raw_quote_date).date()
            except Exception:
                quote_trade_date = None
        if quote_trade_date is None:
            return None
        if latest_trade_date is not None and quote_trade_date > latest_trade_date:
            quote_trade_date = latest_trade_date
        if not MarketCalendar.is_trade_day(quote_trade_date, market=market):
            return None

        rt_high = float(quote.get('high', max(rt_open, rt_close)) or 0)
        rt_low = float(quote.get('low', min(rt_open, rt_close)) or 0)
        if rt_high <= 0:
            rt_high = max(rt_open, rt_close)
        if rt_low <= 0:
            rt_low = min(rt_open, rt_close)

        return {
            'date': quote_trade_date.strftime('%Y-%m-%d'),
            'open': rt_open,
            'high': rt_high,
            'low': rt_low,
            'close': rt_close,
            'volume': float(quote.get('volume', 0) or 0),
        }

    def _normalize_daily_df_index(self, df):
        """统一到按交易日去重的 DatetimeIndex，避免同一天重复K线。"""
        if df is None or len(df) == 0:
            return df
        try:
            df = df.copy()
            idx = pd.to_datetime(df.index, errors='coerce')
            valid = ~idx.isna()
            if not valid.all():
                df = df.loc[valid].copy()
                idx = idx[valid]
            if len(idx) == 0:
                return df
            if isinstance(idx, pd.DatetimeIndex) and idx.tz is not None:
                idx = idx.tz_localize(None)
            df.index = idx.normalize()
            df = df[~df.index.duplicated(keep='last')].sort_index()
            return df
        except Exception as _e:
            log.debug(f"[K线] 日线索引归一化失败: {_e}")
            return df

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
            self._set_status_message("正在同步完整日线数据...", tone="loading")

        # 2. 异步拉取最新日线 + 盘中实时
        def _bg_fetch():
            quote_to_apply = None
            target_trade_date = self._get_cn_target_trade_date()

            local_df = self._normalize_daily_df_index(self.data_provider.get_data(self.code))
            last_local_date = None
            if local_df is not None and not local_df.empty:
                last_local_date = pd.Timestamp(local_df.index[-1]).date()

            need_sync = (
                target_trade_date is None
                or last_local_date is None
                or last_local_date < target_trade_date
            )

            if need_sync:
                # 兼容旧数据中台签名：有的版本不支持 force_sync 关键字参数
                try:
                    fresh_df = self.data_provider.get_data_fresh_for_chart(self.code, force_sync=True)
                except TypeError:
                    fresh_df = self.data_provider.get_data_fresh_for_chart(self.code)
                fresh_df = self._normalize_daily_df_index(fresh_df)
            else:
                fresh_df = local_df

            if (
                not getattr(self.data_provider, '_offline', False)
                and target_trade_date is not None
                and MarketCalendar.is_market_active("CN")
            ):
                last_dt = None
                if fresh_df is not None and not fresh_df.empty:
                    last_dt = pd.Timestamp(fresh_df.index[-1]).date()

                already_has_latest = last_dt is not None and last_dt >= target_trade_date
                if not already_has_latest:
                    try:
                        quotes = self.data_provider.fetch_realtime_quotes_batch([self.code])
                        if quotes and self.code in quotes:
                            quote_to_apply = quotes[self.code]
                    except Exception as e:
                        log.warning(f"[K线] {self.code} 实时行情合并失败: {e}")
            return fresh_df, quote_to_apply, target_trade_date

        def _on_fetch_success(result):
            try:
                if result:
                    fresh_df, quote_to_apply, target_trade_date = result
                    if fresh_df is None or len(fresh_df) == 0:
                        self._set_status_message("未获取到可用日线数据，请检查网络后重试", tone="error")
                        return

                    fresh_df = self._normalize_daily_df_index(fresh_df)

                    if quote_to_apply is not None:
                        rt_open = float(quote_to_apply.get('open', 0) or 0)
                        if rt_open > 0 and target_trade_date is not None:
                            rt_close = float(quote_to_apply.get('close', 0) or 0)
                            rt_high = float(quote_to_apply.get('high', 0) or 0)
                            rt_low = float(quote_to_apply.get('low', 0) or 0)
                            rt_vol = float(quote_to_apply.get('volume', 0) or 0)

                            last_date = pd.Timestamp(fresh_df.index[-1]).date()
                            quote_trade_date = None
                            raw_quote_date = quote_to_apply.get('date')
                            if raw_quote_date:
                                try:
                                    quote_trade_date = pd.Timestamp(raw_quote_date).date()
                                except Exception:
                                    quote_trade_date = None
                            if quote_trade_date is None:
                                # 通达信实时接口不带交易日：按交易日历+交易时段推断该归属哪一日
                                if (
                                    MarketCalendar.is_market_active("CN")
                                    and target_trade_date is not None
                                    and last_date < target_trade_date
                                ):
                                    quote_trade_date = target_trade_date
                                else:
                                    quote_trade_date = last_date
                            if quote_trade_date > target_trade_date:
                                quote_trade_date = target_trade_date

                            if quote_trade_date == last_date:
                                fresh_df.iloc[-1, fresh_df.columns.get_loc('open')] = rt_open
                                if rt_high > 0:
                                    fresh_df.iloc[-1, fresh_df.columns.get_loc('high')] = max(float(fresh_df.iloc[-1]['high']), rt_high)
                                if rt_low > 0:
                                    fresh_df.iloc[-1, fresh_df.columns.get_loc('low')] = min(float(fresh_df.iloc[-1]['low']), rt_low)
                                if rt_close > 0:
                                    fresh_df.iloc[-1, fresh_df.columns.get_loc('close')] = rt_close
                                if 'volume' in fresh_df.columns:
                                    fresh_df.iloc[-1, fresh_df.columns.get_loc('volume')] = rt_vol
                            elif (
                                quote_trade_date > last_date
                                and quote_trade_date <= target_trade_date
                                and MarketCalendar.is_market_active("CN")
                                and rt_close > 0
                            ):
                                prev_row = fresh_df.iloc[-1]
                                tol = 1e-8
                                same_as_prev = (
                                    abs(float(prev_row.get('open', 0)) - rt_open) <= tol
                                    and abs(float(prev_row.get('high', 0)) - rt_high) <= tol
                                    and abs(float(prev_row.get('low', 0)) - rt_low) <= tol
                                    and abs(float(prev_row.get('close', 0)) - rt_close) <= tol
                                )
                                if not same_as_prev:
                                    sim_high = rt_high if rt_high > 0 else max(rt_open, rt_close)
                                    sim_low = rt_low if rt_low > 0 else min(rt_open, rt_close)
                                    new_row = pd.DataFrame({
                                        'open': [rt_open],
                                        'high': [sim_high],
                                        'low': [sim_low],
                                        'close': [rt_close],
                                        'volume': [rt_vol],
                                    }, index=[pd.Timestamp(quote_trade_date)])
                                    fresh_df = fresh_df[fresh_df.index != pd.Timestamp(quote_trade_date)]
                                    fresh_df = pd.concat([fresh_df, new_row])
                                    fresh_df = self._normalize_daily_df_index(fresh_df)

                    self._render_chart(fresh_df, loading=False)
            except RuntimeError:
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
                            df['date'] = pd.to_datetime(df['date']).dt.normalize()
                            df.set_index('date', inplace=True)
                        for col in ['open', 'high', 'low', 'close', 'volume']:
                            if col in df.columns:
                                df[col] = pd.to_numeric(df[col], errors='coerce').astype(float)

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
                
                # --- 用 fast_info / df_today 实时 OHLC 强刷最后一根 K 线 ---
                market = self._get_market()
                latest_trade_date = MarketCalendar.get_latest_trade_date(market)
                last_date = pd.Timestamp(df.index[-1]).date()
                
                rt_close = quote.get('close')
                rt_open = quote.get('open')
                rt_high = quote.get('high')
                rt_low = quote.get('low')
                quote_trade_date = None
                raw_quote_date = quote.get('date')
                if raw_quote_date:
                    try:
                        quote_trade_date = pd.Timestamp(raw_quote_date).date()
                    except Exception:
                        quote_trade_date = None
                if quote_trade_date is None and df_today is not None and not df_today.empty:
                    try:
                        last_dt = pd.Timestamp(df_today.index[-1])
                        if getattr(last_dt, "tzinfo", None) is not None:
                            last_dt = last_dt.tz_localize(None)
                        quote_trade_date = last_dt.date()
                    except Exception:
                        quote_trade_date = None
                
                if rt_close is not None and latest_trade_date is not None and quote_trade_date is not None:
                    if quote_trade_date == last_date:
                        # 报价日期与最后一根一致，仅覆盖，避免将旧报价误追加为新交易日
                        if rt_open:
                            df.iloc[-1, df.columns.get_loc('open')] = float(rt_open)
                        if rt_high:
                            df.iloc[-1, df.columns.get_loc('high')] = max(float(df.iloc[-1]['high']), float(rt_high))
                        if rt_low:
                            df.iloc[-1, df.columns.get_loc('low')] = min(float(df.iloc[-1]['low']), float(rt_low))
                        df.iloc[-1, df.columns.get_loc('close')] = float(rt_close)
                    elif (
                        quote_trade_date > last_date
                        and quote_trade_date <= latest_trade_date
                        and MarketCalendar.is_market_active(market)
                    ):
                        # 只有报价自身日期前进到了新交易日，才允许补新 bar
                        rt_close_val = float(rt_close) if rt_close else 0.0
                        
                        sim_open = float(rt_open) if rt_open else rt_close_val
                        sim_high = float(rt_high) if rt_high else max(sim_open, rt_close_val)
                        sim_low = float(rt_low) if rt_low else min(sim_open, rt_close_val)
                        
                        if rt_close_val > 0:
                            new_row = pd.DataFrame({
                                'open': [sim_open], 
                                'high': [sim_high], 
                                'low': [sim_low],
                                'close': [rt_close_val], 
                                'volume': [0.0]
                            }, index=[pd.Timestamp(quote_trade_date)])
                            df = pd.concat([df, new_row])

            # 统一交由 _render_chart() 去计算指标并生成完整 ECharts，此时必然包含最新日期
            self._render_chart(df, loading=False)
        else:
            self._set_status_message("当前标的暂无历史日线数据", tone="warning")

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
        df = self._normalize_daily_df_index(df)

        if df is None or len(df) < 5:
            if not loading:
                self._set_status_message("历史数据不足，暂无法绘图", tone="warning")
            return

        # 始终要求重算完整指标，避免合并了今天盘中的新K线后由于缺少最新日期的MACD导致JS渲染因含有NaN而雪崩不画K线
        df = VCPEngine.calculate_indicators(df)

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
        if not MarketCalendar.is_market_active(market):
            if self._rt_timer is not None:
                self._rt_timer.stop()
            return
        if market == "CN" and getattr(self.data_provider, '_offline', False):
            return

        if self._rt_timer is None:
            self._rt_timer = QTimer(self)
            self._rt_timer.timeout.connect(self._on_rt_timer)
        self._rt_timer.start(60 * 1000)
        log.debug(f"[K线] {self.code} 实时刷新已启动 (60s)")

    def _on_rt_timer(self):
        """定时器回调：拉取最新实时报价，通过 JS 增量更新最后一根 K 线"""
        market = self._get_market()
        if not MarketCalendar.is_market_active(market):
            if self._rt_timer:
                self._rt_timer.stop()
                log.debug(f"[K线] {self.code} 已收盘，停止实时刷新")
            return

        try:
            if market != "CN":
                quote = self._build_asian_rt_quote()
                if quote is None:
                    import yfinance as yf
                    from ui.tabs.asian_market_tab import GLOBAL_USE_CF_PROXY
                    from vcp.fetchers.yf_session import build_yf_session

                    yf_session = build_yf_session(GLOBAL_USE_CF_PROXY)
                    rt_df = yf.Ticker(self.code, session=yf_session).history(period="5d", interval="1d")
                    if not rt_df.empty:
                        last_row = rt_df.iloc[-1]
                        rt_date = pd.Timestamp(last_row.name)
                        if rt_date.tzinfo is not None:
                            rt_date = rt_date.tz_localize(None)
                        quote = {
                            'date': rt_date.strftime('%Y-%m-%d'),
                            'open': float(last_row['Open']),
                            'high': float(last_row['High']),
                            'low': float(last_row['Low']),
                            'close': float(last_row['Close']),
                            'volume': float(last_row.get('Volume', 0))
                        }
                if quote is not None:
                    self._refresh_last_bar(quote)
            else:
                quotes = self.data_provider.fetch_realtime_quotes_batch([self.code])
                if quotes and self.code in quotes:
                    self._refresh_last_bar(quotes[self.code])
        except Exception as e:
            log.warning(f"[K线] {self.code} 实时刷新异常: {e}")

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

        market = self._get_market()
        latest_trade_date = MarketCalendar.get_latest_trade_date(market)
        rt_date_str = quote.get('date')
        last_date = pd.Timestamp(self.df.index[-1]).date()

        if rt_date_str:
            try:
                rt_date = pd.Timestamp(rt_date_str).date()
            except Exception:
                rt_date = None
        else:
            if (
                market == "CN"
                and latest_trade_date is not None
                and MarketCalendar.is_market_active(market)
                and last_date < latest_trade_date
            ):
                rt_date = latest_trade_date
            else:
                rt_date = last_date

        if latest_trade_date is None:
            rt_date = last_date
        elif rt_date is None:
            rt_date = last_date
        elif not MarketCalendar.is_trade_day(rt_date, market=market):
            rt_date = last_date
        elif rt_date > latest_trade_date:
            rt_date = latest_trade_date

        if rt_date is None:
            rt_date = last_date

        # 更新本地 df 缓存
        if last_date >= rt_date:
            # 覆盖更新最后一根
            self.df.iloc[-1, self.df.columns.get_loc('open')] = rt_open
            if rt_high > 0:
                self.df.iloc[-1, self.df.columns.get_loc('high')] = max(
                    self.df.iloc[-1, self.df.columns.get_loc('high')], rt_high
                )
            if rt_low > 0:
                self.df.iloc[-1, self.df.columns.get_loc('low')] = min(
                    self.df.iloc[-1, self.df.columns.get_loc('low')], rt_low
                )
            self.df.iloc[-1, self.df.columns.get_loc('close')] = rt_close
            if 'volume' in self.df.columns:
                self.df.iloc[-1, self.df.columns.get_loc('volume')] = rt_vol
        else:
            # 新增一根 K 线
            sim_high = rt_high if rt_high > 0 else max(rt_open, rt_close)
            sim_low = rt_low if rt_low > 0 else min(rt_open, rt_close)
            new_row = pd.DataFrame({
                'open': [rt_open], 'high': [sim_high], 'low': [sim_low],
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
        sign = '+' if rt_close >= pre_close else ''
        now_str = datetime.now().strftime('%H:%M:%S')
        self._set_status_message(
            f"实时更新 {now_str} · {rt_close:.2f} · {sign}{pct:.2f}% · 成交量 {rt_vol/10000:.0f}万",
            tone="realtime",
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
            self.vcp_data = self._resolve_vcp_context(self.code, self.name, item_data)

            title = f"{self.name} ({self.code}) - K线图"
            self.setWindowTitle(title)
            self._refresh_header_context()

            # 同步选中主窗口表格行
            if self.main_window and hasattr(self.main_window, 'table_scan'):
                self.main_window.table_scan.selectRow(new_idx)

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

        # 停止定时器
        if self._rt_timer is not None:
            self._rt_timer.stop()
            self._rt_timer = None

        self.df = None

        # 释放 WebEngine（先导航到空白页释放 Chromium 渲染进程）
        try:
            self.browser.setUrl(QUrl("about:blank"))
            self.browser.deleteLater()
        except Exception as _e:
            log.debug(f"[K线] WebEngine 释放异常: {_e}")

        log.debug(f"[K线] {self.code} 窗口关闭")
        super().closeEvent(event)
