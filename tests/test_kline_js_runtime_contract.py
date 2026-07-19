from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ASSET_ROOT = ROOT / "ui" / "assets" / "kline"


def _source(name: str) -> str:
    return (ASSET_ROOT / name).read_text(encoding="utf-8")


def test_apply_snapshot_is_the_only_mutation_api_and_returns_strict_identity_ack():
    source = _source("window_api.js")

    assert "window.applySnapshot = function (payload)" in source
    assert "window.replaceKlineData" not in source
    assert "window.updateLastBar" not in source
    assert "return _snapshotAck(meta, { ok: false, error: 'invalid_snapshot' });" in source
    assert "return _snapshotAck(meta, { applied: true });" in source
    assert "return _snapshotAck(meta, { queued: true });" in source
    assert "return _snapshotAck(meta, { duplicate: true });" in source
    assert "lastAppliedSnapshotKey" in source
    for field in ("windowId", "generation", "code", "points", "snapshotVersion"):
        assert f"{field}: meta.{field}" in source
    for key_part in ("meta.windowId", "meta.generation", "meta.code", "String(snapshotVersion)"):
        assert key_part in source
    assert source.count("chart.on('rendered'") == 1
    assert "window.getSnapshotRenderState = function (payload)" in source
    assert "lastRenderedSnapshotMeta" in source
    assert "pendingRenderedSnapshotMeta = meta;" in source
    assert "lazyUpdate: false" in source
    assert "notMerge: false" in source
    assert "replaceMerge: ['series']" in source


def test_wheel_pointer_and_resize_share_one_animation_frame_scheduler():
    interaction = _source("interaction.js")
    window_api = _source("window_api.js")

    assert interaction.count("requestAnimationFrame(") == 1
    assert "function _scheduleRuntimeFrame()" in interaction
    assert "pendingWheelZoom" in interaction
    assert "pendingPointerIdx" in interaction
    assert "pendingResize" in interaction
    assert "if (nextIdx !== lastPointerIdx)" in interaction
    assert "_queueRuntimeResize();" in window_api
    assert "window.addEventListener('resize', function () {\n            chart.resize();" not in window_api


def test_runtime_pause_queues_latest_snapshot_and_suspends_particles():
    interaction = _source("interaction.js")
    option = _source("option.js")
    window_api = _source("window_api.js")

    assert "let runtimeActive = true;" in interaction
    assert "window.setRuntimeActive = function (payload)" in window_api
    assert "runtimeActive: runtimeActive" in window_api
    assert "pendingRuntimeSnapshot" in window_api
    assert "_setParticlesActive(runtimeActive);" in window_api
    assert "data: runtimeActive ? buildVolumeSpikeParticles() : []" in option


def test_lease_reset_clears_every_toolbar_value_and_transient_style():
    interaction = _source("interaction.js")
    window_api = _source("window_api.js")

    assert "function _resetToolbar()" in interaction
    for element_id in (
        "v-date",
        "v-open",
        "v-high",
        "v-low",
        "v-close",
        "v-pct",
        "v-vol",
        "v-ma10",
        "v-ma20",
        "v-ma50",
        "v-ma150",
        "v-ma200",
    ):
        assert f"'{element_id}'" in interaction
    assert "closeEl.style.color = '';" in interaction
    assert "pctEl.style.color = '';" in interaction
    assert "_resetToolbar();" in window_api
    assert "glassFused = false;" in window_api
