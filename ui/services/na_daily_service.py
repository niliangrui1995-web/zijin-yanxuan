# -*- coding: utf-8 -*-
from __future__ import annotations

import datetime
import glob
import json
import os
import re

from PyQt6.QtCore import QObject

from app.services.runtime_constants import CACHE_DIR
from app.services.ui_event_service import domain_events as event_bus
from core.json_cache import load_json_file, save_json_file
from core.logger import get_logger

log = get_logger(__name__)

NA_DAILY_CACHE_FILE = os.path.join(CACHE_DIR, "na_daily_latest.json")


class NADailyRefreshService(QObject):
    """Background parser/cache for the North America daily battle report."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._rows: list[dict] = []
        self._report_files: list[str] = []
        self._report_signature: tuple[str, ...] = ()
        self._last_result: dict = {}
        self.load_cache()

    @property
    def rows(self) -> list[dict]:
        return [dict(row) for row in self._rows]

    @property
    def report_files(self) -> list[str]:
        return list(self._report_files)

    @property
    def report_signature(self) -> tuple[str, ...]:
        return tuple(self._report_signature)

    def latest_result(self) -> dict:
        return dict(self._last_result or {})

    @staticmethod
    def cache_file() -> str:
        return NA_DAILY_CACHE_FILE

    @staticmethod
    def _project_root() -> str:
        return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

    def _get_na_daily_output_dir(self) -> str:
        return os.path.join(os.path.dirname(self._project_root()), "每日战报", "每日热点输出")

    @staticmethod
    def _parse_report_identity(fpath: str):
        basename = os.path.basename(fpath)
        match = re.search(r"战报_(\d{8})(\d{0,6})", basename)
        if match:
            report_date = match.group(1)
            time_part = match.group(2) or ""
            if time_part:
                padded_time = (time_part + "000000")[:6]
                report_dt = datetime.datetime.strptime(report_date + padded_time, "%Y%m%d%H%M%S")
            else:
                report_dt = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))
                report_date = report_dt.strftime("%Y%m%d")
        else:
            report_dt = datetime.datetime.fromtimestamp(os.path.getmtime(fpath))
            report_date = report_dt.strftime("%Y%m%d")

        report_ts = int(report_dt.strftime("%Y%m%d%H%M%S"))
        return report_date, report_ts, basename

    def _list_recent_report_files(self, limit: int = 5) -> list[str]:
        pattern = os.path.join(self._get_na_daily_output_dir(), "**", "战报_*.md")
        files = glob.glob(pattern, recursive=True)
        if not files:
            return []
        files.sort(key=lambda path: (self._parse_report_identity(path)[1], path))
        return files[-limit:]

    @staticmethod
    def _signature_for(report_files: list[str]) -> tuple[str, ...]:
        return tuple(f"{os.path.basename(path)}:{int(os.path.getmtime(path))}" for path in report_files)

    def load_cache(self) -> dict:
        try:
            payload = load_json_file(NA_DAILY_CACHE_FILE)
        except Exception:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        self._rows = list(payload.get("rows") or [])
        self._report_files = list(payload.get("report_files") or [])
        self._report_signature = tuple(payload.get("report_signature") or ())
        self._last_result = {
            "job_key": "na_daily",
            "status": "cache",
            "records": len(self._rows),
            "report_files": list(self._report_files),
            "report_signature": tuple(self._report_signature),
            "cache_file": NA_DAILY_CACHE_FILE,
        }
        return self.latest_result()

    def _save_cache(self, *, status: str) -> None:
        payload = {
            "saved_at": datetime.datetime.now().isoformat(timespec="seconds"),
            "status": str(status or "").strip(),
            "rows": self._rows,
            "report_files": self._report_files,
            "report_signature": list(self._report_signature),
            "latest_report_date": self._latest_report_date(),
        }
        save_json_file(NA_DAILY_CACHE_FILE, payload)

    def _latest_report_date(self) -> str:
        dates = [
            str(row.get("日报时间", "") or "").strip()
            for row in self._rows
            if isinstance(row, dict) and str(row.get("日报时间", "") or "").strip()
        ]
        return max(dates) if dates else ""

    def refresh_full(self, *, emit_event: bool = True) -> dict:
        rows, report_files, report_signature = self._build_na_daily_rows()
        if not report_files:
            if not self._rows and not self._report_files:
                self.load_cache()
            self._last_result = {
                "job_key": "na_daily_full",
                "status": "skipped",
                "message": "no report files",
                "records": len(self._rows),
                "report_files": list(self._report_files),
                "report_signature": tuple(self._report_signature),
                "cache_file": NA_DAILY_CACHE_FILE,
            }
            return self.latest_result()

        self._rows = list(rows or [])
        self._report_files = list(report_files or [])
        self._report_signature = tuple(report_signature or ())
        self._save_cache(status="full")
        self._last_result = {
            "job_key": "na_daily_full",
            "status": "success",
            "records": len(self._rows),
            "report_files": list(self._report_files),
            "report_signature": tuple(self._report_signature),
            "cache_file": NA_DAILY_CACHE_FILE,
        }
        if emit_event:
            event_bus.sig_na_daily_updated.emit()
        return self.latest_result()

    def refresh_incremental(self, *, emit_event: bool = True) -> dict:
        report_files = self._list_recent_report_files(limit=5)
        if not report_files:
            self._last_result = {
                "job_key": "na_daily_incremental",
                "status": "skipped",
                "message": "no report files",
                "records": len(self._rows),
            }
            return self.latest_result()

        report_signature = self._signature_for(report_files)
        if not self._report_signature:
            self.load_cache()
        if tuple(self._report_signature) == tuple(report_signature):
            self._last_result = {
                "job_key": "na_daily_incremental",
                "status": "skipped",
                "message": "unchanged",
                "records": len(self._rows),
            }
            return self.latest_result()

        result = self.refresh_full(emit_event=emit_event)
        result["job_key"] = "na_daily_incremental"
        result["message"] = "updated"
        self._last_result = dict(result)
        return self.latest_result()

    def _load_report_payload(self, fpath: str):
        json_path = os.path.splitext(fpath)[0] + ".json"
        stocks = []
        rec_map = {}
        parsed_from_json = False

        if os.path.exists(json_path):
            try:
                with open(json_path, "r", encoding="utf-8") as f:
                    data = json.load(f)

                for track in data.get("sniper_tables", []):
                    raw_industry = track.get("track_name", "未知赛道")
                    industry = re.split(r"[（(]", raw_industry)[0].strip()
                    industry = re.sub(r"^赛道[A-Za-z0-9]+[：:\s]*", "", industry)

                    for target in track.get("targets", []):
                        stocks.append(
                            {
                                "行业": industry,
                                "名称": target.get("name", ""),
                                "代码": str(target.get("code", "") or "").strip(),
                                "近3月": target.get("chg_3m", ""),
                                "分位": target.get("percentile_250d", ""),
                                "量能": target.get("volume", ""),
                                "弹性": target.get("elasticity", ""),
                                "催化剂": target.get("catalyst", ""),
                                "风控": target.get("risk", ""),
                            }
                        )

                for advice in data.get("today_advice", []):
                    if isinstance(advice, dict) and advice.get("code"):
                        rec_map[str(advice["code"]).strip()] = {
                            "priority": advice.get("priority", ""),
                            "reason": advice.get("reason", ""),
                            "strategy": advice.get("strategy", ""),
                        }
                parsed_from_json = True
            except (
                json.JSONDecodeError,
                AttributeError,
                KeyError,
                OSError,
                RuntimeError,
                TypeError,
                ValueError,
            ) as exc:
                log.warning(f"解析北美战报 JSON 失败: {exc}")

        if not parsed_from_json:
            try:
                with open(fpath, "r", encoding="utf-8") as f:
                    content = f.read()
            except (FileNotFoundError, PermissionError, OSError) as exc:
                log.debug(f"[北美战报] 读取战报文件失败: {exc}")
                return [], {}
            stocks = self._parse_battle_report(content)
            rec_map = self._parse_recommendations(content)

        return stocks, rec_map

    def _build_na_daily_rows(self):
        report_files = self._list_recent_report_files(limit=5)
        if not report_files:
            return [], [], ()

        latest_rows = {}
        latest_recommendations = {}

        for fpath in report_files:
            report_date, report_ts, _ = self._parse_report_identity(fpath)
            stocks, rec_map = self._load_report_payload(fpath)

            for row_rank, stock in enumerate(stocks):
                code = str(stock.get("代码", "") or "").strip()
                if not code:
                    continue

                raw_elasticity = stock.get("弹性", "")
                clean_elasticity = re.split(r"[（(]", raw_elasticity)[0].strip() if raw_elasticity else ""
                clean_elasticity = "".join(c for c in clean_elasticity if c.isalnum() or "\u4e00" <= c <= "\u9fa5")

                raw_risk = str(stock.get("风控", ""))
                clean_risk = "".join(c for c in raw_risk if c in "\U0001f7e1\U0001f534\U0001f7e2")

                latest_rows[code] = {
                    "代码": code,
                    "名称": stock.get("名称", ""),
                    "现价": "--",
                    "涨幅%": "--",
                    "市值": "--",
                    "日报时间": report_date,
                    "细分板块": stock.get("行业", ""),
                    "股价弹性": clean_elasticity,
                    "催化剂": stock.get("催化剂", ""),
                    "风控": clean_risk,
                    "评级": "",
                    "_report_ts": report_ts,
                    "_report_row_rank": row_rank,
                }

            for code, reason_data in rec_map.items():
                latest_recommendations[str(code).strip()] = reason_data

        final_list = []
        for code, row_data in latest_rows.items():
            rec_data = latest_recommendations.get(code, {})
            row_data["评级"] = rec_data.get("priority", "")
            final_list.append(row_data)

        final_list.sort(
            key=lambda row: (
                -int(row.get("日报时间", "0") or 0),
                -int(row.get("_report_ts", 0) or 0),
                int(row.get("_report_row_rank", 0) or 0),
                str(row.get("代码", "") or ""),
            )
        )
        return final_list, report_files, self._signature_for(report_files)

    def _parse_battle_report(self, content: str) -> list:
        stocks = []
        section_match = re.search(r"##\s*二、标的狙击表(.*?)(?=##\s*三、|$)", content, re.DOTALL)
        if not section_match:
            return stocks

        section = section_match.group(1)
        industry_pattern = re.compile(r"###\s+(?:\U0001f534\s*|\U0001f7e1\s*|\U0001f7e2\s*)?(.+?)[\n\r]")
        industry_matches = list(industry_pattern.finditer(section))

        for idx, industry_match in enumerate(industry_matches):
            raw_industry = industry_match.group(1).strip()
            industry = re.split(r"[（(]", raw_industry)[0].strip()
            industry = re.sub(r"^赛道[A-Za-z0-9]+[：:\s]*", "", industry)
            start = industry_match.end()
            end = industry_matches[idx + 1].start() if idx + 1 < len(industry_matches) else len(section)
            block = section[start:end]
            table_rows = block.strip().split("\n")

            header_cells = []
            info_rows = []
            for row_text in table_rows:
                cells = [cell.strip() for cell in row_text.split("|")]
                if len(cells) >= 3 and cells[0] == "" and cells[-1] == "":
                    cells = cells[1:-1]
                elif not cells:
                    continue

                if "代码" in cells and ("标的" in cells or "名称" in cells):
                    header_cells = cells
                elif header_cells:
                    if all("---" in cell or not cell for cell in cells):
                        continue
                    info_rows.append(cells)

            if not header_cells:
                continue

            def get_col_idx(title_keywords):
                for header_idx, header in enumerate(header_cells):
                    for keyword in title_keywords:
                        if keyword in header:
                            return header_idx
                return -1

            idx_name = get_col_idx(["标的", "名称"])
            idx_code = get_col_idx(["代码"])
            idx_chg_3m = get_col_idx(["近3月"])
            idx_pct_250d = get_col_idx(["分位"])
            idx_elasticity = get_col_idx(["弹性"])
            idx_rs = get_col_idx(["RS"])
            idx_weekly = get_col_idx(["周线"])
            idx_catalyst = get_col_idx(["催化剂"])
            idx_risk = get_col_idx(["风控"])

            for row_data in info_rows:
                if len(row_data) < 3 or idx_code == -1:
                    continue

                def get_val(col_idx):
                    return row_data[col_idx].replace("**", "").strip() if 0 <= col_idx < len(row_data) else ""

                name = get_val(idx_name)
                code = get_val(idx_code)
                if not re.match(r"^\d{6}$", code):
                    continue

                stocks.append(
                    {
                        "行业": industry,
                        "名称": name,
                        "代码": code,
                        "近3月": get_val(idx_chg_3m),
                        "分位": get_val(idx_pct_250d),
                        "弹性": get_val(idx_elasticity),
                        "RS强度": get_val(idx_rs),
                        "周线趋势": get_val(idx_weekly),
                        "催化剂": get_val(idx_catalyst),
                        "风控": get_val(idx_risk),
                    }
                )
        return stocks

    @staticmethod
    def _parse_recommendations(content: str) -> dict:
        result = {}
        section_match = re.search(r"##\s*四、今日操作建议(.*?)(?=##\s*[一二三四五六七八九十]|$)", content, re.DOTALL)
        if not section_match:
            return result
        section = section_match.group(1)
        in_rec_table = False
        found_separator = False

        for line in section.split("\n"):
            stripped = line.strip()
            if not in_rec_table and stripped.startswith("|") and "优先级" in stripped:
                in_rec_table = True
                found_separator = False
                continue
            if in_rec_table:
                if "---" in stripped and stripped.startswith("|"):
                    found_separator = True
                    continue
                if not stripped.startswith("|") or not stripped:
                    break
                if found_separator:
                    code_match = re.search(r"(\d{6})", stripped)
                    if code_match:
                        raw_cells = [cell.strip() for cell in stripped.split("|")]
                        if len(raw_cells) >= 3 and raw_cells[0] == "" and raw_cells[-1] == "":
                            cells = raw_cells[1:-1]
                        else:
                            cells = [cell for cell in raw_cells if cell]

                        if len(cells) >= 3:
                            code = code_match.group(1)
                            priority = cells[0].replace("**", "").strip()
                            reason = cells[3].replace("**", "").strip() if len(cells) > 3 else ""
                            strategy = cells[4].replace("**", "").strip() if len(cells) > 4 else ""
                            result[code] = {"priority": priority, "reason": reason, "strategy": strategy}
        return result
