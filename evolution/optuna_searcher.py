"""
evolution/optuna_searcher.py — Optuna Hyperparameter Searcher
=============================================================

Wraps Optuna to search the parameter space defined by a BaseSignal's
define_params() method, optimising against L1 Valid-Search T+5 Sharpe
with an independent Holdout gate to prevent overfitting.

Anti-overfit mechanisms (Issue #2):
  • Sharpe cap: CV mean_sharpe > 3.0 is truncated with discount factor 0.1.
    Rationale: Sharpe > 3 doesn't exist in live trading; extreme values
    drive Optuna toward specialised params that over-fit the search window.
  • Temporal CV: valid_search window split into cv_folds time-ordered folds.
    Params must work across multiple quarterly regime slices, not just one.
  • Reduced trials: n_trials=20 (was 50) — less search freedom → less overfit.
  • Raised min_signals: 80 (was 30) — statistical significance guard.
  • Hold-Out gate (in Orchestrator): best_params re-evaluated on an
    independent valid_holdout period; rejected if signals < 15, Sharpe ≤ 0,
    or valid_search/holdout Sharpe ratio > 5.0.

Usage
-----
    from evolution.optuna_searcher import OptunaSearcher

    searcher = OptunaSearcher(n_trials=20)
    result = searcher.search(
        signal_cls=MySignal,
        valid_search_data=valid_search_dict,   # {code: DataFrame}
    )
    print(result["best_params"], result["best_score"])
"""

from __future__ import annotations

import gc
import logging
import time
from typing import Any, Dict, List, Optional, Type

import numpy as np

logger = logging.getLogger(__name__)

# Silence Optuna's verbose output by default
_optuna_logger = logging.getLogger("optuna")
_optuna_logger.setLevel(logging.WARNING)

# ── Anti-overfit defaults (Issue #2) ─────────────────────────────────────────
_DEFAULT_N_TRIALS      = 20       # reduced from 50
_DEFAULT_MIN_SIGNALS   = 80       # raised from 30
_DEFAULT_CV_FOLDS      = 4        # raised from 3
_SHARPE_CAP            = 3.0      # truncation threshold
_SHARPE_CAP_DISCOUNT   = 0.1      # (x - cap) * discount + cap


# ─────────────────────────────────────────────────────────────────────────────
# Memory guard helper
# ─────────────────────────────────────────────────────────────────────────────

def _available_memory_gb() -> float:
    """Return available system memory in GB. Returns inf if psutil unavailable."""
    try:
        import psutil
        return psutil.virtual_memory().available / 1024 ** 3
    except ImportError:
        return float("inf")


def _wait_for_memory(
    threshold_gb: float = 2.0,
    check_interval_sec: float = 10.0,
    max_wait_sec: float = 120.0,
) -> bool:
    """
    Block until available RAM >= threshold_gb or max_wait exceeded.

    Returns True if memory is available, False if timed out.
    """
    waited = 0.0
    while _available_memory_gb() < threshold_gb:
        if waited >= max_wait_sec:
            logger.warning(
                "Memory guard: still below %.1f GB after %.0f s — proceeding anyway",
                threshold_gb, max_wait_sec,
            )
            return False
        logger.info(
            "Memory guard: available=%.2f GB < %.1f GB threshold — waiting %.0f s",
            _available_memory_gb(), threshold_gb, check_interval_sec,
        )
        time.sleep(check_interval_sec)
        waited += check_interval_sec
        gc.collect()
    return True


# ─────────────────────────────────────────────────────────────────────────────
# Temporal CV splitter
# ─────────────────────────────────────────────────────────────────────────────

def _temporal_cv_splits(
    data_dict: Dict[str, Any],
    n_folds: int = _DEFAULT_CV_FOLDS,
) -> List[Dict[str, Any]]:
    """
    Split data_dict into n_folds time-ordered folds.

    Each fold is a sub-dict of {code: DataFrame} covering a contiguous
    date slice.  Folds are non-overlapping and ordered oldest → newest.

    Returns a list of n_folds data_dicts.
    """
    if not data_dict:
        return []

    # Collect all unique dates across all stocks
    import pandas as pd
    all_dates: set = set()
    for df in data_dict.values():
        all_dates.update(df.index.tolist())

    sorted_dates = sorted(all_dates)
    if len(sorted_dates) == 0:
        return []

    n = len(sorted_dates)
    fold_size = n // n_folds

    folds: List[Dict[str, Any]] = []
    for i in range(n_folds):
        start_idx = i * fold_size
        # Last fold gets any remainder
        end_idx = (i + 1) * fold_size if i < n_folds - 1 else n
        fold_dates = set(sorted_dates[start_idx:end_idx])

        fold_data: Dict[str, Any] = {}
        for code, df in data_dict.items():
            mask = df.index.isin(fold_dates)
            sub = df[mask]
            if len(sub) >= 60:  # need enough rows for indicators
                fold_data[code] = sub

        if fold_data:
            folds.append(fold_data)

    logger.debug(
        "temporal_cv_splits: %d folds from %d total dates",
        len(folds), n,
    )
    return folds


