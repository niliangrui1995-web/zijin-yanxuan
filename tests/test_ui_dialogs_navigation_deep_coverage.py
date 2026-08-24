# -*- coding: utf-8 -*-

from __future__ import annotations

import datetime as dt
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from PyQt6.QtCore import QDate

from ui.components import scan_dialogs
from ui.components import stock_context_menu as menu_module


def test_scan_dialog_calendar_helpers_cover_cache_and_fallbacks(monkeypatch):
    monkeypatch.setattr(scan_dialogs.MarketCalendar, "_trade_dates", None)
    monkeypatch.setattr(
        scan_dialogs.MarketCalendar,
        "load_trade_dates",
        lambda: ["2026-01-05", "bad", "2026-01-02", "2026-07-15"],
    )
    assert scan_dialogs._cached_cn_trade_dates() == [
        dt.date(2026, 1, 2),
        dt.date(2026, 1, 5),
        dt.date(2026, 7, 15),
    ]
    assert scan_dialogs._recent_trade_window(dt.date(2026, 7, 15), 2) == (
        dt.date(2026, 1, 5),
        dt.date(2026, 7, 15),
    )
    assert scan_dialogs._first_trade_day_of_year(dt.date(2026, 7, 15)) == (
        dt.date(2026, 1, 2),
        dt.date(2026, 7, 15),
    )

    monkeypatch.setattr(scan_dialogs.MarketCalendar, "_trade_dates", [])
    monkeypatch.setattr(
        scan_dialogs.MarketCalendar,
        "is_trade_day",
        lambda value, _market: value.weekday() < 5,
    )
    assert scan_dialogs._cached_cn_trade_dates() == []
    assert scan_dialogs._recent_trade_window(dt.date(2026, 7, 13), 2) == (
        dt.date(2026, 7, 10),
        dt.date(2026, 7, 13),
    )
    assert scan_dialogs._first_trade_day_of_year(dt.date(2026, 1, 4)) == (
        dt.date(2026, 1, 1),
        dt.date(2026, 1, 4),
    )
    monkeypatch.setattr(scan_dialogs.MarketCalendar, "is_trade_day", lambda *_args: False)
    assert scan_dialogs._first_trade_day_of_year(dt.date(2026, 1, 2)) == (
        dt.date(2026, 1, 2),
        dt.date(2026, 1, 2),
    )


def test_trade_date_range_dialog_defaults_theme_and_reversed_range(monkeypatch, qt_application):
    monkeypatch.setattr(scan_dialogs, "_latest_cn_trade_date", lambda: dt.date(2026, 7, 15))
    dialog = scan_dialogs.TradeDateRangeDialog(
        object_name="customRange",
        window_title="回补",
        default_start=dt.date(2026, 7, 10),
        default_end=dt.date(2026, 7, 12),
    )
    try:
        assert dialog.objectName() == "customRange"
        assert dialog.selected_range() == ("2026-07-10", "2026-07-12")
        dialog.start_date_edit.setDate(QDate(2026, 7, 15))
        dialog.end_date_edit.setDate(QDate(2026, 7, 1))
        assert dialog.selected_range() == ("2026-07-01", "2026-07-15")

        old_height = dialog._title_bar.height()
        dialog._on_theme_changed("dark")
        assert dialog._title_bar.height() == old_height
        dialog._btn_close.click()
        assert dialog.result() == dialog.DialogCode.Rejected
    finally:
        dialog.close()
        dialog.deleteLater()


def test_scan_range_dialog_all_presets_manual_and_reversed(monkeypatch, qt_application):
    latest = dt.date(2026, 7, 15)
    monkeypatch.setattr(scan_dialogs, "_latest_cn_trade_date", lambda: latest)
    monkeypatch.setattr(
        scan_dialogs,
        "_recent_trade_window",
        lambda end, count: (end - dt.timedelta(days=count), end),
    )
    monkeypatch.setattr(scan_dialogs, "_first_trade_day_of_year", lambda end: (dt.date(end.year, 1, 2), end))
    dialog = scan_dialogs.VCPScanRangeDialog()
    try:
        assert dialog.selected_range() == ("2026-06-15", "2026-07-15")
        expected = {
            dialog.PRESET_RECENT_30: "2026-06-15",
            dialog.PRESET_RECENT_60: "2026-05-16",
            dialog.PRESET_RECENT_120: "2026-03-17",
            dialog.PRESET_YTD: "2026-01-02",
        }
        for preset, start in expected.items():
            dialog._apply_preset(preset)
            assert dialog.selected_range() == (start, "2026-07-15")
            assert dialog._preset_buttons[preset].property("state") == "active"

        dialog._syncing_dates = True
        dialog._on_manual_date_changed(QDate.currentDate())
        assert dialog._preset_buttons[dialog.PRESET_YTD].property("state") == "active"
        dialog._syncing_dates = False
        dialog._on_manual_date_changed(QDate.currentDate())
        assert all(button.property("state") == "" for button in dialog._preset_buttons.values())

        dialog.start_date_edit.setDate(QDate(2026, 7, 20))
        dialog.end_date_edit.setDate(QDate(2026, 7, 2))
        assert dialog.selected_range() == ("2026-07-02", "2026-07-20")
    finally:
        dialog.close()
        dialog.deleteLater()


