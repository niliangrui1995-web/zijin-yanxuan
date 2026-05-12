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
