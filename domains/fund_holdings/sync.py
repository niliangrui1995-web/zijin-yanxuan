# -*- coding: utf-8 -*-
"""基金持仓数据抓取与同步服务。"""

from __future__ import annotations

import html
import json
import re
import urllib.parse
import urllib.request
from contextlib import suppress
from datetime import date

from core.task_errors import UserFacingTaskError
from domains.fund_holdings.compare import (
    SUBJECT_QFII,
    SUBJECT_RUIYUAN,
    build_qfii_snapshots,
    build_ruiyuan_snapshots,
    get_compare_quarter_key,
    normalize_quarter_key,
    quarter_end_date_text,
    quarter_key_for_date,
    quarter_parts,
    quarter_sort_value,
)
from domains.fund_holdings.store import fund_holdings_store
from infra.http_safety import urlopen_https

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"
_QFII_API_URL = "https://datacenter-web.eastmoney.com/api/data/v1/get"
_RUIYUAN_API_URL = "https://fundf10.eastmoney.com/FundArchivesDatas.aspx"
_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
_QFII_PAGE_SIZE = 500
_QFII_MAX_PAGES = 20
_QFII_MAX_ROWS = _QFII_PAGE_SIZE * _QFII_MAX_PAGES
_QFII_DISCLOSURE_DEADLINE_BY_QUARTER = {
    1: (4, 30),
    2: (8, 31),
    3: (10, 31),
}

_RUIYUAN_SECTION_RE = re.compile(
    r"(?P<header><h4 class='t'>.*?</h4>).*?(?P<table><table.*?</table>)",
    re.S,
)
_RUIYUAN_DATE_RE = re.compile(r"截止至：<font class='px12'>(\d{4}-\d{2}-\d{2})</font>")
_RUIYUAN_QUARTER_RE = re.compile(r"(\d{4})年([1-4])季度股票投资明细")
_RUIYUAN_ROW_RE = re.compile(
    r"<tr>\s*<td>(?P<rank>\d+)</td>\s*"
    r"<td><a[^>]*>(?P<code>[^<]+)</a></td>\s*"
    r"<td[^>]*><a[^>]*>(?P<name>[^<]+)</a></td>\s*"
    r".*?<td class='tor'>(?P<ratio>[^<]+)</td>\s*"
    r"<td class='tor'>(?P<shares>[^<]+)</td>\s*"
    r"<td class='tor'>(?P<market>[^<]+)</td>\s*</tr>",
    re.S,
)


def _raise_if_cancelled(cancellation_token=None) -> None:
    if cancellation_token is not None:
        cancellation_token.raise_if_cancelled()


def _call_with_cancellation(fn, *args, cancellation_token=None, **kwargs):
    if cancellation_token is not None:
        kwargs["cancellation_token"] = cancellation_token
    return fn(*args, **kwargs)


def _cancellable_values(values, cancellation_token=None):
    for value in values:
        _raise_if_cancelled(cancellation_token)
        yield value


def _qfii_payloads_for_sync(quarter_key, cancellation_token=None):
    return _call_with_cancellation(
        _candidate_qfii_payloads,
        quarter_key,
        cancellation_token=cancellation_token,
    )


def _read_limited_response(response, *, max_bytes: int | None = None, cancellation_token=None) -> bytes:
    _raise_if_cancelled(cancellation_token)
    max_bytes = _MAX_RESPONSE_BYTES if max_bytes is None else int(max_bytes)
    payload = response.read(max_bytes + 1)
    _raise_if_cancelled(cancellation_token)
    if len(payload) > max_bytes:
        raise UserFacingTaskError("基金持仓接口返回过大，已停止处理", f"response bytes exceed {max_bytes}")
    return payload


