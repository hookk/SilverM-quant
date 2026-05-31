"""
dashboard/evolution_api.py — Evolution System REST API
=======================================================

Flask Blueprint exposing the evolution system to the frontend dashboard.

Response envelope (all endpoints):
    { success: true,  data: ... }    on success
    { success: false, error: "..." } on failure

GET  endpoints additionally include top-level convenience fields
(e.g. name, count) alongside the envelope for easier frontend access.

Endpoints
---------
  GET  /api/evolution/list
  GET  /api/evolution/<name>/status
  GET  /api/evolution/<name>/history    [?last=N]
  GET  /api/evolution/<name>/code/<iter>
  GET  /api/evolution/<name>/diff/<v1>/<v2>
  GET  /api/evolution/<name>/memory
  GET  /api/evolution/<name>/test_results
  POST /api/evolution/create            { name, direction? }
  POST /api/evolution/<name>/inject     { direction }
  POST /api/evolution/<name>/stop
  POST /api/evolution/<name>/promote    { target_dir? }
  DELETE /api/evolution/<name>/stop     (cancel stop flag)
"""

from __future__ import annotations

import difflib
import json
import logging
import math
import os
import re
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from flask import Blueprint, jsonify, request

logger = logging.getLogger(__name__)

# ── Blueprint & path setup ────────────────────────────────────────────────────

_REPO_ROOT = Path(__file__).parent.parent

# Allow override via environment variable for flexibility (version B feature)
_MEMORY_ROOT = Path(
    os.environ.get("EVOLUTION_MEMORY_ROOT", str(_REPO_ROOT / "memory"))
)

sys.path.insert(0, str(_REPO_ROOT))

evolution_bp = Blueprint("evolution", __name__, url_prefix="/api/evolution")


# ─────────────────────────────────────────────────────────────────────────────
# MemoryManager bridge
# Tries to delegate to MemoryManager when available; falls back to direct
# file I/O so the API stays usable even before the evolution package is
# fully installed.
# ─────────────────────────────────────────────────────────────────────────────

def _try_mm(name: Optional[str] = None):
    """
    Return a MemoryManager instance if the evolution package is importable,
    else None.  Never raises.
    """
    try:
        from evolution.memory_manager import MemoryManager  # type: ignore
        return MemoryManager(memory_root=_MEMORY_ROOT)
    except Exception:
        return None


# ─────────────────────────────────────────────────────────────────────────────
# Response helpers
# ─────────────────────────────────────────────────────────────────────────────

def _ok(data: Any, **extra):
    """JSON success envelope.  extra kwargs are merged at the top level."""
    payload = {"success": True, "data": data}
    payload.update(extra)
    return jsonify(payload)


def _err(msg: str, status: int = 400):
    """JSON error envelope."""
    return jsonify({"success": False, "error": msg}), status


# ─────────────────────────────────────────────────────────────────────────────
# Float / JSON sanitisation helpers
# ─────────────────────────────────────────────────────────────────────────────

def _safe_float(v: Any) -> Optional[float]:
    """Convert to float, returning None for NaN / Inf / unconvertible values."""
    try:
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else round(f, 6)
    except (TypeError, ValueError):
        return None


def _clean_metrics(m: Any) -> Dict:
    """Replace NaN/Inf floats with None for safe JSON serialisation."""
    if not isinstance(m, dict):
        return {}
    return {
        k: (_safe_float(v) if isinstance(v, (int, float)) else v)
        for k, v in m.items()
    }


def _clean_record(r: Dict) -> Dict:
    """Sanitise a single iteration record for JSON output."""
    out = dict(r)
    # Normalise both old-style and new-style metric key names
    for key in ("train_metrics", "valid_metrics", "train", "valid"):
        if key in out and isinstance(out[key], dict):
            out[key] = _clean_metrics(out[key])
    if out.get("hypothesis"):
        out["hypothesis_short"] = str(out["hypothesis"])[:120]
    return out


# ─────────────────────────────────────────────────────────────────────────────
# Path helpers (direct file I/O — used when MemoryManager unavailable)
# ─────────────────────────────────────────────────────────────────────────────

def _mem_dir(name: str) -> Path:
    return _MEMORY_ROOT / name


def _iter_json_path(name: str, iteration: int) -> Path:
    return _mem_dir(name) / "iterations" / f"iter_{iteration:03d}.json"


def _code_file_path(name: str, iteration: int) -> Path:
    return _mem_dir(name) / "code" / f"iter_{iteration:03d}.py"


def _best_json_path(name: str) -> Path:
    return _mem_dir(name) / "best" / "best_result.json"


def _summary_md_path(name: str) -> Path:
    return _mem_dir(name) / "summary.md"


def _stop_flag_path(name: str) -> Path:
    return _mem_dir(name) / ".stop"


def _inject_file_path(name: str) -> Path:
    return _mem_dir(name) / "inject_direction.txt"


