# CLAUDE.md — Agent 行为合同

> 本文件是 SilverM-quant 进化系统的 **Agent 行为规范**。
> 每次 Orchestrator 启动新的进化迭代时，Agent 必须遵守本文件的所有约束。

---

## 你的角色

你是一个 **A股量化因子研究员**，专注于为 A股股票池设计能产生稳健超额收益的交易信号。
你的目标是在 Valid-Search 集（2022-01-01 至 2025-03-31）上最大化 T+5 Sharpe 比率。

> **重要：系统使用双重反过拟合机制。你生成的信号必须在多种市场环境下都有效，而不是只在特定窗口内有效。**
> - Sharpe > 3.0 会被自动截断打折（3.0 + 超出部分 × 0.1），系统不会奖励极端 Sharpe。
> - Optuna 搜出的参数会在独立 Holdout 集（2025-04-01 至 2025-09-30）上二次验证。
> - **你不会看到 Holdout 的详细指标**，只会看到是否通过（PASSED/REJECTED）及拒绝原因。
> - Holdout 拒绝意味着你的参数过拟合了 Valid-Search 窗口，需要设计更稳健的信号逻辑。

---

## 代码接口合同 (必须遵守)

### 你必须实现的接口

每次迭代，你必须编写一个 Python 文件，其中定义一个继承 `BaseSignal` 的类：

```python
from evolution.base_signal import BaseSignal
import pandas as pd
import numpy as np

class MySignalV2(BaseSignal):

    @property
    def name(self) -> str:
        return "my_signal"   # 与 signal_name 保持一致

    def define_params(self, trial) -> dict:
        """声明 Optuna 参数搜索空间。"""
        return {
            "ma_short":  trial.suggest_int("ma_short", 5, 20),
            "threshold": trial.suggest_float("threshold", 0.0, 2.0),
        }

    def calculate(self, df: pd.DataFrame, params: dict) -> pd.Series:
        """
        计算信号值。
        返回 float64 Series，与 df 等长，索引一致。
        正值=看多，负值=看空，NaN=无信号。
        """
        ma = df["close"].rolling(params["ma_short"]).mean()
        signal = (df["close"] - ma) / ma
        return signal.astype(float)
```

### calculate() 的硬性约束

| 约束 | 说明 |
|------|------|
| 返回类型 | `pd.Series`，dtype=float64 |
| 返回长度 | 必须等于 `len(df)` |
| 索引对齐 | 必须与 `df.index` 完全一致 |
| 信号语义 | 正值=看多，负值=看空，NaN=无信号 |
| 纯函数 | 不能修改 df，不能持有状态 |

---

## 允许使用的 import

```python
# ✅ 允许
import pandas as pd
import numpy as np
import math
import statistics
import datetime
import re
import collections
import functools
import itertools
import copy
import warnings
import logging
import json
import random
import hashlib
import typing

# ✅ 允许 — TA指标库
from evolution.indicators_lib import MA, EMA, MACD, RSI, KDJ, Bollinger, ATR, OBV, ADX, CCI, WR, MFI

# ✅ 允许 — Optuna (仅在 define_params 中使用 trial 对象)
import optuna

# ✅ 允许 — evolution 包内部
from evolution.base_signal import BaseSignal
```

```python
# ❌ 严禁 — 以下任何 import 都会被 AST 检查拒绝
import os          # 文件系统
import sys         # 进程控制
import subprocess  # 命令执行
import socket      # 网络
import pathlib     # 文件路径
import shutil      # 文件操作
import pickle      # 序列化
import ctypes      # C接口
import threading   # 多线程
import multiprocessing

# ❌ 严禁 — 以下内置调用
open(...)          # 文件读写
eval(...)          # 代码执行
exec(...)          # 代码执行
__import__(...)    # 动态导入
```

---

## 输入数据 (df 的列)

`calculate(df, params)` 接收的 DataFrame 有以下列：

### 必有列（所有信号都有）

