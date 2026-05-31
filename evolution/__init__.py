"""
evolution — Alpha Signal Auto-Evolution System
==============================================

Entry point for the signal evolution package.

Usage:
    python -m evolution.cli <command>

Subpackages:
    evolution.base_signal       BaseSignal ABC
    evolution.indicators_lib    TA-Lib thin wrapper
    evolution.data_preparer     DataPreparer + data_registry.yaml loader
    evolution.signal_registry   Unified signal registry (singleton)
    evolution.adapted           Adapters for all 7 existing hand-crafted signals
    evolution.sandbox_executor  AST whitelist + subprocess isolation (Phase 3)
    evolution.evaluator         L1 / L2 evaluators (Phase 3)
    evolution.optuna_searcher   Optuna parameter search (Phase 3)
    evolution.memory_manager    File-based per-signal memory (Phase 3)
    evolution.orchestrator      Evolution loop orchestrator (Phase 3)
    evolution.cli               CLI entry point (Phase 4)
"""

__version__ = "0.1.0"
__author__  = "SilverM-quant"

# ── Auto-register all 7 adapted signals on package import ────────────────────
# This import has a controlled side-effect: it calls registry_init.register_all(),
# which registers B1/B2/BLK/BLKB2/DZ30/SCB/S1 into the global SignalRegistry.
# Safe to import multiple times (overwrite=True, idempotent).
try:
    from evolution.adapted import registry_init as _registry_init  # noqa: F401
except Exception as _e:
    import logging as _logging
    _logging.getLogger(__name__).warning(
        "evolution: could not auto-register adapted signals: %s. "
        "Run `from evolution.adapted.registry_init import register_all; register_all()` manually.",
        _e,
    )