def _test_result_path(name: str) -> Path:
    # Support both naming conventions used across the codebase
    for fname in ("test_evaluation.json", "test_result.json"):
        p = _mem_dir(name) / fname
        if p.exists():
            return p
    return _mem_dir(name) / "test_evaluation.json"  # canonical new name


def _l2_feedback_path(name: str) -> Path:
    return _mem_dir(name) / "best" / "l2_feedback.json"


def _signal_exists(name: str) -> bool:
    return _mem_dir(name).is_dir()


def _load_json(path: Path) -> Optional[Dict]:
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _list_iter_jsons(name: str) -> List[int]:
    """Sorted list of iteration numbers that have a saved JSON record."""
    iters_dir = _mem_dir(name) / "iterations"
    if not iters_dir.exists():
        return []
    nums = []
    for p in iters_dir.glob("iter_*.json"):
        m = re.match(r"iter_(\d+)\.json$", p.name)
        if m:
            nums.append(int(m.group(1)))
    return sorted(nums)


def _list_code_iters(name: str) -> List[int]:
    """Sorted list of iteration numbers that have saved code files."""
    code_dir = _mem_dir(name) / "code"
    if not code_dir.exists():
        return []
    nums = []
    for p in code_dir.glob("iter_*.py"):
        m = re.match(r"iter_(\d+)\.py$", p.name)
        if m:
            nums.append(int(m.group(1)))
    return sorted(nums)


# ─────────────────────────────────────────────────────────────────────────────
# Signal summary builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_signal_summary(name: str) -> Dict:
    """
    Build the summary card dict for one signal directory.
    Used by both /list and /status.
    Delegates to MemoryManager.status() when available, falls back to
    direct file reads otherwise.
    """
    if not _signal_exists(name):
        return {"name": name, "exists": False}

    mm = _try_mm()

    # ── Try MemoryManager path ──
    if mm is not None:
        try:
            st   = mm.status(name)
            best = mm.read_best(name) or {}
            vm   = best.get("valid_metrics", {}) or {}
            return {
                "name":            name,
                "exists":          True,
                "current_iter":    st.get("current_iter", -1),
                "best_score":      _safe_float(st.get("best_score")),
                "best_iteration":  st.get("best_iteration", -1),
                "pending_inject":  bool(st.get("pending_inject")),
                "stop_pending":    bool(st.get("stop_pending")),
                "summary_lines":   st.get("summary_lines", 0),
                # flattened metrics for the overview card
                "sharpe_t5":       _safe_float(vm.get("sharpe_t5")),
                "ic_t5":           _safe_float(vm.get("ic_t5")),
                "win_rate_t5":     _safe_float(vm.get("win_rate_t5")),
                "coverage":        _safe_float(vm.get("coverage")),
                "hypothesis":      (best.get("hypothesis") or "")[:120],
            }
        except Exception as exc:
            logger.warning("MemoryManager.status(%s) failed, falling back: %s", name, exc)

    # ── Direct file-read fallback ──
    iters        = _list_iter_jsons(name)
    current_iter = max(iters) if iters else -1
    best         = _load_json(_best_json_path(name)) or {}
    vm           = best.get("valid_metrics", {}) or {}
    best_score   = _safe_float(vm.get("sharpe_t5"))
    best_iter_n  = best.get("iteration", -1)

    hypothesis = ""
    if iters:
        latest    = _load_json(_iter_json_path(name, iters[-1])) or {}
        hypothesis = latest.get("hypothesis", "")

    summary_lines = 0
    sp = _summary_md_path(name)
    if sp.exists():
        try:
            summary_lines = len(sp.read_text(encoding="utf-8").splitlines())
        except Exception:
            pass

    return {
        "name":           name,
        "exists":         True,
        "current_iter":   current_iter,
        "best_score":     best_score,
        "best_iteration": best_iter_n,
        "pending_inject": _inject_file_path(name).exists(),
        "stop_pending":   _stop_flag_path(name).exists(),
        "summary_lines":  summary_lines,
        "sharpe_t5":      best_score,
        "ic_t5":          _safe_float(vm.get("ic_t5")),
        "win_rate_t5":    _safe_float(vm.get("win_rate_t5")),
        "coverage":       _safe_float(vm.get("coverage")),
        "hypothesis":     (hypothesis or "")[:120],
    }


# ─────────────────────────────────────────────────────────────────────────────
# GET /list
# ─────────────────────────────────────────────────────────────────────────────

@evolution_bp.route("/list", methods=["GET"])
def list_signals():
    """
    Summary card for every signal in the memory directory.

    Response data: list[SignalSummary]
    """
    signals: List[Dict] = []
    if _MEMORY_ROOT.exists():
        for d in sorted(_MEMORY_ROOT.iterdir()):
            if d.is_dir() and not d.name.startswith("."):
                signals.append(_build_signal_summary(d.name))
    return _ok(signals, total=len(signals))


