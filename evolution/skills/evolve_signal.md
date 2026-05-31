# evolve_signal.md — Agent Research & Coding Skill Guide

> 本文件是 SilverM-quant 进化系统的 **Agent 研究方法论手册**。
> 与 `CLAUDE.md`（接口合同）配合使用：`CLAUDE.md` 告诉你**能做什么**，本文件告诉你**怎么做好**。

---

## 1. 研究假设框架（Hypothesis Framework）

### 1.1 好的假设的三要素

| 要素 | 说明 | 示例 |
|------|------|------|
| **现象** | 描述观察到的市场规律 | "地量之后往往出现反弹" |
| **条件** | 明确该现象成立的前提 | "在成交量缩至20日均量30%以下时" |
| **量化** | 精确描述如何度量 | "用 OBV 偏离度 + RSI 阈值联合过滤" |

### 1.2 假设模板

```
当 [市场条件/技术形态] 发生，
且 [过滤条件] 成立时，
[T+N天后] 股价倾向于 [上涨/下跌]，
原因是 [市场微观结构 / 行为金融学解释]。
```

**示例：**
> 当股价在过去20日创新低（地量形成），且当日成交量缩至20日均量的30%以下时，
> T+5日后股价倾向于均值回归上涨，原因是卖压枯竭后浮筹减少，买方力量相对增强。

### 1.3 假设质量自检

在提交代码前，对自己的假设问以下问题：

- [ ] 这个假设有没有对应的市场微观结构解释？
- [ ] 在什么市场环境下（牛市/熊市/震荡）这个假设可能失效？
- [ ] 如何用一个简单的 if 语句描述这个信号的触发条件？
- [ ] 这个信号是看多还是看空，还是双向的？

---

## 2. 信号设计方法论

### 2.1 信号的分类

| 类型 | 特征 | 代表指标 | 适用场景 |
|------|------|----------|----------|
| **动量** | 趋势延续 | EMA, MACD, ADX | 趋势市 |
| **反转** | 超买超卖后均值回归 | RSI, KDJ, Williams%R | 震荡市 |
| **量价** | 成交量确认价格行为 | OBV, MFI, 量比 | 放量突破/缩量调整 |
| **波动率** | 波动收缩后的突破 | Bollinger, ATR, 历史波动率 | 盘整→突破 |
| **相对强弱** | 个股与指数/行业的超额 | 相对收益, Beta调整 | 选股层面 |
| **估值** | PE/PB 偏离 | pe_ttm, pb | 长周期反转 |

### 2.2 信号组合的注意事项

**好的组合方式：**
```python
# ✅ 动量主信号 + 成交量确认 = 减少虚假信号
momentum = ema_diff / close  # 主信号方向
vol_confirm = (volume > ma_vol * 1.5).astype(float)  # 放量确认
signal = momentum * vol_confirm  # 无量时信号归零
```

**差的组合方式：**
```python
# ❌ 多个高度相关的指标简单相加 = 没有额外信息
signal = rsi_signal + kdj_signal + cci_signal  # 三者都是超买超卖，高度相关
```

### 2.3 信号平滑技巧

原始技术信号往往噪声大，换手率高。常用平滑方法：

```python
# 方法1：rolling mean（适合减少高频噪声）
raw_signal = ...  
smooth = pd.Series(raw_signal, index=df.index).rolling(3).mean().values

# 方法2：指数加权（对近期数据更敏感）
smooth = pd.Series(raw_signal, index=df.index).ewm(span=5).mean().values

# 方法3：只在信号变化时输出（减少不必要换手）
prev = np.roll(raw_signal, 1)
prev[0] = 0
changed = (np.sign(raw_signal) != np.sign(prev))
output = np.where(changed, raw_signal, 0.0)
```

---

## 3. 参数化策略

### 3.1 什么值得参数化

| 应该参数化 | 不应参数化 |
|-----------|-----------|
| 关键窗口期（如 RSI 的 14、20、30） | 数学常数（如 π、log(2)） |
| 阈值（如 RSI > 70 为超买） | 数据列名 |
| 权重系数（如多因子的权重） | 逻辑结构（if/else 的框架） |
| 衰减系数（如 EMA 的 span） | 已经被 L2 验证稳定的参数 |

