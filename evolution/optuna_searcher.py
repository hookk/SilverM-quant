"""
evolution/optuna_searcher.py — Optuna Hyperparameter Searcher
=============================================================

Wraps Optuna to search the parameter space defined by a BaseSignal's
define_params() method, optimising against L1 Valid-set T+5 Sharpe.

Design:
  • Accepts a BaseSignal subclass (not instance) + pre-loaded data dicts.
  • Default: 50 trials, serial (no parallelism — keeps memory predictable).
  • Objective: Valid-set cross-sectional T+5 Sharpe from SignalEvaluator.L1.
  • Memory guard: pauses if available RAM < 2 GB (from data_registry.yaml).
  • Returns a structured result dict for MemoryManager to persist.
  • Optuna logging is silenced at WARNING level to keep orchestrator output clean.

Usage
-----
    from evolution.optuna_searcher import OptunaSearcher

    searcher = OptunaSearcher(n_trials=50)
    result = searcher.search(
        signal_cls=MySignal,
        train_data=train_dict,   # {code: DataFrame}
        valid_data=valid_dict,   # {code: DataFrame}
    )
    print(result["best_params"], result["best_score"])
"""

from __future__ import annotations

import gc
import logging
import time
from typing import Any, Dict, Optional, Type

import numpy as np

logger = logging.getLogger(__name__)

# Silence Optuna's verbose output by default
_optuna_logger = logging.getLogger("optuna")
_optuna_logger.setLevel(logging.WARNING)


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
# OptunaSearcher
# ─────────────────────────────────────────────────────────────────────────────

