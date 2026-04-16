# -*- coding: utf-8 -*-
"""亚洲寡头 250 日 K 线数据拉取器。

从 industry_dict.py 自动读取 VANGUARD_TICKERS，
筛选出亚洲市场标的（.TW / .KS / .T / .HK），
用 yfinance 拉取 250 个交易日的 OHLCV 日线数据，
输出 JSON 文件供前端看板渲染 K 线图。

用法：
    python asian_kline_fetcher.py              # 拉取全部亚洲标的
    python asian_kline_fetcher.py --market JP   # 只拉日本
    python asian_kline_fetcher.py --ticker 8035.T  # 只拉单只
"""

import argparse
import importlib
import json
import logging
import os
import re
import sys
import time
import uuid
from datetime import datetime

import yfinance as yf

from vcp.fetchers.yf_session import build_yf_session

# Why: 行业字典暂未收入本项目工程，通过项目根目录向上推导兄弟目录，避免硬编码特定机器的绝对路径
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PIPELINE_DIR = os.path.join(os.path.dirname(_PROJECT_ROOT), "每日战报", "每日战报")
if os.path.isdir(_PIPELINE_DIR) and _PIPELINE_DIR not in sys.path:
    sys.path.insert(0, _PIPELINE_DIR)

from industry_dict import OLIGARCH_DICT, VANGUARD_TICKERS  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
)

# ===================================================================
# 亚洲市场后缀映射（与 p6_mapper.py 保持一致）
# ===================================================================
ASIAN_MARKET_MAP = {
    ".TW": "台湾",
    ".TWO": "台湾上柜",
    ".KS": "韩国",
    ".T": "日本",
    ".HK": "香港",
}

# Why: 命令行参数用的简写 → 后缀映射
MARKET_SHORTCUT = {
    "TW": (".TW", ".TWO"),
    "KR": (".KS",),
    "JP": (".T",),
    "HK": (".HK",),
}

# Why: 亚洲页需要优先跟踪本地挂牌代码，避免被 ADR/海外替代代码稀释。
# 这里仅覆盖“亚洲看板”的取数池，不影响每日战报主工程里的全局 ticker 定义。
ASIAN_LOCAL_TICKER_OVERRIDES = {
    "TSMC": "2330.TW",
}

_ALNUM_RE = re.compile(r"[a-z0-9]+")


def _ensure_industry_mappings_loaded() -> None:
    global OLIGARCH_DICT, VANGUARD_TICKERS

    if OLIGARCH_DICT and VANGUARD_TICKERS:
        return

    try:
        industry_module = importlib.import_module("industry_dict")
    except (AttributeError, ImportError, OSError, RuntimeError, TypeError, ValueError):
        return

    real_oligarch = getattr(industry_module, "OLIGARCH_DICT", None)
    real_tickers = getattr(industry_module, "VANGUARD_TICKERS", None)
    if real_oligarch:
        OLIGARCH_DICT = real_oligarch
    if real_tickers:
        VANGUARD_TICKERS = real_tickers


def _get_asian_source_tickers() -> dict[str, str]:
    _ensure_industry_mappings_loaded()
    tickers = dict(VANGUARD_TICKERS)
    tickers.update(ASIAN_LOCAL_TICKER_OVERRIDES)
    return tickers


def _get_market_suffix(ticker: str) -> str | None:
    """提取 ticker 的市场后缀，无后缀返回 None。"""
    for suffix in ASIAN_MARKET_MAP:
        if ticker.endswith(suffix):
            return suffix
    return None


def _get_market_name(ticker: str) -> str:
    """获取 ticker 对应的市场中文名。"""
    suffix = _get_market_suffix(ticker)
    return ASIAN_MARKET_MAP.get(suffix, "未知")


