"""
evolution/adapted/scb_signal.py
==================================
Adapter: wraps the SCB (沙尘暴 / Sandstorm) composite buy signal.

SCB condition (from scan_signals_v2.get_SCB_buy_signal):
    地量基础条件 in last 5 days  AND  今日暴力K  AND  知行多空线 > REF(知行多空线, 60)

Score table (from calculate_scb_signal):
    All 3 conditions: 10
    地量+暴力K only:   7
    地量+多头:         4
    暴力K+多头:        5
    地量 only:         2
    暴力K only:        3
    none:              0

This adapter is more expensive per bar because SCB requires checking
地量基础条件 for the 5 historical days before the current bar.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from evolution.base_signal import BaseSignal
from evolution.adapted._adapter_utils import (
    df_to_indicators, _ensure_singal_cal_on_path
)


class SCBSignal(BaseSignal):
    """
    SCB (沙尘暴) 地量+暴力K+多头发散 买入信号适配器

    Highest-conviction buy signal: volume exhaustion + breakout candle
    + 知行多空线 divergence.
    """

    @property
    def name(self) -> str:
        return "scb"

    @property
    def source(self) -> str:
        return "adapted"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "SCB沙尘暴买入信号 (地量基础+暴力K+多头发散三重共振)"

    def define_params(self, trial) -> dict:
        return {
            # 地量基础条件 minimum volume depletion days X (original: 30)
            "dl_x": trial.suggest_int("dl_x", 20, 45),
            # Lookback days for 地量基础 history check (original: 5)
            "dl_lookback": trial.suggest_int("dl_lookback", 3, 7),
            # Minimum SCB score to treat as bullish (inclusive)
            "min_score": trial.suggest_float("min_score", 3.0, 8.0),
            "min_rows": trial.suggest_int("min_rows", 120, 200),
        }

    def calculate(self, df: pd.DataFrame, params: dict) -> pd.Series:
        _ensure_singal_cal_on_path()
        from SCB_strategy_module import (               # type: ignore
            check_dl_basic_condition,
            calculate_blk_signal,
            calculate_scb_signal,
        )

        code        = str(df.attrs.get("code", "UNKNOWN"))
        dl_lookback = int(params.get("dl_lookback", 5))
        min_rows    = int(params.get("min_rows", 120))

        n      = len(df)
        scores = np.full(n, np.nan, dtype=np.float64)

        for i in range(min_rows - 1, n):
            window = df.iloc[: i + 1]
            try:
                ind = df_to_indicators(window, code)

                # Build 地量基础条件 history for last dl_lookback days
                dl_history = []
                for offset in range(1, dl_lookback + 1):
                    if i - offset < 0:
                        dl_history.append(False)
                        continue
                    hist_window = df.iloc[: i - offset + 1]
                    if len(hist_window) < 2:
                        dl_history.append(False)
                        continue
                    try:
                        hist_ind = df_to_indicators(hist_window, code)
                        dl_history.append(
                            check_dl_basic_condition(hist_ind)
                        )
                    except Exception:
                        dl_history.append(False)

                blk_signal       = calculate_blk_signal(ind)
                _, scb_score     = calculate_scb_signal(ind, blk_signal, dl_history)
                scores[i]        = float(scb_score)
            except Exception:
                pass

        return pd.Series(scores, index=df.index, dtype=np.float64)