def _fetch_text(url: str, *, params: dict | None = None, referer: str = "", cancellation_token=None) -> str:
    _raise_if_cancelled(cancellation_token)
    if params:
        url = f"{url}?{urllib.parse.urlencode(params)}"
    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": _USER_AGENT,
            "Referer": referer or url,
            "Connection": "close",
        },
    )
    try:
        response = urlopen_https(request, timeout=15)
        try:
            payload = _call_with_cancellation(
                _read_limited_response,
                response,
                cancellation_token=cancellation_token,
            )
            _raise_if_cancelled(cancellation_token)
            return payload.decode("utf-8", errors="ignore")
        finally:
            with suppress(AttributeError, OSError, RuntimeError, TypeError):
                response.close()
    except (OSError, RuntimeError, TimeoutError, ValueError) as exc:
        raise UserFacingTaskError("抓取基金持仓数据失败，请稍后重试", str(exc)) from exc


def _fetch_json(url: str, *, params: dict, referer: str = "", cancellation_token=None) -> dict:
    text = _call_with_cancellation(
        _fetch_text,
        url,
        params=params,
        referer=referer,
        cancellation_token=cancellation_token,
    )
    _raise_if_cancelled(cancellation_token)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise UserFacingTaskError("基金持仓接口返回异常，稍后再试", f"JSON 解析失败: {exc}") from exc
    _raise_if_cancelled(cancellation_token)
    return payload


def _coerce_float(value) -> float:
    text = str(value or "").strip().replace(",", "").replace("%", "")
    if not text or text in {"--", "-"}:
        return 0.0
    try:
        return float(text)
    except (TypeError, ValueError):
        return 0.0


def _clean_js_html(raw_text: str) -> str:
    match = re.search(r'content:"(?P<html>.*)",arryear:', raw_text, re.S)
    if not match:
        raise UserFacingTaskError("睿远持仓接口返回异常，未找到内容块", "FundArchivesDatas.aspx 未匹配到 content")
    html_text = match.group("html")
    html_text = (
        html_text.replace("\\r", "").replace("\\n", "").replace("\\t", "").replace("\\/", "/").replace('\\"', '"')
    )
    return html.unescape(html_text)


def _parse_ruiyuan_sections(raw_text: str, *, cancellation_token=None) -> dict[str, dict]:
    _raise_if_cancelled(cancellation_token)
    html_text = _clean_js_html(raw_text)
    quarter_payloads: dict[str, dict] = {}
    for section in _RUIYUAN_SECTION_RE.finditer(html_text):
        _raise_if_cancelled(cancellation_token)
        header = section.group("header")
        table_html = section.group("table")
        date_match = _RUIYUAN_DATE_RE.search(header)
        quarter_match = _RUIYUAN_QUARTER_RE.search(header)
        if not date_match or not quarter_match:
            continue

        quarter_key = f"{int(quarter_match.group(1)):04d}Q{int(quarter_match.group(2))}"
        end_date = date_match.group(1)
        rows = []
        for row_match in _RUIYUAN_ROW_RE.finditer(table_html):
            _raise_if_cancelled(cancellation_token)
            stock_code = str(row_match.group("code") or "").strip()
            if not stock_code:
                continue
            rows.append(
                {
                    "rank_no": int(row_match.group("rank")),
                    "stock_code": stock_code,
                    "stock_name": html.unescape(str(row_match.group("name") or "").strip()),
                    "net_value_ratio_pct": _coerce_float(row_match.group("ratio")),
                    "hold_num_shares": _coerce_float(row_match.group("shares")) * 10000.0,
                    "hold_market_value_cny": _coerce_float(row_match.group("market")) * 10000.0,
                    "latest_source_update": end_date,
                }
            )

        quarter_payloads[quarter_key] = {
            "quarter_key": quarter_key,
            "end_date": end_date,
            "raw_rows": rows,
        }

    _raise_if_cancelled(cancellation_token)
    return quarter_payloads


def _fetch_ruiyuan_year(year: int, *, cancellation_token=None) -> dict[str, dict]:
    raw_text = _call_with_cancellation(
        _fetch_text,
        _RUIYUAN_API_URL,
        params={
            "type": "jjcc",
            "code": SUBJECT_RUIYUAN["subject_code"],
            "topline": "500",
            "year": str(year),
            "month": "12",
        },
        referer=f"https://fund.eastmoney.com/{SUBJECT_RUIYUAN['subject_code']}.html",
        cancellation_token=cancellation_token,
    )
    _raise_if_cancelled(cancellation_token)
    return _call_with_cancellation(
        _parse_ruiyuan_sections,
        raw_text,
        cancellation_token=cancellation_token,
    )


