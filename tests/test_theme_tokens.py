# -*- coding: utf-8 -*-
from PyQt6.QtWidgets import QApplication

from ui.components import VCPTableView
from ui.models.table_models import StockTableModel
from ui.styles.global_qss import generate_global_qss
from ui.theme import THEME_MOYUAN, THEME_YUEBAI
from ui.theme_tokens import build_ui_tokens, get_state_tone


def _hex_to_rgb(color: str) -> tuple[float, float, float]:
    value = color.lstrip("#")
    return tuple(int(value[index:index + 2], 16) / 255 for index in (0, 2, 4))


def _relative_luminance(color: str) -> float:
    def channel(value: float) -> float:
        return value / 12.92 if value <= 0.03928 else ((value + 0.055) / 1.055) ** 2.4

    red, green, blue = _hex_to_rgb(color)
    return 0.2126 * channel(red) + 0.7152 * channel(green) + 0.0722 * channel(blue)


def _contrast_ratio(foreground: str, background: str) -> float:
    light = max(_relative_luminance(foreground), _relative_luminance(background))
    dark = min(_relative_luminance(foreground), _relative_luminance(background))
    return (light + 0.05) / (dark + 0.05)


def test_build_ui_tokens_compact_density_tightens_metrics():
    comfort = build_ui_tokens(THEME_MOYUAN, density="舒展")
    compact = build_ui_tokens(THEME_MOYUAN, density="紧凑")

    assert compact["density"] == "紧凑"
    assert compact["control"]["button_height"] < comfort["control"]["button_height"]
    assert compact["table"]["cell_padding_y"] < comfort["table"]["cell_padding_y"]
    assert compact["table"]["row_height_delta"] == comfort["table"]["row_height_delta"]


def test_theme_tokens_expose_state_tones_for_terminal_statuses():
    info_tone = get_state_tone("info", THEME_YUEBAI)
    offline_tone = get_state_tone("offline", THEME_YUEBAI)
    realtime_tone = get_state_tone("realtime", THEME_YUEBAI)

    assert info_tone["bg"]
    assert info_tone["fg"]
    assert info_tone["border"]
    assert offline_tone["fg"] == THEME_YUEBAI["TEXT_SECONDARY"]
    assert realtime_tone["bg"]


def test_theme_tokens_expose_terminal_layers_and_toolbar_metrics():
    tokens = build_ui_tokens(THEME_YUEBAI, density="紧凑")
    dark_tokens = build_ui_tokens(THEME_MOYUAN, density="紧凑")

    assert "motion" in tokens
    assert "z_index" in tokens
    assert "chart" in tokens
    assert "toolbar_card" in tokens["surface"]
    assert tokens["table"]["selected_rail_width"] > 0
    assert tokens["table"]["current_cell_border"] == THEME_YUEBAI["BRAND_DEEP"]
    assert tokens["table"]["current_cell_bg"]
    assert tokens["table"]["current_cell_bg_selected"]
    assert tokens["table"]["numeric_heat_max_alpha"] >= 32
    assert tokens["shell"]["toolbar_min_height"] >= tokens["control"]["button_height"]
    assert tokens["shell"]["toolbar_min_height"] < 48
    assert tokens["shell"]["toolbar_group_gap"] <= 4
    assert tokens["surface"]["toolbar"] == THEME_YUEBAI["BG_CARD"]
    assert tokens["surface"]["toolbar_card"] != tokens["surface"]["toolbar"]
    assert tokens["surface"]["toolbar_chip"] != THEME_YUEBAI["BG_BUTTON"]
    assert dark_tokens["surface"]["toolbar"] == THEME_MOYUAN["BG_ELEVATED"]
    assert dark_tokens["surface"]["toolbar_chip"] == THEME_MOYUAN["BG_BUTTON"]


def test_light_theme_muted_text_contrast_meets_toolbar_threshold():
    assert _contrast_ratio(THEME_YUEBAI["TEXT_MUTED"], THEME_YUEBAI["BG_CARD"]) >= 4.5
    assert _contrast_ratio(THEME_YUEBAI["TEXT_MUTED"], THEME_YUEBAI["BG_BUTTON"]) >= 4.5
    assert _contrast_ratio(THEME_YUEBAI["TAB_TEXT"], THEME_YUEBAI["BG_CARD"]) >= 4.5


