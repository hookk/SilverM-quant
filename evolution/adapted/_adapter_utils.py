"""
evolution/adapted/_adapter_utils.py
=====================================
Shared helpers for all adapted signal wrappers.

Key responsibility: convert a per-stock pd.DataFrame (DataPreparer output)
into the `indicators` dict that all singal_cal functions expect, using
`basic_module.calculate_indicators`.

The DataFrame columns produced by DataPreparer (after data_registry.yaml):
    open, high, low, close, volume, turnover, pct_change_raw
    (index = DatetimeIndex, rows ordered oldest → newest)

basic_module.calculate_indicators expects a DataFrame with:
    columns: code, open, high, low, close, volume
    (same row order)

So this module handles the column-name bridge and the sys.path injection
needed to import basic_module without touching the original source tree.
"""

from __future__ import annotations

import sys
import os
from pathlib import Path
from typing import Any, Dict

import numpy as np
import pandas as pd

# ── Locate signals/singal_cal on sys.path ─────────────────────────────────────
# evolution/ is at <repo>/evolution/
# singal_cal is at <repo>/signals/singal_cal/
_REPO_ROOT   = Path(__file__).parent.parent.parent    # <repo>/
_SINGAL_CAL  = _REPO_ROOT / "signals" / "singal_cal"

def _ensure_singal_cal_on_path() -> None:
    """Add signals/singal_cal to sys.path (idempotent)."""
    target = str(_SINGAL_CAL)
    if target not in sys.path:
        sys.path.insert(0, target)

_ensure_singal_cal_on_path()


# ── DataFrame → indicators dict ───────────────────────────────────────────────

def df_to_indicators(df: pd.DataFrame, code: str) -> Dict[str, Any]:
    """
    Convert a DataPreparer stock DataFrame to the indicators dict.

    Args:
        df:    DatetimeIndex DataFrame from DataPreparer.
               Required columns: open, high, low, close, volume.
               Column names follow data_registry.yaml exposed names.
        code:  Stock ts_code, e.g. "000001.SZ".

    Returns:
        indicators dict as expected by singal_cal scoring functions.

    Raises:
        ImportError:  If basic_module cannot be found.
        ValueError:   If required columns are missing or df has < 2 rows.
    """
    _ensure_singal_cal_on_path()

    # Validate minimum required columns
    required = {"open", "high", "low", "close", "volume"}
    missing  = required - set(df.columns)
    if missing:
        raise ValueError(
            f"df_to_indicators: DataFrame for {code} is missing columns: {missing}"
        )
    if len(df) < 2:
        raise ValueError(
            f"df_to_indicators: DataFrame for {code} has only {len(df)} rows "
            "(minimum 2 required)."
        )

    from basic_module import calculate_indicators  # type: ignore

    # Build a minimal DataFrame that basic_module.calculate_indicators accepts
    work = df[["open", "high", "low", "close", "volume"]].copy()
    work.insert(0, "code", code)
    work = work.reset_index(drop=True)

    return calculate_indicators(work)


# ── Rolling-window helper ─────────────────────────────────────────────────────

def rolling_score(
    df: pd.DataFrame,
    code: str,
    score_fn,
    min_rows: int = 120,
) -> np.ndarray:
    """
    Apply a scalar score function to a sliding window over df.

    For each row i (0-indexed), calls score_fn(indicators_at_row_i).
    Rows with fewer than min_rows history are filled with NaN.

    This is the core vectorisation bridge: the original scoring functions
    operate on a single day's indicators dict; we iterate and collect scores.

    Args:
        df:       Full stock DataFrame (oldest → newest).
        code:     Stock ts_code.
        score_fn: Callable(indicators) → float  (the singal_cal function).
        min_rows: Minimum history rows before scoring starts.

    Returns:
        np.ndarray of float64, length == len(df).
    """
    n      = len(df)
    scores = np.full(n, np.nan, dtype=np.float64)

    for i in range(min_rows - 1, n):
        window = df.iloc[: i + 1]
        try:
            ind = df_to_indicators(window, code)
            scores[i] = float(score_fn(ind))
        except Exception:
            # Keep NaN for this bar — don't propagate errors
            pass

    return scores