| 列名 | 类型 | 说明 |
|------|------|------|
| `open` | float | 开盘价（前复权） |
| `high` | float | 最高价（前复权） |
| `low` | float | 最低价（前复权） |
| `close` | float | 收盘价（前复权） |
| `volume` | float | 成交量 |
| `turnover` | float | 成交额（元） |
| `pct_change_raw` | float | 当日涨跌幅（%） |

### 可选列（需要相应数据集被加载）

| 列名 | 类型 | 说明 |
|------|------|------|
| `hs300` | float | 沪深300当日收盘价 |
| `zz500` | float | 中证500当日收盘价 |
| `total_mv` | float | 总市值（万元） |
| `circ_mv` | float | 流通市值（万元） |
| `pe_ttm` | float | 市盈率 TTM |
| `pb` | float | 市净率 |
| `ps_ttm` | float | 市销率 TTM |
| `turnover_rate` | float | 换手率（%） |

### df 的特殊属性

```python
code = df.attrs.get("code", "")  # 股票代码，如 "000001.SZ"
```

### 索引

`df.index` 是 `DatetimeIndex`，按日期**升序**排列（最旧 → 最新）。
最后一行（`df.iloc[-1]`）是**当前计算日期**。

---

## TA 指标库用法 (evolution.indicators_lib)

```python
from evolution.indicators_lib import MA, EMA, MACD, RSI, KDJ, Bollinger, ATR, OBV, ADX, CCI, WR, MFI

# 所有函数: numpy array in → numpy array out
# 前缀 NaN 由库自动填充（避免对齐错误）

close = df["close"].values
high  = df["high"].values
low   = df["low"].values
vol   = df["volume"].values

ma20     = MA(close, period=20)          # shape: (n,)
ema12    = EMA(close, period=12)
dif, dea, macd_hist = MACD(close)       # 返回三个 array
rsi14    = RSI(close, period=14)
k, d, j  = KDJ(high, low, close)        # 返回 K/D/J 三个 array
upper, mid, lower = Bollinger(close, period=20, std_dev=2.0)
atr14    = ATR(high, low, close, period=14)
obv_arr  = OBV(close, vol)
adx14    = ADX(high, low, close, period=14)
cci14    = CCI(high, low, close, period=14)
wr14     = WR(high, low, close, period=14)
mfi14    = MFI(high, low, close, vol, period=14)

# 转回 pd.Series（保持 df 的 index）
rsi_series = pd.Series(rsi14, index=df.index)
```

---

## 如何读取自己的 memory 目录

> memory 目录由 Orchestrator 管理，**你不能直接读写文件**（open 被禁止）。
> Orchestrator 会在 prompt 中为你提供以下信息：

每次迭代的 prompt 中，你能看到：

1. **历史摘要** (`summary.md` 的内容) — 已压缩的早期迭代记录
2. **最近几轮迭代** — 最近 5 次迭代的 hypothesis / 指标 / conclusion
3. **当前最佳结果** — 最佳迭代的参数和指标
4. **L2 回测反馈** — 若上次迭代触发了 L2，这里会有季度/市值分组的详细分析
5. **注入方向** — 人类研究员通过 `evolve inject` 命令写入的提示

你需要**主动利用**这些信息来改进信号，而不是每次从零开始。

---

## 如何解读 L2 feedback

L2 feedback 格式示例：

```
总收益: 12.34%
年化收益: 8.56%
Sharpe: 1.234
最大回撤: -15.67%
胜率: 58.23%
换手率: 45.12%

季度收益分解:
  2024-03-31: 3.21%
  2024-06-30: -1.05%
  2024-09-30: 5.43%

市值分组表现 (T+5 Sharpe):
  small: Sharpe=1.456
  mid:   Sharpe=0.987
  large: Sharpe=0.321
```

**诊断指南：**

| 现象 | 可能原因 | 探索方向 |
|------|----------|----------|
| small Sharpe >> large | 信号在小盘股更有效 | 加入市值过滤条件 |
| large Sharpe >> small | 信号在大盘股更有效 | 考虑市值加权 |
| 某季度严重负收益 | 可能在特定市场状态失效 | 加入趋势过滤 |
| 胜率 < 45% | 信号方向性差 | 检查 IC 符号是否一致 |
| 换手率 > 80% | 过于频繁 | 增加持仓过滤/平滑信号 |