# ─────────────────────────────────────────────────────────────────────────────
# GET /<name>/status
# ─────────────────────────────────────────────────────────────────────────────

@evolution_bp.route("/<name>/status", methods=["GET"])
def signal_status(name: str):
    """
    Detailed status for one signal, including best_params and full metric set.
    """
    if not _signal_exists(name):
        return _err(f"Signal '{name}' not found", 404)

    summary = _build_signal_summary(name)

    # Enrich with best_params (not included in overview card to keep it lean)
    best = None
    mm   = _try_mm()
    if mm is not None:
        try:
            best = mm.read_best(name) or {}
        except Exception:
            pass
    if best is None:
        best = _load_json(_best_json_path(name)) or {}

    summary["best_params"]    = best.get("best_params", {})
    summary["best_hypothesis"] = (best.get("hypothesis") or "")[:300]

    return _ok(summary)


# ─────────────────────────────────────────────────────────────────────────────
# GET /<name>/history
# ─────────────────────────────────────────────────────────────────────────────

@evolution_bp.route("/<name>/history", methods=["GET"])
def signal_history(name: str):
    """
    All iteration records as a time-series list.

    Query params:
        last=N  – return only the most recent N records (default: all)

    Response data: list[IterationRecord]
    """
    if not _signal_exists(name):
        return _err(f"Signal '{name}' not found", 404)

    mm = _try_mm()
    records: List[Dict] = []

    if mm is not None:
        try:
            records = mm.read_all_iterations(name) or []
        except Exception as exc:
            logger.warning("MemoryManager.read_all_iterations(%s) failed: %s", name, exc)

    if not records:
        # Direct file-read fallback
        for n in _list_iter_jsons(name):
            rec = _load_json(_iter_json_path(name, n))
            if rec:
                records.append(rec)

    last = request.args.get("last", type=int)
    if last and last > 0:
        records = records[-last:]

    cleaned = [_clean_record(r) for r in records]
    return _ok(cleaned, name=name, count=len(cleaned))


# ─────────────────────────────────────────────────────────────────────────────
# GET /<name>/code/<iter>
# ─────────────────────────────────────────────────────────────────────────────

