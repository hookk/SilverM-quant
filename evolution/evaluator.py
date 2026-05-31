"""
evolution/evaluator.py — Signal Evaluator (L1 + L2)
=====================================================

Two evaluation tiers for evolved signals:

L1  Fast cross-sectional evaluation
    ─────────────────────────────────
    • Runs in seconds.  Used after every Optuna trial.
    • Metrics: forward-return Sharpe (T+3/5/10/20), IC, win-rate, coverage.
    • Operates on raw signal Series + price DataFrame per stock.
    • No transaction costs, no position sizing — pure predictive-power measure.

L2  Full backtest (Valid set only)
    ─────────────────────────────────
    • Runs in ~minutes.  Triggered every N iterations by the orchestrator.
    • Calls the project's existing backtest/engine.py with a signal-derived
      portfolio rule (long top-decile, short bottom-decile, hold T+5 days).
    • Returns summary metrics + quarterly / industry / cap-group breakdowns.
    • Results are shown to the Agent as feedback; Test-set L2 is human-only.

Public API
----------
    evaluator = SignalEvaluator(primary_horizon=5)

    # Called by orchestrator for every iteration
    result = evaluator.evaluate_signal(
        signal_instance=sig,
        data_dict=valid_dict,   # {code: DataFrame}
        params=best_params,
        segment="valid",
        run_l2=False,
    )
    # result["l1"]["primary"]  →  T+5 cross-sectional Sharpe

    # L1-only (called by OptunaSearcher per trial)
    metrics = evaluator.l1_cross_section(signal_dict, data_dict)
    # signal_dict: {code: pre-computed Series}

Implementation notes
--------------------
• L2 calls backtest/engine.py via its Python API (not subprocess).
  If the engine raises ImportError (e.g. test environment), L2 is skipped
  gracefully — L2 result will be None.
• All NaN / Inf values in forward returns are dropped before computing metrics.
• Cross-sectional rank is used for IC (Spearman) to reduce outlier sensitivity.
"""

from __future__ import annotations

import logging
import math
import warnings
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_HORIZONS     = [3, 5, 10, 20]   # forward-return look-ahead days
_DEFAULT_PRIMARY      = 5                # primary metric horizon
_MIN_SAMPLES          = 30               # minimum valid (signal, ret) pairs for a metric
_TRADING_DAYS_PER_YEAR = 252


# ─────────────────────────────────────────────────────────────────────────────
# Metric helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_float(v: Any, default: float = float("nan")) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _sharpe(returns: np.ndarray, annualise: bool = True) -> float:
    """Compute Sharpe ratio of a returns series (std ≠ 0 guard)."""
    if len(returns) < 2:
        return float("nan")
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        mu  = np.nanmean(returns)
        std = np.nanstd(returns, ddof=1)
    if std == 0 or not math.isfinite(std):
        return float("nan")
    sr = mu / std
    if annualise:
        sr *= math.sqrt(_TRADING_DAYS_PER_YEAR)
    return float(sr)


def _ic(signals: np.ndarray, fwd_returns: np.ndarray) -> float:
    """Rank IC (Spearman correlation) between signals and forward returns."""
    mask = np.isfinite(signals) & np.isfinite(fwd_returns)
    if mask.sum() < _MIN_SAMPLES:
        return float("nan")
    s = signals[mask]
    r = fwd_returns[mask]
    # rank-based (Spearman) via numpy
    from scipy.stats import spearmanr  # soft import
    try:
        corr, _ = spearmanr(s, r)
        return float(corr)
    except Exception:
        # fallback: numpy pearson on ranks
        rs = _rank(s)
        rr = _rank(r)
        if np.std(rs) == 0 or np.std(rr) == 0:
            return float("nan")
        return float(np.corrcoef(rs, rr)[0, 1])


