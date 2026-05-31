"""
evolution/adapted/blkb2_signal.py
===================================
Adapter: wraps the composite BLKB2 signal (暴力K + B2 + 倍量柱 + J拐头向上).

This is the strongest buy composite in the system:
    MACD DIF ≥ 0  AND  趋势线 > 多空线  AND  B2 ≥ threshold
    AND  暴力K  AND  倍量柱  AND  J拐头向上

Score formula (from scan_signals_v2):
    score = BLK*0.5 + B2*0.6 + 倍量柱*0.2 + J拐头向上*0.1
where BLK=7, 倍量柱=10, J拐头向上=10 when triggered.

Because BLKB2 depends on B2 score, we compute B2 first per bar.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from evolution.base_signal import BaseSignal
from evolution.adapted._adapter_utils import (
    df_to_indicators, _ensure_singal_cal_on_path
)


class BLKB2Signal(BaseSignal):
    """
    BLKB2 复合买入信号适配器

    Combines 暴力K + B2评分 + 倍量柱 + J拐头向上.
    Strongest buy composite — all 4 sub-conditions must fire.
    """

    @property
    def name(self) -> str:
        return "blkb2"

    @property
    def source(self) -> str:
        return "adapted"

    @property
    def version(self) -> str:
        return "1.0.0"

    @property
    def description(self) -> str:
        return "BLKB2复合买入信号 (暴力K+B2+倍量柱+J拐头向上四重共振)"

    def define_params(self, trial) -> dict:
        return {
            # B2 minimum score gate (original: 8)
            "b2_threshold": trial.suggest_float("b2_threshold", 5.0, 12.0),
            # Composite score weights
            "w_blk":  trial.suggest_float("w_blk",  0.3, 0.8),
            "w_b2":   trial.suggest_float("w_b2",   0.4, 0.9),
            "w_blz":  trial.suggest_float("w_blz",  0.1, 0.4),
            "w_jt":   trial.suggest_float("w_jt",   0.05, 0.3),
            "min_rows": trial.suggest_int("min_rows", 60, 180),
        }

    def calculate(self, df: pd.DataFrame, params: dict) -> pd.Series:
        _ensure_singal_cal_on_path()
        from B2_strategy_module   import calculate_b2_score  # type: ignore
        from BLKB2_strategy_module import (                   # type: ignore
            check_暴力K, check_倍量柱, check_J拐头向上
        )

        code         = str(df.attrs.get("code", "UNKNOWN"))
        b2_threshold = float(params.get("b2_threshold", 8.0))
        w_blk        = float(params.get("w_blk",  0.5))
        w_b2         = float(params.get("w_b2",   0.6))
        w_blz        = float(params.get("w_blz",  0.2))
        w_jt         = float(params.get("w_jt",   0.1))
        min_rows     = int(params.get("min_rows", 120))

        n      = len(df)
        scores = np.full(n, np.nan, dtype=np.float64)

        for i in range(min_rows - 1, n):
            window = df.iloc[: i + 1]
            try:
                ind = df_to_indicators(window, code)

                # Gate conditions
                macd_bull    = ind["dif"] >= 0
                trend_ok     = ind["知行短期趋势线"] > ind["知行多空线"]
                b2_score     = calculate_b2_score(ind)
                b2_ok        = b2_score >= b2_threshold

                if not (macd_bull and trend_ok and b2_ok):
                    scores[i] = 0.0
                    continue

                blk_ok = check_暴力K(ind)
                blz_ok = check_倍量柱(ind)
                jt_ok  = check_J拐头向上(ind)

                if blk_ok and blz_ok and jt_ok:
                    scores[i] = (
                        7.0  * w_blk
                        + b2_score * w_b2
                        + 10.0 * w_blz
                        + 10.0 * w_jt
                    )
                else:
                    scores[i] = 0.0
            except Exception:
                pass

        return pd.Series(scores, index=df.index, dtype=np.float64)
