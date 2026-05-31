"""
evolution/cli.py — Evolution System CLI
========================================

11 subcommands for the signal evolution system.

Usage
-----
    python -m evolution.cli <command> [options]

    # Or, if installed as a package entry-point:
    evolve <command> [options]

Commands
--------
  create   <name>            Initialise memory directory for a new signal
  run      <name>            Start the evolution loop for a signal
  stop     <name>            Request graceful stop of a running evolution loop
  status   <name>            Show current iteration, best score, best params
  inject   <name> <dir>      Inject a research direction hint for the next iteration
  list                       List all signals and their current status
  history  <name>            Print metrics history table for a signal
  diff     <name> <v1> <v2>  Show unified diff between two code iterations
  promote  <name>            Copy best code to signals/singal_cal/ and register
  backtest <name>            Run L2 full backtest on best signal (Valid set)
  evaluate <name>            Evaluate best signal on Test set (human-only)

Examples
--------
    python -m evolution.cli create my_signal --direction "探索价量背离信号"
    python -m evolution.cli run my_signal --max-iterations 50 --model claude-opus-4-6
    python -m evolution.cli status my_signal
    python -m evolution.cli inject my_signal "尝试在小盘股上加入成交量过滤"
    python -m evolution.cli history my_signal
    python -m evolution.cli diff my_signal 3 7
    python -m evolution.cli promote my_signal
    python -m evolution.cli backtest my_signal
    python -m evolution.cli evaluate my_signal
    python -m evolution.cli list
    python -m evolution.cli stop my_signal
"""

from __future__ import annotations

import argparse
import difflib
import json
import logging
import math
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

# ─────────────────────────────────────────────────────────────────────────────
# Logging setup (CLI-level: INFO to stderr, no timestamps for readability)
# ─────────────────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.WARNING,
    format="%(levelname)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Repo root (resolved relative to this file)
# ─────────────────────────────────────────────────────────────────────────────

_REPO_ROOT    = Path(__file__).parent.parent
_MEMORY_ROOT  = _REPO_ROOT / "memory"
_SIGNALS_DIR  = _REPO_ROOT / "signals" / "singal_cal"


# ─────────────────────────────────────────────────────────────────────────────
# Utilities
# ─────────────────────────────────────────────────────────────────────────────

def _safe_float(v: Any, default: float = float("nan")) -> float:
    try:
        f = float(v)
        return f if math.isfinite(f) else default
    except (TypeError, ValueError):
        return default


def _fmt_score(v: Any) -> str:
    f = _safe_float(v)
    return f"{f:.4f}" if not math.isnan(f) else "  N/A "


def _memory_manager():
    """Lazy import MemoryManager."""
    from evolution.memory_manager import MemoryManager
    return MemoryManager(memory_root=_MEMORY_ROOT)


def _require_signal(name: str, mm=None) -> None:
    """Exit with a friendly message if memory dir does not exist."""
    mm = mm or _memory_manager()
    if not mm.signal_exists(name):
        print(f"✗  Signal '{name}' does not exist.")
        print(f"   Run:  python -m evolution.cli create {name}")
        sys.exit(1)


def _col(text: str, width: int, align: str = "<") -> str:
    """Fixed-width column formatting."""
    fmt = f"{{:{align}{width}}}"
    return fmt.format(str(text)[:width])


def _separator(width: int = 80, char: str = "─") -> str:
    return char * width


# ─────────────────────────────────────────────────────────────────────────────
# Command handlers
# ─────────────────────────────────────────────────────────────────────────────


