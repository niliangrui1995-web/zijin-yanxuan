# -*- coding: utf-8 -*-
"""Window command assembly for command palette and stock quick-open flows."""

from __future__ import annotations


class WindowCommandService:
    def __init__(self, main_window):
        self._window = main_window

    def _invoke_host(self, method_name: str, *args, **kwargs):
        callback = getattr(self._window, method_name, None)
        if not callable(callback):
            return None
        return callback(*args, **kwargs)

    def _theme_names(self) -> list[str]:
        theme_names = getattr(self._window, "theme_names", None)
        if not callable(theme_names):
            return []
        return list(theme_names() or [])

    def build_commands(self) -> list[dict]:
        commands: list[dict] = [
            {
                "title": "全局同步",
                "subtitle": "执行盘后缓存与预计算同步",
                "shortcut": "F5",
                "keywords": ["同步", "f5", "刷新", "全局同步"],
                "handler": lambda: self._invoke_host("trigger_global_sync"),
            }
        ]

        workspace = getattr(self._window, "_workspace", None)
        tab_specs = workspace.tab_specs() if workspace is not None and hasattr(workspace, "tab_specs") else []
        for index, spec in enumerate(tab_specs):
            title = str(spec.get("title") or "").strip()
            group = str(spec.get("group") or "").strip()
            if not title:
                continue
            commands.append(
                {
                    "title": f"打开{title}",
                    "subtitle": f"{group} · 切换到{title}页面" if group else f"切换到{title}页面",
                    "keywords": [title, group, "页面", "导航"],
                    "handler": lambda i=index: self._invoke_host("activate_workspace_tab", i),
                }
            )

        if workspace is not None and hasattr(workspace, "toggle_rt_monitor"):
            running = bool(getattr(workspace, "is_rt_monitor_running", lambda: False)())
            commands.append(
                {
                    "title": "停止盘中监控" if running else "开始盘中监控",
                    "subtitle": "切换盘中监控运行状态",
                    "keywords": ["盘中监控", "开始", "停止", "监控"],
                    "handler": workspace.toggle_rt_monitor,
                }
            )

        if workspace is not None:
            if hasattr(workspace, "run_incremental_scan"):
                commands.append(
                    {
                        "title": "新增补扫",
                        "subtitle": "仅扫描最近可用交易日",
                        "keywords": ["扫描", "补扫", "新增补扫"],
                        "handler": workspace.run_incremental_scan,
                    }
                )
            if hasattr(workspace, "open_scan_settings"):
                commands.append(
                    {
                        "title": "扫描参数",
                        "subtitle": "打开 VCP 扫描参数面板",
                        "keywords": ["扫描", "参数"],
                        "handler": workspace.open_scan_settings,
                    }
                )
            if hasattr(workspace, "refresh_lhb_history"):
                commands.append(
                    {
                        "title": "历史回补龙虎榜",
                        "subtitle": "执行龙虎榜历史回补并刷新表格",
                        "keywords": ["龙虎榜", "刷新", "历史回补"],
                        "handler": workspace.refresh_lhb_history,
                    }
                )
            if hasattr(workspace, "run_fund_holdings_sync"):
                commands.append(
                    {
                        "title": "刷新基金持仓",
                        "subtitle": "拉取最新基金持仓并刷新表格",
                        "keywords": ["基金持仓", "刷新", "持仓同步"],
                        "handler": workspace.run_fund_holdings_sync,
                    }
                )

        for theme_name in self._theme_names():
            commands.append(
                {
                    "title": f"切换主题：{theme_name}",
                    "subtitle": "立即切换界面主题",
                    "keywords": ["主题", "切换主题", theme_name],
                    "handler": lambda n=theme_name: self._invoke_host("switch_theme", n),
                }
            )

        commands.extend(
            [
                {
                    "title": "表格密度：紧凑",
                    "subtitle": "切换为紧凑表格密度",
                    "keywords": ["表格密度", "紧凑", "密度"],
                    "handler": lambda: self._invoke_host("apply_table_density", "紧凑"),
                },
                {
                    "title": "表格密度：舒适",
                    "subtitle": "切换为舒适表格密度",
                    "keywords": ["表格密度", "舒适", "密度"],
                    "handler": lambda: self._invoke_host("apply_table_density", "舒适"),
                },
            ]
        )
        return commands

    def build_stock_commands(self, query: str) -> list[dict]:
        raw_query = str(query or "").strip()
        if not raw_query:
            return []

        if not raw_query.isdigit() and len(raw_query) < 2:
            return []

        code_name_map = getattr(self._window.data_provider, "code2name", None) or {}
        if not code_name_map:
            return []

        query_lower = raw_query.lower()
        matches: list[tuple[int, str, str]] = []

        for code, name in code_name_map.items():
            code_text = str(code or "").strip()
            name_text = str(name or "").strip()
            if len(code_text) != 6 or not code_text.isdigit() or not name_text:
                continue

            score: int | None = None
            if code_text == raw_query:
                score = 0
            elif name_text == raw_query or name_text.lower() == query_lower:
                score = 1
            elif code_text.startswith(raw_query):
                score = 2
            elif query_lower in name_text.lower():
                score = 3

            if score is not None:
                matches.append((score, code_text, name_text))

        matches.sort(key=lambda item: (item[0], item[1]))
        commands: list[dict] = []
        for _, code_text, name_text in matches[:12]:
            commands.append(
                {
                    "title": f"打开K线：{code_text} {name_text}",
                    "subtitle": "按代码或名称快速打开个股 K 线",
                    "keywords": [code_text, name_text, "K线", "个股"],
                    "handler": lambda c=code_text: self._invoke_host("open_security_chart", c),
                }
            )
        return commands
