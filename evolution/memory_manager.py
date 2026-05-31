"""
evolution/memory_manager.py — Per-Signal File-Based Memory Manager
===================================================================

Manages the on-disk memory structure for each evolving signal:

    memory/
    └── <signal_name>/
        ├── iterations/
        │   ├── iter_000.json   ← full iteration record
        │   ├── iter_001.json
        │   └── ...
        ├── code/
        │   ├── iter_000.py     ← generated code snapshot
        │   ├── iter_001.py
        │   └── ...
        ├── best/
        │   └── best_result.json  ← best iteration so far
        ├── summary.md            ← rolling compressed summary
        └── inject_direction.txt  ← human-injected research hints

Iteration JSON schema:
    {
      "iteration":          int,
      "hypothesis":         str,   # what the Agent claimed to be testing
      "code_path":          str,   # relative path to code file
      "train_metrics":      dict,  # L1 metrics on Train set
      "valid_metrics":      dict,  # L1 metrics on Valid-Search set (Optuna CV)
      "valid_search_score": float, # Optuna best_score (capped CV Sharpe)
      "best_params":        dict,  # Optuna best params
      "holdout_passed":     bool,  # True = passed holdout gate; False = rejected
      "holdout_score":      float, # holdout T+5 Sharpe (or null if not run)
      "rejection_reason":   str,   # why holdout was rejected (empty if passed)
      "total_signals_search":  int,   # signal triggers in valid_search
      "total_signals_holdout": int,   # signal triggers in valid_holdout
      "conclusion":         str,   # Agent's self-assessment
      "notes":              str,   # orchestrator-added notes
      "timestamp":          str,   # ISO-8601
    }

Key methods:
    write_iteration(name, iter_num, record)   → saves iter_NNN.json
    read_iteration(name, iter_num)             → loads iter_NNN.json
    write_code(name, iter_num, source)         → saves code/iter_NNN.py
    read_latest_code(name)                     → source of highest iter
    update_best(name, record)                  → update best_result.json
    read_best(name)                            → load best_result.json
    compress_summary(name)                     → fold old iters into summary.md
    read_inject_direction(name)                → read injected hints (clears after read)
    append_inject_direction(name, text)        → append hint to inject file
"""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# Default memory root relative to repo root
_DEFAULT_MEMORY_ROOT = Path(__file__).parent.parent / "memory"

# How many old iterations to compress in one call
_COMPRESS_THRESHOLD = 10


# ─────────────────────────────────────────────────────────────────────────────
# MemoryManager
# ─────────────────────────────────────────────────────────────────────────────

