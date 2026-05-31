"""
evolution/orchestrator.py — Evolution Loop Orchestrator
========================================================

The main control loop that ties together all Phase 3 components:

    ┌─────────────────────────────────────────────────────┐
    │  for each iteration:                                │
    │    1. Read memory + inject_direction                │
    │    2. Build Agent prompt (with L2 feedback context) │
    │    3. Spawn: claude -p "..." --model <model>        │
    │    4. Wait for Agent to write code file             │
    │    5. SandboxExecutor: AST check + subprocess run   │
    │    6. OptunaSearcher: 50-trial param search         │
    │    7. SignalEvaluator: L1 Train + Valid metrics      │
    │    8. MemoryManager: write_iteration + update_best  │
    │    9. (every 10 iters) compress_summary             │
    │   10. Check stop flag → break or continue           │
    └─────────────────────────────────────────────────────┘

Features:
  • Hot injection:    write inject_direction.txt → included in next prompt
  • Graceful stop:    write .stop file → loop exits after current iter
  • Memory guard:     psutil watches free RAM; pauses if < 2 GB
  • Auto-compression: every 10 iterations, old iter JSONs folded into summary.md
  • L2 on milestone:  full backtest run every `l2_every_n` iterations

Signal code contract (enforced):
  • Agent MUST write code to the path passed in the prompt.
  • Code MUST define a class that inherits BaseSignal.
  • SandboxExecutor validates before any execution.

Usage
-----
    from evolution.orchestrator import Orchestrator

    orch = Orchestrator(model="claude-opus-4-5")
    orch.run_loop("my_signal", max_iterations=100)
"""

from __future__ import annotations

import gc
import json
import logging
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Constants
# ─────────────────────────────────────────────────────────────────────────────

_DEFAULT_MODEL          = "claude-opus-4-6"
_DEFAULT_MAX_ITERATIONS = 100
_DEFAULT_L2_EVERY_N     = 10      # Run full L2 backtest every N iterations
_AGENT_TIMEOUT_SEC      = 300     # Max seconds to wait for Agent to write code
_PAUSE_FREE_MEM_GB      = 2.0
_CODE_WRITE_POLL_SEC    = 2.0     # Poll interval when waiting for Agent output

_REPO_ROOT   = Path(__file__).parent.parent
_MEMORY_ROOT = _REPO_ROOT / "memory"


# ─────────────────────────────────────────────────────────────────────────────
# Prompt builder
# ─────────────────────────────────────────────────────────────────────────────

def _build_prompt(
    signal_name: str,
    iteration: int,
    code_output_path: str,
    summary_md: str,
    recent_iterations: List[Dict],
    best_result: Optional[Dict],
    inject_direction: str,
    l2_feedback: Optional[Dict],
    data_registry_info: str,
) -> str:
    """
    Construct the full prompt string sent to the Claude agent subprocess.

    The prompt includes:
      - Signal identity and current iteration
      - Memory context (summary + recent iterations)
      - Best result so far
      - L2 feedback (if available)
      - Injected research direction (if any)
      - Exact code output path the Agent must write to
      - Interface contract reminder
    """
    recent_md = _format_recent_iterations(recent_iterations)
    best_md   = _format_best_result(best_result)
    inject_md = f"\n## 研究方向注入 (Human Hint)\n{inject_direction}\n" if inject_direction.strip() else ""
    l2_md     = f"\n## L2 回测反馈 (Valid集)\n```\n{l2_feedback.get('text', '')}\n```\n" if l2_feedback else ""

    prompt = f"""你是一个量化因子研究员，正在为 A股 股票池进化一个信号。

# 任务
信号名称: {signal_name}
当前迭代: {iteration}
你需要编写一个新版本的信号代码，并将其写入以下路径：
```
{code_output_path}
```

# 代码合同 (必须遵守)
1. 你的代码文件必须定义一个继承 `BaseSignal` 的类。
2. 必须实现两个方法：
   - `define_params(self, trial) -> dict`  — 声明 Optuna 参数空间
   - `calculate(self, df, params) -> pd.Series`  — 计算信号
3. `calculate` 必须返回 float64 Series，长度等于输入 df，正值=看多，负值=看空，NaN=无信号。
4. 只能 import: pandas, numpy, math, evolution.indicators_lib, optuna。
5. 禁止: os/sys/subprocess/socket/open()/eval()/exec() 等任何文件或网络操作。
6. 你可以使用 evolution.indicators_lib 中封装的指标函数 (MA/EMA/MACD/RSI/KDJ/Bollinger/ATR/OBV/ADX/CCI/WR/MFI)。

# 数据说明
{data_registry_info}

# 你的历史记忆
## 摘要 (已压缩的历史迭代)
{summary_md or '*(无历史摘要)*'}

## 最近几轮迭代
{recent_md}

## 当前最佳结果
{best_md}
{l2_md}{inject_md}
# 思考框架
1. 分析当前最佳结果的优劣势：哪些市场条件下表现好/差？
2. 提出一个可验证的研究假设 (hypothesis)。
3. 将假设编码为 BaseSignal 实现。
4. 参数化你认为关键的阈值，写入 define_params()。

# 输出格式
请按以下格式先输出你的思考，再写代码：

**HYPOTHESIS:** (一句话描述你在测试什么)
**REASONING:** (为什么这个方向值得探索)
**CODE_FILE:** {code_output_path}

然后将完整的 Python 代码写入文件 `{code_output_path}`。
代码文件只需要包含 import 和类定义，无需 if __name__ == "__main__" 块。
"""
    return prompt.strip()