### 3.2 参数空间设计原则

```python
def define_params(self, trial) -> dict:
    # ✅ 合理：参数范围覆盖可能有效的区间，不过于宽泛
    return {
        "rsi_period":    trial.suggest_int("rsi_period", 5, 30),      # 不要 2~200
        "rsi_threshold": trial.suggest_float("threshold", 20.0, 40.0), # 不要 0.0~100.0
        "smooth_window": trial.suggest_int("smooth_window", 1, 10),
    }
```

```python
def define_params(self, trial) -> dict:
    # ❌ 不合理：参数过多，Optuna 50次试验无法覆盖高维空间
    return {
        "p1": trial.suggest_int("p1", 2, 100),
        "p2": trial.suggest_int("p2", 2, 100),
        "p3": trial.suggest_float("p3", 0.0, 2.0),
        "p4": trial.suggest_float("p4", 0.0, 2.0),
        "p5": trial.suggest_categorical("p5", ["a", "b", "c", "d"]),
        # 5个参数 × 大空间 = Optuna 50次根本不够
    }
```

**建议：参数数量 ≤ 3个，每个参数范围控制在 20 倍以内。**

### 3.3 参数稳定性检验（在 calculate 中检验）

```python
def calculate(self, df, params):
    period = params["ma_period"]
    # ✅ 防御性编程：参数超出合理范围时降级处理
    period = max(5, min(period, len(df) // 2))
    ...
```

---

## 4. 从 Feedback 中学习

### 4.1 L1 指标解读速查

| 指标 | 含义 | 值 | 解读 |
|------|------|-----|------|
| `primary` | T+5 Sharpe | > 1.5 | 优秀 |
| `ic_t5` | T+5 排名相关系数 | > 0.05 | 有效预测力 |
| `win_rate_t5` | 方向准确率 | > 0.55 | 明显胜于随机 |
| `coverage` | 信号非空比例 | 0.3~0.7 | 过低则稀疏，过高则无区分度 |

### 4.2 常见问题及诊断

#### 问题A：Sharpe 低但 IC 尚可
**症状：** `sharpe_t5 = 0.4`，`ic_t5 = 0.04`  
**诊断：** 信号预测力存在，但波动太大（信号本身噪声多）  
**改进：** 对信号做平滑处理（rolling mean 或 EWM）

#### 问题B：Coverage 过低（< 10%）
**症状：** `coverage = 0.05`  
**诊断：** 触发条件太苛刻，绝大多数时间信号为 NaN  
**改进：** 放宽过滤条件，或将严格阈值改为连续得分

#### 问题C：Train >> Valid（过拟合）
**症状：** Train Sharpe = 2.1，Valid Sharpe = 0.2  
**诊断：** 信号在训练集数据上过拟合  
**改进：** 减少参数数量；用更粗的阈值；检查是否意外泄露了未来信息（look-ahead bias）

#### 问题D：某季度严重亏损
**症状：** L2 季度分解中，某个季度收益 = -8%  
**诊断：** 信号在某种市场状态下失效（如单边下跌市）  
**改进：** 加入趋势过滤，例如 HS300 近 20 日收益 < -5% 时不开仓

#### 问题E：小盘股 Sharpe >> 大盘股
**症状：** cap_group: small=1.8, large=0.2  
**诊断：** 信号在小盘股更有效（可能是流动性溢价效应）  
**改进：** 加入市值过滤（只选小盘股），或用市值倒数加权信号

### 4.3 迭代改进模板

```
上一次结果: sharpe=0.42, ic=0.031, 覆盖率=0.61
问题: Sharpe低，IC一般，但覆盖率正常
假设: 信号噪声太大，需要平滑

本次改进:
1. 在原始动量信号基础上增加5日EWM平滑
2. 加入成交量放量过滤（量比 > 0.8）
3. 缩小 RSI 阈值范围（从 20~80 缩到 25~70）

预期效果: Sharpe 提升至 0.8+, IC 提升至 0.05+
```

---