def cmd_create(args: argparse.Namespace) -> int:
    """
    create <name> [--seed FILE] [--direction TEXT]

    Initialise memory directory for a new evolving signal.
    """
    name = args.name
    mm   = _memory_manager()

    seed_code = None
    if args.seed:
        seed_path = Path(args.seed)
        if not seed_path.exists():
            print(f"✗  Seed file not found: {seed_path}")
            return 1
        seed_code = seed_path.read_text(encoding="utf-8")
        print(f"   Loaded seed code from: {seed_path}")

    overwrite = getattr(args, "overwrite", False)
    if mm.signal_exists(name) and not overwrite:
        print(f"ℹ  Signal '{name}' already exists.  Use --overwrite to reset.")
        return 0

    sig_dir = mm.init_signal(
        name,
        seed_code=seed_code,
        initial_direction=getattr(args, "direction", None),
        overwrite=overwrite,
    )
    print(f"✔  Created signal '{name}'")
    print(f"   Memory directory: {sig_dir}")
    if seed_code:
        print(f"   Seed code written to: {sig_dir}/code/iter_000.py")
    if getattr(args, "direction", None):
        print(f"   Initial direction: {args.direction}")
    print()
    print(f"   Next step:  python -m evolution.cli run {name}")
    return 0


def cmd_run(args: argparse.Namespace) -> int:
    """
    run <name> [--max-iterations N] [--model MODEL] [--optuna-trials N]
               [--l2-every N] [--no-data]

    Start the evolution loop.  Blocks until max_iterations or stop flag.
    """
    name = args.name
    mm   = _memory_manager()

    if not mm.signal_exists(name):
        print(f"ℹ  Signal '{name}' not found — creating it first …")
        mm.init_signal(name)
        print(f"   Memory directory created.")

    # Enable INFO logging for the run command so progress is visible
    logging.getLogger("evolution").setLevel(logging.INFO)
    logging.getLogger("evolution.orchestrator").setLevel(logging.INFO)

    try:
        from evolution.orchestrator import Orchestrator
    except ImportError as e:
        print(f"✗  Cannot import Orchestrator: {e}")
        return 1

    orch = Orchestrator(
        model           = getattr(args, "model", "claude-opus-4-6"),
        max_iterations  = getattr(args, "max_iterations", 100),
        optuna_trials   = getattr(args, "optuna_trials", 50),
        l2_every_n      = getattr(args, "l2_every", 10),
        memory_root     = _MEMORY_ROOT,
    )

    print(f"▶  Starting evolution loop for '{name}'")
    print(f"   Model:      {orch.model}")
    print(f"   Max iters:  {orch.max_iterations}")
    print(f"   Optuna:     {orch.optuna_trials} trials/iter")
    print(f"   L2 every:   {orch.l2_every_n} iters")
    print(_separator())

    try:
        orch.run_loop(
            signal_name     = name,
            max_iterations  = orch.max_iterations,
        )
    except KeyboardInterrupt:
        print("\n⚠  Interrupted by user — current iteration will finish before stop.")
    except Exception as e:
        logger.exception("run_loop raised unexpected error")
        print(f"✗  Evolution loop error: {e}")
        return 1

    print(_separator())
    print(f"✔  Evolution loop finished for '{name}'")
    return 0


def cmd_stop(args: argparse.Namespace) -> int:
    """
    stop <name>

    Write a .stop flag file.  The running orchestrator checks this flag
    at the start of each iteration and exits gracefully.
    """
    name = args.name
    mm   = _memory_manager()
    _require_signal(name, mm)

    mm.set_stop(name)
    print(f"✔  Stop flag written for '{name}'.")
    print(f"   The running loop will exit after the current iteration completes.")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    """
    status <name>

    Show current iteration, best score, best params, memory stats.
    """
    name = args.name
    mm   = _memory_manager()
    _require_signal(name, mm)

    st   = mm.status(name)
    best = mm.read_best(name)

    print(f"Signal: {name}")
    print(_separator())
    print(f"  Current iteration : {st.get('current_iter', 'N/A')}")
    print(f"  Best score        : {_fmt_score(st.get('best_score'))}")
    print(f"  Best iteration    : {st.get('best_iteration', 'N/A')}")
    print(f"  Summary size      : {st.get('summary_lines', 0)} lines")
    print(f"  Pending inject    : {'yes' if st.get('pending_inject') else 'no'}")
    print(f"  Stop pending      : {'yes ⚠' if st.get('stop_pending') else 'no'}")

    if best:
        print()
        print("  Best result:")
        vm     = best.get("valid_metrics", {})
        params = best.get("best_params", {})
        hyp    = (best.get("hypothesis") or "").strip()[:120]
        print(f"    hypothesis  : {hyp or '(none)'}")
        print(f"    sharpe_t5   : {_fmt_score(vm.get('sharpe_t5'))}")
        print(f"    ic_t5       : {_fmt_score(vm.get('ic_t5'))}")
        print(f"    win_rate_t5 : {_fmt_score(vm.get('win_rate_t5'))}")
        print(f"    coverage    : {_fmt_score(vm.get('coverage'))}")
        if params:
            print(f"    params      : {json.dumps(params, ensure_ascii=False)}")

    return 0


