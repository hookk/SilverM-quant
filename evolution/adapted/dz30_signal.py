"""
evolution/adapted/dz30_signal.py
==================================
Adapter: wraps the DZ30 (单针30 / Single-needle 30) buy signal.

DZ30 condition (from scan_signals_v2.get_DZ30_buy_signal):
    长期KD ≥ 80  AND  短期KD ≤ 30
    AND  C > 知行短期趋势线
    AND  知行短期趋势线 > 知行多空线
    AND  倍量柱 in last 20 bars
    AND  前20日非阴 (max-volume day in past 20 was a yang candle)

Returns 5.0 when triggered (matching scan_signals_v2 score), 0.0 otherwise.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from evolution.base_signal import BaseSignal
from evolution.adapted._adapter_utils import (
    df_to_indicators, _ensure_singal_cal_on_path
)


class DZ30Signal(BaseSignal):
    """
    单针30 买入信号适配器

    Short-term KD oversold (≤30) while long-term KD overbought (≥80),
    with price above trend line and recent volume surge.
    """

    @property
    def name(self) -> str:
        return "dz30"

    @property
    def source(self) -> str:
        return "adapted"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "单针30买入信号 (长期KD高位+短期KD低位+倍量柱)"

    def define_params(self, trial) -> dict:
        return {
            # Long-term KD must be ≥ this (original: 80)
            "long_kd_min": trial.suggest_float("long_kd_min", 70.0, 90.0),
            # Short-term KD must be ≤ this (original: 30)
            "short_kd_max": trial.suggest_float("short_kd_max", 20.0, 40.0),
            # Score emitted on trigger
            "signal_value": trial.suggest_float("signal_value", 3.0, 8.0),
            "min_rows": trial.suggest_int("min_rows", 60, 180),
        }

    def calculate(self, df: pd.DataFrame, params: dict) -> pd.Series:
        _ensure_singal_cal_on_path()
        from DZ30_strategy_module import (          # type: ignore
            calculate_倍量柱_arr, check_前20日非阴, check_长短期KD
        )

        code         = str(df.attrs.get("code", "UNKNOWN"))
        long_kd_min  = float(params.get("long_kd_min",  80.0))
        short_kd_max = float(params.get("short_kd_max", 30.0))
        signal_value = float(params.get("signal_value",  5.0))
        min_rows     = int(params.get("min_rows", 120))

        n      = len(df)
        scores = np.full(n, np.nan, dtype=np.float64)

        for i in range(min_rows - 1, n):
            window = df.iloc[: i + 1]
            try:
                ind = df_to_indicators(window, code)

                短期KD, 长期KD = check_长短期KD(ind)
                kd_ok   = (长期KD >= long_kd_min) and (短期KD <= short_kd_max)
                price_ok = ind["close"] > ind["知行短期趋势线"]
                trend_ok = ind["知行短期趋势线"] > ind["知行多空线"]

                blz_arr  = calculate_倍量柱_arr(ind)
                blz_ok   = bool(np.sum(blz_arr[-20:]) >= 1) if len(blz_arr) >= 20 else bool(np.sum(blz_arr) >= 1)
                non_yin  = check_前20日非阴(ind)

                if kd_ok and price_ok and trend_ok and blz_ok and non_yin:
                    scores[i] = signal_value
                else:
                    scores[i] = 0.0
            except Exception:
                pass

        return pd.Series(scores, index=df.index, dtype=np.float64)