def test_scan_settings_dialog_presets_save_restore_and_values(monkeypatch, qt_application):
    dialog = scan_dialogs.VCPScanSettingsDialog(
        {"rps": 88, "amp": 0.4, "ma_bind": 0.04, "amount": 2.5, "high250": 0.08},
        {"My preset": {"rps": 77, "amp": 0.3, "ma_bind": 0.06, "amount": 1.1, "high250": 0.2}},
    )
    try:
        assert dialog.values() == {
            "rps": 88.0,
            "amp": 0.4,
            "ma_bind": 0.04,
            "amount": 2.5,
            "high250": 0.08,
        }
        dialog.combo_preset.setCurrentIndex(0)
        dialog._on_preset_selected(0)
        idx = dialog.combo_preset.findData("My preset")
        dialog.combo_preset.setCurrentIndex(idx)
        assert dialog.values()["rps"] == 77.0

        monkeypatch.setattr(scan_dialogs.QInputDialog, "getText", lambda *_args: ("   ", True))
        dialog._on_save_preset()
        assert "" not in dialog.user_presets()
        monkeypatch.setattr(scan_dialogs.QInputDialog, "getText", lambda *_args: ("Fresh", False))
        dialog._on_save_preset()
        assert "Fresh" not in dialog.user_presets()

        dialog.set_values({"rps": 66})
        monkeypatch.setattr(scan_dialogs.QInputDialog, "getText", lambda *_args: (" Fresh ", True))
        dialog._on_save_preset()
        assert dialog.user_presets()["Fresh"]["rps"] == 66.0
        assert dialog.combo_preset.currentData() == "Fresh"

        dialog._restore_defaults()
        assert dialog.values() == dialog._builtin_presets[dialog.DEFAULT_PRESET_NAME]
        assert dialog.combo_preset.currentData() == dialog.DEFAULT_PRESET_NAME
    finally:
        dialog.close()
        dialog.deleteLater()


def test_stock_context_text_cleaning_and_url_limits(tmp_path):
    assert menu_module._normalize_text(" a\x00  b ", max_length=3) == "a b"
    assert menu_module._clean_stock_code("SZ300308.SZ") == "300308"
    assert menu_module._clean_stock_code(" weird/+$ ") == "weird+"
    assert menu_module._clean_stock_name("⭐★ Name\r\nsecond") == "Name"
    assert menu_module._clean_codex_prompt(" a \r\n\x00\n b ") == "a\nb"
    assert len(menu_module._clean_codex_prompt("x" * 900)) == menu_module.CODEX_PROMPT_MAX_LENGTH
    assert menu_module.build_codex_stock_prompt("", "") == menu_module.CODEX_CURRENT_STOCK_PROMPT
    assert menu_module.build_codex_stock_prompt("300308", "中际旭创股票") == "深度研究 中际旭创股票"
    assert menu_module.build_codex_stock_prompt("300308", "") == "深度研究 300308股票"

    parsed = parse_qs(urlparse(menu_module.build_codex_project_thread_url(tmp_path, prompt=" \x00 ")).query)
    assert parsed == {"path": [str(tmp_path)]}
    parsed = parse_qs(urlparse(menu_module.build_codex_project_thread_url(tmp_path, prompt=" research ")).query)
    assert parsed["prompt"] == ["research"]