def _format_recent_iterations(records: List[Dict], n: int = 5) -> str:
    if not records:
        return "*(无迭代记录)*"
    lines = []
    for r in records[-n:]:
        iter_num = r.get("iteration", "?")
        hyp  = (r.get("hypothesis") or "").strip()[:100]
        vm   = r.get("valid_metrics", {})
        score = vm.get("primary", float("nan"))
        concl = (r.get("conclusion") or "").strip()[:150]
        lines.append(
            f"- Iter {iter_num:03d}: score={score:.4f} | hypothesis={hyp} | conclusion={concl}"
            if not _is_nan(score) else
            f"- Iter {iter_num:03d}: score=N/A | hypothesis={hyp} | conclusion={concl}"
        )
    return "\n".join(lines)


def _format_best_result(best: Optional[Dict]) -> str:
    if not best:
        return "*(尚无最佳结果)*"
    iter_num = best.get("iteration", "?")
    vm = best.get("valid_metrics", {})
    score    = vm.get("primary", float("nan"))
    sharpe5  = vm.get("sharpe_t5", float("nan"))
    ic5      = vm.get("ic_t5", float("nan"))
    params   = json.dumps(best.get("best_params", {}), ensure_ascii=False, indent=2)
    hyp      = (best.get("hypothesis") or "").strip()[:200]
    return (
        f"迭代: {iter_num} | score={score:.4f} | sharpe_t5={sharpe5:.4f} | ic_t5={ic5:.4f}\n"
        f"假设: {hyp}\n"
        f"最优参数:\n```json\n{params}\n```"
    )


def _data_registry_info() -> str:
    """Build a concise data column reference for the prompt."""
    return (
        "输入 df 的列 (data_registry.yaml 定义):\n"
        "  必有列: open, high, low, close, volume, turnover, pct_change_raw\n"
        "  可选列 (如已加载): hs300, zz500 (基准指数), total_mv, pe_ttm, pb (市值/估值)\n"
        "  df.index 为 DatetimeIndex，按日期升序排列 (最老→最新)。\n"
        "  df.attrs['code'] 为股票代码，如 '000001.SZ'。"
    )


# ─────────────────────────────────────────────────────────────────────────────
# Memory guard
# ─────────────────────────────────────────────────────────────────────────────

def _available_mem_gb() -> float:
    try:
        import psutil
        return psutil.virtual_memory().available / 1024 ** 3
    except ImportError:
        return float("inf")


def _is_nan(v: Any) -> bool:
    try:
        import math
        return math.isnan(float(v))
    except (TypeError, ValueError):
        return True


# ─────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ─────────────────────────────────────────────────────────────────────────────