def _rank(arr: np.ndarray) -> np.ndarray:
    """Return rank array (1-indexed, average ties)."""
    order = arr.argsort()
    ranks = np.empty_like(order, dtype=float)
    ranks[order] = np.arange(1, len(arr) + 1)
    return ranks


def _win_rate(signals: np.ndarray, fwd_returns: np.ndarray) -> float:
    """Fraction of observations where sign(signal) == sign(forward return)."""
    mask = np.isfinite(signals) & np.isfinite(fwd_returns) & (signals != 0)
    if mask.sum() < _MIN_SAMPLES:
        return float("nan")
    correct = np.sign(signals[mask]) == np.sign(fwd_returns[mask])
    return float(correct.mean())


def _coverage(signal_series: pd.Series) -> float:
    """Fraction of non-NaN signal values."""
    if len(signal_series) == 0:
        return 0.0
    return float(signal_series.notna().mean())


def _compute_forward_return(df: pd.DataFrame, horizon: int) -> pd.Series:
    """
    Compute T+horizon forward return from the 'close' column.

    Returns pct change shifted back by horizon:
        ret[t] = (close[t+horizon] - close[t]) / close[t]
    """
    close = df["close"]
    fwd   = close.shift(-horizon) / close - 1.0
    return fwd


# ─────────────────────────────────────────────────────────────────────────────
# L1 evaluator core
# ─────────────────────────────────────────────────────────────────────────────

def _l1_metrics_for_stock(
    signal: pd.Series,
    df: pd.DataFrame,
    horizons: List[int],
) -> Dict[str, Any]:
    """
    Compute per-stock L1 metrics for a single stock.

    Returns a dict of arrays suitable for pooling across stocks.
    Keys: 'signal', 'ret_T{h}' for each horizon.
    """
    sig_arr = signal.values.astype(float)
    result: Dict[str, np.ndarray] = {"signal": sig_arr}

    for h in horizons:
        fwd = _compute_forward_return(df, h)
        result[f"ret_t{h}"] = fwd.values.astype(float)

    return result


def _aggregate_l1(
    pooled: Dict[str, np.ndarray],
    horizons: List[int],
    primary_horizon: int,
) -> Dict[str, Any]:
    """
    Compute all L1 metrics from pooled (stock-stacked) arrays.

    pooled keys: 'signal', 'ret_t{h}' for each horizon.
    """
    metrics: Dict[str, Any] = {}
    sig = pooled["signal"]

    for h in horizons:
        ret_key = f"ret_t{h}"
        ret = pooled.get(ret_key)
        if ret is None:
            continue

        mask = np.isfinite(sig) & np.isfinite(ret)
        s_clean = sig[mask]
        r_clean = ret[mask]

        if len(s_clean) < _MIN_SAMPLES:
            metrics[f"sharpe_t{h}"]   = float("nan")
            metrics[f"ic_t{h}"]       = float("nan")
            metrics[f"win_rate_t{h}"] = float("nan")
            metrics[f"mean_ret_t{h}"] = float("nan")
            continue

        # Long-minus-short return: top-tercile minus bottom-tercile of signal
        p33, p67 = np.nanpercentile(s_clean, [33, 67])
        long_mask  = s_clean >= p67
        short_mask = s_clean <= p33
        long_ret   = r_clean[long_mask]
        short_ret  = r_clean[short_mask]

        # Long-short per-observation difference (for Sharpe)
        # We compute a portfolio return series across all stocks/dates
        ls_ret = np.concatenate([long_ret, -short_ret]) if len(short_ret) > 0 else long_ret

        metrics[f"sharpe_t{h}"]   = _sharpe(ls_ret)
        metrics[f"ic_t{h}"]       = _ic(s_clean, r_clean)
        metrics[f"win_rate_t{h}"] = _win_rate(s_clean, r_clean)
        metrics[f"mean_ret_t{h}"] = float(np.nanmean(long_ret)) if len(long_ret) > 0 else float("nan")

    # Primary metric
    primary_sharpe = metrics.get(f"sharpe_t{primary_horizon}", float("nan"))
    metrics["primary"] = primary_sharpe

    # Coverage (fraction of non-NaN signals across all stocks)
    metrics["coverage"] = float(np.isfinite(sig).mean())
    metrics["n_obs"]    = int(np.isfinite(sig).sum())

    return metrics