class MemoryManager:
    """
    File-based memory store for the evolution loop.

    One instance manages all signals under a shared memory_root.
    All paths are deterministic and human-readable.

    Args:
        memory_root: Root directory for all signal memories.
                     Defaults to <repo>/memory/.
        compress_every_n: Compress old iterations into summary.md
                          every N iterations (default 10).
    """

    def __init__(
        self,
        memory_root: Optional[Path | str] = None,
        compress_every_n: int = _COMPRESS_THRESHOLD,
    ):
        self.memory_root      = Path(memory_root or _DEFAULT_MEMORY_ROOT)
        self.compress_every_n = compress_every_n

    # ── Directory helpers ─────────────────────────────────────────────────────

    def _signal_dir(self, name: str) -> Path:
        return self.memory_root / name

    def _iter_dir(self, name: str) -> Path:
        return self._signal_dir(name) / "iterations"

    def _code_dir(self, name: str) -> Path:
        return self._signal_dir(name) / "code"

    def _best_dir(self, name: str) -> Path:
        return self._signal_dir(name) / "best"

    def _iter_path(self, name: str, iteration: int) -> Path:
        return self._iter_dir(name) / f"iter_{iteration:03d}.json"

    def _code_path(self, name: str, iteration: int) -> Path:
        return self._code_dir(name) / f"iter_{iteration:03d}.py"

    def _best_path(self, name: str) -> Path:
        return self._best_dir(name) / "best_result.json"

    def _summary_path(self, name: str) -> Path:
        return self._signal_dir(name) / "summary.md"

    def _inject_path(self, name: str) -> Path:
        return self._signal_dir(name) / "inject_direction.txt"

    def _stop_path(self, name: str) -> Path:
        return self._signal_dir(name) / ".stop"

    # ── Initialisation ────────────────────────────────────────────────────────

    def init_signal(
        self,
        name: str,
        seed_code: Optional[str] = None,
        initial_direction: Optional[str] = None,
        overwrite: bool = False,
    ) -> Path:
        """
        Create the directory structure for a new signal.

        Args:
            name:              Signal name (e.g. 'my_momentum').
            seed_code:         Optional Python source to write as iter_000.py.
            initial_direction: Optional research direction to write to inject file.
            overwrite:         If True, delete and recreate existing directory.

        Returns:
            Path to the signal directory.
        """
        sig_dir = self._signal_dir(name)

        if sig_dir.exists():
            if not overwrite:
                logger.info("init_signal: '%s' already exists — skipping", name)
                return sig_dir
            shutil.rmtree(sig_dir)
            logger.info("init_signal: removed existing '%s'", name)

        for sub in ["iterations", "code", "best"]:
            (sig_dir / sub).mkdir(parents=True, exist_ok=True)

        # Write initial summary stub
        summary = self._summary_path(name)
        summary.write_text(
            f"# Evolution Memory: {name}\n\n"
            f"Created: {_now_iso()}\n\n"
            "## Summary\n\n*(no iterations yet)*\n",
            encoding="utf-8",
        )

        if seed_code:
            self._code_path(name, 0).write_text(seed_code, encoding="utf-8")
            logger.info("init_signal: wrote seed code for '%s'", name)

        if initial_direction:
            self.append_inject_direction(name, initial_direction)

        logger.info("init_signal: initialised memory directory for '%s' at %s", name, sig_dir)
        return sig_dir

    def signal_exists(self, name: str) -> bool:
        """Return True if memory directory for this signal exists."""
        return self._signal_dir(name).exists()

    def list_signals(self) -> List[str]:
        """Return all signal names with existing memory directories."""
        if not self.memory_root.exists():
            return []
        return sorted(
            d.name for d in self.memory_root.iterdir()
            if d.is_dir() and not d.name.startswith(".")
        )

    # ── Iteration records ─────────────────────────────────────────────────────

    def write_iteration(
        self,
        name: str,
        iteration: int,
        record: Dict[str, Any],
    ) -> Path:
        """
        Persist an iteration record to iterations/iter_NNN.json.

        The record dict is merged with a timestamp and the iteration number.
        Fields: iteration, hypothesis, code_path, train_metrics,
                valid_metrics, best_params, conclusion, notes.

        Args:
            name:      Signal name.
            iteration: Iteration number (0-indexed).
            record:    Dict of iteration data (see module docstring schema).

        Returns:
            Path to the written JSON file.
        """
        self._iter_dir(name).mkdir(parents=True, exist_ok=True)

        record = dict(record)  # copy — don't mutate caller's dict
        record.setdefault("iteration",     iteration)
        record.setdefault("timestamp",     _now_iso())
        record.setdefault("hypothesis",    "")
        record.setdefault("code_path",     str(self._code_path(name, iteration)))
        record.setdefault("train_metrics",         {})
        record.setdefault("valid_metrics",         {})
        record.setdefault("valid_search_score",    None)   # capped CV Sharpe from Optuna
        record.setdefault("best_params",           {})
        record.setdefault("holdout_passed",        None)  # None=gate not run, True/False=result
        record.setdefault("holdout_score",         None)  # holdout T+5 Sharpe
        record.setdefault("rejection_reason",      "")    # reason if holdout_passed=False
        record.setdefault("total_signals_search",  None)  # signal count on valid_search
        record.setdefault("total_signals_holdout", None)  # signal count on valid_holdout
        record.setdefault("conclusion",            "")
        record.setdefault("notes",                 "")

        path = self._iter_path(name, iteration)
        path.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        logger.debug("write_iteration: '%s' iter %d → %s", name, iteration, path)
        return path

    def read_iteration(self, name: str, iteration: int) -> Optional[Dict[str, Any]]:
        """
        Load an iteration record. Returns None if file doesn't exist.
        """
        path = self._iter_path(name, iteration)
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def read_all_iterations(self, name: str) -> List[Dict[str, Any]]:
        """
        Load all iteration records for a signal, sorted by iteration number.
        """
        iter_dir = self._iter_dir(name)
        if not iter_dir.exists():
            return []
        records = []
        for p in sorted(iter_dir.glob("iter_*.json")):
            try:
                records.append(json.loads(p.read_text(encoding="utf-8")))
            except Exception as e:
                logger.warning("read_all_iterations: failed to read %s: %s", p, e)
        return records

    def current_iteration(self, name: str) -> int:
        """
        Return the highest completed iteration number, or -1 if none.
        """
        iter_dir = self._iter_dir(name)
        if not iter_dir.exists():
            return -1
        nums = [
            int(m.group(1))
            for p in iter_dir.glob("iter_*.json")
            if (m := re.match(r"iter_(\d+)\.json", p.name))
        ]
        return max(nums) if nums else -1

    def next_iteration(self, name: str) -> int:
        """Return the next iteration number to use (current + 1, or 0)."""
        return self.current_iteration(name) + 1

    # ── Code files ────────────────────────────────────────────────────────────

    def write_code(self, name: str, iteration: int, source: str) -> Path:
        """
        Write generated Python source to code/iter_NNN.py.

        Args:
            name:      Signal name.
            iteration: Iteration number.
            source:    Python source code string.

        Returns:
            Path to the written .py file.
        """
        self._code_dir(name).mkdir(parents=True, exist_ok=True)
        path = self._code_path(name, iteration)
        path.write_text(source, encoding="utf-8")
        logger.debug("write_code: '%s' iter %d → %s", name, iteration, path)
        return path

    def read_code(self, name: str, iteration: int) -> Optional[str]:
        """
        Read generated code for a specific iteration.
        Returns None if file doesn't exist.
        """
        path = self._code_path(name, iteration)
        if not path.exists():
            return None
        return path.read_text(encoding="utf-8")

    def read_latest_code(self, name: str) -> Optional[str]:
        """
        Read the code from the most recent iteration.
        Returns None if no code files exist.
        """
        code_dir = self._code_dir(name)
        if not code_dir.exists():
            return None
        py_files = sorted(code_dir.glob("iter_*.py"))
        if not py_files:
            return None
        return py_files[-1].read_text(encoding="utf-8")

    def code_diff(self, name: str, iter_a: int, iter_b: int) -> str:
        """
        Return a unified diff between two code iterations.

        Args:
            name:   Signal name.
            iter_a: First iteration (older).
            iter_b: Second iteration (newer).

        Returns:
            Unified diff string (empty string if either file missing or identical).
        """
        import difflib
        src_a = self.read_code(name, iter_a) or ""
        src_b = self.read_code(name, iter_b) or ""
        if src_a == src_b:
            return ""
        diff = difflib.unified_diff(
            src_a.splitlines(keepends=True),
            src_b.splitlines(keepends=True),
            fromfile=f"iter_{iter_a:03d}.py",
            tofile=f"iter_{iter_b:03d}.py",
        )
        return "".join(diff)

    # ── Best result ───────────────────────────────────────────────────────────

    def update_best(self, name: str, record: Dict[str, Any]) -> bool:
        """
        Update best_result.json if the new record's holdout score is higher.

        Score priority (Issue #2 anti-overfit):
          1. holdout_score (T+5 Sharpe on independent holdout period) — preferred
          2. valid_metrics.primary (valid_search CV Sharpe) — fallback

        Only records where holdout_passed=True (or holdout_passed=None, meaning
        the gate was not run) are considered for best update.

        Args:
            name:   Signal name.
            record: Iteration record (must contain holdout_score or valid_metrics.primary).

        Returns:
            True if this record became the new best; False if existing best kept.
        """
        self._best_dir(name).mkdir(parents=True, exist_ok=True)

        # Holdout-rejected records never become best
        holdout_passed = record.get("holdout_passed")
        if holdout_passed is False:
            logger.debug(
                "update_best: '%s' iter %d holdout_rejected — not updating best",
                name, record.get("iteration", -1),
            )
            return False

        new_score = _extract_score_for_best(record)
        best_path = self._best_path(name)

        if best_path.exists():
            existing = json.loads(best_path.read_text(encoding="utf-8"))
            existing_score = _extract_score_for_best(existing)
            if not _is_nan(existing_score):
                if not _is_nan(new_score) and new_score > existing_score:
                    best_path.write_text(
                        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
                    )
                    logger.info(
                        "update_best: '%s' new best holdout_score=%.4f (was %.4f, iter %d)",
                        name, new_score, existing_score, record.get("iteration", -1),
                    )
                    return True
                else:
                    logger.debug(
                        "update_best: '%s' kept existing best=%.4f > new=%.4f",
                        name, existing_score, new_score,
                    )
                    return False

        # No existing best — write unconditionally
        best_path.write_text(
            json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        logger.info(
            "update_best: '%s' first best score=%.4f (iter %d)",
            name, new_score, record.get("iteration", -1),
        )
        return True

    def read_best(self, name: str) -> Optional[Dict[str, Any]]:
        """Load best_result.json. Returns None if no best exists yet."""
        best_path = self._best_path(name)
        if not best_path.exists():
            return None
        return json.loads(best_path.read_text(encoding="utf-8"))

    # ── Summary compression ───────────────────────────────────────────────────

    def compress_summary(self, name: str, force: bool = False) -> bool:
        """
        Fold completed iteration records into summary.md.

        Called automatically every compress_every_n iterations.
        After compression, old iter_NNN.json files before the cutoff
        are deleted (code files are kept for diff/audit).

        Args:
            name:  Signal name.
            force: If True, compress all iterations regardless of threshold.

        Returns:
            True if compression was performed; False if skipped.
        """
        records = self.read_all_iterations(name)
        if not records:
            return False

        # Determine which iterations are already summarised
        summary_path = self._summary_path(name)
        existing_summary = ""
        if summary_path.exists():
            existing_summary = summary_path.read_text(encoding="utf-8")

        # Find highest summarised iteration number in existing summary
        last_summarised = -1
        m = re.search(r"<!--summarised_up_to:(\d+)-->", existing_summary)
        if m:
            last_summarised = int(m.group(1))

        # Candidate records to summarise: those beyond last_summarised
        candidates = [r for r in records if r.get("iteration", -1) > last_summarised]

        if not force and len(candidates) < self.compress_every_n:
            logger.debug(
                "compress_summary: '%s' only %d new iters < threshold %d — skipping",
                name, len(candidates), self.compress_every_n,
            )
            return False

        if not candidates:
            return False

        # Build markdown block for these iterations
        block_lines = [
            f"\n## Iterations {candidates[0]['iteration']} – "
            f"{candidates[-1]['iteration']} "
            f"(compressed {_now_iso()[:10]})\n"
        ]
        for r in candidates:
            iter_num = r.get("iteration", "?")
            hyp      = (r.get("hypothesis") or "").strip()[:120]
            concl    = (r.get("conclusion") or "").strip()[:200]
            vm       = r.get("valid_metrics", {})
            score    = vm.get("primary", float("nan"))
            sharpe   = vm.get(f"sharpe_t5", float("nan"))
            ic       = vm.get("ic_t5", float("nan"))
            block_lines.append(
                f"### Iter {iter_num:03d} — score={score:.4f} "
                f"sharpe_t5={sharpe:.4f} ic_t5={ic:.4f}\n"
                f"**Hypothesis:** {hyp or '(none)'}\n\n"
                f"**Conclusion:** {concl or '(none)'}\n"
            )

        new_marker = f"<!--summarised_up_to:{candidates[-1]['iteration']}-->"
        block_lines.append(new_marker + "\n")

        # Replace old marker or append
        if m:
            updated = existing_summary.replace(
                m.group(0), "\n".join(block_lines)
            )
        else:
            updated = existing_summary + "\n" + "\n".join(block_lines)

        summary_path.write_text(updated, encoding="utf-8")

        # Delete old iteration JSON files (keep code files)
        for r in candidates:
            jpath = self._iter_path(name, r["iteration"])
            try:
                jpath.unlink(missing_ok=True)
            except Exception:
                pass

        logger.info(
            "compress_summary: '%s' compressed %d iterations into summary.md",
            name, len(candidates),
        )
        return True

    def maybe_compress(self, name: str) -> None:
        """
        Trigger compress_summary if iteration count crosses the threshold.
        Called by orchestrator after each write_iteration().
        """
        current = self.current_iteration(name)
        if current >= 0 and (current + 1) % self.compress_every_n == 0:
            self.compress_summary(name)

    # ── Direction injection ───────────────────────────────────────────────────

    def append_inject_direction(self, name: str, text: str) -> None:
        """
        Append a research direction hint to inject_direction.txt.

        Called by `evolve inject <name> "direction"` CLI command.
        The orchestrator reads and clears this file before building each prompt.
        """
        inject_path = self._inject_path(name)
        inject_path.parent.mkdir(parents=True, exist_ok=True)
        with inject_path.open("a", encoding="utf-8") as f:
            f.write(f"[{_now_iso()}] {text.strip()}\n")
        logger.info("inject_direction: '%s' ← %s", name, text[:80])

    def read_inject_direction(self, name: str, clear: bool = True) -> str:
        """
        Read pending research direction hints.

        Args:
            name:  Signal name.
            clear: If True, delete the file after reading (consumed once).

        Returns:
            String contents of inject_direction.txt, or "" if not present.
        """
        inject_path = self._inject_path(name)
        if not inject_path.exists():
            return ""
        content = inject_path.read_text(encoding="utf-8").strip()
        if clear and content:
            inject_path.unlink(missing_ok=True)
            logger.debug("read_inject_direction: '%s' consumed direction hints", name)
        return content

    # ── Stop flag ─────────────────────────────────────────────────────────────

    def set_stop(self, name: str) -> None:
        """Write .stop flag file to request graceful stop."""
        self._stop_path(name).touch()
        logger.info("set_stop: '%s' stop flag set", name)

    def check_stop(self, name: str) -> bool:
        """Return True if .stop flag exists (and remove it)."""
        stop_path = self._stop_path(name)
        if stop_path.exists():
            stop_path.unlink(missing_ok=True)
            logger.info("check_stop: '%s' stop flag consumed", name)
            return True
        return False

    # ── Status summary ────────────────────────────────────────────────────────

    def status(self, name: str) -> Dict[str, Any]:
        """
        Return a concise status dict for a signal.

        Returns:
            {
              "name":           str,
              "exists":         bool,
              "current_iter":   int,
              "best_score":     float,
              "best_iteration": int,
              "summary_lines":  int,
              "pending_inject": bool,
              "stop_pending":   bool,
            }
        """
        if not self.signal_exists(name):
            return {"name": name, "exists": False}

        best = self.read_best(name)
        best_score = _extract_primary(best) if best else float("nan")
        best_iter  = best.get("iteration", -1) if best else -1

        summary_path = self._summary_path(name)
        summary_lines = 0
        if summary_path.exists():
            summary_lines = len(summary_path.read_text(encoding="utf-8").splitlines())

        return {
            "name":           name,
            "exists":         True,
            "current_iter":   self.current_iteration(name),
            "best_score":     round(float(best_score), 6) if not _is_nan(best_score) else None,
            "best_iteration": best_iter,
            "summary_lines":  summary_lines,
            "pending_inject": self._inject_path(name).exists(),
            "stop_pending":   self._stop_path(name).exists(),
        }

    def __repr__(self) -> str:
        signals = self.list_signals()
        return f"<MemoryManager root={self.memory_root} signals={signals}>"


# ─────────────────────────────────────────────────────────────────────────────
# Module-level singleton
# ─────────────────────────────────────────────────────────────────────────────

memory_manager = MemoryManager()
"""
Default MemoryManager instance using <repo>/memory/ as root.
Override by instantiating MemoryManager(memory_root=...) directly.
"""


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _extract_primary(record: Optional[Dict[str, Any]]) -> float:
    """Extract valid_search CV primary score (used for compress_summary display)."""
    if not record:
        return float("nan")
    # Try valid_search_score first (new schema), fall back to valid_metrics.primary
    vss = record.get("valid_search_score")
    if vss is not None:
        try:
            return float(vss)
        except (TypeError, ValueError):
            pass
    vm = record.get("valid_metrics", {})
    v  = vm.get("primary", float("nan"))
    try:
        return float(v)
    except (TypeError, ValueError):
        return float("nan")


def _extract_score_for_best(record: Optional[Dict[str, Any]]) -> float:
    """
    Extract the score used for best-iteration comparison.

    Priority (Issue #2 anti-overfit):
      1. holdout_score   — reflects generalisation ability
      2. valid_search_score / valid_metrics.primary — fallback when gate not run
    """
    if not record:
        return float("nan")
    hs = record.get("holdout_score")
    if hs is not None:
        try:
            f = float(hs)
            if not _is_nan(f):
                return f
        except (TypeError, ValueError):
            pass
    return _extract_primary(record)


def _is_nan(v: Any) -> bool:
    try:
        import math
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return True


# ── Lazy numpy import (only needed in update_best) ────────────────────────────
try:
    import numpy as np
except ImportError:
    class _NpStub:
        @staticmethod
        def isnan(v):
            return _is_nan(v)
    np = _NpStub()
