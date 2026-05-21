from __future__ import annotations

import pandas as pd
import polars as pl


class IndicatorService:
    """技术指标计算服务。"""

    @staticmethod
    def calculate_indicators(
        df: pd.DataFrame | pl.DataFrame,
        include_chart: bool = True,
    ) -> pd.DataFrame | pl.DataFrame:
        """计算技术指标，兼容 Pandas / Polars。"""
        if df is None or len(df) < 10:
            return df

        is_pandas = isinstance(df, pd.DataFrame)

        if is_pandas:
            if hasattr(df, "attrs") and df.attrs.get("vcp_indicators_ready", False):
                return df

            if df.index.name in ("datetime", "date") or isinstance(df.index, pd.DatetimeIndex):
                if df.index.name is None:
                    df.index.name = "datetime"
                pldf = pl.from_pandas(df.reset_index())
            else:
                pldf = pl.from_pandas(df)
        else:
            pldf = df

        core_done = "entangle" in pldf.columns
        chart_done = "MACD" in pldf.columns

        if core_done and (not include_chart or chart_done):
            return df if is_pandas else pldf

        if not core_done:
            pldf = (
                pldf.with_columns(
                    [
                        pl.col("close").rolling_mean(50).alias("SMA50"),
                        pl.col("close").rolling_mean(150).alias("SMA150"),
                        pl.col("close").rolling_mean(200).alias("SMA200"),
                        pl.col("high").rolling_max(250).alias("High_250"),
                        pl.col("close").shift(1).alias("prev_close"),
                    ]
                )
                .with_columns(
                    [
                        pl.max_horizontal(
                            [
                                pl.col("high") - pl.col("low"),
                                (pl.col("high") - pl.col("prev_close")).abs(),
                                (pl.col("low") - pl.col("prev_close")).abs(),
                            ]
                        ).alias("TR")
                    ]
                )
                .with_columns(
                    [
                        pl.col("TR").rolling_mean(10).alias("ATR10"),
                        pl.col("TR").rolling_mean(20).alias("ATR20"),
                        pl.col("TR").rolling_mean(60).alias("ATR60"),
                        pl.col("volume").rolling_mean(25).alias("vol_ma25"),
                        pl.col("close").rolling_mean(10).alias("ma10"),
                        pl.col("close").rolling_mean(20).alias("ma20"),
                    ]
                )
            )

            if "amount" not in pldf.columns:
                pldf = pldf.with_columns((pl.col("volume") * pl.col("close") * 100).alias("amount"))
            else:
                pldf = pldf.with_columns(
                    [
                        pl.when(pl.col("amount").is_null() | (pl.col("amount") == 0))
                        .then(pl.col("volume") * pl.col("close") * 100)
                        .otherwise(pl.col("amount"))
                        .alias("amount")
                    ]
                )

            pldf = pldf.with_columns(
                [
                    pl.max_horizontal(["ma10", "ma20", "SMA50"]).alias("max_ma"),
                    pl.min_horizontal(["ma10", "ma20", "SMA50"]).alias("min_ma"),
                ]
            ).with_columns([((pl.col("max_ma") / pl.col("min_ma")) - 1).alias("entangle")])

        if include_chart and not chart_done:
            pldf = (
                pldf.with_columns(
                    [
                        pl.col("close").ewm_mean(span=12, adjust=False).alias("exp1"),
                        pl.col("close").ewm_mean(span=26, adjust=False).alias("exp2"),
                    ]
                )
                .with_columns([(pl.col("exp1") - pl.col("exp2")).alias("MACD")])
                .with_columns([pl.col("MACD").ewm_mean(span=9, adjust=False).alias("MACD_Signal")])
                .with_columns([(pl.col("MACD") - pl.col("MACD_Signal")).alias("MACD_Hist")])
            )

            pldf = (
                pldf.with_columns([pl.col("close").diff().alias("delta")])
                .with_columns(
                    [
                        pl.when(pl.col("delta") > 0).then(pl.col("delta")).otherwise(0).rolling_mean(14).alias("gain"),
                        pl.when(pl.col("delta") < 0).then(-pl.col("delta")).otherwise(0).rolling_mean(14).alias("loss"),
                    ]
                )
                .with_columns([(pl.col("gain") / pl.col("loss")).alias("rs")])
                .with_columns([(100 - (100 / (1 + pl.col("rs")))).alias("RSI")])
            )

            pldf = pldf.with_columns([pl.col("close").rolling_std(20).alias("std20")]).with_columns(
                [
                    (pl.col("ma20") + 2 * pl.col("std20")).alias("BB_up"),
                    (pl.col("ma20") - 2 * pl.col("std20")).alias("BB_low"),
                ]
            )

        if is_pandas:
            new_df = pldf.to_pandas()
            if "datetime" in new_df.columns:
                new_df.set_index("datetime", inplace=True)
            elif "date" in new_df.columns:
                new_df.set_index("date", inplace=True)
            new_df.attrs["vcp_core_ready"] = True
            if include_chart:
                new_df.attrs["vcp_chart_ready"] = True
                new_df.attrs["vcp_indicators_ready"] = True
            return new_df
        return pldf
