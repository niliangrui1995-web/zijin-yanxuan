# -*- coding: utf-8 -*-
"""
ui/components/kline_window_manager.py
K 线窗口管理器 — 单例模式 (#1)

为什么要单独管理？
原先 MainWindow 里有两处几乎相同的 K 线窗口创建与清理逻辑
(_on_table_double_click 和 _on_show_kline_with_list)。
窗口数量限制、RuntimeError 防御、静默关闭无反馈等问题散落在两处代码中。
现在统一收口到这里，任何人想开 K 线图只需调用 open_chart()。
"""

from core.logger import get_logger

log = get_logger(__name__)

# 可配置的最大窗口数量
MAX_CHART_WINDOWS = 5


class KLineWindowManager:
    """K 线图窗口池管理器 — 全局单例"""

    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._charts = []
        return cls._instance

    def open_chart(
        self,
        main_window,
        code: str,
        name: str,
        data_provider,
        vcp_data: dict = None,
        code_list: list = None,
        current_idx: int = 0,
    ):
        """打开一个 K 线图窗口，自动管理窗口池数量

        参数:
            main_window: 主窗口引用(KLineChartWindow 需要)
            code: 股票代码
            name: 股票名称
            data_provider: 数据提供器
            vcp_data: VCP 分析数据(可选)
            code_list: 上下文列表(支持翻页)
            current_idx: 当前索引
        """
        from ui.kline_window_qt import KLineChartWindow

        # 清理已关闭/已销毁的窗口
        alive = []
        for chart in self._charts:
            try:
                if chart.isVisible():
                    alive.append(chart)
            except RuntimeError:
                # C++ 对象已被底层销毁，忽略即可
                pass
        self._charts = alive

        # 窗口数量到达上限时，关闭最旧的并给出 toast 提示
        while len(self._charts) >= MAX_CHART_WINDOWS:
            oldest = self._charts.pop(0)
            try:
                # 提取旧窗口的标题用于 toast 提示
                old_title = oldest.windowTitle() or "未知"
                oldest.close()
                # 通知用户（需要在 UI 线程中调用）
                try:
                    from ui.components.toast_widget import show_toast
                    show_toast(
                        f"K线窗口上限{MAX_CHART_WINDOWS}个，已自动关闭: {old_title}",
                        "info",
                        main_window,
                        duration=2000,
                    )
                except Exception as _e:
                    log.debug(f"[K线管理] toast 提示发送失败: {_e}")
            except RuntimeError:
                pass

        # 构建 vcp_data 兜底
        if vcp_data is None:
            vcp_data = {'code': code, 'name': name}

        chart = KLineChartWindow(
            main_window=main_window,
            code=code,
            name=name,
            data_provider=data_provider,
            vcp_data=vcp_data,
            code_list=code_list or [],
            current_idx=current_idx,
        )
        chart.show()
        self._charts.append(chart)
        return chart

    @property
    def active_count(self) -> int:
        """当前活跃的 K 线窗口数量"""
        self._charts = [c for c in self._charts if _is_alive(c)]
        return len(self._charts)


def _is_alive(chart) -> bool:
    """检查窗口是否存活"""
    try:
        return chart.isVisible()
    except RuntimeError:
        return False


# 全局单例
kline_manager = KLineWindowManager()