def cmd_inject(args: argparse.Namespace) -> int:
    """
    inject <name> <direction>

    Append a research direction hint.  Orchestrator consumes it on the
    next iteration (then clears the file).
    """
    name      = args.name
    direction = args.direction
    mm        = _memory_manager()
    _require_signal(name, mm)

    mm.append_inject_direction(name, direction)
    print(f"✔  Direction injected for '{name}':")
    print(f"   {direction}")
    print()
    print(f"   The next evolution iteration will receive this hint.")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """
    list

    Show all signals and their current status in a compact table.
    """
    mm      = _memory_manager()
    signals = mm.list_signals()

    if not signals:
        print("No signals found in memory directory.")
        print(f"Memory root: {_MEMORY_ROOT}")
        print()
        print("Create one with:  python -m evolution.cli create <name>")
        return 0

    # Header
    hdr = (
        f"{'NAME':<22} {'ITER':>5}  {'BEST SCORE':>10}  "
        f"{'BEST ITER':>9}  {'INJECT':>6}  {'STOP':>4}  STATUS"
    )
    print(hdr)
    print(_separator(90))

    for name in signals:
        st = mm.status(name)
        cur_iter  = st.get("current_iter", -1)
        best_sc   = _fmt_score(st.get("best_score"))
        best_it   = st.get("best_iteration", -1)
        inject    = "✓" if st.get("pending_inject") else ""
        stop_flag = "⚠" if st.get("stop_pending") else ""
        status    = "running?" if stop_flag else ("active" if cur_iter >= 0 else "new")

        print(
            f"{_col(name, 22)}  "
            f"{cur_iter:>5}  "
            f"{best_sc:>10}  "
            f"{best_it:>9}  "
            f"{_col(inject, 6, '^')}  "
            f"{_col(stop_flag, 4, '^')}  "
            f"{status}"
        )

    print(_separator(90))
    print(f"Total: {len(signals)} signal(s).  Memory root: {_MEMORY_ROOT}")
    return 0


def cmd_history(args: argparse.Namespace) -> int:
    """
    history <name> [--last N]

    Print a metrics history table for all (or last N) iterations.
    """
    name = args.name
    last = getattr(args, "last", None)
    mm   = _memory_manager()
    _require_signal(name, mm)

    records = mm.read_all_iterations(name)

    if not records:
        print(f"No iteration records found for '{name}'.")
        print(f"   (Compressed iterations are stored in summary.md)")
        return 0

    if last:
        records = records[-last:]

    # Header
    hdr = (
        f"{'ITER':>4}  {'SCORE':>7}  {'SHARPE5':>8}  "
        f"{'IC5':>7}  {'WIN%':>6}  {'COV':>5}  "
        f"HYPOTHESIS"
    )
    print(f"Signal: {name}")
    print(_separator(100))
    print(hdr)
    print(_separator(100))

    for r in records:
        i   = r.get("iteration", "?")
        vm  = r.get("valid_metrics", {})
        sc  = _fmt_score(vm.get("primary"))
        sh5 = _fmt_score(vm.get("sharpe_t5"))
        ic5 = _fmt_score(vm.get("ic_t5"))
        wr5 = _fmt_score(vm.get("win_rate_t5"))
        cov = _fmt_score(vm.get("coverage"))
        hyp = (r.get("hypothesis") or "").strip()[:55]
        print(
            f"{i:>4}  {sc:>7}  {sh5:>8}  "
            f"{ic5:>7}  {wr5:>6}  {cov:>5}  "
            f"{hyp}"
        )

    print(_separator(100))
    print(f"Showing {len(records)} record(s).")

    # Also note if there's compressed history
    summary_path = _MEMORY_ROOT / name / "summary.md"
    if summary_path.exists():
        lines = len(summary_path.read_text(encoding="utf-8").splitlines())
        if lines > 5:
            print(f"ℹ  Additional compressed history in summary.md ({lines} lines).")
            print(f"   View with:  cat {summary_path}")
    return 0


