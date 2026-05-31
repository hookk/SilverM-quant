"""
evolution/indicators_lib.py — Technical Indicators Library
==========================================================

Thin, uniform wrapper over TA-Lib (with a pure-numpy fallback for
environments where TA-Lib is not installed).

All functions follow the same contract:
  • Inputs:  numpy arrays (float64). Pass .values from a pd.Series.
  • Output:  numpy array (float64), same length as the longest input.
             Leading values that cannot be computed are filled with np.nan.
  • No side effects, no global state.

Design decisions:
  • numpy array in / numpy array out: callers own the pd.Series wrapping.
    This keeps the library usable in both pandas and numba contexts.
  • All functions handle NaN gracefully (propagate or skip, documented).
  • TA-Lib availability is checked once at import; a pure-numpy fallback
    is provided for MA, EMA, RSI, Bollinger, ATR so tests pass without TA-Lib.

Available functions
-------------------
  Trend:      ma, ema, dema, tema
  Momentum:   macd, rsi, kdj, cci, williams_r, mfi
  Volatility: bollinger, atr, stddev
  Volume:     obv
  Other:      adx

Usage in signal code
--------------------
    from evolution.indicators_lib import ma, rsi, bollinger, atr

    close = df["close"].values
    signal = rsi(close, period=14) - 50   # center around 0
"""

from __future__ import annotations

import logging
from typing import Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ── TA-Lib availability ───────────────────────────────────────────────────────
try:
    import talib as _ta
    _TALIB = True
    logger.info("indicators_lib: TA-Lib available.")