def test_stock_context_scheme_registration_and_url_fallbacks(monkeypatch):
    monkeypatch.setattr(menu_module, "_is_windows_os", lambda: False)
    assert menu_module._is_codex_scheme_registered()

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    queries = []
    fake_winreg = SimpleNamespace(
        HKEY_CURRENT_USER="cu",
        HKEY_CLASSES_ROOT="cr",
        OpenKey=lambda root, path: _Key(),
        QueryValueEx=lambda key, value: queries.append(value) or ("", 1),
    )
    monkeypatch.setitem(sys.modules, "winreg", fake_winreg)
    monkeypatch.setattr(menu_module, "_is_windows_os", lambda: True)
    assert menu_module._is_codex_scheme_registered()
    assert queries == ["URL Protocol"]

    fake_winreg.OpenKey = lambda *_args: (_ for _ in ()).throw(OSError("missing"))
    assert not menu_module._is_codex_scheme_registered()

    monkeypatch.setattr(menu_module.QDesktopServices, "openUrl", lambda _url: True)
    assert menu_module._open_codex_url("codex://new")
    monkeypatch.setattr(menu_module.QDesktopServices, "openUrl", lambda _url: False)
    monkeypatch.setattr(menu_module.webbrowser, "open_new_tab", lambda _url: 1)
    assert menu_module._open_codex_url("codex://new")
    monkeypatch.setattr(menu_module.webbrowser, "open_new_tab", lambda _url: (_ for _ in ()).throw(RuntimeError("no")))
    assert not menu_module._open_codex_url("codex://new")


def test_stock_context_project_launch_guards_and_platform_routes(monkeypatch, tmp_path):
    warnings = []
    monkeypatch.setattr(menu_module.QMessageBox, "warning", lambda *args: warnings.append(args))
    menu_module._warn_codex_open_failed(None, "bad")
    assert warnings[-1][2] == "bad"
    assert isinstance(menu_module._is_windows_os(), bool)

    launched = []
    monkeypatch.setattr(menu_module, "launch_codex_desktop_thread", lambda url: launched.append(url) or True)
    assert menu_module._open_codex_desktop_thread("codex://new")
    assert launched == ["codex://new"]

    missing = tmp_path / "missing"
    assert not menu_module.open_codex_project_thread(project_path=missing)
    assert "路径不存在" in warnings[-1][2]

    monkeypatch.setattr(menu_module, "_is_windows_os", lambda: False)
    monkeypatch.setattr(menu_module, "_is_codex_scheme_registered", lambda: False)
    assert not menu_module.open_codex_project_thread(project_path=tmp_path)
    assert "没有注册" in warnings[-1][2]

    monkeypatch.setattr(menu_module, "_is_codex_scheme_registered", lambda: True)
    monkeypatch.setattr(menu_module, "_open_codex_url", lambda url: launched.append(url) or True)
    assert menu_module.open_codex_project_thread(project_path=tmp_path, prompt=None)
    assert "prompt=" in launched[-1]

    monkeypatch.setattr(menu_module, "_is_windows_os", lambda: True)
    monkeypatch.setattr(menu_module, "_open_codex_desktop_thread", lambda _url: False)
    assert not menu_module.open_codex_project_thread(project_path=tmp_path, prompt="research")
    assert "没有接受" in warnings[-1][2]


def test_stock_context_resolve_workspace_window_chain_and_cycle():
    workspace = object()
    parent = SimpleNamespace(window=lambda: SimpleNamespace(_workspace=workspace))
    assert menu_module._resolve_workspace(parent) is workspace

    root = SimpleNamespace(_workspace=workspace, parent=lambda: None)
    child = SimpleNamespace(parent=lambda: root)
    child.window = lambda: SimpleNamespace(_workspace=None)
    assert menu_module._resolve_workspace(child) is workspace

    cycle = SimpleNamespace(_workspace=None)
    cycle.window = lambda: None
    cycle.parent = lambda: cycle
    assert menu_module._resolve_workspace(cycle) is None


class _Action:
    def __init__(self, text):
        self.text = text


class _Menu:
    chosen_text = None
    last = None

    def __init__(self, _parent):
        self.actions = []
        _Menu.last = self

    def setStyleSheet(self, _value):
        pass

    def addAction(self, text):
        action = _Action(text)
        self.actions.append(action)
        return action

    def addSeparator(self):
        self.actions.append(_Action("---"))

    def exec(self, _position):
        return next((item for item in self.actions if item.text == self.chosen_text), None)


@pytest.fixture
def fake_stock_menu(monkeypatch):
    monkeypatch.setattr(menu_module, "QMenu", _Menu)
    monkeypatch.setattr(menu_module, "install_menu_fade", lambda _menu: None)
    monkeypatch.setattr(menu_module, "generate_context_menu_qss", lambda: "qss")
    monkeypatch.setattr(menu_module.QCursor, "pos", staticmethod(lambda: None))
    return _Menu


