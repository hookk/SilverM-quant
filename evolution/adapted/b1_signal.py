"""
evolution/adapted/b1_signal.py
================================
Adapter: wraps B1_strategy_module.calculate_b1_score as a BaseSignal.

B1 is a multi-condition buy scoring system (39 conditions, score range ~-15 to +15).
Positive scores → bullish; score ≥ threshold (default 8) → buy signal.

Original gate conditions (replicated as part of the score):
    KDJ J < 13  AND  MACD DIF ≥ 0  AND  趋势线 > 多空线
These are encoded in define_params so Optuna can explore threshold variants.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from evolution.base_signal import BaseSignal
from evolution.adapted._adapter_utils import rolling_score, _ensure_singal_cal_on_path


class B1Signal(BaseSignal):
    """
    B1 买入信号适配器

    Wraps calculate_b1_score() from signals/singal_cal/B1_strategy_module.py.
    Returns the raw B1 score as the signal value.
    Positive = bullish; threshold ≥ 8 historically triggers buy.
    """

    @property
    def name(self) -> str:
        return "b1"

    @property
    def source(self) -> str:
        return "adapted"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "B1多因子买入评分 (39条件综合评分，地量趋势共振)"

    def define_params(self, trial) -> dict:
        return {
            # Buy threshold: scan_signals default is 8
            "buy_threshold": trial.suggest_float("buy_threshold", 5.0, 12.0),
            # Whether to enforce KDJ J < threshold gate
            "kdj_j_gate": trial.suggest_float("kdj_j_gate", 5.0, 25.0),
            # Minimum history rows per window
            "min_rows": trial.suggest_int("min_rows", 60, 180),
        }

    def calculate(self, df: pd.DataFrame, params: dict) -> pd.Series:
        _ensure_singal_cal_on_path()
        from B1_strategy_module import calculate_b1_score  # type: ignore

        code     = str(df.attrs.get("code", "UNKNOWN"))
        min_rows = int(params.get("min_rows", 120))

        raw_scores = rolling_score(df, code, calculate_b1_score, min_rows=min_rows)
        return pd.Series(raw_scores, index=df.index, dtype=np.float64)