class Orchestrator:
    """
    Evolution loop orchestrator.

    Coordinates: prompt building → Claude subprocess → sandbox →
                 Optuna search → L1/L2 evaluation → memory persistence.

    Args:
        model:           Claude model string for `claude -p` CLI.
        max_iterations:  Default iteration cap.
        optuna_trials:   Number of Optuna trials per iteration.
        l2_every_n:      Run full L2 backtest every N iterations.
        pause_mem_gb:    Pause loop if free RAM < this value.
        memory_root:     Override default memory directory.
        data_segments:   Which data segments to load ('train', 'valid').
    """

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        max_iterations: int = _DEFAULT_MAX_ITERATIONS,
        optuna_trials: int = 50,
        l2_every_n: int = _DEFAULT_L2_EVERY_N,
        pause_mem_gb: float = _PAUSE_FREE_MEM_GB,
        memory_root: Optional[Path] = None,
        data_segments: Optional[List[str]] = None,
    ):
        self.model          = model
        self.max_iterations = max_iterations
        self.optuna_trials  = optuna_trials
        self.l2_every_n     = l2_every_n
        self.pause_mem_gb   = pause_mem_gb
        self.memory_root    = Path(memory_root or _MEMORY_ROOT)
        self.data_segments  = data_segments or ["train", "valid"]

        # Lazy-loaded components (initialised in run_loop)
        self._memory:    Optional[Any] = None
        self._evaluator: Optional[Any] = None
        self._searcher:  Optional[Any] = None
        self._sandbox:   Optional[Any] = None
        self._train_data: Optional[Dict] = None
        self._valid_data: Optional[Dict] = None

    # ── Main loop ─────────────────────────────────────────────────────────────

    def run_loop(
        self,
        signal_name: str,
        max_iterations: Optional[int] = None,
        seed_code: Optional[str] = None,
    ) -> None:
        """
        Run the evolution loop for a signal.

        Args:
            signal_name:    Name of the signal to evolve.
            max_iterations: Override default max_iterations.
            seed_code:      Optional seed code for iteration 0.
        """
        max_iter = max_iterations or self.max_iterations
        self._init_components(signal_name, seed_code)

        logger.info(
            "Orchestrator: starting loop for '%s' — max_iter=%d model=%s",
            signal_name, max_iter, self.model,
        )

        for iteration in range(max_iter):
            logger.info("━━━ Iteration %d / %d ━━━", iteration, max_iter - 1)

            # ── Stop flag check ───────────────────────────────────────────────
            if self._memory.check_stop(signal_name):
                logger.info("Orchestrator: stop flag detected — exiting loop")
                break

            # ── Memory guard ──────────────────────────────────────────────────
            self._wait_memory()

            # ── Step 1: Read memory context ───────────────────────────────────
            summary_md        = self._read_summary(signal_name)
            recent_iterations = self._memory.read_all_iterations(signal_name)
            best_result       = self._memory.read_best(signal_name)
            inject_direction  = self._memory.read_inject_direction(signal_name, clear=True)
            l2_feedback       = self._load_last_l2(signal_name)

            # ── Step 2: Build prompt ──────────────────────────────────────────
            code_output_path = str(
                self.memory_root / signal_name / "code" / f"iter_{iteration:03d}.py"
            )
            prompt = _build_prompt(
                signal_name       = signal_name,
                iteration         = iteration,
                code_output_path  = code_output_path,
                summary_md        = summary_md,
                recent_iterations = recent_iterations,
                best_result       = best_result,
                inject_direction  = inject_direction,
                l2_feedback       = l2_feedback,
                data_registry_info= _data_registry_info(),
            )

            # ── Step 3: Invoke Agent ──────────────────────────────────────────
            hypothesis, agent_ok = self._invoke_agent(prompt, signal_name, iteration)
            if not agent_ok:
                logger.warning("Iteration %d: agent invocation failed — skipping", iteration)
                self._write_failed_iteration(signal_name, iteration, hypothesis, "AGENT_FAILED")
                continue

            # ── Step 4: Wait for code file ────────────────────────────────────
            code_ready = self._wait_for_code(code_output_path)
            if not code_ready:
                logger.warning("Iteration %d: code file not found — skipping", iteration)
                self._write_failed_iteration(signal_name, iteration, hypothesis, "CODE_NOT_WRITTEN")
                continue

            # ── Step 5: Sandbox validation ────────────────────────────────────
            sandbox_result = self._sandbox.run_file(
                code_output_path,
                extra_pythonpath=self._extra_pythonpath(),
            )
            if not sandbox_result["success"]:
                logger.warning(
                    "Iteration %d: sandbox rejected code (%s): %s",
                    iteration, sandbox_result["error_type"], sandbox_result["stderr"][:200],
                )
                self._write_failed_iteration(
                    signal_name, iteration, hypothesis,
                    f"SANDBOX:{sandbox_result['error_type']}",
                )
                continue

            # ── Step 6: Load signal class from code file ──────────────────────
            signal_cls = self._load_signal_class(code_output_path, signal_name)
            if signal_cls is None:
                logger.warning("Iteration %d: could not load signal class — skipping", iteration)
                self._write_failed_iteration(signal_name, iteration, hypothesis, "CLASS_LOAD_FAILED")
                continue

            # ── Step 7: Optuna parameter search ──────────────────────────────
            search_result = self._run_optuna(signal_cls, signal_name, iteration)
            best_params   = search_result.get("best_params", {})
            best_score    = search_result.get("best_score", float("nan"))

            # ── Step 8: L1 evaluation ─────────────────────────────────────────
            instance = signal_cls()
            train_metrics = self._evaluate_l1(instance, self._train_data, best_params, "train")
            valid_metrics = self._evaluate_l1(instance, self._valid_data, best_params, "valid")

            # ── Step 9: Optional L2 backtest ─────────────────────────────────
            l2_result = None
            if iteration > 0 and iteration % self.l2_every_n == 0:
                l2_result = self._run_l2(instance, best_params, signal_name, iteration)

            # ── Step 10: Persist to memory ────────────────────────────────────
            record = {
                "iteration":     iteration,
                "hypothesis":    hypothesis,
                "code_path":     code_output_path,
                "train_metrics": train_metrics,
                "valid_metrics": valid_metrics,
                "best_params":   best_params,
                "optuna_score":  float(best_score) if not _is_nan(best_score) else None,
                "conclusion":    self._derive_conclusion(valid_metrics, best_result),
                "notes":         "",
            }
            self._memory.write_iteration(signal_name, iteration, record)
            self._memory.update_best(signal_name, record)
            self._memory.maybe_compress(signal_name)

            if l2_result:
                self._save_l2(signal_name, iteration, l2_result)

            logger.info(
                "Iteration %d complete — valid_primary=%.4f optuna_best=%.4f",
                iteration,
                valid_metrics.get("primary", float("nan")),
                float(best_score) if not _is_nan(best_score) else float("nan"),
            )

            gc.collect()

        logger.info("Orchestrator: loop finished for '%s'", signal_name)

    # ── Initialisation ────────────────────────────────────────────────────────

    def _init_components(self, signal_name: str, seed_code: Optional[str]) -> None:
        """Lazy-initialise all components and load data."""
        from evolution.memory_manager  import MemoryManager
        from evolution.evaluator       import SignalEvaluator
        from evolution.optuna_searcher import OptunaSearcher
        from evolution.sandbox_executor import SandboxExecutor

        self._memory    = MemoryManager(memory_root=self.memory_root)
        self._evaluator = SignalEvaluator()
        self._searcher  = OptunaSearcher(n_trials=self.optuna_trials)
        self._sandbox   = SandboxExecutor()

        # Initialise memory directory for this signal
        if not self._memory.signal_exists(signal_name):
            self._memory.init_signal(signal_name, seed_code=seed_code)

        # Load data
        logger.info("Orchestrator: loading data segments %s …", self.data_segments)
        self._train_data, self._valid_data = self._load_data()
        logger.info(
            "Orchestrator: loaded %d train stocks, %d valid stocks",
            len(self._train_data or {}), len(self._valid_data or {}),
        )

    def _load_data(self):
        """Load train + valid data via DataPreparer."""
        try:
            from evolution.data_preparer import DataPreparer
            preparer = DataPreparer()
            train_data = preparer.load_segment("train")
            valid_data = preparer.load_segment("valid")
            return train_data, valid_data
        except Exception as e:
            logger.warning(
                "Orchestrator: DataPreparer failed: %s — using empty data dicts", e
            )
            return {}, {}

    # ── Agent invocation ──────────────────────────────────────────────────────

    def _invoke_agent(
        self, prompt: str, signal_name: str, iteration: int
    ) -> tuple[str, bool]:
        """
        Invoke the Claude CLI agent with the prompt.

        Returns (hypothesis_str, success_bool).
        """
        # Write prompt to a temp file (avoids shell escaping issues)
        prompt_path = self.memory_root / signal_name / f"_prompt_iter_{iteration:03d}.txt"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        prompt_path.write_text(prompt, encoding="utf-8")

        cmd = [
            "claude",
            "-p", prompt,
            "--model", self.model,
        ]

        logger.info("Orchestrator: invoking agent (iter %d) …", iteration)
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=_AGENT_TIMEOUT_SEC,
                env={**os.environ},
            )
            stdout = result.stdout or ""
            if result.returncode != 0:
                logger.warning(
                    "Agent subprocess returned %d: %s",
                    result.returncode, result.stderr[:300],
                )
                return "", False

            # Extract hypothesis from agent output
            hypothesis = self._extract_hypothesis(stdout)
            logger.debug("Agent hypothesis: %s", hypothesis[:120])
            return hypothesis, True

        except subprocess.TimeoutExpired:
            logger.warning("Agent subprocess timed out after %d s", _AGENT_TIMEOUT_SEC)
            return "", False
        except FileNotFoundError:
            logger.error(
                "Orchestrator: 'claude' CLI not found. "
                "Install with: pip install anthropic  (or npm install -g @anthropic-ai/claude-code)"
            )
            return "", False
        except Exception as e:
            logger.exception("Agent invocation error: %s", e)
            return "", False
        finally:
            try:
                prompt_path.unlink(missing_ok=True)
            except Exception:
                pass

    @staticmethod
    def _extract_hypothesis(agent_output: str) -> str:
        """
        Extract the HYPOTHESIS line from agent output.
        Falls back to first non-empty line if not found.
        """
        for line in agent_output.splitlines():
            stripped = line.strip()
            if stripped.upper().startswith("**HYPOTHESIS:**"):
                return stripped.split(":**", 1)[-1].strip().strip("*").strip()
            if stripped.startswith("HYPOTHESIS:"):
                return stripped.split(":", 1)[-1].strip()
        # Fallback: first non-empty line
        for line in agent_output.splitlines():
            if line.strip():
                return line.strip()[:200]
        return "(no hypothesis)"

    # ── Code file watcher ─────────────────────────────────────────────────────

    def _wait_for_code(
        self,
        code_path: str,
        timeout_sec: float = _AGENT_TIMEOUT_SEC,
    ) -> bool:
        """
        Poll until code_path exists and is non-empty, or timeout.

        Returns True if code file is ready.
        """
        deadline = time.time() + timeout_sec
        while time.time() < deadline:
            p = Path(code_path)
            if p.exists() and p.stat().st_size > 0:
                return True
            time.sleep(_CODE_WRITE_POLL_SEC)
        return False

    # ── Signal class loader ───────────────────────────────────────────────────

    def _load_signal_class(
        self, code_path: str, signal_name: str
    ) -> Optional[type]:
        """
        Dynamically import a signal class from a code file.

        Looks for any class that inherits BaseSignal in the module.
        Returns None if loading fails.
        """
        import importlib.util
        from evolution.base_signal import BaseSignal

        try:
            spec = importlib.util.spec_from_file_location(
                f"_evolved_{signal_name}", code_path
            )
            if spec is None or spec.loader is None:
                logger.warning("_load_signal_class: cannot create spec for %s", code_path)
                return None
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)

            # Find a BaseSignal subclass in the module
            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and issubclass(attr, BaseSignal)
                    and attr is not BaseSignal
                ):
                    logger.debug("Loaded signal class: %s from %s", attr_name, code_path)
                    return attr

            logger.warning("_load_signal_class: no BaseSignal subclass found in %s", code_path)
            return None

        except Exception as e:
            logger.warning("_load_signal_class: failed to load %s: %s", code_path, e)
            return None

    # ── Optuna search ─────────────────────────────────────────────────────────

    def _run_optuna(
        self, signal_cls: type, signal_name: str, iteration: int
    ) -> Dict[str, Any]:
        """Run Optuna search; return result dict."""
        if not self._valid_data:
            logger.warning("Optuna: no valid data — skipping search, using defaults")
            try:
                params = signal_cls().default_params()
            except Exception:
                params = {}
            return {"best_params": params, "best_score": float("nan")}

        try:
            result = self._searcher.search(
                signal_cls  = signal_cls,
                valid_data  = self._valid_data,
                study_name  = f"{signal_name}_iter{iteration:03d}",
            )
            return result
        except Exception as e:
            logger.warning("Optuna search failed (iter %d): %s", iteration, e)
            try:
                params = signal_cls().default_params()
            except Exception:
                params = {}
            return {"best_params": params, "best_score": float("nan")}

    # ── L1 evaluation ─────────────────────────────────────────────────────────

    def _evaluate_l1(
        self,
        instance,
        data_dict: Optional[Dict],
        params: Dict,
        segment: str,
    ) -> Dict[str, Any]:
        """Run L1 cross-sectional evaluation. Returns metric dict."""
        if not data_dict:
            return {"primary": float("nan"), "segment": segment}
        try:
            result = self._evaluator.evaluate_signal(
                signal_instance = instance,
                data_dict       = data_dict,
                params          = params,
                segment         = segment,
                run_l2          = False,
            )
            metrics = result["l1"]
            metrics["segment"] = segment
            return metrics
        except Exception as e:
            logger.warning("L1 evaluation (%s) failed: %s", segment, e)
            return {"primary": float("nan"), "segment": segment}

    # ── L2 backtest ───────────────────────────────────────────────────────────

    def _run_l2(
        self,
        instance,
        params: Dict,
        signal_name: str,
        iteration: int,
    ) -> Optional[Dict]:
        """Run L2 full backtest and return feedback dict."""
        if not self._valid_data:
            return None
        logger.info("Running L2 full backtest (iter %d) …", iteration)
        try:
            result = self._evaluator.evaluate_signal(
                signal_instance = instance,
                data_dict       = self._valid_data,
                params          = params,
                segment         = "valid",
                run_l2          = True,
            )
            return result["l2"]
        except Exception as e:
            logger.warning("L2 backtest failed (iter %d): %s", iteration, e)
            return None

    def _save_l2(self, signal_name: str, iteration: int, l2_result: Dict) -> None:
        """Persist L2 result to memory/<name>/l2_iter_NNN.json."""
        out = self.memory_root / signal_name / f"l2_iter_{iteration:03d}.json"
        try:
            out.write_text(json.dumps(l2_result, ensure_ascii=False, indent=2), encoding="utf-8")
            logger.debug("Saved L2 result → %s", out)
        except Exception as e:
            logger.warning("_save_l2: failed: %s", e)

    def _load_last_l2(self, signal_name: str) -> Optional[Dict]:
        """Load the most recent L2 result file, or None."""
        sig_dir = self.memory_root / signal_name
        if not sig_dir.exists():
            return None
        l2_files = sorted(sig_dir.glob("l2_iter_*.json"))
        if not l2_files:
            return None
        try:
            return json.loads(l2_files[-1].read_text(encoding="utf-8"))
        except Exception:
            return None

    # ── Memory helpers ────────────────────────────────────────────────────────

    def _read_summary(self, signal_name: str) -> str:
        summary_path = self.memory_root / signal_name / "summary.md"
        if not summary_path.exists():
            return ""
        return summary_path.read_text(encoding="utf-8")

    def _write_failed_iteration(
        self,
        signal_name: str,
        iteration: int,
        hypothesis: str,
        error_reason: str,
    ) -> None:
        """Write a minimal failure record so the loop can continue."""
        record = {
            "iteration":     iteration,
            "hypothesis":    hypothesis,
            "code_path":     "",
            "train_metrics": {"primary": float("nan")},
            "valid_metrics": {"primary": float("nan")},
            "best_params":   {},
            "conclusion":    f"FAILED: {error_reason}",
            "notes":         error_reason,
        }
        try:
            self._memory.write_iteration(signal_name, iteration, record)
        except Exception as e:
            logger.warning("write_failed_iteration: could not write: %s", e)

    @staticmethod
    def _derive_conclusion(
        valid_metrics: Dict,
        prev_best: Optional[Dict],
    ) -> str:
        """Auto-generate a one-line conclusion comparing to previous best."""
        score = valid_metrics.get("primary", float("nan"))
        if _is_nan(score):
            return "评估失败，信号可能存在逻辑错误"
        if prev_best is None:
            return f"首次迭代，Valid T+5 Sharpe={score:.4f}"
        prev_score = prev_best.get("valid_metrics", {}).get("primary", float("nan"))
        if _is_nan(prev_score):
            return f"基准Sharpe未知，当前={score:.4f}"
        delta = score - float(prev_score)
        direction = "提升" if delta >= 0 else "下降"
        return f"相比最佳{direction} {abs(delta):.4f}，当前 Sharpe={score:.4f}"

    # ── Memory pressure ───────────────────────────────────────────────────────

    def _wait_memory(self) -> None:
        """Block until free RAM >= pause_mem_gb."""
        while _available_mem_gb() < self.pause_mem_gb:
            logger.info(
                "Memory guard: %.2f GB free < %.1f GB — GC + waiting 10 s",
                _available_mem_gb(), self.pause_mem_gb,
            )
            gc.collect()
            time.sleep(10)

    @staticmethod
    def _extra_pythonpath() -> List[str]:
        """Return extra PYTHONPATH entries for the sandbox subprocess."""
        return [
            str(_REPO_ROOT),
            str(_REPO_ROOT / "signals" / "singal_cal"),
        ]

    def __repr__(self) -> str:
        return (
            f"<Orchestrator model={self.model!r} "
            f"max_iter={self.max_iterations} optuna_trials={self.optuna_trials}>"
        )