def filter_asian_tickers(market_filter: str | None = None) -> dict[str, str]:
    """从 VANGUARD_TICKERS 中筛选亚洲市场标的。

    Args:
        market_filter: 可选市场简写（TW/KR/JP/HK），None 表示全部亚洲
    Returns:
        {公司名: ticker} 字典
    """
    result = {}

    # Why: 确定要筛选的后缀范围
    if market_filter:
        target_suffixes = MARKET_SHORTCUT.get(market_filter.upper())
        if not target_suffixes:
            logging.error(f"未知市场简写: {market_filter}，支持: TW/KR/JP/HK")
            return {}
    else:
        target_suffixes = tuple(ASIAN_MARKET_MAP.keys())

    for name, ticker in _get_asian_source_tickers().items():
        if any(ticker.endswith(s) for s in target_suffixes):
            result[name] = ticker

    return result


def _find_track(ticker: str) -> str:
    """反查 ticker 所属的赛道名称。"""
    _ensure_industry_mappings_loaded()
    # Why: 从 VANGUARD_TICKERS 找到公司名，再从 OLIGARCH_DICT 反查赛道
    company_name = None
    for name, tk in _get_asian_source_tickers().items():
        if tk == ticker:
            company_name = name
            break

    if not company_name:
        return "未知赛道"

    def _normalize_text(value: str) -> str:
        return "".join(_ALNUM_RE.findall(str(value or "").lower()))

    def _build_acronym(value: str) -> str:
        tokens = _ALNUM_RE.findall(str(value or ""))
        if len(tokens) <= 1:
            return ""
        return "".join(token[0] for token in tokens).lower()

    name_lower = company_name.lower()
    name_normalized = _normalize_text(company_name)
    ticker_prefix = str(ticker or "").split(".")[0].lower()
    for track, companies in OLIGARCH_DICT.items():
        for comp in companies:
            comp_lower = comp.lower()
            # Why: 三层匹配——完整包含 > 英文前缀 > 公司名是赛道成员子串
            if name_lower in comp_lower or comp_lower.startswith(name_lower):
                return track
            eng_prefix = comp.split("(")[0].strip()
            eng_prefix_lower = eng_prefix.lower()
            if eng_prefix_lower == name_lower:
                return track
            if name_normalized and _normalize_text(comp) == name_normalized:
                return track
            if name_normalized and _normalize_text(eng_prefix) == name_normalized:
                return track
            if name_lower and _build_acronym(comp) == name_lower:
                return track
            if name_lower and _build_acronym(eng_prefix) == name_lower:
                return track
            if ticker_prefix and _build_acronym(comp) == ticker_prefix:
                return track
            if ticker_prefix and _build_acronym(eng_prefix) == ticker_prefix:
                return track
    return "未知赛道"


def _resolve_cache_output_dir(output_dir: str | None = None) -> str:
    """解析亚洲 K 线缓存输出目录。"""
    if output_dir:
        return output_dir
    return os.path.join(
        os.path.dirname(os.path.abspath(__file__)),
        "..", "..", "data", "Cache"
    )


def _latest_cache_path(output_dir: str | None = None) -> str:
    return os.path.join(_resolve_cache_output_dir(output_dir), "asian_klines_latest.json")


def _rows_to_map(rows: list[dict] | None) -> dict[str, dict]:
    """按 ticker 建立唯一索引，便于做缺票校验与旧缓存回填。"""
    row_map: dict[str, dict] = {}
    for row in rows or []:
        ticker = str((row or {}).get("ticker", "")).strip()
        if ticker and ticker not in row_map:
            row_map[ticker] = row
    return row_map


def _load_cached_row_map(output_dir: str | None = None) -> dict[str, dict]:
    cache_path = _latest_cache_path(output_dir)
    if not os.path.exists(cache_path):
        return {}
    with open(cache_path, "r", encoding="utf-8") as handle:
        raw = json.load(handle)
    return _rows_to_map(raw.get("stocks", []))


def _build_sync_target_map(
    market_filter: str | None = None,
    single_ticker: str | None = None,
) -> dict[str, str]:
    """构建严格同步时的目标股票池。"""
    if single_ticker:
        single_ticker = str(single_ticker).strip()
        if not single_ticker:
            return {}

        for name, ticker in _get_asian_source_tickers().items():
            if ticker == single_ticker:
                return {name: single_ticker}
        return {single_ticker: single_ticker}

    return filter_asian_tickers(market_filter)