# ─────────────────────────────────────────────────────────────────────────────
# L2 backtest helpers
# ─────────────────────────────────────────────────────────────────────────────

def _build_l2_feedback_text(l2_result: Dict[str, Any]) -> str:
    """
    Format L2 backtest result as human-readable text for Agent prompt injection.
    """
    if not l2_result:
        return "(L2 回测结果不可用)"

    lines = []
    summary = l2_result.get("summary", {})

    def _fmt(key: str, label: str, fmt: str = ".4f") -> None:
        v = summary.get(key)
        if v is not None and math.isfinite(float(v)):
            lines.append(f"{label}: {float(v):{fmt}}")

    _fmt("total_return",    "总收益",       ".2%")
    _fmt("annual_return",   "年化收益",     ".2%")
    _fmt("sharpe_ratio",    "Sharpe")
    _fmt("max_drawdown",    "最大回撤",     ".2%")
    _fmt("win_rate",        "胜率",         ".2%")
    _fmt("turnover_rate",   "换手率",       ".2%")

    # Quarterly breakdown
    q_breakdown = l2_result.get("quarterly", {})
    if q_breakdown:
        lines.append("\n季度收益分解:")
        for period, ret in sorted(q_breakdown.items()):
            if ret is not None and math.isfinite(float(ret)):
                lines.append(f"  {period}: {float(ret):.2%}")

    # Cap-group breakdown
    cap_breakdown = l2_result.get("cap_group", {})
    if cap_breakdown:
        lines.append("\n市值分组表现 (T+5 Sharpe):")
        for group, sharpe in sorted(cap_breakdown.items()):
            if sharpe is not None and math.isfinite(float(sharpe)):
                lines.append(f"  {group}: Sharpe={float(sharpe):.3f}")

    # Industry breakdown (top 5 by absolute Sharpe)
    ind_breakdown = l2_result.get("industry", {})
    if ind_breakdown:
        top5 = sorted(
            ind_breakdown.items(),
            key=lambda x: abs(float(x[1])) if x[1] is not None and math.isfinite(float(x[1])) else 0,
            reverse=True,
        )[:5]
        if top5:
            lines.append("\n行业表现 Top 5 (T+5 Sharpe):")
            for ind, sharpe in top5:
                lines.append(f"  {ind}: {float(sharpe):.3f}")

    return "\n".join(lines) if lines else "(L2 结果为空)"


# ─────────────────────────────────────────────────────────────────────────────
# SignalEvaluator
# ─────────────────────────────────────────────────────────────────────────────

