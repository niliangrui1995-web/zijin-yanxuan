from __future__ import annotations

from types import SimpleNamespace

from app.use_cases.window_command_service import WindowCommandService


class _Workspace:
    def __init__(self):
        self.calls = []

    def tab_specs(self):
        return [
            {"title": "Alpha", "group": "Scan"},
            {"title": "", "group": "Ignored"},
            {"title": "Beta", "group": ""},
        ]

    def run_incremental_scan(self):
        self.calls.append(("incremental",))

    def open_scan_settings(self):
        self.calls.append(("settings",))

    def refresh_lhb_history(self):
        self.calls.append(("lhb",))

    def run_fund_holdings_sync(self):
        self.calls.append(("fund",))


class _Window:
    def __init__(self):
        self.calls = []
        self._workspace = _Workspace()
        self.data_provider = SimpleNamespace(
            code2name={
                "000001": "Alpha Bank",
                "000002": "Beta Tech",
                "000003": "",
                "123456": "Gamma",
                "bad": "Bad Code",
            }
        )

    def trigger_global_sync(self):
        self.calls.append(("sync",))

    def activate_workspace_tab(self, index):
        self.calls.append(("tab", index))

    def theme_names(self):
        return ["Light", "Dark"]

    def switch_theme(self, name):
        self.calls.append(("theme", name))

    def apply_table_density(self, density):
        self.calls.append(("density", density))

    def open_security_chart(self, code):
        self.calls.append(("chart", code))


def test_window_command_service_builds_workspace_and_theme_commands():
    window = _Window()
    commands = WindowCommandService(window).build_commands()

    commands[0]["handler"]()
    next(command for command in commands if "Alpha" in command["title"])["handler"]()
    next(command for command in commands if "Dark" in command["title"])["handler"]()
    next(command for command in commands if command["handler"] == window._workspace.run_incremental_scan)["handler"]()

    assert window.calls == [("sync",), ("tab", 0), ("theme", "Dark")]
    assert window._workspace.calls == [("incremental",)]


def test_window_command_service_stock_commands_filter_sort_and_open_chart():
    window = _Window()
    service = WindowCommandService(window)

    assert service.build_stock_commands("") == []
    assert service.build_stock_commands("A") == []

    by_prefix = service.build_stock_commands("000")
    assert len(by_prefix) == 2
    assert "000001" in by_prefix[0]["title"]
    assert "000002" in by_prefix[1]["title"]

    by_name = service.build_stock_commands("Alpha")
    assert len(by_name) == 1
    assert "000001" in by_name[0]["title"]

    by_name[0]["handler"]()
    assert window.calls == [("chart", "000001")]


def test_window_command_service_handles_missing_host_hooks_and_exact_matches():
    window = SimpleNamespace(data_provider=SimpleNamespace(code2name={}))
    service = WindowCommandService(window)

    assert service._invoke_host("missing") is None
    assert service._theme_names() == []
    assert service.build_stock_commands("000001") == []

    window.data_provider.code2name = {
        "000001": "Alpha Bank",
        "000002": "000001",
    }

    by_code = service.build_stock_commands("000001")
    by_name = service.build_stock_commands("Alpha Bank")

    assert "000001" in by_code[0]["title"]
    assert "Alpha Bank" in by_name[0]["title"]