def fetch_single_kline(
    name: str,
    ticker: str,
    period: str = "1y",
    use_cf_proxy: bool = True,
    session=None,
) -> dict | None:
    """拉取单只标的的 K 线数据。

    Returns:
        {
            "name": "TEL",
            "ticker": "8035.T",
            "market": "日本",
            "track": "晶圆制造与材料设备",
            "currency": "JPY",
            "klines": [
                {"date": "2025-04-01", "open": 100, "high": 105, "low": 98, "close": 103, "volume": 12345},
                ...
            ]
        }
    """
    try:
        yf_session = session or build_yf_session(use_cf_proxy)
        t = yf.Ticker(ticker, session=yf_session)
        hist = t.history(period=period)

        if hist.empty:
            logging.warning(f"⚠️ {name}({ticker}): 无数据")
            return None

        # Why: 获取货币单位，方便前端显示
        info = t.fast_info
        currency = getattr(info, "currency", "N/A") if info else "N/A"

        # 向量化构建 K 线数据（比 iterrows 快 5-10 倍）
        hist_reset = hist.reset_index()
        hist_reset["date"] = hist_reset.iloc[:, 0].dt.strftime("%Y-%m-%d")
        klines = [
            {
                "date": row["date"],
                "open": round(float(row["Open"]), 2),
                "high": round(float(row["High"]), 2),
                "low": round(float(row["Low"]), 2),
                "close": round(float(row["Close"]), 2),
                "volume": int(row["Volume"]),
            }
            for row in hist_reset.to_dict("records")
        ]

        return {
            "name": name,
            "ticker": ticker,
            "market": _get_market_name(ticker),
            "track": _find_track(ticker),
            "currency": currency,
            "kline_count": len(klines),
            "klines": klines,
        }

    except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
        logging.error(f"❌ {name}({ticker}): 拉取失败 — {e}")
        return None


def fetch_all_asian_klines(
    market_filter: str | None = None,
    single_ticker: str | None = None,
    max_workers: int = 6,
    period: str = "1y",
    use_cf_proxy: bool = True,
) -> list[dict]:
    """并发拉取亚洲寡头 K 线数据。

    Args:
        market_filter: 市场筛选（TW/KR/JP/HK）
        single_ticker: 只拉单只
        max_workers: 并发线程数（别太高，Yahoo 会限速）
        period: yfinance 的 period 参数，默认 1y（约 250 个交易日）
    """
    # Why: 支持单只调试模式
    if single_ticker:
        name = None
        for n, tk in _get_asian_source_tickers().items():
            if tk == single_ticker:
                name = n
                break
        if not name:
            logging.error(f"ticker {single_ticker} 不在 VANGUARD_TICKERS 中")
            return []
        tickers = {name: single_ticker}
    else:
        tickers = filter_asian_tickers(market_filter)

    if not tickers:
        logging.error("没有找到符合条件的亚洲标的")
        return []

    logging.info(
        f"📊 开始拉取 {len(tickers)} 只亚洲标的的 K 线数据 "
        f"(period={period}, workers={max_workers})"
    )

    results = []
    failed = []
    yf_session = build_yf_session(use_cf_proxy)

    for name, ticker in tickers.items():
        time.sleep(0.3)
        try:
            data = fetch_single_kline(
                name,
                ticker,
                period=period,
                use_cf_proxy=use_cf_proxy,
                session=yf_session,
            )
            if data:
                results.append(data)
                logging.info(
                    f"  ✅ {name}({ticker}) [{data['market']}]: "
                    f"{data['kline_count']} 根K线"
                )
            else:
                failed.append(f"{name}({ticker})")
        except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as e:
            failed.append(f"{name}({ticker})")
            logging.error(f"  ❌ {name}({ticker}): {e}")

    # Why: 按市场分组排序，便于前端渲染
    results.sort(key=lambda x: (x["market"], x["name"]))

    logging.info(
        f"\n📊 拉取完成: 成功 {len(results)}/{len(tickers)}"
        + (f"，失败 {len(failed)}: {', '.join(failed)}" if failed else "")
    )

    return results


