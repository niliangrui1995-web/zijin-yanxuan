"""Qt-free watchlist indicator calculation and persistence."""

from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from core.buy_point import BUY_POINT_TEXT
from core.exceptions import CacheIOError, DataFormatError

RadarData = tuple[Any, Any, Any, Any, Any, Any]


def _cancelled(cancellation_token: Any) -> bool:
    return cancellation_token is not None and bool(cancellation_token.cancelled)


def _active_items(values: Iterable[Any], cancellation_token: Any = None) -> Iterable[Any]:
    for value in values:
        if _cancelled(cancellation_token):
            return
        yield value


def _rps_bundle(radar_data: Sequence[Any] | None, loader: Callable[[], Any]) -> Any:
    bundle = radar_data[5] if radar_data and len(radar_data) > 5 else None
    if bundle:
        return bundle
    try:
        return loader()
    except (CacheIOError, DataFormatError, ValueError):
        return None


def _radar_sources(radar_data: Sequence[Any] | None) -> tuple[Any, Any, Any, Any, Any]:
    if not radar_data:
        return {}, {}, {}, {}, {}
    return radar_data[0], radar_data[1], radar_data[2], radar_data[3], radar_data[4]


def _detail_fields(value: Any, *, numeric_key: str) -> tuple[Any, Any]:
    if isinstance(value, dict):
        return value.get("text", ""), value.get(numeric_key, "")
    return value or "", ""


def _rps_display(code: str, rps120_series: Any, rps250_series: Any) -> str:
    rps120 = 0.0
    if rps120_series is not None and code in rps120_series:
        rps120 = float(rps120_series.get(code, 0))
    rps250 = 0.0
    if rps250_series is not None and code in rps250_series:
        rps250 = float(rps250_series.get(code, 0))
    if rps250 <= 0:
        return "--"
    return f"{rps250:.0f}/{rps120:.0f}" if rps120 > 0 else f"{rps250:.0f}"


def _indicator_row(
    code: str,
    *,
    rps120_series: Any,
    rps250_series: Any,
    remark_data: Any,
    subsector_data: Any,
    block_data: Any,
    earnings_data: Any,
    lhb_data: Any,
) -> dict[str, Any]:
    block_text, block_amount = _detail_fields(block_data.get(code, {}), numeric_key="amount_wan")
    earnings_text, earnings_qoq = _detail_fields(earnings_data.get(code, {}), numeric_key="qoq_pct")
    return {
        "rps": _rps_display(code, rps120_series, rps250_series),
        "subsector": subsector_data.get(code, ""),
        "remark": remark_data.get(code, ""),
        "block_trade": block_text,
        "block_trade_amount_wan": block_amount,
        "earnings": earnings_text,
        "earnings_qoq_pct": earnings_qoq,
        "lhb": lhb_data.get(code, ""),
    }


def build_watchlist_indicator_results(
    codes_with_rows: Sequence[tuple[int, str]] | None,
    *,
    radar_data: Sequence[Any] | None = None,
    rps_loader: Callable[[], Any] | None = None,
    cancellation_token: Any = None,
) -> dict[str, dict[str, Any]] | None:
    """Build table-neutral watchlist indicators from one immutable data snapshot."""

    if codes_with_rows is None:
        return None
    if rps_loader is None:
        from app.services.f5_snapshot_service import load_active_rps_payload

        rps_loader = load_active_rps_payload
    bundle = _rps_bundle(radar_data, rps_loader)
    rps120_series = bundle.get("rps120") if bundle else None
    rps250_series = bundle.get("rps250") if bundle else None
    remark_data, subsector_data, block_data, earnings_data, lhb_data = _radar_sources(radar_data)
    results: dict[str, dict[str, Any]] = {}
    for _, code in _active_items(codes_with_rows, cancellation_token):
        try:
            results[code] = _indicator_row(
                code,
                rps120_series=rps120_series,
                rps250_series=rps250_series,
                remark_data=remark_data,
                subsector_data=subsector_data,
                block_data=block_data,
                earnings_data=earnings_data,
                lhb_data=lhb_data,
            )
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError):
            continue
    return results


def _lhb_patch(value: Any, *, buy_point_text: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {"龙虎榜": str(value or ""), "龙虎榜日期": "", "龙虎榜净额(万)": ""}
    net_amount = value.get("net_wan", "")
    return {
        "龙虎榜": buy_point_text if str(value.get("buy_point", "") or "").strip() else "",
        "龙虎榜日期": str(value.get("date", "") or ""),
        "龙虎榜净额(万)": net_amount if net_amount not in (None, "") else "",
    }


def _metric_patch(data: Mapping[str, Any], *, buy_point_text: str) -> dict[str, Any]:
    patch = {
        "RPS强度": str(data.get("rps", "")),
        "备注": str(data.get("remark", "") or ""),
        "大宗交易": str(data.get("block_trade", "") or ""),
        "大宗交易金额(万)": data.get("block_trade_amount_wan", ""),
        "业绩异动": str(data.get("earnings", "") or ""),
        "业绩环比%": data.get("earnings_qoq_pct", ""),
    }
    if data.get("subsector"):
        patch["细分板块"] = str(data["subsector"])
    patch.update(_lhb_patch(data.get("lhb", ""), buy_point_text=buy_point_text))
    return patch


def build_watchlist_metric_patch(
    results: Mapping[str, Mapping[str, Any]],
    *,
    cancellation_token: Any = None,
    buy_point_text: str = BUY_POINT_TEXT,
) -> dict[str, dict[str, Any]]:
    return {
        str(code): _metric_patch(data, buy_point_text=buy_point_text)
        for code, data in _active_items(results.items(), cancellation_token)
    }


def persist_watchlist_metrics(
    results: Mapping[str, Mapping[str, Any]],
    *,
    cancellation_token: Any = None,
) -> None:
    if not results:
        return
    patch = build_watchlist_metric_patch(results, cancellation_token=cancellation_token)
    if _cancelled(cancellation_token):
        return
    from domains.watchlist import watchlist_vm

    watchlist_vm.bulk_patch_entries(patch, remove_keys=["催化剂", "美股日报", "热点板块"])


__all__ = [
    "build_watchlist_indicator_results",
    "build_watchlist_metric_patch",
    "persist_watchlist_metrics",
]
