"""
evolution/base_signal.py — BaseSignal Abstract Base Class
=========================================================

All signals — hand-crafted adapters AND LLM-evolved signals — must
inherit from BaseSignal and implement exactly two abstract methods:

    define_params(trial)  →  dict
    calculate(df, params) →  pd.Series

Contract (enforced at runtime):
  • calculate() must return a pd.Series of float64
  • Length must equal len(df)
  • Index must align with df.index
  • Values: positive = bullish, negative = bearish, 0 = neutral
  • NaN is allowed (treated as no-signal by the evaluator)

Design notes:
  • Vectorized: one call processes the entire date range.
  • No state mutation: calculate() is pure — same inputs → same output.
  • Optuna integration: define_params() declares the parameter search
    space; the orchestrator calls it, not the signal author.
"""

from __future__ import annotations

import abc
import logging
from typing import Any

import numpy as np
import pandas as pd

try:
    import optuna
    _OPTUNA_AVAILABLE = True
except ImportError:
    _OPTUNA_AVAILABLE = False

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# Contract validation helpers
# ─────────────────────────────────────────────────────────────────────────────

class SignalContractError(ValueError):
    """Raised when a signal implementation violates the BaseSignal contract."""


def _validate_signal_output(
    result: Any,
    df: pd.DataFrame,
    signal_name: str,
) -> pd.Series:
    """
    Validate that calculate() output satisfies the contract.

    Args:
        result:      Return value from calculate().
        df:          Input DataFrame that was passed to calculate().
        signal_name: Signal class name (for error messages).

    Returns:
        The validated Series (dtype coerced to float64).

    Raises:
        SignalContractError: If any contract violation is detected.
    """
    # ── Type check ───────────────────────────────────────────────────────────
    if not isinstance(result, pd.Series):
        raise SignalContractError(
            f"[{signal_name}] calculate() must return pd.Series, "
            f"got {type(result).__name__}"
        )

    # ── Length check ─────────────────────────────────────────────────────────
    if len(result) != len(df):
        raise SignalContractError(
            f"[{signal_name}] calculate() returned Series of length "
            f"{len(result)}, expected {len(df)} (same as input df)"
        )

    # ── Index alignment check ─────────────────────────────────────────────────
    if not result.index.equals(df.index):
        raise SignalContractError(
            f"[{signal_name}] calculate() returned Series whose index does "
            f"not align with the input DataFrame index. "
            f"First 3 result: {result.index[:3].tolist()}, "
            f"First 3 df: {df.index[:3].tolist()}"
        )

    # ── Dtype coercion ────────────────────────────────────────────────────────
    try:
        result = result.astype(np.float64)
    except (ValueError, TypeError) as exc:
        raise SignalContractError(
            f"[{signal_name}] calculate() returned Series that cannot be "
            f"coerced to float64: {exc}"
        ) from exc

    # ── Finite / NaN ratio warning (not an error) ─────────────────────────────
    nan_ratio = result.isna().mean()
    if nan_ratio > 0.5:
        logger.warning(
            "[%s] calculate() returned >50%% NaN values (%.1f%%). "
            "This signal may have insufficient data or a logic error.",
            signal_name, nan_ratio * 100,
        )

    return result


# ─────────────────────────────────────────────────────────────────────────────
# BaseSignal
# ─────────────────────────────────────────────────────────────────────────────

