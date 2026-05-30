# -*- coding: utf-8 -*-

from app.services.ui_config_service import app_config
from ui.models import table_model_helpers


def test_table_density_cache_uses_explicit_invalidation(monkeypatch):
    table_model_helpers.invalidate_table_token_cache("紧凑")
    assert table_model_helpers._current_table_density() == "紧凑"

    monkeypatch.setattr(app_config, "table_density", "舒适", raising=False)
    assert table_model_helpers._current_table_density() == "紧凑"

    table_model_helpers.invalidate_table_token_cache()
    assert table_model_helpers._current_table_density() == "舒适"


def test_table_density_cache_can_be_replaced_without_qsettings_read():
    table_model_helpers.invalidate_table_token_cache("紧凑")
    assert table_model_helpers._current_table_density() == "紧凑"

    table_model_helpers.invalidate_table_token_cache("舒适")
    assert table_model_helpers._current_table_density() == "舒适"


def test_build_cell_tooltip_does_not_insert_fixed_width_line_breaks():
    text = "光纤预制棒、光纤、光缆一体化龙头，AI算力网络带动数据中心内部及集群间光互联需求；空芯光纤、G.654.E需求提升。"

    tooltip = table_model_helpers._build_cell_tooltip(text)

    assert tooltip == text
    assert "空芯光\n纤" not in tooltip


def test_build_cell_tooltip_preserves_source_newlines_only():
    text = "第一行是原文自带换行\n第二行仍然交给悬浮窗按宽度自然换行"

    tooltip = table_model_helpers._build_cell_tooltip(text)

    assert tooltip == text