def _candidate_ruiyuan_payloads(
    target_quarter_key: str | None = None,
    *,
    cancellation_token=None,
) -> tuple[dict[str, dict], str]:
    quarter_payloads: dict[str, dict] = {}

    if target_quarter_key:
        resolved = normalize_quarter_key(target_quarter_key)
        compare_quarter = get_compare_quarter_key(SUBJECT_RUIYUAN["subject_type"], resolved)
        target_years = {quarter_parts(resolved)[0], quarter_parts(compare_quarter)[0]}
        for year in sorted(target_years, reverse=True):
            _raise_if_cancelled(cancellation_token)
            quarter_payloads.update(
                _call_with_cancellation(
                    _fetch_ruiyuan_year,
                    year,
                    cancellation_token=cancellation_token,
                )
            )
        if resolved not in quarter_payloads:
            raise UserFacingTaskError(
                f"睿远 {resolved} 暂无已披露持仓数据",
                f"睿远指定季度未披露: {resolved}",
            )
        return quarter_payloads, resolved

    natural_current = quarter_key_for_date(date.today())
    compare_quarter = get_compare_quarter_key(SUBJECT_RUIYUAN["subject_type"], natural_current)
    target_quarters = (natural_current, compare_quarter)
    target_years = sorted({quarter_parts(quarter_key)[0] for quarter_key in target_quarters}, reverse=True)
    fetched_payloads: dict[str, dict] = {}
    for year in target_years:
        _raise_if_cancelled(cancellation_token)
        fetched_payloads.update(
            _call_with_cancellation(
                _fetch_ruiyuan_year,
                year,
                cancellation_token=cancellation_token,
            )
        )

    for quarter_key in target_quarters:
        _raise_if_cancelled(cancellation_token)
        quarter_payloads[quarter_key] = fetched_payloads.get(quarter_key) or _empty_quarter_payload(quarter_key)

    return quarter_payloads, natural_current


def _fetch_qfii_quarter(quarter_key: str, *, cancellation_token=None) -> dict:
    norm_quarter = normalize_quarter_key(quarter_key)
    end_date = quarter_end_date_text(norm_quarter)
    page_number = 1
    raw_rows: list[dict] = []
    while True:
        _raise_if_cancelled(cancellation_token)
        payload = _call_with_cancellation(
            _fetch_json,
            _QFII_API_URL,
            params={
                "sortColumns": "UPDATE_DATE,SECURITY_CODE",
                "sortTypes": "-1,-1",
                "pageSize": str(_QFII_PAGE_SIZE),
                "pageNumber": str(page_number),
                "reportName": "RPT_F10_EH_FREEHOLDERS",
                "columns": "ALL",
                "source": "WEB",
                "client": "WEB",
                "filter": f"(END_DATE='{end_date}')(HOLDER_TYPE=\"QFII\")",
            },
            referer="https://data.eastmoney.com/",
            cancellation_token=cancellation_token,
        )
        _raise_if_cancelled(cancellation_token)
        result = payload.get("result") or {}
        try:
            pages = int(result.get("pages") or 0)
        except (TypeError, ValueError) as exc:
            raise UserFacingTaskError("基金持仓接口返回异常，稍后再试", "QFII pages 不是整数") from exc
        if pages < 0 or pages > _QFII_MAX_PAGES:
            raise UserFacingTaskError("基金持仓接口返回过多分页，已停止处理", f"QFII pages={pages}")
        data = result.get("data") or []
        if not isinstance(data, list):
            raise UserFacingTaskError("基金持仓接口返回异常，稍后再试", "QFII data 不是列表")
        if not data:
            break
        if len(raw_rows) + len(data) > _QFII_MAX_ROWS:
            raise UserFacingTaskError("基金持仓接口返回过多记录，已停止处理", f"QFII rows>{_QFII_MAX_ROWS}")
        raw_rows.extend(dict(row) for row in data)
        _raise_if_cancelled(cancellation_token)
        if page_number >= pages:
            break
        page_number += 1

    return {
        "quarter_key": norm_quarter,
        "end_date": end_date,
        "raw_rows": raw_rows,
    }


