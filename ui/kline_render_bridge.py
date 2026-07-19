# -*- coding: utf-8 -*-
"""Pure helpers for the Python-to-WebEngine K-line snapshot boundary."""

from __future__ import annotations

from collections.abc import Mapping


def prepared_matches_current_load(controller, prepared) -> bool:
    current = getattr(controller, "current_identity", None)
    return bool(
        not getattr(controller, "closed", True)
        and current is not None
        and str(getattr(prepared, "owner_id", "")) == current.window_id
        and int(getattr(prepared, "generation", -1)) == current.generation
        and str(getattr(prepared, "code", "")) == current.code
    )


def build_apply_snapshot_script(payload_json: str) -> str:
    payload = str(payload_json or "").strip()
    if not payload:
        raise ValueError("payload_json must not be blank")
    return (
        "(function(payload){"
        "if(typeof window.applySnapshot!=='function')return {ok:false,error:'api_unavailable'};"
        "return window.applySnapshot(payload);"
        "})(" + payload + ");"
    )


def build_runtime_active_script(active: bool) -> str:
    literal = "true" if active else "false"
    return (
        "(function(payload){"
        "if(typeof window.setRuntimeActive!=='function')return {ok:false,error:'api_unavailable'};"
        "return window.setRuntimeActive(payload);"
        "})({active:" + literal + "});"
    )


def build_snapshot_render_state_script(snapshot) -> str:
    import json

    payload = json.dumps(
        {
            "windowId": str(snapshot.window_id),
            "generation": int(snapshot.generation),
            "code": str(snapshot.code),
            "points": int(snapshot.points),
            "snapshotVersion": int(snapshot.version),
        },
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return (
        "(function(payload){"
        "if(typeof window.getSnapshotRenderState!=='function')"
        "return {ok:false,rendered:false,error:'api_unavailable'};"
        "return window.getSnapshotRenderState(payload);"
        "})(" + payload + ");"
    )


def build_reset_lease_script(title: str) -> str:
    import json

    payload = json.dumps({"title": str(title or "K线")}, ensure_ascii=False)
    return (
        "(function(payload){"
        "if(typeof window.resetForLease!=='function')return {ok:false,error:'api_unavailable'};"
        "return window.resetForLease(payload);"
        "})(" + payload + ");"
    )


def _integer(value) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _ack_identity(snapshot, ack) -> tuple:
    version = ack.get("snapshotVersion")
    if version is None:
        version = ack.get("snapshot_version")
    return (
        str(ack.get("windowId") or ack.get("window_id") or ""),
        _integer(ack.get("generation")),
        str(ack.get("code") or ""),
        _integer(ack.get("points")),
        _integer(version),
    )


def _snapshot_identity(snapshot) -> tuple:
    return (
        snapshot.window_id,
        snapshot.generation,
        snapshot.code,
        snapshot.points,
        _integer(snapshot.version),
    )


def snapshot_ack_matches(snapshot, ack) -> bool:
    if snapshot is None or not isinstance(ack, Mapping) or ack.get("ok") is not True:
        return False
    completed = ack.get("applied") is True or ack.get("duplicate") is True
    return bool(completed and _ack_identity(snapshot, ack) == _snapshot_identity(snapshot))


def snapshot_ack_is_queued(snapshot, ack) -> bool:
    if snapshot is None or not isinstance(ack, Mapping):
        return False
    queued = ack.get("ok") is True and ack.get("queued") is True
    return bool(queued and _ack_identity(snapshot, ack) == _snapshot_identity(snapshot))


def snapshot_render_ack_matches(snapshot, ack) -> bool:
    if snapshot is None or not isinstance(ack, Mapping):
        return False
    rendered = ack.get("ok") is True and ack.get("rendered") is True
    return bool(rendered and _ack_identity(snapshot, ack) == _snapshot_identity(snapshot))


def snapshot_render_ack_is_pending(snapshot, ack) -> bool:
    if snapshot is None or not isinstance(ack, Mapping):
        return False
    pending = ack.get("ok") is True and ack.get("rendered") is False
    return bool(pending and _ack_identity(snapshot, ack) == _snapshot_identity(snapshot))
