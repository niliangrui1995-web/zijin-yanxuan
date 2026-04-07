# -*- coding: utf-8 -*-
"""
core/cache_manager.py
专属的文件读写与盘中缓存管家，剥离原 DataCacheMixin 的存取逻辑
"""
import os
import datetime
import pickle
import re

from core.logger import get_logger

log = get_logger(__name__)

class CacheManager:
    """仅负责本地 pkl 缓存的存取、清理，不直接操作 UI"""
    
    def __init__(self):
        # 确定 Cache 目录绝对路径
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        self.cache_dir = os.path.join(project_root, 'data', 'Cache')
        os.makedirs(self.cache_dir, exist_ok=True)
        self.rps_path = os.path.join(self.cache_dir, 'vcp_rps_precomputed.pkl')

    def try_load_rps_from_disk(self, engine, set_status_callback=None):
        """
        尝试从磁盘加载 F5 预计算的 RPS 缓存
        :param engine: VCPEngine 实例，用于存放预计算矩阵
        :param set_status_callback: 回调函数，用于安全更新 UI 文本（例如 self.lbl_status.setText）
        """
        if not os.path.exists(self.rps_path):
            return
            
        try:
            with open(self.rps_path, 'rb') as f:
                pkg = pickle.load(f)
                
            cached_date = pkg.get('date', '')
            rps120 = pkg.get('rps120')
            rps250 = pkg.get('rps250')
            if rps120 is None or rps250 is None:
                return
                
            engine.set_precomputed_rps(cached_date, rps120, rps250)
            count = int(rps120.notna().sum()) if hasattr(rps120, 'notna') else 0
            log.info(f"[RPS] ✓ 从磁盘加载预计算RPS(基准日 {cached_date},{count} 只有效排名)")
            
            if set_status_callback:
                set_status_callback(f"RPS缓存已加载({cached_date},{count}只)")
        except Exception as e:
            log.error(f"[RPS] 磁盘加载失败: {e}")

    def save_rt_cache(self, table):
        """保存盘中监控当日缓存到 pkl 文件"""
        try:
            rows = []
            headers = []
            
            if hasattr(table, 'model') and getattr(table, 'model', lambda: None)():
                model = table.model()
                if hasattr(model, 'sourceModel'): model = model.sourceModel()
                
                if hasattr(model, 'row_data'):
                    if not model.row_data: return
                    headers = model.headers if hasattr(model, 'headers') else []
                    for row_dict in model.row_data:
                        rows.append([str(row_dict.get(h, '')) for h in headers])
            
            if not rows: return
            
            if rows and rows[0]:
                first_cell = rows[0][0]
                if len(first_cell) > 10 or '(' in first_cell or ',' in first_cell:
                    log.error("[盘中缓存] 检测到异常数据,跳过保存")
                    return
                    
            data = {
                'date': datetime.date.today().isoformat(),
                'version': 2,
                'rows': rows,
                'headers': headers,
            }
            path = os.path.join(
                self.cache_dir, f"rt_monitor_{datetime.date.today().isoformat()}.pkl"
            )
            with open(path, 'wb') as f:
                pickle.dump(data, f, protocol=4)
            log.info(f"[盘中缓存] 已保存 {len(rows)} 条信号到 {os.path.basename(path)}")

            # 清理超过 10 天的旧缓存
            self._cleanup_old_rt_caches(10)
            
        except Exception as e:
            log.error(f"[盘中缓存] 保存失败: {e}")

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
            with open(path, 'rb') as f:
                data = pickle.load(f)
                
            raw_rows = data.get('rows', [])
            if not raw_rows:
                return
            cache_date = data.get('date', '?')

            if hasattr(table, 'model') and getattr(table, 'model', lambda: None)():
                model = table.model()
                if hasattr(model, 'sourceModel'): model = model.sourceModel()
                
                if hasattr(model, 'update_data') and hasattr(model, 'headers'):
                    historical_headers = data.get('headers', [])
                    effective_headers = historical_headers if historical_headers else model.headers
                    
                    final_data = []
                    for row_vals in raw_rows:
                        # 兼容旧版格式
                        if isinstance(row_vals, (list, tuple)) and len(row_vals) == 2 and isinstance(row_vals[0], (list, tuple)):
                            row_vals = row_vals[0]
                            
                        row_dict = {}
                        for c, val in enumerate(row_vals):
                            if c < len(effective_headers):
                                row_dict[effective_headers[c]] = val
                        final_data.append(row_dict)
                        
                    model.update_data(final_data)
                    if set_status_callback:
                        set_status_callback(f"已恢复盘中MVC缓存 ({cache_date}, {len(raw_rows)} 条)")
                    return

            log.warning("[盘中缓存] table_rt 未找到有效 Model，跳过加载")
        except Exception as e:
            log.error(f"[盘中缓存] 加载失败: {e}")

    def _cleanup_old_rt_caches(self, retention_days=10):
        """清理过期的历史盘中监控日志"""
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