## 5. 常用信号模式（Code Templates）

### 5.1 动量信号

```python
def calculate(self, df, params):
    close = df["close"].values
    fast  = EMA(close, period=params["fast"])
    slow  = EMA(close, period=params["slow"])
    
    # 归一化动量 = (快线 - 慢线) / 慢线
    momentum = np.where(slow != 0, (fast - slow) / slow, 0.0)
    
    return pd.Series(momentum, index=df.index)
```

### 5.2 超买超卖反转信号

```python
def calculate(self, df, params):
    close = df["close"].values
    rsi   = RSI(close, period=params["rsi_period"])
    
    # 超卖区域为看多，超买区域为看空，中间区域为 NaN
    signal = np.where(rsi < params["oversold"],  1.0,
             np.where(rsi > params["overbought"], -1.0, np.nan))
    
    return pd.Series(signal, index=df.index)
```

### 5.3 量价背离信号

```python
def calculate(self, df, params):
    close  = df["close"].values
    volume = df["volume"].values
    
    obv    = OBV(close, volume)
    ma_obv = MA(obv, period=params["obv_period"])
    
    # OBV 相对于均线的偏离度
    obv_deviation = np.where(ma_obv != 0, (obv - ma_obv) / np.abs(ma_obv), 0.0)
    
    # 价格动量（短期）
    price_mom = np.where(close[:-1] != 0,
                         np.diff(close) / close[:-1], 0.0)
    price_mom = np.append([0.0], price_mom)
    
    # 量价共振：OBV 和价格方向一致时信号增强
    signal = obv_deviation * np.sign(price_mom + 1e-9)
    
    return pd.Series(signal, index=df.index)
```

### 5.4 布林带突破信号

```python
def calculate(self, df, params):
    close     = df["close"].values
    upper, mid, lower = Bollinger(close,
                                   period=params["bb_period"],
                                   std_dev=params["std_dev"])
    
    # 正规化得分：在通道内的相对位置（0=下轨，1=上轨）
    band_width = upper - lower
    position   = np.where(band_width > 0,
                          (close - lower) / band_width, 0.5)
    
    # 信号：超过上轨=强势，低于下轨=弱势，中间线性
    signal = position * 2 - 1.0  # 映射到 [-1, 1]
    
    return pd.Series(signal, index=df.index)
```

### 5.5 趋势过滤器（结合 df 中的 hs300 列）

```python
def calculate(self, df, params):
    # 主信号
    close  = df["close"].values
    rsi    = RSI(close, period=14)
    raw    = np.where(rsi < 35, 1.0, np.where(rsi > 65, -1.0, np.nan))
    
    # 趋势过滤：hs300 20日动量
    if "hs300" in df.columns:
        hs300      = df["hs300"].values
        hs300_ma   = MA(hs300, period=params["trend_window"])
        trend_up   = (hs300 > hs300_ma).astype(float)
        # 熊市中只做空，牛市中只做多
        raw = np.where(trend_up == 1, np.where(raw > 0, raw, 0.0),
                       np.where(raw < 0, raw, 0.0))
    
    return pd.Series(raw, index=df.index)
```

---

## 6. 常见陷阱（Pitfalls）

### 6.1 未来函数（Look-ahead Bias）

```python
# ❌ 错误：用了未来的收盘价来计算今天的信号
def calculate(self, df, params):
    max_close_future = df["close"].rolling(5, center=True).max()  # center=True 用了未来数据！
    signal = (max_close_future - df["close"]) / df["close"]
    return signal

# ✅ 正确：只用历史数据
def calculate(self, df, params):
    max_close_past = df["close"].rolling(5).max()  # 只用过去5天
    signal = (df["close"] - max_close_past.shift(1)) / df["close"]
    return signal
```

### 6.2 对齐问题

```python
# ❌ 错误：numpy 操作可能改变长度
def calculate(self, df, params):
    close = df["close"].values
    diff  = np.diff(close)  # 长度变为 n-1，返回时不等长！
    return pd.Series(diff, index=df.index)  # 长度不匹配，会抛出异常

# ✅ 正确：保持等长
def calculate(self, df, params):
    close = df["close"].values
    diff  = np.diff(close)
    diff  = np.append([np.nan], diff)  # 补 NaN 到第一位，恢复 n 长
    return pd.Series(diff, index=df.index)
```

