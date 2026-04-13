# -*- coding: utf-8 -*-
from PyQt6.QtWidgets import QApplication

from ui.components import VCPTableView
from ui.styles.global_qss import generate_global_qss
from ui.theme import THEME_MOYUAN, THEME_YUEBAI
from ui.theme_tokens import build_ui_tokens, get_state_tone


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
    tokens = build_ui_tokens(THEME_YUEBAI)

    assert "motion" in tokens
    assert "z_index" in tokens
    assert "chart" in tokens
    assert tokens["table"]["selected_rail_width"] > 0
    assert tokens["table"]["numeric_heat_max_alpha"] >= 32
    assert tokens["shell"]["toolbar_min_height"] >= tokens["control"]["button_height"]


def test_global_qss_uses_density_tokens_for_table_and_controls():
    compact_qss = generate_global_qss(THEME_YUEBAI, density="紧凑")
    comfort_tokens = build_ui_tokens(THEME_YUEBAI, density="舒展")
    compact_tokens = build_ui_tokens(THEME_YUEBAI, density="紧凑")

    assert f"min-height: {compact_tokens['control']['button_height']}px;" in compact_qss
    assert f"padding: {compact_tokens['table']['cell_padding_y']}px {compact_tokens['table']['cell_padding_x']}px;" in compact_qss
    assert "QWidget#tabToolbar" in compact_qss
    assert "QLabel#tabStatusLabel" in compact_qss
    assert compact_tokens["control"]["button_height"] < comfort_tokens["control"]["button_height"]


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