def test_stock_context_menu_no_selection_and_chart_paths(monkeypatch, fake_stock_menu):
    emissions = []
    monkeypatch.setattr(
        menu_module,
        "event_bus",
        SimpleNamespace(
            sig_show_kline=SimpleNamespace(emit=lambda *args: emissions.append(("chart", args))),
            sig_show_kline_with_list=SimpleNamespace(emit=lambda *args: emissions.append(("list", args))),
        ),
    )
    monkeypatch.setattr(menu_module.watchlist_vm, "is_in_watchlist", lambda _code: False)

    fake_stock_menu.chosen_text = None
    assert menu_module.build_stock_context_menu(object(), "300308", "中际旭创") is None
    fake_stock_menu.chosen_text = "查看 K 线图"
    menu_module.build_stock_context_menu(object(), "300308", "中际旭创", show_watchlist_toggle=False)
    menu_module.build_stock_context_menu(
        object(),
        "300308",
        "中际旭创",
        show_watchlist_toggle=False,
        vcp_data={"signal": "vcp"},
    )
    assert emissions[0] == ("chart", ("300308",))
    assert emissions[1][0] == "list"
    assert emissions[1][1][1][0] == {"signal": "vcp", "代码": "300308", "名称": "中际旭创"}


@pytest.mark.parametrize(
    ("chosen", "expected"),
    [
        ("加入关注池", "toggle"),
        ("置顶", "pin"),
        ("置底", "bottom"),
        ("跳转通达信", "tdx"),
        ("跳转东方财富", "eastmoney"),
        ("打开 ChatGPT", "codex"),
        ("导出当前表", "export"),
        ("Extra", "extra"),
    ],
)
def test_stock_context_menu_action_dispatch(monkeypatch, fake_stock_menu, chosen, expected):
    calls = []
    favorite = chosen in {"置顶", "置底"}
    monkeypatch.setattr(menu_module.watchlist_vm, "is_in_watchlist", lambda _code: favorite)
    monkeypatch.setattr(menu_module.watchlist_vm, "toggle_stock", lambda *args: calls.append(("toggle", args)))
    monkeypatch.setattr(menu_module.watchlist_vm, "pin_to_top", lambda code: calls.append(("pin", code)))
    monkeypatch.setattr(menu_module.watchlist_vm, "move_to_bottom", lambda code: calls.append(("bottom", code)))
    monkeypatch.setattr(
        menu_module, "open_codex_project_thread", lambda *args, **kwargs: calls.append(("codex", args, kwargs))
    )
    parent = SimpleNamespace(
        window=lambda: None,
        parent=lambda: None,
        launch_tdx=lambda code: calls.append(("tdx", code)),
        launch_eastmoney=lambda code: calls.append(("eastmoney", code)),
    )
    fake_stock_menu.chosen_text = chosen
    menu_module.build_stock_context_menu(
        parent,
        "300308",
        "⭐中际旭创\nnoise",
        show_export=True,
        export_callback=lambda: calls.append(("export",)),
        extra_actions=[("", lambda: None), ("bad", None), ("Extra", lambda: calls.append(("extra",)))],
        vcp_data={"x": 1},
    )
    assert calls[0][0] == expected
    if expected == "toggle":
        assert calls[0][1][1] == "中际旭创"


def test_stock_context_menu_detail_copy_and_missing_launchers(monkeypatch, fake_stock_menu, qt_application):
    calls = []
    workspace = SimpleNamespace(open_security_detail=lambda *args: calls.append(args))
    parent = SimpleNamespace(window=lambda: SimpleNamespace(_workspace=workspace), parent=lambda: None)
    monkeypatch.setattr(menu_module.watchlist_vm, "is_in_watchlist", lambda _code: False)

    fake_stock_menu.chosen_text = "查看股票全景"
    menu_module.build_stock_context_menu(parent, "300308", "中际旭创", vcp_data=None)
    assert calls == [("300308", {"name": "中际旭创", "vcp_data": {}})]

    fake_stock_menu.chosen_text = "复制代码"
    menu_module.build_stock_context_menu(parent, "300308", "中际旭创")
    assert qt_application.clipboard().text() == "300308"

    parent_without_launchers = SimpleNamespace(window=lambda: None, parent=lambda: None)
    fake_stock_menu.chosen_text = "跳转通达信"
    menu_module.build_stock_context_menu(parent_without_launchers, "300308", "中际旭创")
    fake_stock_menu.chosen_text = "跳转东方财富"
    menu_module.build_stock_context_menu(parent_without_launchers, "300308", "中际旭创")
