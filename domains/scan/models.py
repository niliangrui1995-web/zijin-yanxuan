from __future__ import annotations

from dataclasses import dataclass

from core.runtime_paths import DEFAULT_AMP_THRESHOLD


@dataclass
class VCPParams:
    """VCP 策略参数；领域层是该配置的唯一事实来源。"""

    rps_threshold: int = 80
    amp_threshold: float = DEFAULT_AMP_THRESHOLD
    ma_bind_threshold: float = 0.05
    high_250_threshold: float = 0.10
    min_amount_20d: float = 8e7
    min_history_days: int = 250
    target_code: str = ""
    enable_ma_slope: bool = True
