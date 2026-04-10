# -*- coding: utf-8 -*-
"""
core/cache_manager.py
文件级缓存管理：负责磁盘 pkl 缓存读写与清理逻辑。
"""
import os
import datetime
import pickle
import re

from core.logger import get_logger
from core.exceptions import CacheIOError, DataFormatError, BusinessRuleError

log = get_logger(__name__)

class CacheManager:
    """仅负责本地 pkl 缓存的读写与清理，不直接操作 UI。"""
    
    def __init__(self):
        # 确定 Cache 目录绝对路径
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.cache_dir = os.path.join(project_root, 'data', 'Cache')
        os.makedirs(self.cache_dir, exist_ok=True)
        self.rps_path = os.path.join(self.cache_dir, 'vcp_rps_precomputed.pkl')

    def _load_pickle(self, path: str):
        try:
            with open(path, 'rb') as f:
                return pickle.load(f)
        except (FileNotFoundError, PermissionError, OSError) as e:
            raise CacheIOError(f"cache read failed: {path}") from e
        except (pickle.UnpicklingError, EOFError) as e:
            raise DataFormatError(f"pickle payload invalid: {path}") from e

    def _save_pickle(self, path: str, data) -> None:
        try:
            with open(path, 'wb') as f:
                pickle.dump(data, f, protocol=4)
        except (PermissionError, OSError) as e:
            raise CacheIOError(f"cache write failed: {path}") from e

    @staticmethod
    def _count_valid_rps_values(rps120) -> int:
        """统计有效 RPS120 条目数（兼容 dict / pandas Series）。"""
        if isinstance(rps120, dict):
            cnt = 0
            for v in rps120.values():
                try:
                    fv = float(v)
                except (TypeError, ValueError):
                    continue
                if fv == fv:  # NaN != NaN
                    cnt += 1
            return cnt

        if hasattr(rps120, 'notna'):
            try:
                return int(rps120.notna().sum())
            except Exception:
                return 0

        return 0

    def try_load_rps_from_disk(self, engine, set_status_callback=None):
        """
        尝试从磁盘加载 F5 预计算 RPS 缓存。
        :param engine: VCPEngine 实例，用于注入预计算矩阵
        :param set_status_callback: 可选 UI 状态回调
        """
        if not os.path.exists(self.rps_path):
            return

        try:
            pkg = self._load_pickle(self.rps_path)
            cached_date = pkg.get('date', '')
            rps120 = pkg.get('rps120')
            rps250 = pkg.get('rps250')
            if rps120 is None or rps250 is None:
                raise BusinessRuleError("rps120/rps250 missing in cache payload")

            engine.set_precomputed_rps(cached_date, rps120, rps250)
            count = self._count_valid_rps_values(rps120)
            log.info(f"[RPS] 从磁盘加载预计算RPS成功(基准日:{cached_date}, 仅{count}条有效)")

            if set_status_callback:
                set_status_callback(f"RPS cache loaded: {cached_date}, {count} symbols")
        except CacheIOError as e:
            log.error(f"[RPS][I/O] 磁盘加载失败: {e}")
        except DataFormatError as e:
            log.error(f"[RPS][FORMAT] 缓存格式异常: {e}")
        except BusinessRuleError as e:
            log.warning(f"[RPS][RULE] 缓存不可用: {e}")
    def save_rt_cache(self, table):
        """保存盘中监控当日缓存到 pkl 文件"""
        try:
            rows = []
            headers = []

            if hasattr(table, 'model') and getattr(table, 'model', lambda: None)():
                model = table.model()
                if hasattr(model, 'sourceModel'):
                    model = model.sourceModel()

                if hasattr(model, 'row_data'):
                    if not model.row_data:
                        return
                    headers = model.headers if hasattr(model, 'headers') else []
                    for row_dict in model.row_data:
                        rows.append([str(row_dict.get(h, '')) for h in headers])

            if not rows:
                return

            if rows and rows[0]:
                first_cell = rows[0][0]
                if len(first_cell) > 10 or '(' in first_cell or ',' in first_cell:
                    raise BusinessRuleError("abnormal first cell value in rt cache")

            data = {
                'date': datetime.date.today().isoformat(),
                'version': 2,
                'rows': rows,
                'headers': headers,
            }
            path = os.path.join(
                self.cache_dir, f"rt_monitor_{datetime.date.today().isoformat()}.pkl"
            )
            self._save_pickle(path, data)
            log.info(f"[盘中缓存] 已保存 {len(rows)} 条信号到 {os.path.basename(path)}")

            self._cleanup_old_rt_caches(10)

        except CacheIOError as e:
            log.error(f"[盘中缓存][I/O] 保存失败: {e}")
        except DataFormatError as e:
            log.error(f"[盘中缓存][FORMAT] 保存前校验失败: {e}")
        except BusinessRuleError as e:
            log.warning(f"[盘中缓存][RULE] 跳过保存: {e}")
    def load_rt_cache(self, table, set_status_callback=None):
        """启动时加载最近的盘中监控缓存"""
        path = None
        for days_ago in range(10):
            check_date = datetime.date.today() - datetime.timedelta(days=days_ago)
            candidate = os.path.join(
                self.cache_dir, f"rt_monitor_{check_date.isoformat()}.pkl"
            )
            if os.path.exists(candidate):
                path = candidate
                break

        if not path:
            return

        try:
            data = self._load_pickle(path)

            raw_rows = data.get('rows', [])
            if not raw_rows:
                raise BusinessRuleError("rows is empty")
            cache_date = data.get('date', '?')

            if hasattr(table, 'model') and getattr(table, 'model', lambda: None)():
                model = table.model()
                if hasattr(model, 'sourceModel'):
                    model = model.sourceModel()

                if hasattr(model, 'update_data') and hasattr(model, 'headers'):
                    historical_headers = data.get('headers', [])
                    effective_headers = historical_headers if historical_headers else model.headers

                    final_data = []
                    for row_vals in raw_rows:
                        if isinstance(row_vals, (list, tuple)) and len(row_vals) == 2 and isinstance(row_vals[0], (list, tuple)):
                            row_vals = row_vals[0]

                        row_dict = {}
                        for c, val in enumerate(row_vals):
                            if c < len(effective_headers):
                                row_dict[effective_headers[c]] = val
                        final_data.append(row_dict)

                    model.update_data(final_data)
                    if set_status_callback:
                        set_status_callback(f"RT cache restored ({cache_date}, {len(raw_rows)} rows)")
                    return

            log.warning("[RT cache] table model not available, skip restore")
        except CacheIOError as e:
            log.error(f"[盘中缓存][I/O] 加载失败: {e}")
        except DataFormatError as e:
            log.error(f"[盘中缓存][FORMAT] 加载失败: {e}")
        except BusinessRuleError as e:
            log.warning(f"[盘中缓存][RULE] 缓存不可用: {e}")
    def _cleanup_old_rt_caches(self, retention_days=10):
        """Cleanup expired realtime monitor cache files."""
        today = datetime.date.today()
        for fname in os.listdir(self.cache_dir):
            if fname.startswith('rt_monitor_') and fname.endswith('.pkl'):
                m = re.search(r'rt_monitor_(\d{4}-\d{2}-\d{2})\.pkl', fname)
                if m:
                    try:
                        fdate = datetime.datetime.strptime(
                            m.group(1), '%Y-%m-%d'
                        ).date()
                        if (today - fdate).days > retention_days:
                            os.remove(os.path.join(self.cache_dir, fname))
                    except (ValueError, OSError) as _e:
                        log.debug(f"[缓存管理] 清理旧缓存 {fname} 失败: {_e}")