def sync_asian_kline_cache(
    market_filter: str | None = None,
    single_ticker: str | None = None,
    max_workers: int = 6,
    period: str = "1y",
    use_cf_proxy: bool = True,
    output_dir: str | None = None,
) -> tuple[bool, str, dict]:
    """严格同步亚洲 K 线缓存。

    统一规则：
    1. 先按目标池做全量拉取；
    2. 对缺失票做单票补抓；
    3. 仍缺失时尝试从旧缓存回填；
    4. 若最终仍缺票，则拒绝覆盖 latest 缓存。
    """
    target_map = _build_sync_target_map(market_filter=market_filter, single_ticker=single_ticker)
    if not target_map:
        message = "没有找到符合条件的亚洲标的"
        return False, message, {
            "target_count": 0,
            "written_count": 0,
            "single_recovered": [],
            "reused": [],
            "missing": [],
        }

    ticker_to_name = {ticker: name for name, ticker in target_map.items()}
    target_tickers = set(target_map.values())
    output_dir = _resolve_cache_output_dir(output_dir)

    data = fetch_all_asian_klines(
        market_filter=market_filter,
        single_ticker=single_ticker,
        max_workers=max_workers,
        period=period,
        use_cf_proxy=use_cf_proxy,
    )
    if not data:
        message = "亚洲 K 线缓存全量拉取失败"
        return False, message, {
            "target_count": len(target_tickers),
            "written_count": 0,
            "single_recovered": [],
            "reused": [],
            "missing": sorted(target_tickers),
        }

    row_map = _rows_to_map(data)
    missing = sorted(target_tickers - set(row_map.keys()))
    single_recovered: list[str] = []

    if missing:
        logging.warning(f"⚠️ 全量抓取缺失 {len(missing)} 只，开始单票补抓: {missing}")
        rescue_session = build_yf_session(use_cf_proxy)
        for ticker in list(missing):
            name = ticker_to_name.get(ticker, ticker)
            try:
                one = fetch_single_kline(
                    name,
                    ticker,
                    period=period,
                    use_cf_proxy=use_cf_proxy,
                    session=rescue_session,
                )
                if one:
                    row_map[ticker] = one
                    single_recovered.append(ticker)
            except (AttributeError, KeyError, OSError, RuntimeError, TypeError, ValueError) as exc:
                logging.warning(f"⚠️ 单票补抓失败 {ticker}: {exc}")
        missing = sorted(target_tickers - set(row_map.keys()))

    reused: list[str] = []
    if missing:
        try:
            old_map = _load_cached_row_map(output_dir)
            for ticker in list(missing):
                if ticker in old_map:
                    row_map[ticker] = old_map[ticker]
                    reused.append(ticker)
            if reused:
                logging.warning(f"⚠️ 已从旧缓存回填 {len(reused)} 只: {sorted(reused)}")
            missing = sorted(target_tickers - set(row_map.keys()))
        except (FileNotFoundError, PermissionError, OSError, TypeError, ValueError, json.JSONDecodeError) as exc:
            logging.warning(f"⚠️ 旧缓存回填失败: {exc}")

    if missing:
        message = (
            f"亚洲 K 线缓存同步失败，仍缺失 {len(missing)} 只"
            f"({', '.join(missing)})，未覆盖现有缓存"
        )
        return False, message, {
            "target_count": len(target_tickers),
            "written_count": 0,
            "single_recovered": single_recovered,
            "reused": reused,
            "missing": missing,
        }

    final_data = list(row_map.values())
    final_data.sort(key=lambda item: (item.get("market", ""), item.get("name", "")))
    save_kline_data(final_data, output_dir)

    parts = [f"亚洲 K 线缓存同步完成，共 {len(final_data)} 只"]
    if single_recovered:
        parts.append(f"单票补抓 {len(single_recovered)} 只")
    if reused:
        parts.append(f"旧缓存回填 {len(reused)} 只")
    message = "，".join(parts)
    return True, message, {
        "target_count": len(target_tickers),
        "written_count": len(final_data),
        "single_recovered": single_recovered,
        "reused": reused,
        "missing": [],
    }