def cmd_diff(args: argparse.Namespace) -> int:
    """
    diff <name> <v1> <v2>

    Show unified diff between two code iterations.
    """
    name = args.name
    v1   = int(args.v1)
    v2   = int(args.v2)
    mm   = _memory_manager()
    _require_signal(name, mm)

    code_dir = _MEMORY_ROOT / name / "code"
    path1    = code_dir / f"iter_{v1:03d}.py"
    path2    = code_dir / f"iter_{v2:03d}.py"

    missing = []
    if not path1.exists():
        missing.append(str(path1))
    if not path2.exists():
        missing.append(str(path2))

    if missing:
        for p in missing:
            print(f"✗  File not found: {p}")
        return 1

    lines1 = path1.read_text(encoding="utf-8").splitlines(keepends=True)
    lines2 = path2.read_text(encoding="utf-8").splitlines(keepends=True)

    diff = list(
        difflib.unified_diff(
            lines1, lines2,
            fromfile=f"iter_{v1:03d}.py",
            tofile=f"iter_{v2:03d}.py",
            lineterm="",
        )
    )

    if not diff:
        print(f"ℹ  iter_{v1:03d} and iter_{v2:03d} are identical.")
        return 0

    # Colour output if terminal supports it
    use_color = sys.stdout.isatty()

    for line in diff:
        if use_color:
            if line.startswith("+") and not line.startswith("+++"):
                print(f"\033[92m{line}\033[0m")   # green
            elif line.startswith("-") and not line.startswith("---"):
                print(f"\033[91m{line}\033[0m")   # red
            elif line.startswith("@@"):
                print(f"\033[96m{line}\033[0m")   # cyan
            else:
                print(line)
        else:
            print(line)

    print()
    adds    = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removes = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    print(f"   +{adds} lines added, -{removes} lines removed")
    return 0


def cmd_promote(args: argparse.Namespace) -> int:
    """
    promote <name> [--overwrite]

    Copy the best code to signals/singal_cal/<name>_evolved.py and register
    it in the SignalRegistry.
    """
    name = args.name
    mm   = _memory_manager()
    _require_signal(name, mm)

    best = mm.read_best(name)
    if not best:
        print(f"✗  No best result found for '{name}'. Run the evolution loop first.")
        return 1

    best_code_path = best.get("code_path", "")
    if not best_code_path or not Path(best_code_path).exists():
        # Try to find it from iteration number
        best_iter = best.get("iteration", -1)
        if best_iter >= 0:
            best_code_path = str(_MEMORY_ROOT / name / "code" / f"iter_{best_iter:03d}.py")
        if not Path(best_code_path).exists():
            print(f"✗  Best code file not found: {best_code_path}")
            return 1

    # Destination
    _SIGNALS_DIR.mkdir(parents=True, exist_ok=True)
    dest = _SIGNALS_DIR / f"{name}_evolved.py"

    if dest.exists() and not getattr(args, "overwrite", False):
        print(f"✗  File already exists: {dest}")
        print(f"   Use --overwrite to replace it.")
        return 1

    import shutil
    shutil.copy2(best_code_path, dest)
    print(f"✔  Promoted best code → {dest}")

    # Show best metrics
    vm    = best.get("valid_metrics", {})
    score = _fmt_score(vm.get("primary"))
    print(f"   Best Valid Sharpe (T+5): {score}")
    print(f"   From iteration:          {best.get('iteration', 'N/A')}")
    print()

    # Attempt to register in SignalRegistry
    try:
        from evolution.signal_registry import registry
        import importlib.util

        spec   = importlib.util.spec_from_file_location(f"{name}_evolved", str(dest))
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        from evolution.base_signal import BaseSignal
        promoted_cls = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and issubclass(attr, BaseSignal) and attr is not BaseSignal:
                promoted_cls = attr
                break

        if promoted_cls:
            registry.register_cls(
                name        = f"{name}_evolved",
                cls         = promoted_cls,
                source      = "evolved",
                overwrite   = True,
                description = f"Promoted from evolution loop (iter {best.get('iteration', '?')})",
            )
            print(f"   Registered '{name}_evolved' in SignalRegistry.")
        else:
            print(f"   ⚠  Could not find BaseSignal subclass in promoted file — registry not updated.")

    except Exception as e:
        print(f"   ⚠  Registry update failed (code was still copied): {e}")

    print()
    print(f"   To use in backtest, import: from signals.singal_cal.{name}_evolved import *")
    return 0


