# models.py - 数据类定义
# 从 vcp_hunter.pyw 提取，零逻辑变更
from dataclasses import dataclass
from vcp.constants import DEFAULT_AMP_THRESHOLD


@dataclass
class VCPParams:
    """VCP 策略参数配置"""
    rps_threshold: int = 80
    amp_threshold: float = DEFAULT_AMP_THRESHOLD  # 左区区间振幅上限，默认45%
    ma_bind_threshold: float = 0.05
    high_250_threshold: float = 0.10
    min_amount_20d: float = 8e7   # 默认 8000 万
    min_history_days: int = 250
    target_code: str = ""
    # 弹性区间参数
    enable_flexible_peaks: bool = True
    enable_rps_stability: bool = True
    enable_ma_slope: bool = True
    enable_volatility_filter: bool = True
    enable_pre_spread: bool = True