except ImportError:
    _TALIB = False
    logger.warning(
        "indicators_lib: TA-Lib not installed. "
        "Falling back to pure-numpy implementations. "
        "Install TA-Lib for full indicator coverage: "
        "  pip install TA-Lib"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _to_f64(arr: np.ndarray) -> np.ndarray:
    """Ensure array is contiguous float64."""
    return np.ascontiguousarray(arr, dtype=np.float64)


def _nan_array(n: int) -> np.ndarray:
    out = np.empty(n, dtype=np.float64)
    out[:] = np.nan
    return out


def _rolling_mean(arr: np.ndarray, period: int) -> np.ndarray:
    """Pure-numpy simple moving average."""
    n = len(arr)
    out = _nan_array(n)
    if period > n:
        return out
    kernel = np.ones(period) / period
    conv = np.convolve(arr, kernel, mode="full")
    out[period - 1:] = conv[period - 1: n]
    return out


def _ema_numpy(arr: np.ndarray, period: int) -> np.ndarray:
    """Pure-numpy EMA (exponential moving average)."""
    n = len(arr)
    out = _nan_array(n)
    if period > n:
        return out
    k = 2.0 / (period + 1)
    # Seed: first non-NaN SMA over `period` bars
    for i in range(period - 1, n):
        window = arr[max(0, i - period + 1): i + 1]
        if np.any(np.isnan(window)):
            continue
        out[period - 1] = np.nanmean(window[:period])
        break
    for i in range(period, n):
        if np.isnan(out[i - 1]) or np.isnan(arr[i]):
            out[i] = out[i - 1]
        else:
            out[i] = arr[i] * k + out[i - 1] * (1 - k)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# §1  Trend Indicators
# ─────────────────────────────────────────────────────────────────────────────

def ma(close: np.ndarray, period: int = 20) -> np.ndarray:
    """
    Simple Moving Average.

    Args:
        close:  1-D array of closing prices.
        period: Lookback window. Default 20.

    Returns:
        np.ndarray (float64), length == len(close), NaN for first period-1 bars.
    """
    close = _to_f64(close)
    if _TALIB:
        return _ta.SMA(close, timeperiod=period)
    return _rolling_mean(close, period)


def ema(close: np.ndarray, period: int = 20) -> np.ndarray:
    """
    Exponential Moving Average.

    Args:
        close:  1-D array of closing prices.
        period: EMA period. Default 20.

    Returns:
        np.ndarray (float64).
    """
    close = _to_f64(close)
    if _TALIB:
        return _ta.EMA(close, timeperiod=period)
    return _ema_numpy(close, period)


def dema(close: np.ndarray, period: int = 20) -> np.ndarray:
    """Double EMA (DEMA = 2*EMA - EMA(EMA))."""
    close = _to_f64(close)
    if _TALIB:
        return _ta.DEMA(close, timeperiod=period)
    e1 = _ema_numpy(close, period)
    valid = ~np.isnan(e1)
    e2 = _nan_array(len(close))
    if valid.any():
        e2[valid] = _ema_numpy(e1[valid], period)
    return 2 * e1 - e2


def tema(close: np.ndarray, period: int = 20) -> np.ndarray:
    """Triple EMA (TEMA = 3*EMA - 3*EMA(EMA) + EMA(EMA(EMA)))."""
    close = _to_f64(close)
    if _TALIB:
        return _ta.TEMA(close, timeperiod=period)
    e1 = _ema_numpy(close, period)
    e2 = _ema_numpy(e1, period)
    e3 = _ema_numpy(e2, period)
    return 3 * e1 - 3 * e2 + e3


# ─────────────────────────────────────────────────────────────────────────────
# §2  Momentum Indicators
# ─────────────────────────────────────────────────────────────────────────────

def macd(
    close: np.ndarray,
    fast: int = 12,
    slow: int = 26,
    signal_period: int = 9,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    MACD, Signal, Histogram.

    Returns:
        (macd_line, signal_line, histogram) — each float64 array.
    """
    close = _to_f64(close)
    if _TALIB:
        return _ta.MACD(close, fastperiod=fast, slowperiod=slow, signalperiod=signal_period)
    # Pure-numpy fallback
    fast_ema = _ema_numpy(close, fast)
    slow_ema = _ema_numpy(close, slow)
    macd_line = fast_ema - slow_ema
    sig_line = _ema_numpy(macd_line, signal_period)
    histogram = macd_line - sig_line
    return macd_line, sig_line, histogram


def rsi(close: np.ndarray, period: int = 14) -> np.ndarray:
    """
    Relative Strength Index (0–100).

    Overbought: >70.  Oversold: <30.

    Args:
        close:  Closing prices.
        period: RSI period. Default 14.

    Returns:
        np.ndarray (float64) in range [0, 100].
    """
    close = _to_f64(close)
    if _TALIB:
        return _ta.RSI(close, timeperiod=period)
    # Pure-numpy fallback (Wilder smoothing)
    n = len(close)
    out = _nan_array(n)
    if n <= period:
        return out
    delta = np.diff(close)
    gain = np.where(delta > 0, delta, 0.0)
    loss = np.where(delta < 0, -delta, 0.0)
    avg_gain = np.nanmean(gain[:period])
    avg_loss = np.nanmean(loss[:period])
    for i in range(period, n - 1):
        avg_gain = (avg_gain * (period - 1) + gain[i]) / period
        avg_loss = (avg_loss * (period - 1) + loss[i]) / period
        rs = avg_gain / avg_loss if avg_loss != 0 else np.inf
        out[i + 1] = 100 - 100 / (1 + rs)
    return out


def kdj(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    fastk_period: int = 9,
    slowk_period: int = 3,
    slowd_period: int = 3,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    KDJ Stochastic Oscillator.

    Returns:
        (K, D, J) — each float64 array.
        J = 3*K - 2*D  (Chinese variant).
    """
    high = _to_f64(high)
    low = _to_f64(low)
    close = _to_f64(close)
    if _TALIB:
        k, d = _ta.STOCH(
            high, low, close,
            fastk_period=fastk_period,
            slowk_period=slowk_period,
            slowk_matype=0,
            slowd_period=slowd_period,
            slowd_matype=0,
        )
        j = 3 * k - 2 * d
        return k, d, j
    # Pure-numpy fallback
    n = len(close)
    k_arr = _nan_array(n)
    for i in range(fastk_period - 1, n):
        h = np.max(high[i - fastk_period + 1: i + 1])
        l = np.min(low[i - fastk_period + 1: i + 1])
        denom = h - l
        k_arr[i] = 100 * (close[i] - l) / denom if denom != 0 else 50.0
    k_smooth = _rolling_mean(np.where(np.isnan(k_arr), 0, k_arr), slowk_period)
    d_smooth = _rolling_mean(np.where(np.isnan(k_smooth), 0, k_smooth), slowd_period)
    j_arr = 3 * k_smooth - 2 * d_smooth
    return k_smooth, d_smooth, j_arr


def cci(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """
    Commodity Channel Index.

    Overbought: >100.  Oversold: <-100.
    """
    high = _to_f64(high)
    low = _to_f64(low)
    close = _to_f64(close)
    if _TALIB:
        return _ta.CCI(high, low, close, timeperiod=period)
    n = len(close)
    out = _nan_array(n)
    tp = (high + low + close) / 3.0
    for i in range(period - 1, n):
        window = tp[i - period + 1: i + 1]
        mean_tp = np.mean(window)
        mad = np.mean(np.abs(window - mean_tp))
        out[i] = (tp[i] - mean_tp) / (0.015 * mad) if mad != 0 else 0.0
    return out


def williams_r(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """
    Williams %R  (-100 to 0).

    Overbought: > -20.  Oversold: < -80.
    """
    high = _to_f64(high)
    low = _to_f64(low)
    close = _to_f64(close)
    if _TALIB:
        return _ta.WILLR(high, low, close, timeperiod=period)
    n = len(close)
    out = _nan_array(n)
    for i in range(period - 1, n):
        h = np.max(high[i - period + 1: i + 1])
        l = np.min(low[i - period + 1: i + 1])
        out[i] = -100 * (h - close[i]) / (h - l) if (h - l) != 0 else 0.0
    return out


def mfi(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    volume: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """
    Money Flow Index (volume-weighted RSI, 0–100).

    Overbought: >80.  Oversold: <20.
    """
    high = _to_f64(high)
    low = _to_f64(low)
    close = _to_f64(close)
    volume = _to_f64(volume)
    if _TALIB:
        return _ta.MFI(high, low, close, volume, timeperiod=period)
    n = len(close)
    out = _nan_array(n)
    tp = (high + low + close) / 3.0
    mf = tp * volume
    for i in range(period, n):
        pos_mf = sum(
            mf[j] for j in range(i - period + 1, i + 1)
            if tp[j] > tp[j - 1]
        )
        neg_mf = sum(
            mf[j] for j in range(i - period + 1, i + 1)
            if tp[j] < tp[j - 1]
        )
        mfr = pos_mf / neg_mf if neg_mf != 0 else np.inf
        out[i] = 100 - 100 / (1 + mfr)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# §3  Volatility Indicators
# ─────────────────────────────────────────────────────────────────────────────

def bollinger(
    close: np.ndarray,
    period: int = 20,
    num_std: float = 2.0,
) -> Tuple[np.ndarray, np.ndarray, np.ndarray]:
    """
    Bollinger Bands.

    Returns:
        (upper, middle, lower) — each float64 array.

    Usage in signals:
        upper, mid, lower = bollinger(close)
        pct_b = (close - lower) / (upper - lower)   # 0=lower, 1=upper
    """
    close = _to_f64(close)
    if _TALIB:
        return _ta.BBANDS(close, timeperiod=period, nbdevup=num_std, nbdevdn=num_std, matype=0)
    # Pure-numpy fallback
    n = len(close)
    mid = _rolling_mean(close, period)
    upper = _nan_array(n)
    lower = _nan_array(n)
    for i in range(period - 1, n):
        window = close[i - period + 1: i + 1]
        std = np.std(window, ddof=0)
        upper[i] = mid[i] + num_std * std
        lower[i] = mid[i] - num_std * std
    return upper, mid, lower


def atr(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """
    Average True Range.

    Measures volatility; often used for stop-loss sizing.
    ATR is not directional: it is always positive.
    """
    high = _to_f64(high)
    low = _to_f64(low)
    close = _to_f64(close)
    if _TALIB:
        return _ta.ATR(high, low, close, timeperiod=period)
    # Pure-numpy fallback
    n = len(close)
    out = _nan_array(n)
    tr = _nan_array(n)
    for i in range(1, n):
        tr[i] = max(
            high[i] - low[i],
            abs(high[i] - close[i - 1]),
            abs(low[i] - close[i - 1]),
        )
    out[period] = np.nanmean(tr[1: period + 1])
    for i in range(period + 1, n):
        out[i] = (out[i - 1] * (period - 1) + tr[i]) / period
    return out


def stddev(close: np.ndarray, period: int = 20) -> np.ndarray:
    """Rolling standard deviation of close prices."""
    close = _to_f64(close)
    if _TALIB:
        return _ta.STDDEV(close, timeperiod=period, nbdev=1)
    n = len(close)
    out = _nan_array(n)
    for i in range(period - 1, n):
        out[i] = np.std(close[i - period + 1: i + 1], ddof=0)
    return out


# ─────────────────────────────────────────────────────────────────────────────
# §4  Volume Indicators
# ─────────────────────────────────────────────────────────────────────────────

def obv(close: np.ndarray, volume: np.ndarray) -> np.ndarray:
    """
    On Balance Volume.

    Cumulative volume: added on up-days, subtracted on down-days.
    Use rate-of-change of OBV as a signal rather than raw value.
    """
    close = _to_f64(close)
    volume = _to_f64(volume)
    if _TALIB:
        return _ta.OBV(close, volume)
    n = len(close)
    out = _nan_array(n)
    out[0] = volume[0]
    for i in range(1, n):
        if np.isnan(close[i]) or np.isnan(volume[i]):
            out[i] = out[i - 1]
        elif close[i] > close[i - 1]:
            out[i] = out[i - 1] + volume[i]
        elif close[i] < close[i - 1]:
            out[i] = out[i - 1] - volume[i]
        else:
            out[i] = out[i - 1]
    return out


# ─────────────────────────────────────────────────────────────────────────────
# §5  Other / Composite
# ─────────────────────────────────────────────────────────────────────────────

def adx(
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    period: int = 14,
) -> np.ndarray:
    """
    Average Directional Movement Index (trend strength, 0–100).

    >25 = strong trend.  <20 = weak / no trend.
    ADX alone has no direction; combine with +DI/-DI for direction.
    """
    high = _to_f64(high)
    low = _to_f64(low)
    close = _to_f64(close)
    if _TALIB:
        return _ta.ADX(high, low, close, timeperiod=period)
    # Simplified pure-numpy (Wilder smoothing of |+DM|, |-DM|, TR)
    n = len(close)
    out = _nan_array(n)
    tr_arr = _nan_array(n)
    pdm = _nan_array(n)
    ndm = _nan_array(n)
    for i in range(1, n):
        tr_arr[i] = max(high[i]-low[i], abs(high[i]-close[i-1]), abs(low[i]-close[i-1]))
        up = high[i] - high[i - 1]
        dn = low[i - 1] - low[i]
        pdm[i] = up if up > dn and up > 0 else 0.0
        ndm[i] = dn if dn > up and dn > 0 else 0.0
    if n <= period:
        return out
    tr_s = np.nansum(tr_arr[1: period + 1])
    pd_s = np.nansum(pdm[1: period + 1])
    nd_s = np.nansum(ndm[1: period + 1])
    dx_arr = _nan_array(n)
    def _dx(p, nn, t):
        pdi = 100 * p / t if t else 0
        ndi = 100 * nn / t if t else 0
        s = pdi + ndi
        return 100 * abs(pdi - ndi) / s if s else 0
    dx_arr[period] = _dx(pd_s, nd_s, tr_s)
    for i in range(period + 1, n):
        tr_s = tr_s - tr_s / period + tr_arr[i]
        pd_s = pd_s - pd_s / period + pdm[i]
        nd_s = nd_s - nd_s / period + ndm[i]
        dx_arr[i] = _dx(pd_s, nd_s, tr_s)
    out[2 * period - 1] = np.nanmean(dx_arr[period: 2 * period])
    for i in range(2 * period, n):
        out[i] = (out[i - 1] * (period - 1) + dx_arr[i]) / period
    return out


# ─────────────────────────────────────────────────────────────────────────────
# §6  Convenience: returns (for signal composition)
# ─────────────────────────────────────────────────────────────────────────────

def pct_change(close: np.ndarray, period: int = 1) -> np.ndarray:
    """Percentage change over `period` bars. NaN for first `period` bars."""
    close = _to_f64(close)
    out = _nan_array(len(close))
    out[period:] = (close[period:] - close[:-period]) / np.where(
        close[:-period] != 0, close[:-period], np.nan
    )
    return out


def log_return(close: np.ndarray, period: int = 1) -> np.ndarray:
    """Log return over `period` bars."""
    close = _to_f64(close)
    out = _nan_array(len(close))
    with np.errstate(divide="ignore", invalid="ignore"):
        out[period:] = np.log(close[period:] / close[:-period])
    return out


# ─────────────────────────────────────────────────────────────────────────────
# §7  Public API list (for Agent documentation)
# ─────────────────────────────────────────────────────────────────────────────

__all__ = [
    # Trend
    "ma", "ema", "dema", "tema",
    # Momentum
    "macd", "rsi", "kdj", "cci", "williams_r", "mfi",
    # Volatility
    "bollinger", "atr", "stddev",
    # Volume
    "obv",
    # Other
    "adx",
    # Convenience
    "pct_change", "log_return",
]

INDICATOR_DOCS = {
    fn: globals()[fn].__doc__
    for fn in __all__
    if globals()[fn].__doc__
}