def cmd_backtest(args: argparse.Namespace) -> int:
    """
    backtest <name>

    Run L2 full backtest on the best signal (Valid set).
    Results are shown to the user and saved to memory/<name>/l2_backtest_best.json.
    """
    name = args.name
    mm   = _memory_manager()
    _require_signal(name, mm)

    best = mm.read_best(name)
    if not best:
        print(f"✗  No best result for '{name}'. Run the evolution loop first.")
        return 1

    best_code_path = best.get("code_path", "")
    best_iter      = best.get("iteration", -1)
    if not best_code_path or not Path(best_code_path).exists():
        if best_iter >= 0:
            best_code_path = str(_MEMORY_ROOT / name / "code" / f"iter_{best_iter:03d}.py")
        if not Path(best_code_path).exists():
            print(f"✗  Best code file not found: {best_code_path}")
            return 1

    print(f"▶  Loading best signal from iter_{best_iter:03d} …")

    # Load signal class
    try:
        import importlib.util
        from evolution.base_signal import BaseSignal

        spec   = importlib.util.spec_from_file_location(f"_backtest_{name}", best_code_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        sig_cls = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and issubclass(attr, BaseSignal) and attr is not BaseSignal:
                sig_cls = attr
                break

        if sig_cls is None:
            print("✗  No BaseSignal subclass found in best code file.")
            return 1

    except Exception as e:
        print(f"✗  Failed to load signal class: {e}")
        return 1

    sig_instance = sig_cls()
    best_params  = best.get("best_params", sig_instance.default_params())

    # Load valid data
    print("   Loading valid data …")
    try:
        from evolution.data_preparer import DataPreparer
        preparer   = DataPreparer()
        valid_data = preparer.load_segment("valid")
        print(f"   Loaded {len(valid_data)} stocks.")
    except Exception as e:
        print(f"✗  DataPreparer failed: {e}")
        return 1

    # Run L2
    print("   Running L2 backtest (Valid set 2024-01-01 → 2025-09-30) …")
    try:
        from evolution.evaluator import SignalEvaluator
        evaluator = SignalEvaluator()
        result    = evaluator.evaluate_signal(
            signal_instance = sig_instance,
            data_dict       = valid_data,
            params          = best_params,
            segment         = "valid",
            run_l2          = True,
        )
        l2 = result.get("l2")
    except Exception as e:
        print(f"✗  Evaluation failed: {e}")
        return 1

    if not l2:
        print("✗  L2 backtest returned no results.")
        return 1

    # Print results
    print()
    print(f"L2 Backtest Results — '{name}' (Valid set)")
    print(_separator(60))

    summary = l2.get("summary", {})
    metrics = [
        ("Total Return",   summary.get("total_return"),  ".2%"),
        ("Annual Return",  summary.get("annual_return"), ".2%"),
        ("Sharpe Ratio",   summary.get("sharpe_ratio"),  ".4f"),
        ("Max Drawdown",   summary.get("max_drawdown"),  ".2%"),
        ("Win Rate",       summary.get("win_rate"),      ".2%"),
        ("Turnover Rate",  summary.get("turnover_rate"), ".2%"),
    ]
    for label, val, fmt in metrics:
        v = _safe_float(val)
        if not math.isnan(v):
            print(f"  {label:<16}: {v:{fmt}}")

    q = l2.get("quarterly", {})
    if q:
        print()
        print("  Quarterly Returns:")
        for period, ret in sorted(q.items()):
            v = _safe_float(ret)
            if not math.isnan(v):
                print(f"    {period}: {v:.2%}")

    cap = l2.get("cap_group", {})
    if cap:
        print()
        print("  Cap-Group Sharpe (T+5):")
        for grp, sh in sorted(cap.items()):
            v = _safe_float(sh)
            s = f"{v:.3f}" if not math.isnan(v) else "N/A"
            print(f"    {grp:<8}: {s}")

    # Save results
    out_path = _MEMORY_ROOT / name / "l2_backtest_best.json"
    try:
        out_path.write_text(json.dumps(l2, ensure_ascii=False, indent=2), encoding="utf-8")
        print()
        print(f"✔  Results saved → {out_path}")
    except Exception as e:
        print(f"   ⚠  Could not save results: {e}")

    return 0


def cmd_evaluate(args: argparse.Namespace) -> int:
    """
    evaluate <name>

    Evaluate the best signal on the Test set (2025-10-01 onward).
    Results are written to memory/<name>/test_evaluation.json.

    This is a HUMAN-ONLY operation.  Results are NEVER fed back to the Agent
    — the Test set must remain unseen by the evolution loop.
    """
    name = args.name
    mm   = _memory_manager()
    _require_signal(name, mm)

    print("=" * 60)
    print("⚠  TEST SET EVALUATION — HUMAN-ONLY")
    print("   Results will NOT be shown to the evolution Agent.")
    print("   This segment must remain unseen by the loop.")
    print("=" * 60)
    print()

    best = mm.read_best(name)
    if not best:
        print(f"✗  No best result for '{name}'. Run the evolution loop first.")
        return 1

    best_code_path = best.get("code_path", "")
    best_iter      = best.get("iteration", -1)
    if not best_code_path or not Path(best_code_path).exists():
        if best_iter >= 0:
            best_code_path = str(_MEMORY_ROOT / name / "code" / f"iter_{best_iter:03d}.py")
        if not Path(best_code_path).exists():
            print(f"✗  Best code file not found: {best_code_path}")
            return 1

    print(f"   Loading signal from iter_{best_iter:03d} …")

    try:
        import importlib.util
        from evolution.base_signal import BaseSignal

        spec   = importlib.util.spec_from_file_location(f"_test_{name}", best_code_path)
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        sig_cls = None
        for attr_name in dir(module):
            attr = getattr(module, attr_name)
            if isinstance(attr, type) and issubclass(attr, BaseSignal) and attr is not BaseSignal:
                sig_cls = attr
                break

        if sig_cls is None:
            print("✗  No BaseSignal subclass found.")
            return 1

    except Exception as e:
        print(f"✗  Failed to load signal: {e}")
        return 1

    sig_instance = sig_cls()
    best_params  = best.get("best_params", sig_instance.default_params())

    # Load TEST data (not valid!)
    print("   Loading TEST data (2025-10-01 onward) …")
    try:
        from evolution.data_preparer import DataPreparer
        preparer  = DataPreparer()
        test_data = preparer.load_segment("test")
        print(f"   Loaded {len(test_data)} stocks.")
    except Exception as e:
        print(f"✗  DataPreparer failed: {e}")
        return 1

    if not test_data:
        print("✗  Test data is empty (period may not have enough data yet).")
        return 1

    print("   Running evaluation on Test set …")
    try:
        from evolution.evaluator import SignalEvaluator
        evaluator = SignalEvaluator()
        result    = evaluator.evaluate_signal(
            signal_instance = sig_instance,
            data_dict       = test_data,
            params          = best_params,
            segment         = "test",
            run_l2          = True,
        )
        l1 = result.get("l1", {})
        l2 = result.get("l2") or {}
    except Exception as e:
        print(f"✗  Evaluation failed: {e}")
        return 1

    # Print results
    print()
    print(f"Test Set Evaluation — '{name}'")
    print(_separator(60))

    print("  L1 Metrics (Test):")
    l1_rows = [
        ("primary (T+5 Sharpe)", l1.get("primary")),
        ("sharpe_t3",  l1.get("sharpe_t3")),
        ("sharpe_t5",  l1.get("sharpe_t5")),
        ("sharpe_t10", l1.get("sharpe_t10")),
        ("sharpe_t20", l1.get("sharpe_t20")),
        ("ic_t5",      l1.get("ic_t5")),
        ("win_rate_t5", l1.get("win_rate_t5")),
        ("coverage",   l1.get("coverage")),
    ]
    for label, val in l1_rows:
        v = _safe_float(val)
        if not math.isnan(v):
            print(f"    {label:<24}: {v:.4f}")

    if l2:
        summary = l2.get("summary", {})
        if summary:
            print()
            print("  L2 Backtest (Test):")
            for label, key, fmt in [
                ("Total Return",  "total_return",  ".2%"),
                ("Annual Return", "annual_return", ".2%"),
                ("Sharpe",        "sharpe_ratio",  ".4f"),
                ("Max Drawdown",  "max_drawdown",  ".2%"),
                ("Win Rate",      "win_rate",      ".2%"),
            ]:
                v = _safe_float(summary.get(key))
                if not math.isnan(v):
                    print(f"    {label:<16}: {v:{fmt}}")

    # Save (DO NOT inject into Agent memory)
    out = {
        "signal_name":  name,
        "iteration":    best_iter,
        "best_params":  best_params,
        "l1_metrics":   l1,
        "l2_backtest":  l2,
        "note":         "TEST SET — human-only. Never fed to evolution Agent.",
    }
    out_path = _MEMORY_ROOT / name / "test_evaluation.json"
    try:
        out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
        print()
        print(f"✔  Results saved → {out_path}")
        print(f"   ⚠  This file is for human review only.")
    except Exception as e:
        print(f"   ⚠  Could not save results: {e}")

    return 0


# ─────────────────────────────────────────────────────────────────────────────
# Argument parser
# ─────────────────────────────────────────────────────────────────────────────

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="evolve",
        description="SilverM-quant Signal Evolution System CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python -m evolution.cli create my_signal --direction "探索价量背离"
  python -m evolution.cli run my_signal --max-iterations 50
  python -m evolution.cli status my_signal
  python -m evolution.cli inject my_signal "关注小盘股缩量反弹"
  python -m evolution.cli history my_signal --last 20
  python -m evolution.cli diff my_signal 3 7
  python -m evolution.cli promote my_signal
  python -m evolution.cli backtest my_signal
  python -m evolution.cli evaluate my_signal
  python -m evolution.cli list
  python -m evolution.cli stop my_signal
""",
    )

    sub = parser.add_subparsers(dest="command", metavar="COMMAND")
    sub.required = True

    # ── create ────────────────────────────────────────────────────────────────
    p_create = sub.add_parser("create", help="Initialise memory for a new signal")
    p_create.add_argument("name", help="Signal name (snake_case)")
    p_create.add_argument("--seed", metavar="FILE", help="Path to seed .py file")
    p_create.add_argument("--direction", metavar="TEXT", help="Initial research direction")
    p_create.add_argument("--overwrite", action="store_true", help="Reset existing memory")

    # ── run ───────────────────────────────────────────────────────────────────
    p_run = sub.add_parser("run", help="Start the evolution loop")
    p_run.add_argument("name", help="Signal name")
    p_run.add_argument("--max-iterations", type=int, default=100, metavar="N",
                       help="Max iterations (default: 100)")
    p_run.add_argument("--model", default="claude-opus-4-6", metavar="MODEL",
                       help="Claude model string (default: claude-opus-4-6)")
    p_run.add_argument("--optuna-trials", type=int, default=50, metavar="N",
                       help="Optuna trials per iteration (default: 50)")
    p_run.add_argument("--l2-every", type=int, default=10, metavar="N",
                       help="Run L2 backtest every N iterations (default: 10)")

    # ── stop ──────────────────────────────────────────────────────────────────
    p_stop = sub.add_parser("stop", help="Request graceful stop")
    p_stop.add_argument("name", help="Signal name")

    # ── status ────────────────────────────────────────────────────────────────
    p_status = sub.add_parser("status", help="Show signal status")
    p_status.add_argument("name", help="Signal name")

    # ── inject ────────────────────────────────────────────────────────────────
    p_inject = sub.add_parser("inject", help="Inject a research direction hint")
    p_inject.add_argument("name", help="Signal name")
    p_inject.add_argument("direction", help="Research direction text (quote if multi-word)")

    # ── list ──────────────────────────────────────────────────────────────────
    sub.add_parser("list", help="List all signals")

    # ── history ───────────────────────────────────────────────────────────────
    p_hist = sub.add_parser("history", help="Print iteration history table")
    p_hist.add_argument("name", help="Signal name")
    p_hist.add_argument("--last", type=int, default=None, metavar="N",
                        help="Show only last N iterations")

    # ── diff ──────────────────────────────────────────────────────────────────
    p_diff = sub.add_parser("diff", help="Diff two code iterations")
    p_diff.add_argument("name", help="Signal name")
    p_diff.add_argument("v1", help="First iteration number")
    p_diff.add_argument("v2", help="Second iteration number")

    # ── promote ───────────────────────────────────────────────────────────────
    p_promo = sub.add_parser("promote", help="Copy best code to signals/ and register")
    p_promo.add_argument("name", help="Signal name")
    p_promo.add_argument("--overwrite", action="store_true",
                         help="Overwrite existing promoted file")

    # ── backtest ──────────────────────────────────────────────────────────────
    p_bt = sub.add_parser("backtest", help="Run L2 backtest on best signal (Valid set)")
    p_bt.add_argument("name", help="Signal name")

    # ── evaluate ──────────────────────────────────────────────────────────────
    p_ev = sub.add_parser("evaluate", help="Evaluate best signal on Test set (human-only)")
    p_ev.add_argument("name", help="Signal name")

    return parser


# ─────────────────────────────────────────────────────────────────────────────
# Dispatch table
# ─────────────────────────────────────────────────────────────────────────────

_COMMANDS = {
    "create":   cmd_create,
    "run":      cmd_run,
    "stop":     cmd_stop,
    "status":   cmd_status,
    "inject":   cmd_inject,
    "list":     cmd_list,
    "history":  cmd_history,
    "diff":     cmd_diff,
    "promote":  cmd_promote,
    "backtest": cmd_backtest,
    "evaluate": cmd_evaluate,
}


def main(argv: Optional[List[str]] = None) -> int:
    """
    Main entry point.

    Returns exit code (0 = success, non-zero = error).
    Can be called programmatically:
        from evolution.cli import main
        main(["status", "my_signal"])
    """
    parser = _build_parser()
    args   = parser.parse_args(argv)

    handler = _COMMANDS.get(args.command)
    if handler is None:
        parser.print_help()
        return 1

    try:
        return handler(args) or 0
    except KeyboardInterrupt:
        print("\n⚠  Interrupted.")
        return 130
    except Exception as e:
        logger.exception("Unexpected error in command '%s'", args.command)
        print(f"✗  Unexpected error: {e}")
        return 1


# ─────────────────────────────────────────────────────────────────────────────
# Entry point for python -m evolution.cli
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    sys.exit(main())