### 6.3 信号缩放

```python
# ❌ 注意：信号数量级过大可能影响 Sharpe 计算的数值稳定性
signal = (close - ma) * 1000000  # 数量级太大

# ✅ 建议归一化
signal = (close - ma) / (ma + 1e-9)  # 相对偏离度，量纲无关
```

---


## 8. 反过拟合设计指南（Issue #2）

### 8.1 为什么之前会过拟合

实测数据：Valid-Search Sharpe=7.98，Holdout Sharpe=-1.26，Test Sharpe=-1.26。
**根因**：50 次 Optuna 试验在 7 维参数空间中搜到了恰好匹配特定行情的极端参数组合，
而不是具备真实 alpha 的信号逻辑。

### 8.2 系统已实施的防护（你需要配合）

| 机制 | 说明 |
|------|------|
| **Sharpe 截断** | Valid-Search CV Sharpe > 3.0 被截断：`3.0 + (x-3.0)*0.1`。不要以高 Sharpe 为追求目标。 |
| **Temporal CV** | valid_search 窗口切分为 4 折，参数必须在每折上都有效（跨牛/熊/震荡 regime）。 |
| **Holdout 门控** | 最终参数在独立 6 个月窗口验证，详见 CLAUDE.md 的门控机制章节。 |
| **减少试验数** | n_trials=20（原50），降低搜索自由度。 |
| **提高 min_signals** | 每折至少需要 80 个信号触发才被记为有效折。 |

### 8.3 你应该做的（信号设计层面）

**好的抗过拟合信号设计：**
```python
def define_params(self, trial) -> dict:
    # ✅ 参数少（≤3个），范围合理
    return {
        "rsi_period":    trial.suggest_int("rsi_period", 10, 25),      # 覆盖常见有效区间
        "threshold":     trial.suggest_float("threshold", 25.0, 35.0),  # 不要 0~100
    }
```

**会导致过拟合的设计：**
```python
def define_params(self, trial) -> dict:
    # ❌ 参数太多 + 范围太宽 = Optuna 找到的是噪声
    return {
        "vol_mult":      trial.suggest_float("vol_mult", 0.5, 5.0),     # 10倍范围
        "ma_short":      trial.suggest_int("ma_short", 2, 120),          # 60倍范围
        "ma_long":       trial.suggest_int("ma_long", 5, 250),
        "rsi_low":       trial.suggest_float("rsi_low", 5.0, 50.0),
        "rsi_high":      trial.suggest_float("rsi_high", 50.0, 95.0),
    }
```

### 8.4 当迭代被 Holdout 拒绝时

```
例：rejection_reason = "valid/holdout Sharpe比值=8.2 > 阈值5.0"
```

诊断步骤：
1. 检查 valid_search_score — 是否异常高（如 > 4.0）？如果是，信号逻辑可能记忆了训练集的特定模式。
2. 检查参数空间 — 是否某个参数的范围过宽，导致 Optuna 找到极端值？
3. 检查信号触发条件 — 是否过于复杂（多个严格 AND 条件），导致只在特定行情触发？

改进方向：
- 将严格阈值（如 rsi < 25 AND vol > 3x）改为连续得分（weighted sum）
- 减少参数数量（3个以内）
- 加入跨 regime 的稳健性检验（如 hs300 趋势过滤，使信号不依赖单一方向市场）

---
## 9. 输出格式规范

每次迭代，你的回复必须包含：

```
**HYPOTHESIS:** [一句话，描述你在验证什么市场假设]

**REASONING:** [2-3句话，解释为什么这个假设值得验证，参考上一轮的feedback]

**CHANGES:** [列出与上一版本相比的具体改动]

**CODE_FILE:** [完整的文件路径]
```

然后将代码写入指定文件。

---

*本文件由 Orchestrator 在每次进化迭代时提供给 Agent。*
*版本：1.0 | 维护：SilverM-quant*
