"""
evolution/adapted/b2_signal.py
================================
Adapter: wraps B2_strategy_module.calculate_b2_score as a BaseSignal.

B2 triggers on oversold bounces:
    (J ≤ 21 OR RSI1 ≤ 21)  AND  VOL > REF(VOL,1)  AND  涨幅 > 3.95%
    AND  VOL > VOL_MA60  AND  C > O
Score range is roughly 0–15; threshold default is 8.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from evolution.base_signal import BaseSignal
from evolution.adapted._adapter_utils import rolling_score, _ensure_singal_cal_on_path


class B2Signal(BaseSignal):
    """
    B2 超卖反弹买入信号适配器

    Wraps calculate_b2_score() from B2_strategy_module.
    Triggers on KDJ/RSI oversold + volume breakout.
    """

    @property
    def name(self) -> str:
        return "b2"

    @property
    def source(self) -> str:
        return "adapted"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "B2超卖反弹买入评分 (KDJ/RSI低位+量能突破)"

    def define_params(self, trial) -> dict:
        return {
            "buy_threshold": trial.suggest_float("buy_threshold", 5.0, 12.0),
            # RSI oversold gate
            "rsi_oversold_gate": trial.suggest_float("rsi_oversold_gate", 15.0, 30.0),
            # KDJ J gate
            "kdj_j_gate": trial.suggest_float("kdj_j_gate", 15.0, 30.0),
            # Minimum price change % to qualify
            "min_pct_change": trial.suggest_float("min_pct_change", 3.0, 6.0),
            "min_rows": trial.suggest_int("min_rows", 60, 180),
        }

    def calculate(self, df: pd.DataFrame, params: dict) -> pd.Series:
        _ensure_singal_cal_on_path()
        from B2_strategy_module import calculate_b2_score  # type: ignore

        code     = str(df.attrs.get("code", "UNKNOWN"))
        min_rows = int(params.get("min_rows", 120))

        raw_scores = rolling_score(df, code, calculate_b2_score, min_rows=min_rows)
        return pd.Series(raw_scores, index=df.index, dtype=np.float64)