class BaseSignal(abc.ABC):
    """
    Abstract base class for all signals in the evolution system.

    Subclass and implement:
        define_params(trial) → dict
        calculate(df, params) → pd.Series

    Example
    -------
    >>> class MySignal(BaseSignal):
    ...     @property
    ...     def name(self):
    ...         return "my_signal"
    ...
    ...     def define_params(self, trial):
    ...         return {
    ...             "ma_short": trial.suggest_int("ma_short", 5, 20),
    ...             "ma_long":  trial.suggest_int("ma_long", 20, 60),
    ...             "threshold": trial.suggest_float("threshold", 0.0, 1.0),
    ...         }
    ...
    ...     def calculate(self, df, params):
    ...         short = df["close"].rolling(params["ma_short"]).mean()
    ...         long  = df["close"].rolling(params["ma_long"]).mean()
    ...         return (short - long) / long  # positive = bullish
    """

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> str:
        """
        Unique signal name used for registration and file naming.
        Default: class name in snake_case. Override if needed.
        """
        cls = type(self).__name__
        # CamelCase → snake_case
        import re
        s = re.sub(r"(?<=[a-z0-9])([A-Z])", r"_\1", cls).lower()
        return s.replace("_signal", "")

    @property
    def source(self) -> str:
        """
        'adapted'  — wraps an existing hand-crafted signal
        'evolved'  — produced by the LLM evolution loop
        Override in subclasses.
        """
        return "evolved"

    @property
    def version(self) -> str:
        """Semantic version string. Override in evolved signals."""
        return "0.1.0"

    @property
    def description(self) -> str:
        """Human-readable one-liner. Override for better registry display."""
        return self.__class__.__doc__ or "(no description)"

    # ── Abstract interface ────────────────────────────────────────────────────

    @abc.abstractmethod
    def define_params(self, trial: Any) -> dict:
        """
        Declare the Optuna parameter search space.

        Called by the orchestrator to create an Optuna trial.
        If the signal has no tunable parameters, return {}.

        Args:
            trial: optuna.Trial object (or a compatible mock for unit tests).
                   Use trial.suggest_int / suggest_float / suggest_categorical.

        Returns:
            dict of {param_name: value} for the current trial.

        Example:
            return {
                "window": trial.suggest_int("window", 5, 60),
                "threshold": trial.suggest_float("threshold", 0.0, 2.0),
            }
        """

    @abc.abstractmethod
    def calculate(self, df: pd.DataFrame, params: dict) -> pd.Series:
        """
        Compute the signal for the given price/factor DataFrame.

        Args:
            df:     DataFrame indexed by date (DatetimeIndex or string dates).
                    Columns guaranteed by data_registry.yaml:
                      open, high, low, close, volume  (always present)
                      turnover, adj_factor             (if requested)
                      Additional columns when extra datasets are loaded.
            params: Dict returned by define_params() — already resolved
                    to concrete values (not an Optuna trial object).

        Returns:
            pd.Series of float64, same index as df.
            Positive values → bullish (buy candidate).
            Negative values → bearish (sell candidate).
            Zero / NaN     → neutral (no signal).

        Constraints (enforced by _safe_calculate):
            • Must NOT mutate df in place.
            • Must NOT perform I/O, network requests, or random seed resets.
            • Must be deterministic: same df + same params → same output.
        """

    # ── Safe wrapper (called by evaluator, never directly) ────────────────────

    def _safe_calculate(self, df: pd.DataFrame, params: dict) -> pd.Series:
        """
        Validated wrapper around calculate().
        Called by the evaluator — signal authors do not call this directly.

        Raises SignalContractError on any contract violation.
        """
        result = self.calculate(df, params)
        return _validate_signal_output(result, df, self.name)

    # ── Default params (used when Optuna is skipped) ─────────────────────────

    def default_params(self) -> dict:
        """
        Return a concrete default param dict without running Optuna.

        Uses a lightweight mock trial that always returns the midpoint
        of numeric ranges and the first choice of categorical ranges.
        This lets you run the signal for quick checks without Optuna.

        Returns:
            dict of {param_name: default_value}
        """
        mock = _MockTrial()
        return self.define_params(mock)

    # ── Repr ──────────────────────────────────────────────────────────────────

    def __repr__(self) -> str:
        return (
            f"<{type(self).__name__} "
            f"name={self.name!r} source={self.source!r} v={self.version}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# MockTrial  (for default_params() without Optuna)
# ─────────────────────────────────────────────────────────────────────────────

class _MockTrial:
    """
    Lightweight stand-in for optuna.Trial.

    Returns midpoint for numeric suggestions,
    first option for categorical suggestions.
    Used by BaseSignal.default_params().
    """

    def suggest_int(self, name: str, low: int, high: int, step: int = 1, **_) -> int:
        return (low + high) // 2

    def suggest_float(self, name: str, low: float, high: float, step=None, log: bool = False, **_) -> float:
        if log:
            import math
            return math.exp((math.log(low) + math.log(high)) / 2)
        return (low + high) / 2.0

    def suggest_categorical(self, name: str, choices: list, **_):
        return choices[0]

    def suggest_uniform(self, name: str, low: float, high: float, **_) -> float:
        return (low + high) / 2.0

    def suggest_loguniform(self, name: str, low: float, high: float, **_) -> float:
        import math
        return math.exp((math.log(low) + math.log(high)) / 2)

    def suggest_discrete_uniform(self, name: str, low: float, high: float, q: float, **_) -> float:
        return low