class SignalEvaluator:
    """
    Two-tier signal evaluator.

    L1: fast cross-sectional metrics (Sharpe, IC, win-rate, coverage).
    L2: full backtest via backtest/engine.py (runs on Valid segment only).

    Args:
        horizons:         Forward-return horizons in trading days (default [3,5,10,20]).
        primary_horizon:  Horizon used as the main optimisation target (default 5).
        top_pct:          Top-decile fraction for L2 long portfolio (default 0.1).
        bot_pct:          Bottom-decile fraction for L2 short portfolio (default 0.1).
    """

    def __init__(
        self,
        horizons: Optional[List[int]] = None,
        primary_horizon: int = _DEFAULT_PRIMARY,
        top_pct: float = 0.1,
        bot_pct: float = 0.1,
    ):
        self.horizons        = horizons or _DEFAULT_HORIZONS
        self.primary_horizon = primary_horizon
        self.top_pct         = top_pct
        self.bot_pct         = bot_pct

        if primary_horizon not in self.horizons:
            self.horizons = sorted(set(self.horizons) | {primary_horizon})

    # ── Public: full evaluation ───────────────────────────────────────────────

    def evaluate_signal(
        self,
        signal_instance: Any,         # BaseSignal subclass instance
        data_dict: Dict[str, pd.DataFrame],
        params: Dict[str, Any],
        segment: str = "valid",
        run_l2: bool = False,
    ) -> Dict[str, Any]:
        """
        Evaluate a signal instance on a data dict.

        Steps:
          1. Run _safe_calculate() for each stock.
          2. Pool results → compute L1 metrics.
          3. If run_l2=True → call L2 full backtest.

        Args:
            signal_instance: Instantiated BaseSignal subclass.
            data_dict:       {code: DataFrame} for one segment.
            params:          Resolved parameter dict (from Optuna or defaults).
            segment:         Segment label ('train' | 'valid') for logging.
            run_l2:          Whether to also run the full L2 backtest.

        Returns:
            {
              "l1": { primary, sharpe_t5, ic_t5, win_rate_t5, coverage, ... },
              "l2": { summary, quarterly, cap_group, industry, text } | None,
              "signal_dict": {code: Series},   # computed signals
            }
        """
        # ── Step 1: Compute signals ───────────────────────────────────────────
        signal_dict: Dict[str, pd.Series] = {}
        n_failed = 0

        for code, df in data_dict.items():
            try:
                sig = signal_instance._safe_calculate(df, params)
                signal_dict[code] = sig
            except Exception as e:
                n_failed += 1
                logger.debug("evaluate_signal: %s failed: %s", code, e)

        if n_failed > 0:
            logger.debug(
                "evaluate_signal [%s]: %d/%d stocks failed signal calculation",
                segment, n_failed, len(data_dict),
            )

        # ── Step 2: L1 metrics ────────────────────────────────────────────────
        l1_metrics = self.l1_cross_section(signal_dict, data_dict)
        l1_metrics["n_stocks_ok"]     = len(signal_dict)
        l1_metrics["n_stocks_failed"] = n_failed

        logger.info(
            "L1 [%s]: primary=%.4f ic_t5=%.4f coverage=%.2f n=%d",
            segment,
            _safe_float(l1_metrics.get("primary")),
            _safe_float(l1_metrics.get(f"ic_t{self.primary_horizon}")),
            _safe_float(l1_metrics.get("coverage")),
            l1_metrics.get("n_stocks_ok", 0),
        )

        # ── Step 3: Optional L2 ───────────────────────────────────────────────
        l2_result = None
        if run_l2:
            l2_result = self._run_l2_backtest(signal_dict, data_dict, segment)
            if l2_result:
                l2_result["text"] = _build_l2_feedback_text(l2_result)

        return {
            "l1":          l1_metrics,
            "l2":          l2_result,
            "signal_dict": signal_dict,
        }

    # ── Public: L1 cross-section (called by OptunaSearcher per trial) ─────────

    def l1_cross_section(
        self,
        signal_dict: Dict[str, pd.Series],
        data_dict: Dict[str, pd.DataFrame],
    ) -> Dict[str, Any]:
        """
        Compute L1 metrics from pre-computed signal dict.

        Called directly by OptunaSearcher (signals already computed outside
        to avoid re-running calculate() for every metric computation).

        Args:
            signal_dict: {code: pd.Series of signal values}
            data_dict:   {code: pd.DataFrame with 'close' column}

        Returns:
            Dict with keys: primary, sharpe_t{h}, ic_t{h}, win_rate_t{h},
                            mean_ret_t{h}, coverage, n_obs.
        """
        if not signal_dict:
            return {"primary": float("nan"), "coverage": 0.0, "n_obs": 0}

        # Pool signal values + forward returns across all stocks
        pooled_arrays: Dict[str, List[np.ndarray]] = {
            "signal": []
        }
        for h in self.horizons:
            pooled_arrays[f"ret_t{h}"] = []

        for code, sig in signal_dict.items():
            df = data_dict.get(code)
            if df is None or len(df) == 0:
                continue

            sig_arr = sig.values.astype(float)
            pooled_arrays["signal"].append(sig_arr)

            for h in self.horizons:
                try:
                    fwd = _compute_forward_return(df, h).values.astype(float)
                    # Align lengths (signal and df should be same length)
                    min_len = min(len(sig_arr), len(fwd))
                    pooled_arrays[f"ret_t{h}"].append(fwd[:min_len])
                    if len(sig_arr) > min_len:
                        # Re-trim signal for this horizon
                        pass  # handled below via concatenation alignment
                except Exception:
                    pooled_arrays[f"ret_t{h}"].append(np.full_like(sig_arr, np.nan))

        if not pooled_arrays["signal"]:
            return {"primary": float("nan"), "coverage": 0.0, "n_obs": 0}

        # Concatenate across all stocks
        pooled_concat: Dict[str, np.ndarray] = {}
        base_sig = np.concatenate(pooled_arrays["signal"])
        pooled_concat["signal"] = base_sig

        for h in self.horizons:
            key = f"ret_t{h}"
            arrs = pooled_arrays[key]
            if arrs:
                ret_cat = np.concatenate(arrs)
                # Align with signal if needed (truncate to common length)
                min_len = min(len(base_sig), len(ret_cat))
                pooled_concat[key]          = ret_cat[:min_len]
                pooled_concat["signal"]     = base_sig[:min_len]
            else:
                pooled_concat[key] = np.full(len(base_sig), np.nan)

        return _aggregate_l1(pooled_concat, self.horizons, self.primary_horizon)

    # ── L2 backtest ───────────────────────────────────────────────────────────

    def _run_l2_backtest(
        self,
        signal_dict: Dict[str, pd.Series],
        data_dict: Dict[str, pd.DataFrame],
        segment: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Run L2 full backtest using the project's backtest/engine.py.

        Strategy: on each rebalance date, go long the top `top_pct` of
        stocks by signal value and hold for `primary_horizon` days.

        Returns a structured result dict, or None if backtest is unavailable.
        """
        try:
            from backtest.engine import BacktestEngine
        except ImportError:
            logger.warning("L2: backtest.engine not available — skipping L2")
            return self._l2_simple_backtest(signal_dict, data_dict, segment)

        try:
            return self._l2_via_engine(signal_dict, data_dict, BacktestEngine)
        except Exception as e:
            logger.warning("L2 via engine failed: %s — falling back to simple L2", e)
            return self._l2_simple_backtest(signal_dict, data_dict, segment)

    def _l2_via_engine(
        self,
        signal_dict: Dict[str, pd.Series],
        data_dict: Dict[str, pd.DataFrame],
        BacktestEngine: type,
    ) -> Optional[Dict[str, Any]]:
        """
        Attempt to run L2 through BacktestEngine if its API is compatible.

        The engine is expected to accept a signal_func or signal_series dict.
        If the API doesn't match, raise an exception to trigger fallback.
        """
        # Build a combined signal DataFrame (date × stock)
        signal_frames = {}
        for code, sig in signal_dict.items():
            signal_frames[code] = sig

        combined = pd.DataFrame(signal_frames)

        # Try engine with signal DataFrame API
        engine = BacktestEngine()

        # Call the engine — we try a few common API patterns
        result = None
        if hasattr(engine, "run_signal_backtest"):
            result = engine.run_signal_backtest(
                signal_df    = combined,
                data_dict    = data_dict,
                hold_days    = self.primary_horizon,
                top_pct      = self.top_pct,
            )
        elif hasattr(engine, "backtest"):
            result = engine.backtest(
                signals  = combined,
                prices   = {c: df["close"] for c, df in data_dict.items()},
            )
        else:
            raise AttributeError("BacktestEngine has no compatible method")

        if result is None:
            return None

        # Normalise result to our schema
        return self._normalise_engine_result(result, data_dict)

    def _normalise_engine_result(
        self,
        raw: Any,
        data_dict: Dict[str, pd.DataFrame],
    ) -> Dict[str, Any]:
        """
        Convert whatever BacktestEngine returns into our standard schema:
        { summary, quarterly, cap_group, industry }
        """
        if isinstance(raw, dict):
            # Already a dict — try to extract known keys
            summary = {
                "total_return":  _safe_float(raw.get("total_return",  raw.get("total_pnl"))),
                "annual_return": _safe_float(raw.get("annual_return",  raw.get("cagr"))),
                "sharpe_ratio":  _safe_float(raw.get("sharpe_ratio",   raw.get("sharpe"))),
                "max_drawdown":  _safe_float(raw.get("max_drawdown",   raw.get("mdd"))),
                "win_rate":      _safe_float(raw.get("win_rate")),
                "turnover_rate": _safe_float(raw.get("turnover_rate",  raw.get("turnover"))),
            }
            return {
                "summary":   summary,
                "quarterly": raw.get("quarterly", {}),
                "cap_group": raw.get("cap_group", {}),
                "industry":  raw.get("industry", {}),
            }

        if hasattr(raw, "to_dict"):
            return self._normalise_engine_result(raw.to_dict(), data_dict)

        return {"summary": {}, "quarterly": {}, "cap_group": {}, "industry": {}}

    def _l2_simple_backtest(
        self,
        signal_dict: Dict[str, pd.Series],
        data_dict: Dict[str, pd.DataFrame],
        segment: str,
    ) -> Optional[Dict[str, Any]]:
        """
        Lightweight pure-pandas L2 backtest (fallback when engine unavailable).

        Strategy:
          • Build a common date index across all stocks.
          • On each date, rank stocks by signal value.
          • Go long the top `top_pct` decile; compute T+hold_days return.
          • Portfolio return = equal-weighted average of long-leg returns.
          • Compute Sharpe, total return, max drawdown.
          • Breakdown by calendar quarter.
          • Breakdown by cap group (using total_mv if available).
        """
        hold = self.primary_horizon

        # ── Build wide DataFrames ─────────────────────────────────────────────
        close_frames   = {}
        signal_frames  = {}
        cap_frames     = {}

        for code, df in data_dict.items():
            sig = signal_dict.get(code)
            if sig is None:
                continue
            close_frames[code]  = df["close"]
            signal_frames[code] = sig
            if "total_mv" in df.columns:
                cap_frames[code] = df["total_mv"]

        if not close_frames:
            return None

        close_df  = pd.DataFrame(close_frames).sort_index()
        signal_df = pd.DataFrame(signal_frames).reindex(close_df.index)
        cap_df    = pd.DataFrame(cap_frames).reindex(close_df.index) if cap_frames else None

        # ── Compute forward returns (T+hold) for each stock ───────────────────
        fwd_ret_df = close_df.shift(-hold) / close_df - 1.0

        # ── Build portfolio returns ───────────────────────────────────────────
        portfolio_rets: List[Tuple[pd.Timestamp, float]] = []

        for date in signal_df.index[:-hold]:
            day_sig = signal_df.loc[date].dropna()
            if len(day_sig) < 5:
                continue

            n_long = max(1, int(len(day_sig) * self.top_pct))
            top_stocks = day_sig.nlargest(n_long).index.tolist()

            day_ret = fwd_ret_df.loc[date]
            long_rets = day_ret[top_stocks].dropna()

            if len(long_rets) == 0:
                continue

            portfolio_rets.append((date, float(long_rets.mean())))

        if not portfolio_rets:
            return {"summary": {}, "quarterly": {}, "cap_group": {}, "industry": {}}

        ret_series = pd.Series(
            dict(portfolio_rets),
            dtype=float,
        ).sort_index()

        # ── Summary metrics ───────────────────────────────────────────────────
        total_ret   = float((1 + ret_series).prod() - 1)
        n_days      = len(ret_series)
        annual_ret  = float((1 + total_ret) ** (_TRADING_DAYS_PER_YEAR / max(n_days, 1)) - 1)
        sharpe      = _sharpe(ret_series.values, annualise=True)

        # Max drawdown
        cum  = (1 + ret_series).cumprod()
        peak = cum.cummax()
        dd   = (cum - peak) / peak
        mdd  = float(dd.min())

        win_rate = float((ret_series > 0).mean()) if len(ret_series) > 0 else float("nan")

        summary = {
            "total_return":  total_ret,
            "annual_return": annual_ret,
            "sharpe_ratio":  sharpe,
            "max_drawdown":  mdd,
            "win_rate":      win_rate,
            "turnover_rate": float("nan"),  # simple backtest doesn't track turnover
        }

        # ── Quarterly breakdown ───────────────────────────────────────────────
        quarterly: Dict[str, float] = {}
        try:
            qret = ret_series.resample("QE").apply(lambda r: float((1 + r).prod() - 1))
            for ts, v in qret.items():
                quarterly[str(ts.date())] = _safe_float(v)
        except Exception:
            pass

        # ── Cap-group breakdown ───────────────────────────────────────────────
        cap_group: Dict[str, float] = {}
        if cap_df is not None:
            try:
                cap_group = self._cap_group_sharpe(signal_df, fwd_ret_df, cap_df)
            except Exception as e:
                logger.debug("cap_group breakdown failed: %s", e)

        return {
            "summary":   summary,
            "quarterly": quarterly,
            "cap_group": cap_group,
            "industry":  {},   # industry data not available without extra joins
        }

    def _cap_group_sharpe(
        self,
        signal_df: pd.DataFrame,
        fwd_ret_df: pd.DataFrame,
        cap_df: pd.DataFrame,
        thresholds_bn: Tuple[float, float] = (20.0, 200.0),
    ) -> Dict[str, float]:
        """
        Compute T+primary_horizon Sharpe for small / mid / large cap groups.

        thresholds_bn: (small_cap_max, mid_cap_max) in billion CNY.
        cap_df is in 万元 → divide by 100_000 to get 亿元.
        """
        # Use median cap per stock over the period
        median_cap = (cap_df / 100_000).median(axis=0)  # unit: 亿元
        small_th, mid_th = thresholds_bn  # 亿元 thresholds

        groups: Dict[str, List[str]] = {"small": [], "mid": [], "large": []}
        for code, mv in median_cap.items():
            if not math.isfinite(mv):
                continue
            if mv <= small_th:
                groups["small"].append(code)
            elif mv <= mid_th:
                groups["mid"].append(code)
            else:
                groups["large"].append(code)

        result: Dict[str, float] = {}
        for grp_name, codes in groups.items():
            if not codes:
                result[grp_name] = float("nan")
                continue
            grp_sig = signal_df[codes].values.flatten()
            grp_ret = fwd_ret_df[codes].values.flatten()
            mask    = np.isfinite(grp_sig) & np.isfinite(grp_ret) & (grp_sig != 0)
            if mask.sum() < _MIN_SAMPLES:
                result[grp_name] = float("nan")
                continue
            s = grp_sig[mask]
            r = grp_ret[mask]
            p33, p67 = np.nanpercentile(s, [33, 67])
            long_ret  = r[s >= p67]
            short_ret = r[s <= p33]
            ls = np.concatenate([long_ret, -short_ret]) if len(short_ret) > 0 else long_ret
            result[grp_name] = _sharpe(ls)

        return result

    def __repr__(self) -> str:
        return (
            f"<SignalEvaluator horizons={self.horizons} "
            f"primary=T+{self.primary_horizon}>"
        )
