import json
import os
import sqlite3
import threading
from copy import deepcopy

from core.event_bus import event_bus
from core.logger import get_logger
from vcp.constants import SPECIAL_LATEST_DATA

log = get_logger(__name__)

class WatchlistViewModel:
    """
    负责维护“关注池”的幕后管家 (ViewModel)。
    为什么要有这个类？
    为了避免各个 UI 窗口各自为战，频繁且重复地去读取和写入 JSON 文件导致文件损坏或界面卡顿。
    所有的读写、校验、状态维护统一在这里进行。
    """
    _instance = None
    _SOURCE_TAG_PRIORITY = ("战报", "龙虎", "业绩", "大宗", "扫描", "手动")

    def __new__(cls, *args, **kwargs):
        if not cls._instance:
            cls._instance = super().__new__(cls)
            cls._instance._lock = threading.RLock()
            with cls._instance._lock:
                cls._instance._cache = {}
                cls._instance._load_data()

        return cls._instance

    def _load_data(self):
        """将数据加载到内存中，优先读SQLite数据库，不存在则兼容读JSON"""
        try:
            from core.data_store import DataStore
            data = DataStore().load_json("watchlist_special")

            if not data:
                # 兼容旧 JSON 迁移
                if os.path.exists(SPECIAL_LATEST_DATA):
                    with open(SPECIAL_LATEST_DATA, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if isinstance(data, dict):
                        DataStore().save_json("watchlist_special", data)
                        try:
                            # 迁移后标记旧文件，留作备用30天后自动清除
                            os.rename(SPECIAL_LATEST_DATA, SPECIAL_LATEST_DATA + ".migrated")
                            log.info("[WatchlistVM] 关注池数据已自动迁移入 SQLite")
                        except OSError as _e:
                            log.debug(f"[WatchlistVM] 迁移旧 JSON 文件重命名失败: {_e}")

            if isinstance(data, dict):
                # 洗掉硬盘上的旧照片：抹除老旧的历史价格快照，全部重置为横杠 '--'
                for code, entry in data.items():
                    if isinstance(entry, dict):
                        for volatile_key in ["现价", "涨幅%", "市值"]:
                            entry[volatile_key] = "--"
                        entry["来源标签"] = self.derive_source_tags(
                            entry,
                            existing_tags=entry.get("来源标签"),
                        )
                self._cache = data
            else:
                self._cache = {}
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError, sqlite3.Error, json.JSONDecodeError) as e:
            log.error(f"[WatchlistVM] 读取关注池失败，退化为空: {e}")
            self._cache = {}

    def _save_data(self):
        """安全地将内存中的数据刷入 SQLite（原子写入）"""
        try:
            with self._lock:
                save_data = deepcopy(self._cache)
            from core.data_store import DataStore
            DataStore().save_json("watchlist_special", save_data)
        except (AttributeError, OSError, RuntimeError, TypeError, ValueError, sqlite3.Error) as e:
            log.error(f"[WatchlistVM] 写入关注池失败: {e}")

    def is_in_watchlist(self, stock_code: str) -> bool:
        """判断特定股票是否在关注池中"""
        with self._lock:
            return stock_code in self._cache

    def get_all_codes(self) -> list:
        """返回所有被关注的股票代码列表"""
        with self._lock:
            return list(self._cache.keys())

    def get_watchlist_data(self) -> dict:
        """获取整个关注池的详尽数据拷贝，防止外部直接引用修改污染缓存"""
        with self._lock:
            return deepcopy(self._cache)

    @staticmethod
    def _normalize_entry_value(value):
        if hasattr(value, "item"):
            return value.item()
        if isinstance(value, (str, int, float, bool, list, dict, type(None))):
            return deepcopy(value)
        return str(value)

    @classmethod
    def _unique_source_tags(cls, tags) -> list[str]:
        ordered = []
        for tag in tags or []:
            text = str(tag or "").strip()
            if text and text not in ordered:
                ordered.append(text)

        prioritized = [tag for tag in cls._SOURCE_TAG_PRIORITY if tag in ordered]
        extras = [tag for tag in ordered if tag not in prioritized]
        return prioritized + extras

    @classmethod
    def normalize_source_tags(cls, value) -> list[str]:
        if isinstance(value, str):
            raw_tags = [part.strip() for part in value.replace("|", "｜").split("｜")]
        elif isinstance(value, (list, tuple, set)):
            raw_tags = [str(part or "").strip() for part in value]
        else:
            raw_tags = []
        return cls._unique_source_tags(raw_tags)

    @classmethod
    def format_source_tags(cls, value) -> str:
        return "｜".join(cls.normalize_source_tags(value))

    @classmethod
    def derive_source_tags(
        cls,
        entry_data: dict | None = None,
        *,
        base_tags=None,
        existing_tags=None,
    ) -> list[str]:
        payload = dict(entry_data or {})
        tags = cls.normalize_source_tags(existing_tags)
        tags.extend(cls.normalize_source_tags(base_tags))

        if str(payload.get("催化剂", "") or payload.get("美股日报", "")).strip():
            tags.append("战报")
        if str(payload.get("龙虎榜", "")).strip():
            tags.append("龙虎")
        if str(payload.get("业绩异动", "")).strip():
            tags.append("业绩")
        if str(payload.get("大宗交易", "")).strip():
            tags.append("大宗")
        if any(
            str(payload.get(key, "")).strip()
            for key in ("触发日期", "突破状态", "区间振幅", "距突破", "热门板块")
        ):
            tags.append("扫描")

        if not tags:
            tags.append("手动")
        return cls._unique_source_tags(tags)

    def patch_entry(self, stock_code: str, updates: dict | None = None, remove_keys: list[str] | tuple[str, ...] | None = None) -> bool:
        """更新单个关注池条目并持久化。"""
        stock_code = str(stock_code or "").strip()
        if not stock_code:
            return False

        with self._lock:
            entry = self._cache.get(stock_code)
            if not isinstance(entry, dict):
                return False

            changed = False
            merged = dict(entry)
            for key, value in dict(updates or {}).items():
                normalized = self._normalize_entry_value(value)
                if merged.get(key) != normalized:
                    merged[key] = normalized
                    changed = True

            for key in remove_keys or ():
                if key in merged:
                    merged.pop(key, None)
                    changed = True

            resolved_sources = self.derive_source_tags(
                merged,
                existing_tags=merged.get("来源标签"),
            )
            if merged.get("来源标签") != resolved_sources:
                merged["来源标签"] = resolved_sources
                changed = True

            if not changed:
                return False

            self._cache[stock_code] = merged

        self._save_data()
        return True

    def bulk_patch_entries(
        self,
        updates_by_code: dict[str, dict] | None,
        remove_keys: list[str] | tuple[str, ...] | None = None,
    ) -> bool:
        """批量更新关注池条目，统一落盘一次。"""
        updates_by_code = dict(updates_by_code or {})
        if not updates_by_code:
            return False

        changed = False
        with self._lock:
            for stock_code, updates in updates_by_code.items():
                code = str(stock_code or "").strip()
                entry = self._cache.get(code)
                if not code or not isinstance(entry, dict):
                    continue

                merged = dict(entry)
                entry_changed = False
                for key, value in dict(updates or {}).items():
                    normalized = self._normalize_entry_value(value)
                    if merged.get(key) != normalized:
                        merged[key] = normalized
                        entry_changed = True

                for key in remove_keys or ():
                    if key in merged:
                        merged.pop(key, None)
                        entry_changed = True

                resolved_sources = self.derive_source_tags(
                    merged,
                    existing_tags=merged.get("来源标签"),
                )
                if merged.get("来源标签") != resolved_sources:
                    merged["来源标签"] = resolved_sources
                    entry_changed = True

                if entry_changed:
                    self._cache[code] = merged
                    changed = True

        if changed:
            self._save_data()
        return changed

    def replace_watchlist_data(self, new_cache: dict | None) -> bool:
        """整体替换关注池缓存，用于按最新视觉顺序落盘。"""
        if not isinstance(new_cache, dict) or not new_cache:
            return False

        normalized_cache: dict[str, dict] = {}
        for raw_code, raw_entry in new_cache.items():
            code = str(raw_code or "").strip()
            if not code or not isinstance(raw_entry, dict):
                continue
            normalized_cache[code] = {
                str(key): self._normalize_entry_value(value)
                for key, value in raw_entry.items()
            }
            normalized_cache[code]["来源标签"] = self.derive_source_tags(
                normalized_cache[code],
                existing_tags=normalized_cache[code].get("来源标签"),
            )

        if not normalized_cache:
            return False

        with self._lock:
            same_content = self._cache == normalized_cache
            same_order = list(self._cache.keys()) == list(normalized_cache.keys())
            if same_content and same_order:
                return False
            self._cache = normalized_cache

        self._save_data()
        return True

    @classmethod
    def _build_watchlist_entry(cls, name: str, vcp_data: dict = None, source_tags=None) -> dict:
        entry = {"名称": name, "现价": "--", "涨幅%": "--", "市值": "--", "评分": ""}
        if vcp_data and isinstance(vcp_data, dict):
            for k, v in vcp_data.items():
                # 海鲜数据不写硬盘：坚决防守，切断时效性极强的实时数据污染持久层
                if k in ["现价", "涨幅%", "市值", "最低", "最高", "开盘", "昨收", "成交额", "换手%"]:
                    continue
                if hasattr(v, 'item'):
                    entry[k] = v.item()
                elif isinstance(v, (str, int, float, bool, list, dict, type(None))):
                    entry[k] = v
                else:
                    entry[k] = str(v)
        entry["来源标签"] = cls.derive_source_tags(
            entry,
            base_tags=source_tags,
            existing_tags=entry.get("来源标签"),
        )
        return entry

    def add_stock(self, stock_code: str, name: str, vcp_data: dict = None, source_tags=None) -> bool:
        """向关注池新增一只股票；若已存在则保持不动并返回 False。"""
        stock_code = str(stock_code or "").strip()
        name = str(name or stock_code).strip()
        if not stock_code:
            return False

        with self._lock:
            if stock_code in self._cache:
                return False
            self._cache[stock_code] = self._build_watchlist_entry(name, vcp_data, source_tags=source_tags)

        self._save_data()
        event_bus.sig_system_log.emit("info", f"[{name}] 已加入关注池")
        event_bus.sig_watchlist_changed.emit("add", stock_code)
        return True

    def toggle_stock(self, stock_code: str, name: str, vcp_data: dict = None, source_tags=None):
        """
        翻转股票的关注状态。
        如果是关注，就移除；如果没关注，就添加并保留当时的 VCP 分析数据。
        """
        stock_code = str(stock_code or "").strip()
        name = str(name or stock_code).strip()

        with self._lock:
            is_fav = stock_code in self._cache

            if is_fav:
                self._cache.pop(stock_code, None)
                action = "remove"
                event_bus.sig_system_log.emit("info", f"[{name}] 已移出关注池")
            else:
                self._cache[stock_code] = self._build_watchlist_entry(name, vcp_data, source_tags=source_tags)
                action = "add"
                event_bus.sig_system_log.emit("info", f"[{name}] 已加入关注池")

        self._save_data()

        # 触发全局广播，让所有的 UI 界面自己去更新星星图标或重新加载数据
        event_bus.sig_watchlist_changed.emit(action, stock_code)

    def pin_to_top(self, stock_code: str):
        """将股票排到关注池最前排（置顶）"""
        with self._lock:
            if stock_code in self._cache:
                data = self._cache.pop(stock_code)
                new_cache = {stock_code: data}
                new_cache.update(self._cache)
                self._cache = new_cache

        self._save_data()
        event_bus.sig_watchlist_changed.emit("reorder", stock_code)

    def move_to_bottom(self, stock_code: str):
        """将股票排到关注池最末尾（置底）"""
        with self._lock:
            if stock_code in self._cache:
                data = self._cache.pop(stock_code)
                self._cache[stock_code] = data

        self._save_data()
        event_bus.sig_watchlist_changed.emit("reorder", stock_code)

    def reorder(self, new_codes_list: list):
        """
        根据新的代码列表重新排序关注池，并持久化保存。
        如果有新列表里没有但在旧缓存里存在的代码（防丢），会追加到末尾。
        """
        with self._lock:
            new_cache = {}
            # 1. 按照传入的新顺序依次构建
            for code in new_codes_list:
                if code in self._cache:
                    new_cache[code] = self._cache[code]

            # 2. 安全兜底：如果有些在老缓存里的代码没能在新列表出现，加到最后
            for code, data in self._cache.items():
                if code not in new_cache:
                    new_cache[code] = data

            self._cache = new_cache

        self._save_data()

# 全局单例
watchlist_vm = WatchlistViewModel()
