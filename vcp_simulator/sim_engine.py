import pandas as pd
import random
from dataclasses import dataclass
from datetime import datetime
from PyQt6.QtCore import QThread, pyqtSignal
from vcp.models import VCPParams

# ---------- 状态机常量 ----------
STATE_READY = "READY"
STATE_OBSERVING = "OBSERVING"
STATE_HOLDING = "HOLDING"
STATE_FINISHED = "FINISHED"

# 交易摩擦成本（滑点+佣金/印花税，千分之1.5）
FRICTION_COST = 0.0015

# 日期格式
DATE_FMT = "%Y-%m-%d"


@dataclass
class TradeRecord:
    """一笔完整的买卖交易记录"""
    code: str
    name: str
    trigger_date: str
    buy_price: float       # 原始成交价（不含摩擦）
    sell_price: float      # 原始成交价（不含摩擦）
    hold_days: int
    ret: float             # 扣除摩擦后的真实收益率
    max_drawdown: float | None = None
    entry_loc: int = -1    # 在 df 中的买入索引
    exit_loc: int = -1     # 在 df 中的卖出索引


class SimBankWorker(QThread):
    """后台线程：构建 VCP 训练题库"""
    progress = pyqtSignal(str)
    bank_ready = pyqtSignal(bool, list, dict, str)  # ok, hits, rps_matrix, msg

    def __init__(self, data_provider, engine, sd, ed, rps_th, hold_days):
        super().__init__()
        self.data_provider = data_provider
        self.vcp_engine = engine
        self.sd = sd
        self.ed = ed
        self.rps_th = rps_th
        self.hold_days = hold_days

    def run(self):
        try:
            # 日期格式化
            sd_fmt = f"{self.sd[:4]}-{self.sd[4:6]}-{self.sd[6:8]}" if len(self.sd) == 8 else self.sd
            ed_fmt = f"{self.ed[:4]}-{self.ed[4:6]}-{self.ed[6:8]}" if len(self.ed) == 8 else self.ed

            # 获取全市场标的
            self.progress.emit("连接行情源获取全市场标的...")
            all_codes_dict = getattr(self.data_provider, "code2name", None) or self.data_provider.get_all_codes()
            if not all_codes_dict:
                self.bank_ready.emit(False, [], {}, "无法获取股票列表。")
                return

            # 同步 K 线数据
            self.progress.emit("同步高速缓存历史 K 线数据...")
            self.data_provider.sync_market_data(all_codes_dict, force_refresh=False)
            all_data = self.data_provider.get_all_valid_data()
            if not all_data:
                self.bank_ready.emit(False, [], {}, "本地缓存为空。")
                return

            # 计算 RPS 矩阵
            self.progress.emit("全市场 RPS 动量矩阵计算中...")
            rps_matrix = self.vcp_engine.build_rps_matrix(all_data, sd_fmt, ed_fmt)

            # VCP 参数
            params = VCPParams(
                rps_threshold=self.rps_th, amp_threshold=0.45, ma_bind_threshold=0.05,
                high_250_threshold=0.10, min_amount_20d=1e8, min_history_days=250,
                future_days=10, target_code=""
            )

            # 扫描每日切片
            hits = []
            sorted_dates = sorted(rps_matrix.keys())
            total_days = len(sorted_dates)

            for i, d_str in enumerate(sorted_dates):
                d_rps = rps_matrix[d_str]
                if i % 3 == 0:
                    self.progress.emit(f"扫描切片: {d_str}  ({i+1}/{total_days})")

                # RPS 候选池过滤（与 vcp_hunter 一致）
                candidates = [
                    k for k, v in d_rps.get("rps250", {}).items()
                    if pd.notna(v) and v >= params.rps_threshold
                    and (v >= 90 or v >= d_rps.get("rps120", {}).get(k, 0))
                ]

                for code in candidates:
                    df = all_data.get(code)
                    if df is None:
                        continue
                    try:
                        ok, _, m = self.vcp_engine.evaluate_conditions(
                            df, pd.to_datetime(d_str),
                            d_rps.get("rps120", {}).get(code, 0),
                            d_rps.get("rps250", {}).get(code, 0),
                            None, params
                        )
                        if ok:
                            hits.append({
                                "code": code,
                                "name": all_codes_dict.get(code, ""),
                                "trigger_date": d_str,
                                "details": m
                            })
                    except Exception:
                        continue

            # 去重：每只股票仅保留最后一次触发
            if hits:
                df_hits = pd.DataFrame(hits).sort_values("trigger_date")
                df_hits = df_hits.drop_duplicates(subset=["code"], keep="last")
                hits = df_hits.to_dict("records")

            # 随机打乱
            if hits:
                random.shuffle(hits)
                self.bank_ready.emit(True, hits, rps_matrix, f"✅ 构建完成！共拦截 {len(hits)} 个实战样本...")
            else:
                self.bank_ready.emit(False, [], rps_matrix, "未找到符合的 VCP 信号。")

        except Exception as e:
            self.bank_ready.emit(False, [], {}, str(e))