def test_global_qss_uses_density_tokens_for_table_and_controls():
    compact_qss = generate_global_qss(THEME_YUEBAI, density="紧凑")
    comfort_tokens = build_ui_tokens(THEME_YUEBAI, density="舒展")
    compact_tokens = build_ui_tokens(THEME_YUEBAI, density="紧凑")

    assert f"min-height: {compact_tokens['control']['button_height']}px;" in compact_qss
    assert f"padding: {compact_tokens['table']['cell_padding_y']}px {compact_tokens['table']['cell_padding_x']}px;" in compact_qss
    assert "QWidget#tabToolbar" in compact_qss
    assert "QLabel#tabStatusLabel" in compact_qss
    assert 'QPushButton[inToolbar="true"]' in compact_qss
    assert 'QLineEdit[inToolbar="true"]' in compact_qss
    assert "QPushButton:focus {" in compact_qss
    assert 'QToolButton[class="toolbarGhost"]:focus {' in compact_qss
    assert f"background-color: {compact_tokens['surface']['toolbar']};" in compact_qss
    assert f"background-color: {compact_tokens['surface']['toolbar_card']};" in compact_qss
    assert f"background-color: {compact_tokens['surface']['toolbar_chip']};" in compact_qss
    assert compact_tokens["control"]["button_height"] < comfort_tokens["control"]["button_height"]


def test_global_qss_selected_tab_does_not_use_brand_top_rule():
    qss = generate_global_qss(THEME_YUEBAI, density="紧凑")

    assert "QTabBar::tab:selected" in qss
    assert "border-top: 2px solid transparent;" in qss
    assert f"border-top: 2px solid {THEME_YUEBAI['BRAND_PRIMARY']};" not in qss


def test_global_qss_includes_themed_tooltip_style():
    qss = generate_global_qss(THEME_MOYUAN, density="紧凑")

    assert "QToolTip {" in qss
    assert f"background-color: {THEME_MOYUAN['BG_ELEVATED']};" in qss
    assert f"color: {THEME_MOYUAN['TEXT_PRIMARY']};" in qss
    assert "border-radius: 0px;" in qss


def test_global_qss_themes_date_edit_and_dialog_shell():
    qss = generate_global_qss(THEME_MOYUAN, density="紧凑")

    assert "QDateEdit {" in qss
    assert "QDialog#scanRangeDialog QFrame#dialogContainer" in qss
    assert "QDialog#scanRangeDialog QWidget#dialogTitleBar" in qss
    assert "QToolButton#dialogCloseButton" in qss


def test_vcp_table_view_apply_density_updates_row_height():
    table = VCPTableView()
    try:
        table.apply_density("舒展")
        comfort_height = table.verticalHeader().defaultSectionSize()

        table.apply_density("紧凑")
        compact_height = table.verticalHeader().defaultSectionSize()

        assert compact_height < comfort_height
    finally:
        table.deleteLater()


def test_vcp_table_view_width_is_capped_by_available_screen():
    table = VCPTableView()
    try:
        screen = QApplication.primaryScreen()
        available_width = screen.availableGeometry().width() if screen else 1920

        assert table.maximumWidth() <= available_width
        assert table.sizeHint().width() <= available_width
        assert table.minimumSizeHint().width() <= available_width
    finally:
        table.deleteLater()


def test_vcp_table_view_tooltip_style_uses_larger_font_without_rounded_corners():
    table = VCPTableView()
    try:
        style = table.styleSheet()
        assert "QToolTip" in style
        assert "font-size: 14px;" in style
        assert "border-radius: 0px;" in style
    finally:
        table.deleteLater()


def test_vcp_table_view_tooltip_only_shows_when_text_is_elided():
    table = VCPTableView()
    try:
        model = StockTableModel(["代码", "名称", "热点板块"])
        model.update_data([
            {
                "代码": "000001",
                "名称": "平安银行",
                "热点板块": "光通信(15d=100) | CPO概念(15d=96) | 铜连接(20d=93)",
            }
        ])
        table.setModel(model)
        target_col = model.headers.index("热点板块")
        idx = model.index(0, target_col)

        table.setColumnWidth(target_col, 120)
        assert table._should_show_tooltip_for_index(idx) is True

        table.setColumnWidth(target_col, 520)
        assert table._should_show_tooltip_for_index(idx) is False
    finally:
        table.deleteLater()