@evolution_bp.route("/<name>/code/<int:iter_num>", methods=["GET"])
def signal_code(name: str, iter_num: int):
    """
    Source code for a specific iteration.

    Response data: { iter, source, path }
    Also exposes legacy key 'code' as alias for 'source' for compat.
    """
    if not _signal_exists(name):
        return _err(f"Signal '{name}' not found", 404)

    mm = _try_mm()
    source: Optional[str] = None

    if mm is not None:
        try:
            source = mm.read_code(name, iter_num)
        except Exception as exc:
            logger.warning("MemoryManager.read_code(%s, %d) failed: %s", name, iter_num, exc)

    if source is None:
        path = _code_file_path(name, iter_num)
        if not path.exists():
            return _err(f"Code for iter {iter_num} not found", 404)
        try:
            source = path.read_text(encoding="utf-8")
        except Exception as e:
            return _err(str(e), 500)

    rel_path = str(_MEMORY_ROOT / name / "code" / f"iter_{iter_num:03d}.py")
    return _ok({
        "iter":   iter_num,
        "source": source,
        "code":   source,          # backward-compat alias
        "path":   rel_path,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /<name>/diff/<v1>/<v2>
# ─────────────────────────────────────────────────────────────────────────────

@evolution_bp.route("/<name>/diff/<int:v1>/<int:v2>", methods=["GET"])
def signal_diff(name: str, v1: int, v2: int):
    """
    Unified diff between two code iterations.

    Response data: {
        v1, v2,
        diff_lines: [str, ...],   # per-line array — easy for frontend rendering
        diff_text:  str,          # raw unified diff — useful for download / copy
        lines_added, lines_removed,
        identical: bool
    }
    """
    if not _signal_exists(name):
        return _err(f"Signal '{name}' not found", 404)

    # Resolve source texts (MM first, then direct read)
    def _get_code(n: int) -> Optional[str]:
        mm = _try_mm()
        if mm is not None:
            try:
                return mm.read_code(name, n)
            except Exception:
                pass
        p = _code_file_path(name, n)
        return p.read_text(encoding="utf-8") if p.exists() else None

    code_v1 = _get_code(v1)
    code_v2 = _get_code(v2)

    missing = []
    if code_v1 is None:
        missing.append(f"iter_{v1:03d}")
    if code_v2 is None:
        missing.append(f"iter_{v2:03d}")
    if missing:
        return _err(f"Code not found: {', '.join(missing)}", 404)

    lines_a = code_v1.splitlines(keepends=True)  # type: ignore[union-attr]
    lines_b = code_v2.splitlines(keepends=True)  # type: ignore[union-attr]

    diff_iter   = difflib.unified_diff(
        lines_a, lines_b,
        fromfile=f"iter_{v1:03d}.py",
        tofile=f"iter_{v2:03d}.py",
    )
    diff_raw    = list(diff_iter)
    diff_text   = "".join(diff_raw)
    diff_lines  = [l.rstrip("\n") for l in diff_raw]

    lines_added   = sum(1 for l in diff_lines if l.startswith("+") and not l.startswith("+++"))
    lines_removed = sum(1 for l in diff_lines if l.startswith("-") and not l.startswith("---"))

    return _ok({
        "v1":            v1,
        "v2":            v2,
        "diff_lines":    diff_lines,    # frontend line-by-line rendering
        "diff_text":     diff_text,     # raw text for copy/download
        "lines_added":   lines_added,
        "lines_removed": lines_removed,
        "identical":     len(diff_lines) == 0,
        # legacy aliases
        "adds":          lines_added,
        "removes":       lines_removed,
    })


# ─────────────────────────────────────────────────────────────────────────────
# GET /<name>/memory
# ─────────────────────────────────────────────────────────────────────────────

@evolution_bp.route("/<name>/memory", methods=["GET"])
def signal_memory(name: str):
    """
    Summary memory view: best result + summary.md + L2 feedback + inject preview.

    Response data: {
        best_result:    dict | null,
        summary_md:     str | null,
        l2_feedback:    dict | null,
        inject_pending: str | null,   # read-only preview of inject_direction.txt
        code_iters:     [int, ...],
    }
    """
    if not _signal_exists(name):
        return _err(f"Signal '{name}' not found", 404)

    # best result
    best: Optional[Dict] = None
    mm = _try_mm()
    if mm is not None:
        try:
            raw  = mm.read_best(name)
            best = _clean_record(raw) if raw else None
        except Exception as exc:
            logger.warning("MemoryManager.read_best(%s) failed: %s", name, exc)
    if best is None:
        raw = _load_json(_best_json_path(name))
        best = _clean_record(raw) if raw else None

    # summary.md
    summary_md: Optional[str] = None
    sp = _summary_md_path(name)
    if sp.exists():
        try:
            summary_md = sp.read_text(encoding="utf-8")
        except Exception:
            pass

    # L2 feedback — check standalone file first, then embedded in best_result
    l2_feedback: Optional[Dict] = _load_json(_l2_feedback_path(name))
    if l2_feedback is None and best and "l2_feedback" in best:
        l2_feedback = best.get("l2_feedback")

    # inject direction preview (never cleared here — orchestrator owns it)
    inject_text: Optional[str] = None
    ip = _inject_file_path(name)
    if ip.exists():
        try:
            inject_text = ip.read_text(encoding="utf-8").strip() or None
        except Exception:
            pass

    code_iters = _list_code_iters(name)

    return _ok({
        "best_result":    best,
        "summary_md":     summary_md,
        "l2_feedback":    l2_feedback,
        "inject_pending": inject_text,
        "code_iters":     code_iters,
    }, name=name)


# ─────────────────────────────────────────────────────────────────────────────
# GET /<name>/test_results
# ─────────────────────────────────────────────────────────────────────────────

@evolution_bp.route("/<name>/test_results", methods=["GET"])
def signal_test_results(name: str):
    """
    Test set evaluation results — human-only, never shown to the Agent.

    Response data: dict | null  (null = not yet evaluated)
    Frontend should check success=true AND data!=null.
    """
    if not _signal_exists(name):
        return _err(f"Signal '{name}' not found", 404)

    path = _test_result_path(name)
    if not path.exists():
        return _ok(None, exists=False,
                   message=f"No test evaluation yet. Run: python -m evolution.cli evaluate {name}")

    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(data.get("l1_metrics"), dict):
            data["l1_metrics"] = _clean_metrics(data["l1_metrics"])
        return _ok(data, exists=True)
    except Exception as e:
        return _err(f"Failed to read test results: {e}", 500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /create
# ─────────────────────────────────────────────────────────────────────────────

@evolution_bp.route("/create", methods=["POST"])
def create_signal():
    """
    Initialise memory directory for a new signal.

    Request JSON: { name: str, direction?: str }
    Response data: { name: str, path: str }
    """
    body      = request.get_json(silent=True) or {}
    name      = str(body.get("name", "")).strip()
    direction = str(body.get("direction", "")).strip()

    if not name:
        return _err("'name' is required")
    if not re.match(r"^[a-zA-Z][a-zA-Z0-9_]{0,63}$", name):
        return _err("name must start with a letter, contain only letters/digits/underscores, max 64 chars")
    if _signal_exists(name):
        return _err(f"Signal '{name}' already exists", 409)

    try:
        for sub in ("iterations", "code", "best"):
            (_mem_dir(name) / sub).mkdir(parents=True, exist_ok=True)

        if direction:
            ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
            with open(_inject_file_path(name), "w", encoding="utf-8") as f:
                f.write(f"[{ts}] {direction}\n")

        mem_path = str(_mem_dir(name))
        logger.info("Created signal '%s' at %s", name, mem_path)
        return _ok({"name": name, "path": mem_path})

    except Exception as e:
        return _err(str(e), 500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /<name>/inject
# ─────────────────────────────────────────────────────────────────────────────

@evolution_bp.route("/<name>/inject", methods=["POST"])
def inject_direction(name: str):
    """
    Append a research direction for the next evolution iteration.
    Directions accumulate with timestamps; orchestrator reads the file each loop.

    Request JSON: { direction: str }
    Response data: { message: str, direction_preview: str }
    """
    if not _signal_exists(name):
        return _err(f"Signal '{name}' not found", 404)

    body      = request.get_json(silent=True) or {}
    direction = str(body.get("direction", "")).strip()
    if not direction:
        return _err("'direction' field is required and cannot be empty")

    # Delegate to MemoryManager when available
    mm = _try_mm()
    if mm is not None:
        try:
            mm.append_inject_direction(name, direction)
            return _ok({
                "message":           f"Direction injected for '{name}'",
                "direction_preview": direction[:80],
            })
        except Exception as exc:
            logger.warning("MemoryManager.append_inject_direction failed: %s", exc)

    # Direct file fallback
    try:
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        with open(_inject_file_path(name), "a", encoding="utf-8") as f:
            f.write(f"[{ts}] {direction}\n")
        return _ok({
            "message":           f"Direction injected for '{name}'",
            "direction_preview": direction[:80],
        })
    except Exception as e:
        return _err(str(e), 500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /<name>/stop   /   DELETE /<name>/stop
# ─────────────────────────────────────────────────────────────────────────────

@evolution_bp.route("/<name>/stop", methods=["POST"])
def stop_signal(name: str):
    """
    Write .stop flag — orchestrator exits gracefully after the current iteration.

    Response data: { message: str }
    """
    if not _signal_exists(name):
        return _err(f"Signal '{name}' not found", 404)

    mm = _try_mm()
    if mm is not None:
        try:
            mm.set_stop(name)
            return _ok({"message": f"Stop flag set for '{name}'. Loop will exit after current iteration."})
        except Exception as exc:
            logger.warning("MemoryManager.set_stop failed: %s", exc)

    try:
        _stop_flag_path(name).touch()
        return _ok({"message": f"Stop flag set for '{name}'. Loop will exit after current iteration."})
    except Exception as e:
        return _err(str(e), 500)


@evolution_bp.route("/<name>/stop", methods=["DELETE"])
def cancel_stop(name: str):
    """
    Remove the .stop flag to allow the evolution loop to continue.

    Response data: { message: str }
    """
    if not _signal_exists(name):
        return _err(f"Signal '{name}' not found", 404)

    flag = _stop_flag_path(name)
    if not flag.exists():
        return _ok({"message": f"No stop flag found for '{name}' — already running."})
    try:
        flag.unlink()
        return _ok({"message": f"Stop flag removed for '{name}'."})
    except Exception as e:
        return _err(str(e), 500)


# ─────────────────────────────────────────────────────────────────────────────
# POST /<name>/promote
# ─────────────────────────────────────────────────────────────────────────────

@evolution_bp.route("/<name>/promote", methods=["POST"])
def promote_signal(name: str):
    """
    Copy the best iteration's code to signals/singal_cal/<name>_evolved.py
    (or a custom target directory supplied in the request body).

    Request JSON: { target_dir?: str }
    Response data: { source_iter, source_path, destination }
    """
    if not _signal_exists(name):
        return _err(f"Signal '{name}' not found", 404)

    # Resolve best iteration
    best: Optional[Dict] = None
    mm = _try_mm()
    if mm is not None:
        try:
            best = mm.read_best(name)
        except Exception:
            pass
    if best is None:
        best = _load_json(_best_json_path(name))

    if not best:
        return _err("No best result found — run at least one iteration before promoting", 400)

    best_iter = best.get("iteration")
    if best_iter is None:
        return _err("best_result.json missing 'iteration' field", 400)

    src = _code_file_path(name, int(best_iter))
    if not src.exists():
        return _err(f"Best code file not found: iter_{best_iter:03d}.py", 404)

    # Resolve destination directory
    body       = request.get_json(silent=True) or {}
    target_dir = body.get("target_dir")
    if target_dir:
        dst_dir = Path(target_dir)
    else:
        dst_dir = _REPO_ROOT / "signals" / "singal_cal"

    dst_dir.mkdir(parents=True, exist_ok=True)
    dst = dst_dir / f"{name}_evolved.py"

    try:
        shutil.copy2(src, dst)
        rel = dst.relative_to(_REPO_ROOT) if dst.is_relative_to(_REPO_ROOT) else dst
        logger.info("[promote] %s iter_%03d → %s", name, best_iter, dst)
        return _ok({
            "source_iter":  int(best_iter),
            "source_path":  str(src),
            "destination":  str(rel),
        })
    except Exception as e:
        return _err(str(e), 500)


# ─────────────────────────────────────────────────────────────────────────────
# Evaluation metric helpers (Issue #3)
# ─────────────────────────────────────────────────────────────────────────────

def _sortino(returns: "np.ndarray", annualise: bool = True) -> float:
    """Sortino ratio: mean / downside std (negative returns only)."""
    import math, numpy as np
    r = returns[np.isfinite(returns)]
    if len(r) < 2:
        return float("nan")
    mu = float(np.mean(r))
    neg = r[r < 0]
    if len(neg) == 0:
        return float("inf") if mu > 0 else float("nan")
    ds = float(np.std(neg, ddof=1))
    if ds == 0:
        return float("nan")
    sr = mu / ds
    if annualise:
        sr *= math.sqrt(252)
    return sr


def _profit_factor(returns: "np.ndarray") -> float:
    """Gross profit / |gross loss|. Returns nan if no losses."""
    import numpy as np
    r = returns[np.isfinite(returns)]
    gross_profit = float(r[r > 0].sum()) if len(r[r > 0]) > 0 else 0.0
    gross_loss   = abs(float(r[r < 0].sum())) if len(r[r < 0]) > 0 else 0.0
    if gross_loss == 0:
        return float("inf") if gross_profit > 0 else float("nan")
    return gross_profit / gross_loss


def _max_drawdown(returns: "np.ndarray") -> float:
    """Max drawdown from a returns series."""
    import numpy as np
    r = returns[np.isfinite(returns)]
    if len(r) == 0:
        return float("nan")
    cum = (1 + r).cumprod()
    peak = np.maximum.accumulate(cum)
    dd = (cum - peak) / peak
    return float(dd.min())


def _full_period_stats(
    returns: "np.ndarray",
    horizon: int,
) -> Dict:
    """Compute full stat dict for a given horizon's return series."""
    import numpy as np, math
    r = returns[np.isfinite(returns)]
    if len(r) < 5:
        nan = None
        return {
            "period": horizon, "count": int(len(r)),
            "mean": nan, "median": nan, "win_rate": nan,
            "max": nan, "min": nan, "std": nan,
            "sharpe": nan, "sortino": nan,
            "max_drawdown": nan, "profit_factor": nan,
        }
    mu  = float(np.nanmean(r))
    std = float(np.nanstd(r, ddof=1))
    sr  = (mu / std * math.sqrt(252)) if std > 0 else float("nan")
    return {
        "period":        horizon,
        "count":         int(len(r)),
        "mean":          _safe_float(mu),
        "median":        _safe_float(float(np.median(r))),
        "win_rate":      _safe_float(float((r > 0).mean())),
        "max":           _safe_float(float(r.max())),
        "min":           _safe_float(float(r.min())),
        "std":           _safe_float(std),
        "sharpe":        _safe_float(sr),
        "sortino":       _safe_float(_sortino(r)),
        "max_drawdown":  _safe_float(_max_drawdown(r)),
        "profit_factor": _safe_float(_profit_factor(r)),
    }


def _collect_signal_events(
    signal_instance: Any,
    data_dict: Dict,
    params: Dict,
    horizons: List[int],
    threshold: float = 0.0,
) -> List[Dict]:
    """
    Collect per-signal trigger events with forward returns.

    Returns list of:
      { stock_code, signal_date, strength, returns: {T+h: float, ...} }
    """
    import numpy as np, pandas as pd
    events: List[Dict] = []
    for code, df in data_dict.items():
        try:
            sig = signal_instance._safe_calculate(df, params)
        except Exception:
            continue
        triggers = sig[sig.abs() > threshold].dropna()
        for dt, strength in triggers.items():
            returns: Dict[str, Optional[float]] = {}
            for h in horizons:
                try:
                    loc = df.index.get_loc(dt)
                    future_loc = loc + h
                    if future_loc < len(df):
                        c0 = df["close"].iloc[loc]
                        ch = df["close"].iloc[future_loc]
                        returns[f"t{h}"] = _safe_float((ch - c0) / c0) if c0 > 0 else None
                    else:
                        returns[f"t{h}"] = None
                except Exception:
                    returns[f"t{h}"] = None
            events.append({
                "stock_code":   code,
                "signal_date":  str(dt.date() if hasattr(dt, "date") else dt)[:10],
                "strength":     _safe_float(float(strength)),
                "returns":      returns,
            })
    # Sort by date descending (most recent first)
    events.sort(key=lambda e: e["signal_date"], reverse=True)
    return events


# ─────────────────────────────────────────────────────────────────────────────
# GET /<name>/test-eval  (Issue #3 — enhanced test-set evaluation)
# ─────────────────────────────────────────────────────────────────────────────

@evolution_bp.route("/<name>/test-eval", methods=["GET"])
def signal_test_eval(name: str):
    """
    Live test-set evaluation for the best signal.

    Changes from the old /test_results endpoint (Issue #3):
      • Computes T+1 through T+20 (vs only [3,5,10,20] from config).
      • Returns full stat dict per period: sharpe, sortino, max_drawdown,
        profit_factor, mean, median, win_rate, std, max, min.
      • Adds signal_details list: stock_code, signal_date, strength,
        per-period forward returns — for individual trade verification.
      • This endpoint is human-only; results are NOT fed back to the Agent.

    Response data:
      {
        signal_name: str,
        evaluated_at: str,
        best_iteration: int,
        best_params: dict,
        holding_period_stats: [ { period, count, sharpe, sortino,
                                   max_drawdown, profit_factor, ... } ],
        signal_details: [ { stock_code, signal_date, strength,
                             returns: {t1, t3, t5, t10, t20} } ],
        summary: { total_signals, date_range },
      }
    """
    if not _signal_exists(name):
        return _err(f"Signal '{name}' not found", 404)

    # ── Load best result ──────────────────────────────────────────────────────
    best = _load_json(_best_json_path(name))
    if not best:
        return _err("No best result found — run at least one iteration first", 404)

    best_iter  = best.get("iteration")
    best_params = best.get("best_params", {})

    # ── Load signal class ─────────────────────────────────────────────────────
    sig_instance = None
    code_path = _code_file_path(name, int(best_iter)) if best_iter is not None else None
    if code_path and code_path.exists():
        try:
            import importlib.util, sys as _sys
            spec    = importlib.util.spec_from_file_location(f"_evolved_{name}", code_path)
            mod     = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)  # type: ignore[union-attr]
            # Find the BaseSignal subclass
            from evolution.base_signal import BaseSignal
            for attr in dir(mod):
                obj = getattr(mod, attr)
                if (
                    isinstance(obj, type)
                    and issubclass(obj, BaseSignal)
                    and obj is not BaseSignal
                ):
                    sig_instance = obj()
                    break
        except Exception as exc:
            logger.warning("test-eval: failed to load signal code: %s", exc)

    if sig_instance is None:
        return _err(
            "Could not load signal class from best iteration code. "
            "Ensure the code file exists and defines a valid BaseSignal subclass.",
            500,
        )

    # ── Load test data ────────────────────────────────────────────────────────
    try:
        from evolution.data_preparer import DataPreparer
        dp        = DataPreparer()
        test_data = dp.prepare_segment("test")
    except Exception as exc:
        return _err(f"Failed to load test data: {exc}", 500)

    if not test_data:
        return _err("Test data is empty — check data_registry.yaml test split", 500)

    # ── Full T+1 ~ T+20 evaluation ────────────────────────────────────────────
    try:
        import numpy as np
        HORIZONS = list(range(1, 21))

        # Pool signal values + forward returns across all stocks
        all_returns_by_h: Dict[int, List[float]] = {h: [] for h in HORIZONS}

        for code, df in test_data.items():
            try:
                sig = sig_instance._safe_calculate(df, best_params)
            except Exception:
                continue
            # Triggered signals only
            triggers = sig[sig.abs() > 0].dropna()
            for h in HORIZONS:
                close = df["close"]
                for dt in triggers.index:
                    try:
                        loc = df.index.get_loc(dt)
                        fl  = loc + h
                        if fl < len(df):
                            c0 = float(close.iloc[loc])
                            ch = float(close.iloc[fl])
                            if c0 > 0:
                                all_returns_by_h[h].append((ch - c0) / c0)
                    except Exception:
                        pass

        holding_period_stats = []
        for h in HORIZONS:
            r_arr = np.array(all_returns_by_h[h], dtype=float)
            holding_period_stats.append(_full_period_stats(r_arr, h))

        # ── Signal details (for individual trade inspection) ──────────────────
        detail_horizons = [1, 3, 5, 10, 20]
        signal_details = _collect_signal_events(
            sig_instance, test_data, best_params, detail_horizons,
        )
        # Cap at 500 entries (performance guard)
        signal_details = signal_details[:500]

        total_signals = sum(s["count"] for s in holding_period_stats if s["period"] == 5)

        return _ok({
            "signal_name":          name,
            "evaluated_at":         datetime.now().isoformat(),
            "best_iteration":       best_iter,
            "best_params":          best_params,
            "holding_period_stats": holding_period_stats,
            "signal_details":       signal_details,
            "summary": {
                "total_signals":    total_signals,
                "n_stocks":         len(test_data),
            },
        }, human_only=True)

    except Exception as exc:
        logger.exception("test-eval: evaluation failed for '%s'", name)
        return _err(f"Evaluation failed: {exc}", 500)


# ─────────────────────────────────────────────────────────────────────────────
# GET /<name>/config  (Issue #3 — evaluation configuration endpoint)
# ─────────────────────────────────────────────────────────────────────────────

@evolution_bp.route("/<name>/config", methods=["GET"])
def signal_config(name: str):
    """
    Return merged global + signal-level configuration.

    Global config: evolution/data_registry.yaml (splits, evaluation, holdout_gate)
    Signal config: memory/<name>/config.yaml (model, research_direction, created_at)

    Response data:
      {
        data_splits:   { train, valid_search, valid_holdout, test },
        evaluation:    { l1_forward_returns, l1_primary_metric },
        holdout_gate:  { min_signals, min_sharpe, max_overfit_ratio },
        optuna:        { n_trials, cv_folds, min_signals, sharpe_cap },
        signal: {
          model, research_direction, created_at,
          best_iteration, best_score,
        },
      }
    """
    if not _signal_exists(name):
        return _err(f"Signal '{name}' not found", 404)

    # ── Global config from data_registry.yaml ────────────────────────────────
    global_cfg: Dict = {}
    try:
        import yaml
        reg_path = _REPO_ROOT / "evolution" / "data_registry.yaml"
        if reg_path.exists():
            with open(reg_path, encoding="utf-8") as f:
                reg = yaml.safe_load(f)
            splits = reg.get("splits", {})
            global_cfg = {
                "data_splits": {
                    k: {
                        "start": v.get("start"),
                        "end":   v.get("end"),
                        "description": v.get("description", ""),
                    }
                    for k, v in splits.items()
                },
                "evaluation":   reg.get("evaluation", {}),
                "holdout_gate": reg.get("holdout_gate", {}),
                "optuna": {
                    "n_trials":     reg.get("resources", {}).get("optuna_n_trials", 20),
                    "cv_folds":     reg.get("resources", {}).get("optuna_cv_folds", 4),
                    "min_signals":  reg.get("resources", {}).get("optuna_min_signals", 80),
                    "sharpe_cap":   reg.get("resources", {}).get("optuna_sharpe_cap", 3.0),
                },
            }
    except Exception as exc:
        logger.warning("signal_config: failed to read data_registry.yaml: %s", exc)

    # ── Signal-level config ───────────────────────────────────────────────────
    signal_cfg: Dict = {}

    # Try signal-level config.yaml
    sig_config_path = _mem_dir(name) / "config.yaml"
    if sig_config_path.exists():
        try:
            import yaml
            with open(sig_config_path, encoding="utf-8") as f:
                signal_cfg = yaml.safe_load(f) or {}
        except Exception as exc:
            logger.warning("signal_config: failed to read signal config.yaml: %s", exc)

    # Enrich with best result metadata
    best = _load_json(_best_json_path(name)) or {}
    vm   = best.get("valid_metrics", {}) or {}
    signal_cfg.setdefault("research_direction", "")
    signal_cfg.setdefault("model", "")
    signal_cfg.setdefault("created_at", "")
    signal_cfg["best_iteration"] = best.get("iteration")
    signal_cfg["best_score"]     = _safe_float(
        best.get("holdout_score") or vm.get("primary")
    )
    signal_cfg["holdout_passed"] = best.get("holdout_passed")
    signal_cfg["holdout_score"]  = _safe_float(best.get("holdout_score"))

    # Also try inject file for research_direction
    if not signal_cfg["research_direction"]:
        ip = _inject_file_path(name)
        if ip.exists():
            try:
                lines = ip.read_text(encoding="utf-8").strip().splitlines()
                if lines:
                    signal_cfg["research_direction"] = lines[0][:200]
            except Exception:
                pass

    return _ok({
        **global_cfg,
        "signal": signal_cfg,
    }, name=name)


# ─────────────────────────────────────────────────────────────────────────────
# GET /<name>/signals  (Issue #3 alias — frontend may call /signals/<name>/*)
# ─────────────────────────────────────────────────────────────────────────────
# The blueprint prefix is /api/evolution; the frontend (Issue #3) expects
# /api/evolution/signals/<name>/... paths. We register thin aliases so both
# old /<name>/... and new /signals/<name>/... paths work simultaneously.

@evolution_bp.route("/signals", methods=["GET"])
def list_signals_alias():
    """Alias for /list — matches Issue #3 frontend expectation."""
    return list_signals()


@evolution_bp.route("/signals/<name>/history", methods=["GET"])
def signal_history_alias(name: str):
    return signal_history(name)


@evolution_bp.route("/signals/<name>/test-eval", methods=["GET"])
def signal_test_eval_alias(name: str):
    return signal_test_eval(name)


@evolution_bp.route("/signals/<name>/config", methods=["GET"])
def signal_config_alias(name: str):
    return signal_config(name)


@evolution_bp.route("/signals/<name>/code/<int:iter_num>", methods=["GET"])
def signal_code_alias(name: str, iter_num: int):
    return signal_code(name, iter_num)
