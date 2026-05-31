"""
evolution/adapted/blk_signal.py
=================================
Adapter: wraps BLKB2_strategy_module.check_暴力K as a BaseSignal.

BLK (暴力K / Explosive Candle) is a binary buy signal:
    C > REF(C,1)  AND  VOL > REF_VOL * 1.8  AND  涨幅 > 4%
    AND  upper_shadow ≤ body/4  AND  VOL > VOL_MA60
    AND  趋势线 > 多空线

Returns 7.0 when triggered, 0.0 otherwise (matching scan_signals_v2 scoring).
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from evolution.base_signal import BaseSignal
from evolution.adapted._adapter_utils import rolling_score, _ensure_singal_cal_on_path


class BLKSignal(BaseSignal):
    """
    暴力K 买入信号适配器

    Wraps check_暴力K() from BLKB2_strategy_module.
    Binary: returns signal_value when triggered, 0 otherwise.
    """

    @property
    def name(self) -> str:
        return "blk"

    @property
    def source(self) -> str:
        return "adapted"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "暴力K买入信号 (放量大阳线+趋势共振)"

    def define_params(self, trial) -> dict:
        return {
            # Score value emitted when signal fires
            "signal_value": trial.suggest_float("signal_value", 5.0, 10.0),
            # Volume multiplier threshold (original: 1.8)
            "vol_multiplier": trial.suggest_float("vol_multiplier", 1.4, 2.5),
            # Min price change % (original: 4%)
            "min_pct_change": trial.suggest_float("min_pct_change", 3.0, 6.0),
            "min_rows": trial.suggest_int("min_rows", 60, 180),
        }

    def calculate(self, df: pd.DataFrame, params: dict) -> pd.Series:
        _ensure_singal_cal_on_path()
        from BLKB2_strategy_module import check_暴力K  # type: ignore

        code         = str(df.attrs.get("code", "UNKNOWN"))
        min_rows     = int(params.get("min_rows", 120))
        signal_value = float(params.get("signal_value", 7.0))

        def _score(indicators) -> float:
            return signal_value if check_暴力K(indicators) else 0.0

        raw_scores = rolling_score(df, code, _score, min_rows=min_rows)
        return pd.Series(raw_scores, index=df.index, dtype=np.float64)