def _empty_quarter_payload(quarter_key: str) -> dict:
    norm_quarter = normalize_quarter_key(quarter_key)
    return {
        "quarter_key": norm_quarter,
        "end_date": quarter_end_date_text(norm_quarter),
        "raw_rows": [],
    }


def _qfii_quarter_disclosure_deadline(quarter_key: str) -> date:
    year, quarter = quarter_parts(normalize_quarter_key(quarter_key))
    if quarter == 4:
        return date(year + 1, 4, 30)
    month, day = _QFII_DISCLOSURE_DEADLINE_BY_QUARTER[quarter]
    return date(year, month, day)


def _qfii_quarter_disclosure_ready(quarter_key: str, as_of: date | None = None) -> bool:
    check_date = as_of or date.today()
    return check_date > _qfii_quarter_disclosure_deadline(quarter_key)


def _candidate_qfii_payloads(
    target_quarter_key: str | None = None,
    *,
    cancellation_token=None,
) -> tuple[dict[str, dict], str]:
    quarter_payloads: dict[str, dict] = {}

    if target_quarter_key:
        resolved = normalize_quarter_key(target_quarter_key)
        quarter_payloads[resolved] = _call_with_cancellation(
            _fetch_qfii_quarter,
            resolved,
            cancellation_token=cancellation_token,
        )
        if not quarter_payloads[resolved]["raw_rows"]:
            raise UserFacingTaskError(f"QFII {resolved} 暂无已披露持仓数据", f"QFII 指定季度无数据: {resolved}")
        compare_quarter = get_compare_quarter_key(SUBJECT_QFII["subject_type"], resolved)
        _raise_if_cancelled(cancellation_token)
        quarter_payloads[compare_quarter] = _call_with_cancellation(
            _fetch_qfii_quarter,
            compare_quarter,
            cancellation_token=cancellation_token,
        )
        return quarter_payloads, resolved

    natural_current = quarter_key_for_date(date.today())
    compare_quarter = get_compare_quarter_key(SUBJECT_QFII["subject_type"], natural_current)
    for quarter_key in (natural_current, compare_quarter):
        _raise_if_cancelled(cancellation_token)
        quarter_payloads[quarter_key] = _call_with_cancellation(
            _fetch_qfii_quarter,
            quarter_key,
            cancellation_token=cancellation_token,
        )
    return quarter_payloads, natural_current