# ─────────────────────────────────────────────────────────────────────────────
# Sharpe cap helper (Issue #2)
# ─────────────────────────────────────────────────────────────────────────────

def _apply_sharpe_cap(sharpe: float) -> float:
    """
    Truncate extreme Sharpe values to discourage Optuna from chasing
    unrealistic parameters that overfit the search window.

    For sharpe > _SHARPE_CAP:
        capped = _SHARPE_CAP + (sharpe - _SHARPE_CAP) * _SHARPE_CAP_DISCOUNT

    Example: Sharpe=7.98 → 3.0 + 4.98 * 0.1 = 3.498
    """
    import math
    if math.isnan(sharpe) or math.isinf(sharpe):
        return sharpe
    if sharpe > _SHARPE_CAP:
        return _SHARPE_CAP + (sharpe - _SHARPE_CAP) * _SHARPE_CAP_DISCOUNT
    return sharpe


# ─────────────────────────────────────────────────────────────────────────────
# OptunaSearcher
# ─────────────────────────────────────────────────────────────────────────────

class OptunaSearcher:
    """
    Parameter searcher using Optuna TPE sampler with anti-overfit mechanisms.

    Anti-overfit features (Issue #2):
      • Sharpe cap: scores > 3.0 are truncated before being returned to Optuna.
      • Temporal CV: valid_search window split into cv_folds time-ordered folds;
        objective = mean fold Sharpe (not single-window Sharpe).
      • Reduced n_trials (20 vs 50): less search freedom.
      • Raised min_signals (80 vs 30): statistical significance guard.

    Attributes:
        n_trials:           Number of Optuna trials (default 20).
        direction:          'maximize' or 'minimize' (default 'maximize').
        primary_horizon:    Forward-return horizon for the objective (default 5).
        pause_mem_gb:       Pause trials if free RAM < this (default 2.0 GB).
        timeout_sec:        Total search wall-clock timeout in seconds.
        cv_folds:           Number of temporal CV folds (default 4).
        min_signals:        Minimum signals per fold to accept a trial (default 80).
        sharpe_cap:         Sharpe values above this are truncated (default 3.0).
    """

    def __init__(
        self,
        n_trials: int = _DEFAULT_N_TRIALS,
        direction: str = "maximize",
        primary_horizon: int = 5,
        pause_mem_gb: float = 2.0,
        timeout_sec: Optional[float] = None,
        use_pruner: bool = True,
        cv_folds: int = _DEFAULT_CV_FOLDS,
        min_signals: int = _DEFAULT_MIN_SIGNALS,
        sharpe_cap: float = _SHARPE_CAP,
    ):
        self.n_trials        = n_trials
        self.direction       = direction
        self.primary_horizon = primary_horizon
        self.pause_mem_gb    = pause_mem_gb
        self.timeout_sec     = timeout_sec
        self.use_pruner      = use_pruner
        self.cv_folds        = cv_folds
        self.min_signals     = min_signals
        self.sharpe_cap      = sharpe_cap

    # ── Main search entry point ───────────────────────────────────────────────

    def search(
        self,
        signal_cls: Type,                              # BaseSignal subclass
        valid_search_data: Dict[str, Any],             # {code: DataFrame}
        train_data: Optional[Dict[str, Any]] = None,   # optional, unused in objective
        study_name: Optional[str] = None,
        extra_callbacks: Optional[list] = None,
    ) -> Dict[str, Any]:
        """
        Run Optuna parameter search for a signal class using temporal CV.

        The objective function (per trial):
          1. Instantiate signal_cls() and call define_params(trial)
          2. For each temporal CV fold of valid_search_data:
             a. Run _safe_calculate on each stock in the fold
             b. Compute L1 T+{primary_horizon} Sharpe for the fold
             c. Skip fold if n_signals < min_signals
          3. Compute mean fold Sharpe (nan if too few folds passed)
          4. Apply Sharpe cap to the mean (see _apply_sharpe_cap)
          5. Return capped mean as the objective value

        Args:
            signal_cls:         BaseSignal subclass (not an instance).
            valid_search_data:  Valid-search data dict {code: DataFrame}.
            train_data:         Training data (unused in objective, reserved).
            study_name:         Optuna study name.
            extra_callbacks:    Additional Optuna callbacks.

        Returns:
            {
              "best_params":          dict,
              "best_score":           float,  # capped CV mean T+5 Sharpe
              "best_score_uncapped":  float,  # pre-cap score for diagnostics
              "best_trial":           int,
              "n_trials":             int,
              "n_failed":             int,
              "all_trials":           list,
              "duration_sec":         float,
              "signal_name":          str,
              "cv_folds_used":        int,
            }
        """
        try:
            import optuna
        except ImportError as e:
            raise ImportError(
                "optuna is required for OptunaSearcher. "
                "Install with: pip install optuna"
            ) from e

        from evolution.evaluator import SignalEvaluator

        evaluator   = SignalEvaluator(primary_horizon=self.primary_horizon)
        signal_name = getattr(signal_cls, "__name__", str(signal_cls))
        study_name  = study_name or f"evolve_{signal_name}"

        # Build temporal CV folds once (shared across all trials)
        cv_folds_data = _temporal_cv_splits(valid_search_data, n_folds=self.cv_folds)
        n_folds_actual = len(cv_folds_data)

        logger.info(
            "OptunaSearcher: starting '%s' — %d trials, %d CV folds, "
            "sharpe_cap=%.1f, min_signals=%d, direction=%s",
            signal_name, self.n_trials, n_folds_actual,
            self.sharpe_cap, self.min_signals, self.direction,
        )

        # ── Build Optuna study ────────────────────────────────────────────────
        pruner = (
            optuna.pruners.MedianPruner(n_startup_trials=5, n_warmup_steps=0)
            if self.use_pruner else
            optuna.pruners.NopPruner()
        )
        sampler = optuna.samplers.TPESampler(seed=42)

        study = optuna.create_study(
            study_name=study_name,
            direction=self.direction,
            sampler=sampler,
            pruner=pruner,
        )

        # ── Objective closure ─────────────────────────────────────────────────
        trial_records: list = []
        n_failed = 0
        # Track uncapped scores for diagnostics
        _uncapped_scores: dict = {}

        def objective(trial: "optuna.Trial") -> float:
            nonlocal n_failed

            # Memory guard before each trial
            _wait_for_memory(
                threshold_gb=self.pause_mem_gb,
                check_interval_sec=5.0,
                max_wait_sec=60.0,
            )

            # Instantiate signal and sample params
            try:
                instance = signal_cls()
                params   = instance.define_params(trial)
            except Exception as e:
                logger.debug("Trial %d: define_params failed: %s", trial.number, e)
                n_failed += 1
                raise optuna.exceptions.TrialPruned()

            # ── Temporal CV evaluation ────────────────────────────────────────
            fold_sharpes: List[float] = []

            for fold_idx, fold_data in enumerate(cv_folds_data):
                signal_dict: Dict[str, Any] = {}
                for code, df in fold_data.items():
                    try:
                        sig = instance._safe_calculate(df, params)
                        signal_dict[code] = sig
                    except Exception:
                        pass

                if len(signal_dict) == 0:
                    continue

                # Count total signals (non-NaN, non-zero above threshold)
                total_sigs = sum(
                    int((s.abs() > 0).sum())
                    for s in signal_dict.values()
                )
                if total_sigs < self.min_signals:
                    logger.debug(
                        "Trial %d fold %d: too few signals (%d < %d), skipping",
                        trial.number, fold_idx, total_sigs, self.min_signals,
                    )
                    continue

                try:
                    metrics = evaluator.l1_cross_section(signal_dict, fold_data)
                    fold_sh = metrics.get("primary", float("nan"))
                except Exception as e:
                    logger.debug(
                        "Trial %d fold %d: L1 eval failed: %s",
                        trial.number, fold_idx, e,
                    )
                    continue

                if np.isfinite(fold_sh):
                    fold_sharpes.append(float(fold_sh))

            # Require at least 2 folds to pass; too few = unstable signal
            if len(fold_sharpes) < 2:
                logger.debug(
                    "Trial %d: only %d valid folds — pruning",
                    trial.number, len(fold_sharpes),
                )
                n_failed += 1
                raise optuna.exceptions.TrialPruned()

            mean_sharpe_raw = float(np.mean(fold_sharpes))
            mean_sharpe     = _apply_sharpe_cap(mean_sharpe_raw)

            # Store uncapped for diagnostics
            _uncapped_scores[trial.number] = mean_sharpe_raw

            # Record for all_trials summary
            trial_records.append({
                "trial_number":    trial.number,
                "params":          params,
                "score":           round(mean_sharpe, 6),
                "score_uncapped":  round(mean_sharpe_raw, 6),
                "n_folds":         len(fold_sharpes),
                "state":           "COMPLETE",
            })

            logger.debug(
                "Trial %d: score=%.4f (uncapped=%.4f, folds=%d) params=%s",
                trial.number, mean_sharpe, mean_sharpe_raw, len(fold_sharpes),
                {k: round(v, 4) if isinstance(v, float) else v for k, v in params.items()},
            )
            return mean_sharpe

        # ── Run optimization ──────────────────────────────────────────────────
        t_start = time.time()
        callbacks = extra_callbacks or []

        def _log_progress(study: "optuna.Study", trial: "optuna.FrozenTrial") -> None:
            best = study.best_value if study.trials else float("nan")
            if trial.number % 5 == 0 or trial.number == self.n_trials - 1:
                logger.info(
                    "Optuna [%s] trial %d/%d — current=%.4f best=%.4f",
                    signal_name, trial.number + 1, self.n_trials,
                    trial.value if trial.value is not None else float("nan"),
                    best,
                )

        callbacks.append(_log_progress)

        try:
            study.optimize(
                objective,
                n_trials=self.n_trials,
                timeout=self.timeout_sec,
                callbacks=callbacks,
                show_progress_bar=False,
                gc_after_trial=True,
            )
        except KeyboardInterrupt:
            logger.warning("OptunaSearcher: interrupted by user")

        duration = time.time() - t_start

        # ── Extract results ───────────────────────────────────────────────────
        completed = [
            t for t in study.trials
            if t.state.name == "COMPLETE"
        ]

        if completed:
            best_trial     = study.best_trial
            best_params    = best_trial.params
            best_score     = best_trial.value       # capped score
            best_score_raw = _uncapped_scores.get(best_trial.number, best_score)
            best_number    = best_trial.number
        else:
            logger.warning(
                "OptunaSearcher: no completed trials for '%s'", signal_name
            )
            try:
                instance    = signal_cls()
                best_params = instance.default_params()
            except Exception:
                best_params = {}
            best_score     = float("nan")
            best_score_raw = float("nan")
            best_number    = -1

        result = {
            "best_params":         best_params,
            "best_score":          float(best_score) if best_score is not None else float("nan"),
            "best_score_uncapped": float(best_score_raw),
            "best_trial":          best_number,
            "n_trials":            len(study.trials),
            "n_completed":         len(completed),
            "n_failed":            n_failed,
            "all_trials":          trial_records,
            "duration_sec":        round(duration, 2),
            "signal_name":         signal_name,
            "study_name":          study_name,
            "cv_folds_used":       n_folds_actual,
            "sharpe_cap_applied":  self.sharpe_cap,
        }

        logger.info(
            "OptunaSearcher done '%s': best_score=%.4f (uncapped=%.4f) "
            "in %d/%d trials, %d CV folds (%.1f s)",
            signal_name, result["best_score"], result["best_score_uncapped"],
            result["n_completed"], result["n_trials"],
            n_folds_actual, duration,
        )
        return result

    # ── Convenience: search from registry ────────────────────────────────────

    def search_by_name(
        self,
        signal_name: str,
        valid_search_data: Dict[str, Any],
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Look up signal_name in SignalRegistry and run search().

        Args:
            signal_name:       Registered signal name (e.g. 'b1').
            valid_search_data: Valid-search data dict.
            **kwargs:          Forwarded to search().
        """
        from evolution.signal_registry import registry
        signal_cls = registry.get(signal_name)
        return self.search(signal_cls, valid_search_data, **kwargs)

    def __repr__(self) -> str:
        return (
            f"<OptunaSearcher n_trials={self.n_trials} "
            f"cv_folds={self.cv_folds} sharpe_cap={self.sharpe_cap} "
            f"min_signals={self.min_signals} "
            f"direction={self.direction} primary_horizon=T+{self.primary_horizon}>"
        )
