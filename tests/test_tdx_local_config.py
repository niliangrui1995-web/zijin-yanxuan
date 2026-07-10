import json
import os

import pytest

from core.runtime_paths import DEFAULT_TDX_ROOT, DEFAULT_TDX_VIPDOC
from vcp import utils


def test_tdx_local_config_defaults_to_huatai_installation(monkeypatch):
    monkeypatch.setattr(utils.os.path, "exists", lambda _path: False)
    monkeypatch.setattr(utils, "_check_vipdoc_valid", lambda path: path == DEFAULT_TDX_VIPDOC)

    assert DEFAULT_TDX_ROOT == r"D:\HT"
    assert DEFAULT_TDX_VIPDOC == r"D:\HT\vipdoc"
    assert utils._load_tdx_local_config() == DEFAULT_TDX_VIPDOC


@pytest.mark.parametrize("config_points_to_vipdoc", [False, True])
def test_tdx_local_config_accepts_install_root_or_vipdoc(tmp_path, monkeypatch, config_points_to_vipdoc):
    install_root = tmp_path / "huatai"
    vipdoc = install_root / "vipdoc"
    (vipdoc / "sh").mkdir(parents=True)
    (vipdoc / "sz").mkdir()
    configured_path = vipdoc if config_points_to_vipdoc else install_root
    config_path = tmp_path / "vcp_tdx_config.json"
    config_path.write_text(
        json.dumps({"tdx_vipdoc_root": str(configured_path)}),
        encoding="utf-8",
    )
    real_exists = os.path.exists
    monkeypatch.setattr(
        utils.os.path,
        "exists",
        lambda path: real_exists(path) if os.fspath(path) == os.fspath(config_path) else False,
    )
    monkeypatch.setattr(utils, "PROJECT_ROOT", os.fspath(tmp_path))

    assert utils._load_tdx_local_config() == os.fspath(vipdoc)