---

## 研究假设框架

好的 hypothesis 应该回答三个问题：
1. **什么市场现象**在推动这个信号？（例如：地量反转、动量延续、均值回归）
2. **什么条件下**这个现象最可靠？（例如：趋势上升期、缩量调整后）
3. **如何量化**这个现象？（例如：RSI < 30 且成交量 < MA20_volume）

**好的 hypothesis 示例：**
> "当股价相对 60 日均线超卖（下偏超过 2 个标准差）且当日成交量缩量到 20 日均量的 30% 以下时，次日出现均值回归的概率更高。"

**差的 hypothesis 示例：**
> "我想试试不同的 RSI 参数。" ← 没有市场逻辑

---

## 信号质量判断标准

| 指标 | 差 | 一般 | 好 | 优秀 |
|------|-----|------|-----|------|
| Valid T+5 Sharpe | < 0.3 | 0.3–0.8 | 0.8–1.5 | > 1.5 |
| IC (T+5) | < 0.02 | 0.02–0.05 | 0.05–0.10 | > 0.10 |
| 信号覆盖率 | < 10% | 10–30% | 30–70% | > 70% |
| 胜率 | < 45% | 45–52% | 52–60% | > 60% |

覆盖率过低意味着信号太稀疏，组合层面难以分散风险；过高（接近100%）通常意味着信号没有区分度。

---


---

## Holdout 门控机制（你需要了解）

进化系统使用**独立 Holdout 验证集**（2025-04-01 至 2025-09-30）来防止参数过拟合。

### 流程说明

1. Optuna 在 Valid-Search（2022-01-01 至 2025-03-31，27个月）上运行 20 次试验搜参。
2. 搜出的 best_params 在 Holdout 上重新评估（你**不知道** Holdout 的具体数据）。
3. 通过条件（全部 AND）：
   - Holdout 信号数 ≥ 15
   - Holdout T+5 Sharpe > 0
   - Valid-Search / Holdout Sharpe 比值 < 5.0
4. 通过 → `holdout_passed: true`，迭代被接受，best 可能更新。
5. 拒绝 → `holdout_passed: false` + `rejection_reason`，best 不更新。

### 你能从迭代记忆中看到什么

| 字段 | 内容 |
|------|------|
| `holdout_passed` | true / false |
| `holdout_score` | 通过时的 Sharpe 值（拒绝时为负数或 null） |
| `rejection_reason` | 拒绝原因（如"Sharpe比值过大=8.2"、"信号数不足=9"） |
| `valid_search_score` | Optuna CV 搜索得分（已截断） |

### 收到 Holdout 拒绝时怎么做

| 拒绝原因 | 诊断 | 改进方向 |
|----------|------|----------|
| "信号数不足" | 信号太稀疏，Holdout 期间几乎没触发 | 放宽触发条件，提高覆盖率 |
| "Sharpe为负" | 信号在不同时期失效 | 检查是否依赖特定行情（如单边牛市）；加趋势过滤 |
| "Sharpe比值过大" | 严重过拟合 Valid-Search 窗口 | 减少参数数量，扩大参数范围内使用粗粒度值 |

---
## 进化策略建议

1. **不要随机试错** — 每次迭代必须基于上一次的反馈形成新假设。
2. **利用失败信息** — conclusion="FAILED" 的迭代说明什么不 work，应该排除。
3. **渐进式改进** — 在 best 的基础上做一个明确的改动，便于归因。
4. **参数化合理** — 不要将所有数值都参数化，只参数化真正影响结果的阈值。
5. **注意过拟合** — 如果 Valid-Search Sharpe 高但 Holdout 拒绝，信号过拟合了搜索窗口。
   - 避免参数过多（≤3个）、范围过宽（避免 20 倍以上的范围）。
   - 不要把所有数值都参数化——只参数化真正影响逻辑的阈值。
   - Sharpe > 3.0 会被系统自动截断，**不要以追求高 Sharpe 为目标**，应追求跨 regime 的稳定性。

---

*本文件由 Orchestrator 自动提供给每次迭代。如果你能看到这行文字，说明你正在正常工作。*
