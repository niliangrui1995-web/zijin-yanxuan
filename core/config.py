# -*- coding: utf-8 -*-
"""统一配置管理器 — 集中管理所有用户可调参数

读取优先级：环境变量 > vcp_config.ini > 默认值
"""

import os
import configparser
from dataclasses import dataclass, field


# 项目根目录（紫金研选/）
_PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_CONFIG_FILE = os.path.join(_PROJECT_ROOT, 'vcp_config.ini')


@dataclass
class AppConfig:
    """全局应用配置（单例）"""

    # --- 通达信 ---
    tdx_root: str = r'D:\HT'

    # --- 数据 ---
    cache_dir: str = field(default_factory=lambda: os.path.join(_PROJECT_ROOT, 'data', 'Cache'))
    parquet_dir: str = field(default_factory=lambda: os.path.join(_PROJECT_ROOT, 'data'))
    finance_cache_ttl_days: int = 7

    # --- VCP 引擎默认参数 ---
    rps_threshold: int = 80
    amp_threshold: float = 0.45
    ma_bind_threshold: float = 0.10
    min_amount_20d: float = 5000
    min_history_days: int = 200

    # --- AI 服务 ---
    kimi_api_key: str = ''
    kimi_model: str = 'moonshot-v1-128k'

    # --- UI ---
    window_width: int = 1600
    window_height: int = 900

    # --- 网络 ---
    offline_mode: bool = True
    speed_test_top_n: int = 5

    _instance = None

    @classmethod
    def get_instance(cls) -> 'AppConfig':
        """获取全局唯一配置实例"""
        if cls._instance is None:
            cls._instance = cls()
            cls._instance._load_from_file()
            cls._instance._load_from_env()
        return cls._instance

    def _load_from_file(self):
        """从 vcp_config.ini 加载（如果存在）"""
        if not os.path.exists(_CONFIG_FILE):
            return
        try:
            cp = configparser.ConfigParser()
            cp.read(_CONFIG_FILE, encoding='utf-8')

            # [tdx] 段
            if cp.has_section('tdx'):
                self.tdx_root = cp.get('tdx', 'root', fallback=self.tdx_root)

            # [data] 段
            if cp.has_section('data'):
                self.cache_dir = cp.get('data', 'cache_dir', fallback=self.cache_dir)
                self.finance_cache_ttl_days = cp.getint(
                    'data', 'finance_cache_ttl_days', fallback=self.finance_cache_ttl_days)

            # [vcp] 段
            if cp.has_section('vcp'):
                self.rps_threshold = cp.getint('vcp', 'rps_threshold', fallback=self.rps_threshold)
                self.amp_threshold = cp.getfloat('vcp', 'amp_threshold', fallback=self.amp_threshold)

            # [ai] 段
            if cp.has_section('ai'):
                self.kimi_api_key = cp.get('ai', 'api_key', fallback=self.kimi_api_key)
                self.kimi_model = cp.get('ai', 'model', fallback=self.kimi_model)

            # [ui] 段
            if cp.has_section('ui'):
                self.window_width = cp.getint('ui', 'width', fallback=self.window_width)
                self.window_height = cp.getint('ui', 'height', fallback=self.window_height)

        except Exception as e:
            print(f"[配置] ⚠ 加载 {_CONFIG_FILE} 失败: {e}")

    def _load_from_env(self):
        """环境变量覆盖（优先级最高）"""
        env_tdx = os.environ.get('VCP_TDX_ROOT')
        if env_tdx:
            self.tdx_root = env_tdx
        env_key = os.environ.get('KIMI_API_KEY')
        if env_key:
            self.kimi_api_key = env_key

    @property
    def tdx_vipdoc(self) -> str:
        """通达信 vipdoc 路径"""
        return os.path.join(self.tdx_root, 'vipdoc')

    def get(self, key: str, default=None):
        """字典式访问"""
        return getattr(self, key, default)


# 全局快捷引用
app_config = AppConfig.get_instance()