class FundHoldingsSyncService:
    def __init__(self, store=None):
        self._store = store or fund_holdings_store

    def sync_qfii(self, quarter_key: str | None = None, *, cancellation_token=None) -> dict:
        quarter_payloads, resolved_quarter = _qfii_payloads_for_sync(quarter_key, cancellation_token)
        skipped_quarters: dict[str, str] = {}
        available_payloads: dict[str, dict] = {}
        quarter_payloads_to_store: dict[str, dict] = {}
        for key, value in _cancellable_values(quarter_payloads.items(), cancellation_token):
            if not value.get("raw_rows"):
                continue
            if not quarter_key and not _qfii_quarter_disclosure_ready(key):
                skipped_quarters[key] = _qfii_quarter_disclosure_deadline(key).isoformat()
                quarter_payloads_to_store[key] = _empty_quarter_payload(key)
                continue
            available_payloads[key] = value
            quarter_payloads_to_store[key] = value
        for payload in _cancellable_values(available_payloads.values(), cancellation_token):
            payload["snapshots"] = build_qfii_snapshots(
                payload["raw_rows"],
                SUBJECT_QFII,
                payload["quarter_key"],
                payload["end_date"],
            )
        message = (
            f"QFII 已检查 {resolved_quarter} / {get_compare_quarter_key(SUBJECT_QFII['subject_type'], resolved_quarter)}"
            if not quarter_key
            else f"QFII 指定季度 {resolved_quarter} 已同步"
        )
        if not quarter_key:
            message += "（固定抓取当季与上一季度）"
            if skipped_quarters:
                skipped = "、".join(sorted(skipped_quarters, key=quarter_sort_value, reverse=True))
                message += f"；已跳过未到完整披露窗口的季度：{skipped}"

        _raise_if_cancelled(cancellation_token)
        self._store.replace_qfii_quarters(
            SUBJECT_QFII,
            quarter_payloads_to_store,
            sync_scope="current" if not quarter_key else "specific",
            requested_quarter_key=quarter_key,
            resolved_quarter_key=resolved_quarter,
            message=message,
            payload_meta={
                "checked_quarters": list(quarter_payloads.keys()),
                "available_quarters": sorted(available_payloads.keys(), key=quarter_sort_value, reverse=True),
                "skipped_quarters": skipped_quarters,
                "raw_counts": {key: len(value.get("raw_rows") or []) for key, value in quarter_payloads.items()},
            },
        )
        return {
            "subject_code": SUBJECT_QFII["subject_code"],
            "subject_name": SUBJECT_QFII["subject_name"],
            "resolved_quarter_key": resolved_quarter,
            "message": message,
            "raw_count": len((available_payloads.get(resolved_quarter) or {}).get("raw_rows") or []),
            "snapshot_count": len((available_payloads.get(resolved_quarter) or {}).get("snapshots") or []),
        }

    def sync_ruiyuan(self, quarter_key: str | None = None, *, cancellation_token=None) -> dict:
        quarter_payloads, resolved_quarter = _call_with_cancellation(
            _candidate_ruiyuan_payloads,
            quarter_key,
            cancellation_token=cancellation_token,
        )
        available_payloads = {key: value for key, value in quarter_payloads.items() if value.get("raw_rows")}
        for payload in _cancellable_values(available_payloads.values(), cancellation_token):
            payload["snapshots"] = build_ruiyuan_snapshots(
                payload["raw_rows"],
                SUBJECT_RUIYUAN,
                payload["quarter_key"],
                payload["end_date"],
            )

        message = (
            f"睿远成长价值混合A 已检查 {resolved_quarter} / {get_compare_quarter_key(SUBJECT_RUIYUAN['subject_type'], resolved_quarter)}"
            if not quarter_key
            else f"睿远成长价值混合A 指定季度 {resolved_quarter} 已同步"
        )
        if not quarter_key:
            message += "（固定抓取当季与上一季度）"

        _raise_if_cancelled(cancellation_token)
        self._store.replace_ruiyuan_quarters(
            SUBJECT_RUIYUAN,
            available_payloads,
            sync_scope="current" if not quarter_key else "specific",
            requested_quarter_key=quarter_key,
            resolved_quarter_key=resolved_quarter,
            message=message,
            payload_meta={
                "checked_quarters": list(quarter_payloads.keys()),
                "available_quarters": sorted(available_payloads.keys(), key=quarter_sort_value, reverse=True),
                "raw_counts": {key: len(value.get("raw_rows") or []) for key, value in quarter_payloads.items()},
            },
        )
        return {
            "subject_code": SUBJECT_RUIYUAN["subject_code"],
            "subject_name": SUBJECT_RUIYUAN["subject_name"],
            "resolved_quarter_key": resolved_quarter,
            "message": message,
            "raw_count": len((available_payloads.get(resolved_quarter) or {}).get("raw_rows") or []),
            "snapshot_count": len((available_payloads.get(resolved_quarter) or {}).get("snapshots") or []),
        }

    def sync_latest_all(self, *, cancellation_token=None) -> dict:
        qfii_result = _call_with_cancellation(
            self.sync_qfii,
            cancellation_token=cancellation_token,
        )
        _raise_if_cancelled(cancellation_token)
        ruiyuan_result = _call_with_cancellation(
            self.sync_ruiyuan,
            cancellation_token=cancellation_token,
        )
        _raise_if_cancelled(cancellation_token)
        return {
            "subject_code": "ALL",
            "message": f"{qfii_result['message']}；{ruiyuan_result['message']}",
            "results": [qfii_result, ruiyuan_result],
        }


fund_holdings_sync_service = FundHoldingsSyncService()