class OptunaSearcher:
    """
    Parameter searcher using Optuna TPE sampler.

    Attributes:
        n_trials:           Number of Optuna trials (default 50).
        direction:          'maximize' or 'minimize' (default 'maximize').
        primary_horizon:    Forward-return horizon for the objective (default 5).
        pause_mem_gb:       Pause trials if free RAM < this (default 2.0 GB).
        timeout_sec:        Total search wall-clock timeout in seconds (None = no limit).
        pruner:             Optuna pruner instance (default MedianPruner).
    """

    def __init__(
        self,
        n_trials: int = 50,
        direction: str = "maximize",
        primary_horizon: int = 5,
        pause_mem_gb: float = 2.0,
        timeout_sec: Optional[float] = None,
        use_pruner: bool = True,
    ):
        self.n_trials        = n_trials
        self.direction       = direction
        self.primary_horizon = primary_horizon
        self.pause_mem_gb    = pause_mem_gb
        self.timeout_sec     = timeout_sec
        self.use_pruner      = use_pruner

    # ── Main search entry point ───────────────────────────────────────────────

    def search(
        self,
        signal_cls: Type,                        # BaseSignal subclass
        valid_data: Dict[str, Any],              # {code: DataFrame}
        train_data: Optional[Dict[str, Any]] = None,  # optional, not used in objective
        study_name: Optional[str] = None,
        extra_callbacks: Optional[list] = None,
    ) -> Dict[str, Any]:
        """
        Run Optuna parameter search for a signal class.

        The objective function:
          1. Instantiates signal_cls()
          2. Calls define_params(trial) to sample params
          3. Runs _safe_calculate on valid_data for each stock
          4. Computes L1 cross-sectional T+{primary_horizon} Sharpe
          5. Returns that Sharpe as the objective value

        Args:
            signal_cls:  BaseSignal subclass (not an instance).
            valid_data:  Validation-set data dict {code: DataFrame}.
            train_data:  Training-set data (unused in objective, reserved).
            study_name:  Optuna study name (default: signal class name).
            extra_callbacks: Additional Optuna callbacks.

        Returns:
            {
              "best_params":   dict,
              "best_score":    float,    # Valid T+5 Sharpe
              "best_trial":    int,      # trial number of best
              "n_trials":      int,      # completed trials
              "n_failed":      int,      # failed/pruned trials
              "all_trials":    list,     # [{trial_number, params, score, state}]
              "duration_sec":  float,
              "signal_name":   str,
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

        logger.info(
            "OptunaSearcher: starting '%s' — %d trials, direction=%s",
            signal_name, self.n_trials, self.direction,
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

            # Compute signal for all valid stocks
            signal_dict: Dict[str, Any] = {}
            for code, df in valid_data.items():
                try:
                    sig = instance._safe_calculate(df, params)
                    signal_dict[code] = sig
                except Exception:
                    pass  # skip stocks that fail

            if len(signal_dict) == 0:
                logger.debug("Trial %d: all stocks failed", trial.number)
                n_failed += 1
                raise optuna.exceptions.TrialPruned()

            # L1 cross-sectional evaluation
            try:
                metrics = evaluator.l1_cross_section(signal_dict, valid_data)
                score   = metrics.get("primary", float("nan"))
            except Exception as e:
                logger.debug("Trial %d: L1 evaluation failed: %s", trial.number, e)
                n_failed += 1
                raise optuna.exceptions.TrialPruned()

            if np.isnan(score) or np.isinf(score):
                n_failed += 1
                raise optuna.exceptions.TrialPruned()

            # Record for all_trials summary
            trial_records.append({
                "trial_number": trial.number,
                "params":       params,
                "score":        round(float(score), 6),
                "state":        "COMPLETE",
            })

            logger.debug(
                "Trial %d: score=%.4f params=%s",
                trial.number, score,
                {k: round(v, 4) if isinstance(v, float) else v for k, v in params.items()},
            )
            return score

        # ── Run optimization ──────────────────────────────────────────────────
        t_start = time.time()
        callbacks = extra_callbacks or []

        # Progress logging callback
        def _log_progress(study: "optuna.Study", trial: "optuna.FrozenTrial") -> None:
            best = study.best_value if study.trials else float("nan")
            if trial.number % 10 == 0 or trial.number == self.n_trials - 1:
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
            if t.state == optuna.trial.TrialState.COMPLETE
        ]

        if completed:
            best_trial  = study.best_trial
            best_params = best_trial.params
            best_score  = best_trial.value
            best_number = best_trial.number
        else:
            logger.warning(
                "OptunaSearcher: no completed trials for '%s'", signal_name
            )
            # Fall back to default params
            try:
                instance    = signal_cls()
                best_params = instance.default_params()
            except Exception:
                best_params = {}
            best_score  = float("nan")
            best_number = -1

        result = {
            "best_params":  best_params,
            "best_score":   float(best_score) if best_score is not None else float("nan"),
            "best_trial":   best_number,
            "n_trials":     len(study.trials),
            "n_completed":  len(completed),
            "n_failed":     n_failed,
            "all_trials":   trial_records,
            "duration_sec": round(duration, 2),
            "signal_name":  signal_name,
            "study_name":   study_name,
        }

        logger.info(
            "OptunaSearcher done '%s': best_score=%.4f in %d/%d trials (%.1f s)",
            signal_name, result["best_score"],
            result["n_completed"], result["n_trials"],
            duration,
        )
        return result

    # ── Convenience: search from registry ────────────────────────────────────

    def search_by_name(
        self,
        signal_name: str,
        valid_data: Dict[str, Any],
        **kwargs,
    ) -> Dict[str, Any]:
        """
        Look up signal_name in SignalRegistry and run search().

        Args:
            signal_name: Registered signal name (e.g. 'b1', 'my_evolved').
            valid_data:  Validation data dict.
            **kwargs:    Forwarded to search().
        """
        from evolution.signal_registry import registry
        signal_cls = registry.get(signal_name)
        return self.search(signal_cls, valid_data, **kwargs)

    def __repr__(self) -> str:
        return (
            f"<OptunaSearcher n_trials={self.n_trials} "
            f"direction={self.direction} primary_horizon=T+{self.primary_horizon}>"
        )
