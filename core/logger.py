# -*- coding: utf-8 -*-
"""core/logger.py — 统一日志管理

替代散落的 print() 调用，提供：
- 分级日志（DEBUG/INFO/WARN/ERROR）
- 可选文件输出
- 与 event_bus 集成，将日志推送到 UI 状态栏
"""

import logging
import os
import sys
from datetime import datetime
from typing import Optional


_logger: Optional[logging.Logger] = None


class EventBusHandler(logging.Handler):
    """将日志发送到系统的事件总线上，从而显示在 UI 日志面板中"""
    def emit(self, record):
        try:
            msg = self.format(record)
            level = record.levelname.lower()
            if level == 'warning':
                level = 'warn'
            # 动态导入，避免启动时循环引用或Qt尚未就绪
            from core.event_bus import event_bus
            event_bus.sig_system_log.emit(level, msg + '\n')
        except Exception:
            self.handleError(record)


def get_logger(name: str = "vcp_hunter") -> logging.Logger:
    """获取全局日志实例（单例）"""
    global _logger
    if _logger is not None:
        return _logger

    _logger = logging.getLogger(name)
    _logger.setLevel(logging.DEBUG)

    console_fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S",
    )

    # 控制台输出 (底层原始标准输出)
    if sys.stdout is not None and hasattr(sys.stdout, 'write'):
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(logging.INFO)
        console_handler.setFormatter(console_fmt)
        _logger.addHandler(console_handler)

    # 事件总线输出 (UI 展示)
    eb_handler = EventBusHandler()
    eb_handler.setLevel(logging.INFO)
    eb_handler.setFormatter(console_fmt)
    _logger.addHandler(eb_handler)

    # 文件输出（RotatingFileHandler: 单文件 1MB 上限，保留 3 个滚动备份）
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)

        # 日志自净：启动时删除 7 天前的旧日志文件
        _clean_old_logs(log_dir, max_age_days=7)

        log_file = os.path.join(log_dir, f"vcp_{datetime.now().strftime('%Y%m%d')}.log")
        from logging.handlers import RotatingFileHandler
        file_handler = RotatingFileHandler(
            log_file, maxBytes=1 * 1024 * 1024, backupCount=3, encoding="utf-8"
        )
        file_handler.setLevel(logging.DEBUG)
        file_fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        file_handler.setFormatter(file_fmt)
        _logger.addHandler(file_handler)
    except Exception as _e:
        sys.stderr.write(f"[logger] 日志文件创建失败: {_e}\n")

    return _logger


def _clean_old_logs(log_dir: str, max_age_days: int = 7):
    """日志自净：删除超过 max_age_days 天的 .log 文件，防止磁盘积灰"""
    import time
    now = time.time()
    cutoff = now - (max_age_days * 86400)

    try:
        for filename in os.listdir(log_dir):
            if not filename.endswith('.log'):
                continue
            filepath = os.path.join(log_dir, filename)
            if os.path.isfile(filepath) and os.path.getmtime(filepath) < cutoff:
                os.remove(filepath)
    except OSError as _e:
        sys.stderr.write(f"[logger] 旧日志清理失败: {_e}\n")
