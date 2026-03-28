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


def get_logger(name: str = "vcp_hunter") -> logging.Logger:
    """获取全局日志实例（单例）"""
    global _logger
    if _logger is not None:
        return _logger

    _logger = logging.getLogger(name)
    _logger.setLevel(logging.DEBUG)

    # 控制台输出
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO)
    console_fmt = logging.Formatter(
        "[%(asctime)s] %(levelname)-5s | %(message)s",
        datefmt="%H:%M:%S",
    )
    console_handler.setFormatter(console_fmt)
    _logger.addHandler(console_handler)

    # 文件输出（可选）
    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        log_file = os.path.join(log_dir, f"vcp_{datetime.now().strftime('%Y%m%d')}.log")
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setLevel(logging.DEBUG)
        file_fmt = logging.Formatter(
            "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        )
        file_handler.setFormatter(file_fmt)
        _logger.addHandler(file_handler)
    except Exception:
        pass  # 日志文件创建失败不影响主程序运行

    return _logger
