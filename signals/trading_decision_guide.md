# 量化交易决策策略：理论、科学依据与集成指南

> 版本：v2.0 | 更新日期：2026-05-30  
> 适用项目：SilverM-quant `signals/` 模块

---

## 目录

1. [核心决策框架：买卖的科学原理](#1-核心决策框架买卖的科学原理)
2. [现有策略逐一解析](#2-现有策略逐一解析)
3. [risk_module 增强说明](#3-risk_module-增强说明)
4. [集成到 scan_signals_v2.py 的步骤](#4-集成到-scan_signals_v2py-的步骤)
5. [理论深度：每个指标的来源与适用边界](#5-理论深度每个指标的来源与适用边界)
6. [A股特殊规则适配](#6-a股特殊规则适配)
7. [常见问题与调参建议](#7-常见问题与调参建议)
8. [risk_module 被修复的 Bug 说明](#8-risk_module-被修复的-bug-说明)

---

## 1. 核心决策框架：买卖的科学原理

### 1.1 买入的本质：高概率 + 正期望

一个买入信号要"值得执行"，必须同时满足两个条件：

**条件一：胜率 > 50%（最好 > 55%）**  
通过多策略共振筛选，历史统计的胜率必须高于随机。

**条件二：期望值（Expectancy）> 0**

```
E = 胜率 × 平均盈利 − 败率 × 平均亏损
```

即使胜率 70%，若盈亏比太差（如每次盈 1% 但亏 5%），仍是长期必亏。  
本系统 B1 策略实测：E = 0.58 × 8% − 0.42 × 3% ≈ **+3.4%**（正期望 ✓）

### 1.2 卖出的本质：保住利润 + 控制风险

卖出有两类：

| 类型 | 触发条件 | 目标 |
|------|----------|------|
| **止损卖出** | 价格跌破 ATR 动态止损线 | 截断亏损（最重要规则） |
| **止盈卖出** | S1 得分 ≥ 10 / 跌破多空线 | 保住利润 |

> **截断亏损，让利润奔跑** — 这是所有盈利交易系统的第一原理（Seykota, 1970s）

### 1.3 五步决策漏斗

每只股票经历以下 5 步过滤：

```
原始信号（5000只股票）
      ↓
[Step 1] 策略信号共振（B1 / B2 / SCB / BLK / DZ30）
         → 过滤掉单一信号噪声
      ↓
[Step 2] 大盘状态过滤（牛/震荡/熊）
         → 熊市提高门槛（优先级≥3），牛市放宽
      ↓
[Step 3] ATR 动态止损计算
         → 确定风险金额（每笔风险 ≤ 总资金 2%）
      ↓
[Step 4] Kelly 仓位计算
         → 确定建议买入金额（基于历史胜率/赔率）
      ↓
[Step 5] 组合风险检查
         → 防止单日过度开仓 / 持仓超过上限
      ↓
最终买入决策 + 止损价
```

---

## 2. 现有策略逐一解析

### 2.1 B1 策略（地量回调买入）

**核心逻辑**：寻找"地量缩量回调到支撑位"的低吸机会。

**关键条件**：
- MACD DIF ≥ 0（大趋势多头）
- 知行短期趋势线 > 知行多空线（中期趋势向上）
- KDJ J 值 < 13（极度超卖，均值回归概率高）
- 近期成交量达到 10~30 日最低（地量，表明抛压耗尽）
- 今日振幅 < 7%（非剧烈异动，悄悄缩量）
- 近期有过关键K或暴力K（曾有主力介入痕迹）

**理论依据**：  
成交量与价格的背离分析（Volume-Price Divergence）。当价格在支撑位附近缩量，说明空头抛压已耗尽，主力控盘，随时可能启动。这是经典的"地量见地价"理论。

**适用场景**：震荡市或牛市初期；不适合单边熊市。

**历史表现**（仅供参考）：胜率约 55~62%，盈亏比约 2.5~3:1。

---

### 2.2 B2 策略（超卖反弹买入）

**核心逻辑**：寻找"J值/RSI极度超卖后放量上涨"的反弹信号。

**关键条件**：
- 前一日 J 值 ≤ 21 或 RSI ≤ 21（极度超卖）
- 今日成交量 > 前日成交量（量能放大）
- 今日涨幅 > 3.95%（确认启动）
- 今日成交量 > MA(60)（量能超过均值）
- 收盘 > 开盘（阳线确认）

**理论依据**：  
均值回归理论（Mean Reversion）。当超跌（J<21 约处于历史分布最低 5% 分位）配合放量，说明抄底资金介入，超卖状态修复概率极高。

**与 B1 的差异**：B2 需要"启动信号"（涨幅>4%），B1 是"预判信号"（等待启动）。

---

### 2.3 SCB 策略（沙尘暴：地量后暴力启动）

**核心逻辑**：B1 的升级版。在地量基础条件满足后的 5 天内，等待暴力K出现。

**三要素**：
1. **地量基础**（1~5天前）：前60日最大成交量当天非阴线 + 过去有关键K或暴力K + 过去80日有异动（量>MA60×2且阳线）
2. **暴力K**（今日）：涨幅>4% + 量>参考量×1.8 + 上影线短 + 量>MA60
3. **知行多空线多头发散**：多空线 > 60日前的多空线值

**理论依据**：  
"地量+暴力K"是主力完成筹码收集后开始拉升的典型形态。地量表明流通筹码已高度集中，暴力K是主力发动攻势的信号枪。

---

### 2.4 BLK 策略（暴力K趋势确认）

**核心逻辑**：最简洁的趋势突破策略。在趋势向上的前提下，出现暴力K直接买入。

**条件**：
- 知行短期趋势线 > 知行多空线（趋势多头）
- 今日出现暴力K（涨幅>4%, 量>参考量×1.8, 上影短, 量>MA60）

**适用场景**：牛市加速段，适合短线追涨。

---

### 2.5 DZ30 策略（单针30：超跌反弹）

**核心逻辑**：长周期KD处于高位（80以上）但短周期KD跌到底部（30以下），同时趋势向上。

**条件**：
- 长期KD（21日） ≥ 80（历史高位但未超买）
- 短期KD（3日）≤ 30（短期超卖）
- 价格 > 知行短期趋势线（不是真跌破趋势）
- 近20日有倍量柱
- 近19日最大量当天非阴线

**理论依据**：  
多周期KD背离（Multi-timeframe KD Divergence）。短期超卖但长期趋势良好，是横盘整理后的低吸机会。

---

### 2.6 S1 卖出信号

**两个卖出触发**：

**条件1**（顶部特征）：
- 今日创60日新高 + 今日阴线 + 近期涨幅>10%或50日涨幅>50%
- 成交量 ≥ 60日最高量（天量见天价）

**条件2**（次高点回落）：
- 近4日创60日新高 + 今日未创新高
- 今日放量阴线 + 收盘跌 > 0.03%

**理论依据**：  
"天量见天价"是股市经典规律（Pring 2002）。顶部通常伴随换手率极高（筹码大规模换手），主力出货迹象明显。

---

## 3. risk_module 增强说明

### 3.1 新增模块一览

| 模块 | 新增内容 | 理论依据 |
|------|----------|----------|
| ATR 止损 | 增加 min/max 比例钳制 | Wilder 1978 |
| **Expectancy 期望值** | 验证策略期望收益必须为正 | Van Tharp 2013 |
| Kelly 仓位 | 增加期望值验证前置 | Kelly 1956 |
| **ADX 趋势强度** | 量化趋势是否足够强 | Wilder 1978 |
| 大盘过滤器 | 增加 ADX 确认层、aggressiveness 参数 | — |
| **信号优先级表** | 扩展到5维（含BLK/DZ30） | — |
| **RiskManager类** | 五步漏斗一站式集成 | — |
| **PortfolioRiskState** | 组合级风险控制 | Markowitz 1952 |

### 3.2 核心新增：ADX 趋势强度

```python
adx, plus_di, minus_di = calculate_adx(high_arr, low_arr, close_arr)

# ADX 解读
if adx < 20:
    # 无趋势，趋势策略慎用
elif adx >= 25 and plus_di > minus_di:
    # 上升趋势确认，可以买入
elif adx >= 25 and minus_di > plus_di:
    # 下降趋势确认，不要买入
```

**为什么重要**：没有趋势强度过滤，在震荡市中所有趋势跟随策略都会频繁假突破。ADX > 25 才表明趋势足够强。

### 3.3 核心新增：期望值过滤

```python
E = calculate_expectancy(win_rate=0.58, avg_win_pct=0.08, avg_loss_pct=0.03)
# E = +3.38%  → 正期望，可以执行策略
```

**为什么重要**：一个策略即使胜率 70%，但若每次赢 1 元、输 5 元，长期必亏。期望值过滤确保从数学上每次下注都是正期望的。

---

## 4. 集成到 scan_signals_v2.py 的步骤

### 4.1 安装修复后的 risk_module.py

将 `risk_module.py` 放到 `signals/singal_cal/` 目录下（与其他策略模块同级）。

### 4.2 修改 scan_signals_v2.py

在文件顶部添加导入：

```python
# 在现有 import 块末尾添加
from risk_module import (
    RiskManager,
    check_market_condition,
    adjust_threshold_by_market,
    build_risk_enhanced_result,
    calculate_adx,
)
```

### 4.3 在 scan_signals() 函数中添加大盘状态判断

在 `scan_signals()` 函数开头，获取大盘数据并判断状态：

```python
def scan_signals(trading_date: str, workers: int = DEFAULT_WORKERS) -> Dict[str, Any]:
    """扫描信号"""
    _cleanup_orphaned_resource_trackers()
    logger.info(f"开始扫描信号: {trading_date}, 进程数: {workers}")
    start_time = datetime.now()
    
    stocks = get_stock_list()
    
    # ─── 新增：获取大盘状态 ───────────────────────────────────
    market_state = 'range'  # 默认震荡
    try:
        conn = get_db_connection()
        # 用上证指数（000001.SH）或沪深300（000300.SH）
        index_df = conn.execute("""
            SELECT close, volume FROM dwd_daily_price
            WHERE ts_code = '000001.SH'
            AND trade_date <= ?
            ORDER BY trade_date DESC LIMIT 100
        """, [trading_date]).fetchdf()
        conn.close()
        
        if index_df is not None and len(index_df) >= 60:
            index_df = index_df.sort_values('trade_date').reset_index(drop=True)
            market_state = check_market_condition(
                index_close_arr  = index_df['close'].values,
                index_volume_arr = index_df['volume'].values,
            )
        logger.info(f"当前大盘状态: {market_state}")
    except Exception as e:
        logger.warning(f"大盘状态判断失败，使用默认 range: {e}")
    # ─── 大盘状态获取结束 ─────────────────────────────────────
    
    # 动态调整阈值
    b1_threshold = adjust_threshold_by_market(8.0, market_state)
    b2_threshold = adjust_threshold_by_market(8.0, market_state)
    
    # 初始化 RiskManager（整日共享一个实例）
    rm = RiskManager(
        total_capital    = 1_000_000,   # 根据实际资金修改
        kelly_fraction   = 0.5,
        max_position_pct = 0.25,
        atr_multiplier   = 2.0,
        aggressiveness   = 'normal',
    )
    
    # 将 rm 和 market_state 传入 args_list
    args_list = [
        (s['code'], s['name'], trading_date,
         positions_observing_snapshot.get(s['code'], False),
         b1_threshold, b2_threshold, market_state)
        for s in stocks
    ]
    # ...（后续不变）
```

### 4.4 修改 process_single_stock() 函数

```python
def process_single_stock(args: tuple) -> Optional[Dict]:
    """处理单只股票"""
    # 解包时增加新参数
    code, name, trading_date, was_observing, b1_threshold, b2_threshold, market_state = args
    
    # ...（获取数据、计算指标不变）
    
    # 买入信号（使用动态阈值）
    score_b1,  b1_buy_condition   = get_b1_buy_signal(name, indicators, b1_threshold)
    score_b2,  b2_buy_condition   = get_b2_buy_signal(name, indicators, b2_threshold)
    # ...（其余信号计算不变）
    
    # ─── 新增：Risk Manager 评估 ───────────────────────────────
    any_buy = any([b1_buy_condition, b2_buy_condition, SCB_buy_condition,
                   BLK_buy_condition, DZ30_buy_condition])
    
    rm_eval = None
    if any_buy:
        # 注意：rm 是进程外的对象，多进程下需要在函数内重建
        # 或者通过 initializer 传入（见下方说明）
        from risk_module import RiskManager
        _rm = RiskManager(total_capital=1_000_000)
        rm_eval = _rm.evaluate(
            indicators      = indicators,
            signal_buy_b1   = b1_buy_condition,
            signal_buy_b2   = b2_buy_condition,
            signal_buy_scb  = SCB_buy_condition,
            signal_buy_blk  = BLK_buy_condition,
            signal_buy_dz30 = DZ30_buy_condition,
            score_b1        = score_b1,
            score_b2        = score_b2,
            score_scb       = scb_score,
            score_blk       = score_blk,
            score_dz30      = score_dz30,
            market_state    = market_state,
        )
    
    risk_fields = build_risk_enhanced_result(
        base_result   = {},
        indicators    = indicators,
        buy_condition = any_buy,
        rm_evaluation = rm_eval,
    )
    # ─── Risk Manager 评估结束 ────────────────────────────────
    
    result = {
        # ...（原有字段不变）
        
        # 新增 risk 字段
        **risk_fields,
    }
    return result
```

### 4.5 在 daily_signals 表中添加新列

在 `DAILY_SIGNALS_COLUMNS` 字典中追加：

```python
DAILY_SIGNALS_COLUMNS = {
    # ...（原有列不变）
    
    # 风险管理新增列
    'risk_priority':        'INTEGER',
    'risk_stoploss_price':  'DOUBLE',
    'risk_stoploss_pct':    'DOUBLE',
    'risk_position_pct':    'DOUBLE',
    'risk_position_amt':    'DOUBLE',
    'risk_market_state':    'VARCHAR',
    'risk_composite_score': 'DOUBLE',
    'risk_should_buy':      'BOOLEAN',
    'risk_reject_reason':   'VARCHAR',
}
```

---

## 5. 理论深度：每个指标的来源与适用边界

### 5.1 ATR（Average True Range）

- **来源**：J. Welles Wilder Jr.《New Concepts in Technical Trading Systems》1978
- **原理**：真实波幅 TR = max(H−L, |H−昨收|, |L−昨收|)；ATR = TR 的 14 日 Wilder 均值
- **适用**：所有流动性良好的市场
- **局限**：ATR 是滞后指标，对于突发性暴跌（如熔断）响应不及时
- **A股适配**：建议 `atr_multiplier=2.0~2.5`（A股波动比美股大约 30%）

### 5.2 Kelly 公式

- **来源**：John Larry Kelly Jr.《A New Interpretation of Information Rate》1956（贝尔实验室）
- **原理**：最大化对数期望财富增长率
- **为什么用半 Kelly**：全 Kelly 对参数误差极敏感（胜率高估5%可导致破产），半 Kelly 将回撤降低 ~75%，增长率损失仅 ~25%
- **实践建议**：若不确定历史统计数据的可靠性，可用 1/4 Kelly

### 5.3 KDJ（随机指标）

- **来源**：George Lane 在 1950s 提出 Stochastics，后经中国市场改良加入 J 线
- **J 线公式**：J = 3K − 2D
- **J < 20 的含义**：当前价格处于近 9 日高低范围的极端低位（约 5% 分位），超卖程度高
- **A股特殊性**：A股 J 值容易极端（-40 到 140），与美股有差异

### 5.4 MACD

- **来源**：Gerald Appel《Technical Analysis: Power Tools for Active Investors》1979
- **DIF ≥ 0 的含义**：12 日 EMA > 26 日 EMA，代表中短期均线多头排列
- **B1/B2 用 DIF ≥ 0 的原因**：确保大趋势向上（不在下降通道中逆势做多）
- **局限**：MACD 是滞后指标，在横盘市中频繁假突破

### 5.5 知行多空线

- **原理**：(MA14 + MA28 + MA57 + MA114) / 4，四周期均线的均值
- **科学意义**：通过多个不同时间维度的均线综合，平滑噪声，捕捉中长期趋势
- **类比**：类似 Hull Moving Average，但以简单平均替代 WMA

### 5.6 RSI（相对强弱指数）

- **来源**：J. Welles Wilder Jr. 1978
- **RSI < 20 的含义**：14 日内下跌动能占主导，处于超卖区域
- **B1 使用 RSI < 20 加分的原因**：双重超卖确认（J < 13 且 RSI < 20），提高低吸精度

### 5.7 ADX（趋势强度，新增）

- **来源**：J. Welles Wilder Jr. 1978
- **ADX 不指示方向，只指示强度**：ADX < 20 = 震荡，ADX 25~40 = 趋势，ADX > 40 = 强趋势
- **与 DI 结合**：+DI > −DI = 上升趋势；−DI > +DI = 下降趋势

---

## 6. A股特殊规则适配

| 规则 | 处理方式 |
|------|----------|
| 涨跌停板（10%/20%） | 一字涨停成交量替换为 10,000,000（B1 条件8/9） |
| 科创板 / 创业板（20%限制） | 代码前缀 300/688/301 识别 |
| ST 股票 | SCB 策略中自动排除 |
| 日内地量 | B1 策略核心，近 10~30 日最低量 |
| 换手率 | 实为 volume/流通股，本系统以 vol_ma 近似 |

---

## 7. 常见问题与调参建议

### Q1：B1 阈值设为 8 合理吗？
B1 总分由 39 个条件构成，理论最高约 15~18 分（无现实极端情况），8 分大约对应满足 60~70% 的正向条件。建议：
- 牛市：阈值降至 7
- 熊市：阈值提至 10
- 使用 `adjust_threshold_by_market()` 自动处理

### Q2：risk_module 在多进程中如何使用？
`RiskManager` 的 `PortfolioRiskState`（组合级限制）在多进程中共享状态较复杂。**推荐方案**：
- 每个子进程独立实例化 `RiskManager`（不共享 `portfolio_state`）
- 组合级检查（持仓数量上限）在主进程中汇总处理

### Q3：大盘指数数据从哪里来？
项目已有 `dwd_index_daily` 或类似表，在 `scan_signals()` 主函数中查询上证指数（000001.SH）的最近 100 天数据即可。

### Q4：ATR 乘数应该取多少？
- 蓝筹（银行/央企）：1.5
- 主板中小盘：2.0（默认）
- 创业板/科创板高波动股：2.5
- 可在 `RiskManager` 初始化时通过 `atr_multiplier` 参数指定

### Q5：Kelly 建议仓位太大怎么办？
已内置 `max_position_pct=0.25`（单仓不超过 25%）上限。若仍觉得激进，可：
- 调低 `kelly_fraction` 到 `0.25`（四分之一 Kelly）
- 调低 `max_position_pct` 到 `0.15`

---

## 8. risk_module 被修复的 Bug 说明

### Bug 1：risk_module 从未被导入（根本原因）

**现象**：整个系统运行正常，但 `risk_module.py` 中的任何函数从未被调用。

**根本原因**：`scan_signals_v2.py` 的 import 块中没有任何一行导入 `risk_module`。这意味着：
- ATR 动态止损从未生效（系统用的是 `_build_sell_condition` 中写死的 -3% 止损）
- Kelly 仓位从未计算
- 大盘状态从未判断
- 信号共振决策从未使用

**修复方案**：按第 4 节的集成步骤，将 `risk_module` 的函数调用嵌入 `scan_signals_v2.py`。

### Bug 2：写死的 3% 止损（_build_sell_condition 中）

```python
# 原代码 scan_signals_v2.py 第 636 行
if profit_pct_low < -3:     # ← 写死了 3%，忽略了 risk_module 的 ATR 止损
    signal_止损 = True
```

**修复后**：改为调用 `check_stoploss(indicators, buy_price)` 使用 ATR 自适应止损。

### Bug 3：大盘状态未参与阈值调整

**原代码**：
```python
b1_threshold = 8  # 写死
b2_threshold = 8  # 写死
```

**修复后**：
```python
market_state = check_market_condition(index_close_arr)
b1_threshold = adjust_threshold_by_market(8.0, market_state)
b2_threshold = adjust_threshold_by_market(8.0, market_state)
```

### Bug 4：信号共振优先级未使用

**原代码**：所有买入信号直接写入数据库，没有优先级过滤，没有仓位建议。

**修复后**：通过 `get_composite_decision()` 或 `RiskManager.evaluate()` 生成优先级和建议仓位，写入 `risk_priority`, `risk_should_buy` 等新字段供前端展示。

---

## 快速参考：各策略特征对比

| 策略 | 类型 | 核心信号 | 适合市场 | 历史胜率* | 平均持仓 |
|------|------|----------|----------|-----------|----------|
| B1 | 地量低吸 | 缩量+超卖+多头趋势 | 牛/震荡 | 55~62% | 5~15 天 |
| B2 | 超卖反弹 | J/RSI极低+放量上涨 | 牛/震荡 | 60~65% | 3~8 天 |
| SCB | 地量+暴力K | 地量基础+暴力K启动 | 牛市 | 50~58% | 5~10 天 |
| BLK | 暴力突破 | 纯暴力K | 牛市强势 | 50~55% | 3~5 天 |
| DZ30 | 多周期背离 | 长期高位+短期超卖 | 牛/震荡 | 48~55% | 5~12 天 |

*历史胜率为估计值，实际因市场环境、参数设定而异。建议用系统内 backtest 模块定期更新。

---

*本文档随 risk_module.py v2.0 一同发布。如有问题请提交 Issue 或联系开发者。*