def save_kline_data(data: list[dict], output_dir: str | None = None) -> str:
    """保存 K 线数据到 JSON 文件。"""
    output_dir = _resolve_cache_output_dir(output_dir)
    os.makedirs(output_dir, exist_ok=True)

    now_str = datetime.now().strftime("%Y%m%d_%H%M")
    filepath = os.path.join(output_dir, f"asian_klines_{now_str}.json")

    # Why: 生成元数据摘要，前端可以读 meta 做标题/更新时间显示
    meta = {
        "generated_at": datetime.now().isoformat(),
        "total_stocks": len(data),
        "markets": {},
    }
    for item in data:
        market = item["market"]
        meta["markets"].setdefault(market, 0)
        meta["markets"][market] += 1

    output = {
        "meta": meta,
        "stocks": data,
    }

    tmp_filepath = f"{filepath}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    with open(tmp_filepath, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    os.replace(tmp_filepath, filepath)

    logging.info(f"💾 K 线数据已保存: {filepath} ({os.path.getsize(filepath) / 1024:.0f} KB)")

    # Why: 同时生成一份 latest.json 供看板前端固定路径读取
    latest_path = os.path.join(output_dir, "asian_klines_latest.json")
    tmp_latest_path = f"{latest_path}.{os.getpid()}.{uuid.uuid4().hex}.tmp"
    with open(tmp_latest_path, "w", encoding="utf-8") as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    os.replace(tmp_latest_path, latest_path)
    logging.info(f"💾 最新快照已更新: {latest_path}")

    return filepath


def main():
    parser = argparse.ArgumentParser(description="亚洲寡头 250 日 K 线数据拉取器")
    parser.add_argument(
        "--market",
        choices=["TW", "KR", "JP", "HK"],
        help="只拉指定市场（TW=台湾 KR=韩国 JP=日本 HK=香港）",
    )
    parser.add_argument(
        "--ticker",
        help="只拉单只标的（如 8035.T）",
    )
    parser.add_argument(
        "--period",
        default="1y",
        help="K 线周期（默认 1y ≈ 250 个交易日）",
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=6,
        help="并发线程数（默认 6，太高会被 Yahoo 限速）",
    )
    parser.add_argument(
        "--output-dir",
        help="输出目录（默认：亚洲寡头行情/data/）",
    )
    parser.add_argument(
        "--strict-sync",
        action="store_true",
        help="严格同步模式：缺票时拒绝覆盖 latest 缓存",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="只列出目标标的，不实际拉数据",
    )

    args = parser.parse_args()

    # Why: dry-run 模式方便确认标的列表是否正确
    if args.dry_run:
        tickers = filter_asian_tickers(args.market)
        print(f"\n📋 目标标的 ({len(tickers)} 只):\n")
        for name, ticker in sorted(tickers.items(), key=lambda x: x[1]):
            print(f"  {ticker:>12s}  {name:<20s}  [{_get_market_name(ticker)}] {_find_track(ticker)}")
        return

    if args.strict_sync:
        success, message, _report = sync_asian_kline_cache(
            market_filter=args.market,
            single_ticker=args.ticker,
            max_workers=args.workers,
            period=args.period,
            output_dir=args.output_dir,
        )
        if success:
            logging.info(message)
            return
        logging.error(message)
        raise SystemExit(1)

    data = fetch_all_asian_klines(
        market_filter=args.market,
        single_ticker=args.ticker,
        max_workers=args.workers,
        period=args.period,
    )

    if data:
        save_kline_data(data, args.output_dir)


if __name__ == "__main__":
    main()
