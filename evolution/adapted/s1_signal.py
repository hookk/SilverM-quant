"""
evolution/adapted/s1_signal.py
================================
Adapter: wraps S1_module.calculate_s1_score as a BaseSignal.

S1 is a SELL scoring system (not a buy signal).
By convention, sell signals are returned as NEGATIVE values so the
evaluator and portfolio logic can distinguish:
    positive → bullish (buy)
    negative → bearish (sell)

S1 score range: 0 – ~14.  Thresholds from scan_signals_v2:
    score ≥ 10 → full sell
    5 ≤ score < 10 → half sell

This adapter negates the score: returned value is in [-14, 0].
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from evolution.base_signal import BaseSignal
from evolution.adapted._adapter_utils import rolling_score, _ensure_singal_cal_on_path


class S1Signal(BaseSignal):
    """
    S1 卖出信号适配器

    Wraps calculate_s1_score() from S1_module.
    Returns NEGATIVE scores: more negative = stronger sell.
    Full-sell threshold: score ≤ -10 (i.e. original ≥ 10).
    """

    @property
    def name(self) -> str:
        return "s1"

    @property
    def source(self) -> str:
        return "adapted"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "S1卖出信号 (高点放量阴线+次高点识别，负分=看空)"

    def define_params(self, trial) -> dict:
        return {
            # Full-sell threshold on the ORIGINAL (positive) score
            "full_sell_threshold": trial.suggest_float("full_sell_threshold", 8.0, 12.0),
            # Half-sell threshold
            "half_sell_threshold": trial.suggest_float("half_sell_threshold", 4.0, 8.0),
            "min_rows": trial.suggest_int("min_rows", 60, 120),
        }

    def calculate(self, df: pd.DataFrame, params: dict) -> pd.Series:
        _ensure_singal_cal_on_path()
        from S1_module import calculate_s1_score  # type: ignore

        code     = str(df.attrs.get("code", "UNKNOWN"))
        min_rows = int(params.get("min_rows", 60))

        # Negate: positive raw S1 score → negative signal (bearish)
        def _neg_score(ind) -> float:
            return -float(calculate_s1_score(ind))

        raw = rolling_score(df, code, _neg_score, min_rows=min_rows)
        return pd.Series(raw, index=df.index, dtype=np.float64)
