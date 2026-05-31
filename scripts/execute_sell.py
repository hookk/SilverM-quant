#!/usr/bin/env python
# coding=utf-8
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/execute_sell.py — 卖出执行引擎 v1.0

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计原则（华尔街量化交易规范）:

  1. 信号优先级分层（Signal Priority Ladder）
       L1  硬止损    — ATR动态止损 / 固定3%止损 触发最低价 → 立即全仓清出
       L2  S1满仓    — score_s1 >= 10，顶部特征确认         → 全仓卖出
       L3  跌破多空线 — 连续两日收盘低于多空线               → 全仓卖出
       L4  S1半仓    — score_s1 in [5,10)，疑似顶部         → 半仓减持
       （同一只股票同一交易日只执行一次，最高优先级优先）

  2. 成交价计算（Execution Price）
       使用当日收盘价作为模拟成交价（T+1实盘取次日开盘价，
       此处为数据驱动回测，收盘价为当日最终可观测价格）。
       滑点: 0.05%（买卖各0.025%，接近A股交易实际）

  3. 交易成本（Transaction Cost）
       印花税: 0.05%（单向，仅卖出方）
       佣金:   0.025%（双向，此处仅卖出方）
       过户费: 0.001%（沪市）
       合计卖出摩擦成本: 0.076%

  4. 幂等性保护（Idempotency Guard）
       同一 (code, sell_date) 不重复执行。
       通过 positions 表唯一主键 + status 字段保证。

  5. 审计日志（Audit Trail）
       每笔执行写入 trade_audit_log 表，记录信号来源、
       执行价、止损价、盈亏、执行耗时，不可删除只可追加。

  6. 干跑模式（Dry-Run）
       --dry-run 参数: 仅打印将要执行的操作，不写数据库。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
用法:
    # 执行今日卖出信号（自动取最新交易日）
    python scripts/execute_sell.py

    # 执行指定日期
    python scripts/execute_sell.py --date 20260529

    # 干跑模式（只打印，不写库）
    python scripts/execute_sell.py --date 20260529 --dry-run

    # 回填历史（批量执行一个日期区间）
    python scripts/execute_sell.py --start 20260401 --end 20260529

    # 只处理指定股票
    python scripts/execute_sell.py --code 600519

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import sys
import argparse
import time
from datetime import datetime, date
from typing import Optional, List, Dict, Any, Tuple
from dataclasses import dataclass, field
from enum import Enum

import duckdb

# ── 路径初始化 ────────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'signals'))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'signals', 'singal_cal'))

from scripts.log_utils import setup_logger

logger = setup_logger('execute_sell', 'pipeline')

DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'Astock3.duckdb')

# ── 交易成本常量 ──────────────────────────────────────────────────
STAMP_DUTY_RATE    = 0.0005   # 印花税 0.05%（卖出方单边）
COMMISSION_RATE    = 0.00025  # 佣金 0.025%（最低5元，此处不设最低）
TRANSFER_RATE      = 0.00001  # 过户费 0.001%（沪市）
SLIPPAGE_RATE      = 0.0005   # 滑点 0.05%（双边各半）
TOTAL_SELL_COST    = STAMP_DUTY_RATE + COMMISSION_RATE + TRANSFER_RATE + SLIPPAGE_RATE
# 合计: 0.076%

FIXED_STOPLOSS_PCT = 0.03     # 固定止损阈值 3%（ATR不可用时的后备）


# ═══════════════════════════════════════════════════════════════
# §1  数据模型
# ═══════════════════════════════════════════════════════════════

class SellReason(Enum):
    """卖出原因枚举（信号优先级 L1 > L2 > L3 > L4）"""
    STOPLOSS       = ('止损',    1, 'FULL')   # L1 硬止损
    S1_FULL        = ('S1满仓',  2, 'FULL')   # L2 S1满仓卖出
    BREAK_MALINE   = ('跌破多空线', 3, 'FULL') # L3 跌破多空线
    S1_HALF        = ('S1半仓',  4, 'HALF')   # L4 S1半仓减持

    def __init__(self, label: str, priority: int, size: str):
        self.label    = label
        self.priority = priority
        self.size     = size   # 'FULL' | 'HALF'


@dataclass
class SellDecision:
    """单笔卖出决策"""
    position_id:   int
    code:          str
    name:          str
    strategy:      str
    buy_price:     float
    buy_date:      date
    shares:        int           # 持仓总手数
    sell_shares:   int           # 本次卖出手数
    sell_price:    float         # 执行价（含滑点）
    sell_reason:   SellReason
    signal_date:   str           # 信号产生日期
    score_s1:      float = 0.0
    stoploss_price: float = 0.0
    # 计算字段（execute后填充）
    gross_proceeds: float = 0.0  # 卖出总收入（税前）
    net_proceeds:   float = 0.0  # 卖出净收入（税后）
    cost_basis:     float = 0.0  # 买入总成本
    profit_loss:    float = 0.0  # 净盈亏（元）
    profit_pct:     float = 0.0  # 净收益率（%）
    is_full_exit:   bool  = True  # 是否清仓


@dataclass
class ExecutionReport:
    """执行汇总报告"""
    trade_date:     str
    dry_run:        bool
    decisions:      List[SellDecision] = field(default_factory=list)
    executed:       int  = 0
    skipped:        int  = 0
    errors:         int  = 0
    total_profit:   float = 0.0
    elapsed_sec:    float = 0.0


# ═══════════════════════════════════════════════════════════════
# §2  数据库工具
# ═══════════════════════════════════════════════════════════════

def get_conn(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(DB_PATH, read_only=read_only)


def _ensure_audit_table(conn: duckdb.DuckDBPyConnection) -> None:
    """确保 trade_audit_log 表存在"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_audit_log (
            id              INTEGER NOT NULL PRIMARY KEY,
            trade_date      DATE    NOT NULL,
            position_id     INTEGER,
            code            VARCHAR NOT NULL,
            name            VARCHAR,
            strategy        VARCHAR,
            action          VARCHAR NOT NULL,   -- 'SELL_FULL' | 'SELL_HALF'
            sell_reason     VARCHAR NOT NULL,
            sell_shares     INTEGER,
            sell_price      DOUBLE,
            buy_price       DOUBLE,
            stoploss_price  DOUBLE,
            score_s1        DOUBLE,
            gross_proceeds  DOUBLE,
            net_proceeds    DOUBLE,
            cost_basis      DOUBLE,
            profit_loss     DOUBLE,
            profit_pct      DOUBLE,
            dry_run         BOOLEAN DEFAULT FALSE,
            executed_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes           VARCHAR
        )
    """)


def get_holding_positions(
    conn: duckdb.DuckDBPyConnection,
    code_filter: Optional[str] = None
) -> List[Dict[str, Any]]:
    """
    获取所有持仓中的头寸。

    返回字段与 positions 表完全一致，方便后续按列名访问。
    """
    sql = """
        SELECT
            id, code, name, strategy,
            buy_date, buy_price, shares,
            stop_loss_pct, notes
        FROM positions
        WHERE status = 'holding'
    """
    params = []
    if code_filter:
        sql += " AND code = ?"
        params.append(code_filter)
    sql += " ORDER BY buy_date"
    rows = conn.execute(sql, params).fetchall()
    cols = ['id', 'code', 'name', 'strategy',
            'buy_date', 'buy_price', 'shares',
            'stop_loss_pct', 'notes']
    return [dict(zip(cols, r)) for r in rows]


def get_sell_signals(
    conn: duckdb.DuckDBPyConnection,
    trade_date: str,
    code_filter: Optional[str] = None
) -> Dict[str, Dict[str, Any]]:
    """
    从 daily_signals 表读取指定日期的卖出相关信号。

    返回 {code: signal_dict}，只包含有持仓的代码。
    """
    sql = """
        SELECT
            code, name,
            close, low, high,
            score_s1,
            signal_s1_full,
            signal_s1_half,
            "signal_跌破多空线",
            "signal_止损",
            indicators
        FROM daily_signals
        WHERE date = ?
    """
    params = [trade_date]
    if code_filter:
        sql += " AND code = ?"
        params.append(code_filter)

    rows = conn.execute(sql, params).fetchall()
    cols = ['code', 'name', 'close', 'low', 'high',
            'score_s1', 'signal_s1_full', 'signal_s1_half',
            'signal_跌破多空线', 'signal_止损', 'indicators']
    return {r[0]: dict(zip(cols, r)) for r in rows}


def get_close_price(
    conn: duckdb.DuckDBPyConnection,
    code: str,
    trade_date: str
) -> Optional[float]:
    """从 dwd_daily_price 取收盘价（ts_code 格式自动推断）"""
    ts_code = f"{code}.SH" if code.startswith('6') else f"{code}.SZ"
    row = conn.execute(
        "SELECT close FROM dwd_daily_price WHERE ts_code = ? AND trade_date = ?",
        [ts_code, trade_date]
    ).fetchone()
    return float(row[0]) if row and row[0] else None


def position_already_sold(
    conn: duckdb.DuckDBPyConnection,
    position_id: int,
    sell_date: str
) -> bool:
    """幂等性检查：当前持仓是否已在该日期执行过卖出"""
    row = conn.execute(
        "SELECT status FROM positions WHERE id = ?",
        [position_id]
    ).fetchone()
    if row and row[0] == 'sold':
        return True
    # 再检查 audit log（半仓情况下可能已存在记录）
    row2 = conn.execute(
        "SELECT COUNT(*) FROM trade_audit_log WHERE position_id = ? AND trade_date = ?",
        [position_id, sell_date]
    ).fetchone()
    return row2 is not None and row2[0] > 0


def get_trading_dates_in_range(
    conn: duckdb.DuckDBPyConnection,
    start: str, end: str
) -> List[str]:
    """返回 [start, end] 内所有实际交易日（YYYY-MM-DD）"""
    rows = conn.execute("""
        SELECT DISTINCT trade_date
        FROM dwd_daily_price
        WHERE trade_date >= ? AND trade_date <= ?
        ORDER BY trade_date
    """, [start, end]).fetchall()
    return [str(r[0]) for r in rows]


def get_latest_trading_date(conn: duckdb.DuckDBPyConnection) -> Optional[str]:
    row = conn.execute(
        "SELECT MAX(trade_date) FROM dwd_daily_price"
    ).fetchone()
    return str(row[0]) if row and row[0] else None


# ═══════════════════════════════════════════════════════════════
# §3  止损判断
# ═══════════════════════════════════════════════════════════════

def _check_atr_stoploss(
    signal: Dict[str, Any],
    buy_price: float,
    stop_loss_pct: float,
) -> Tuple[bool, float]:
    """
    判断是否触发止损，返回 (triggered, stoploss_price)。

    优先使用 ATR 动态止损；ATR 不可用时退回固定阈值。
    使用日内最低价（而非收盘价）判断，符合实盘止损单触发机制。
    """
    low = signal.get('low') or 0.0
    if low <= 0 or buy_price <= 0:
        return False, 0.0

    # ── 尝试从 indicators JSON 取 ATR ──────────────────────────
    atr_value = 0.0
    try:
        import json
        indicators_raw = signal.get('indicators')
        if indicators_raw:
            ind = json.loads(indicators_raw) if isinstance(indicators_raw, str) else indicators_raw
            atr_value = float(ind.get('atr', 0.0) or 0.0)
    except Exception:
        pass

    if atr_value > 0:
        # ATR 动态止损（Wilder 1978，倍数 2.0）
        ATR_MULTIPLIER = 2.0
        MIN_LOSS_PCT   = 1.5
        MAX_LOSS_PCT   = 8.0
        raw_pct = atr_value * ATR_MULTIPLIER / buy_price * 100
        clamped_pct = max(MIN_LOSS_PCT, min(MAX_LOSS_PCT, raw_pct))
        stoploss_price = buy_price * (1 - clamped_pct / 100)
    else:
        # 后备：使用 positions.stop_loss_pct 或默认 3%
        pct = stop_loss_pct if stop_loss_pct and stop_loss_pct > 0 else FIXED_STOPLOSS_PCT
        stoploss_price = buy_price * (1 - pct)

    triggered = low < stoploss_price
    return triggered, stoploss_price


# ═══════════════════════════════════════════════════════════════
# §4  决策引擎
# ═══════════════════════════════════════════════════════════════

def evaluate_sell_decision(
    position: Dict[str, Any],
    signal:   Optional[Dict[str, Any]],
    trade_date: str,
) -> Optional[SellDecision]:
    """
    对单个持仓头寸执行信号优先级决策（Signal Priority Ladder）。

    返回 SellDecision 或 None（无需卖出）。
    """
    buy_price     = float(position['buy_price'] or 0)
    shares        = int(position['shares'] or 0)
    stop_loss_pct = float(position['stop_loss_pct'] or FIXED_STOPLOSS_PCT)

    if buy_price <= 0 or shares <= 0:
        logger.warning(f"[{position['code']}] 持仓数据异常: buy_price={buy_price} shares={shares}，跳过")
        return None

    # ── 无信号（当日未扫描或数据缺失）→ 检查硬止损 ──────────────
    if signal is None:
        logger.debug(f"[{position['code']}] 当日无信号记录，跳过")
        return None

    close_price = signal.get('close') or 0.0
    score_s1    = float(signal.get('score_s1') or 0.0)

    # ── L1: 硬止损 ─────────────────────────────────────────────
    # signal_止损 由 scan_signals 基于3%固定止损计算，
    # 此处再叠加 ATR 动态止损（更精准）
    atr_triggered, stoploss_price = _check_atr_stoploss(
        signal, buy_price, stop_loss_pct
    )
    signal_止损 = bool(signal.get('signal_止损')) or atr_triggered

    if signal_止损:
        return SellDecision(
            position_id   = position['id'],
            code          = position['code'],
            name          = position['name'],
            strategy      = position['strategy'] or '',
            buy_price     = buy_price,
            buy_date      = position['buy_date'],
            shares        = shares,
            sell_shares   = shares,
            sell_price    = close_price,
            sell_reason   = SellReason.STOPLOSS,
            signal_date   = trade_date,
            score_s1      = score_s1,
            stoploss_price= stoploss_price,
            is_full_exit  = True,
        )

    # ── L2: S1满仓卖出 ─────────────────────────────────────────
    if bool(signal.get('signal_s1_full')) or score_s1 >= 10:
        return SellDecision(
            position_id   = position['id'],
            code          = position['code'],
            name          = position['name'],
            strategy      = position['strategy'] or '',
            buy_price     = buy_price,
            buy_date      = position['buy_date'],
            shares        = shares,
            sell_shares   = shares,
            sell_price    = close_price,
            sell_reason   = SellReason.S1_FULL,
            signal_date   = trade_date,
            score_s1      = score_s1,
            stoploss_price= stoploss_price,
            is_full_exit  = True,
        )

    # ── L3: 跌破多空线 ─────────────────────────────────────────
    if bool(signal.get('signal_跌破多空线')):
        return SellDecision(
            position_id   = position['id'],
            code          = position['code'],
            name          = position['name'],
            strategy      = position['strategy'] or '',
            buy_price     = buy_price,
            buy_date      = position['buy_date'],
            shares        = shares,
            sell_shares   = shares,
            sell_price    = close_price,
            sell_reason   = SellReason.BREAK_MALINE,
            signal_date   = trade_date,
            score_s1      = score_s1,
            stoploss_price= stoploss_price,
            is_full_exit  = True,
        )

    # ── L4: S1半仓减持 ─────────────────────────────────────────
    if bool(signal.get('signal_s1_half')) or (5 <= score_s1 < 10):
        # 半仓: 向下取整到整百手
        half_shares = max(100, (shares // 2 // 100) * 100)
        return SellDecision(
            position_id   = position['id'],
            code          = position['code'],
            name          = position['name'],
            strategy      = position['strategy'] or '',
            buy_price     = buy_price,
            buy_date      = position['buy_date'],
            shares        = shares,
            sell_shares   = half_shares,
            sell_price    = close_price,
            sell_reason   = SellReason.S1_HALF,
            signal_date   = trade_date,
            score_s1      = score_s1,
            stoploss_price= stoploss_price,
            is_full_exit  = (half_shares >= shares),
        )

    return None


def compute_pnl(decision: SellDecision) -> SellDecision:
    """
    填充盈亏字段。

    cost_basis      = 买入价 × 卖出手数 × (1 + 佣金)
    gross_proceeds  = 卖出价 × 卖出手数
    net_proceeds    = gross_proceeds × (1 - 卖出成本率)
    profit_loss     = net_proceeds - cost_basis
    profit_pct      = profit_loss / cost_basis × 100
    """
    BUY_COST_RATE = 0.00025   # 买入佣金（已在买入时扣除，此处用于核算成本基础）
    cost_basis     = decision.buy_price * decision.sell_shares * (1 + BUY_COST_RATE)
    gross_proceeds = decision.sell_price * decision.sell_shares
    net_proceeds   = gross_proceeds * (1 - TOTAL_SELL_COST)
    profit_loss    = net_proceeds - cost_basis
    profit_pct     = (profit_loss / cost_basis * 100) if cost_basis > 0 else 0.0

    decision.cost_basis     = round(cost_basis,     2)
    decision.gross_proceeds = round(gross_proceeds, 2)
    decision.net_proceeds   = round(net_proceeds,   2)
    decision.profit_loss    = round(profit_loss,    2)
    decision.profit_pct     = round(profit_pct,     4)
    return decision


# ═══════════════════════════════════════════════════════════════
# §5  执行写库
# ═══════════════════════════════════════════════════════════════

def execute_decision(
    conn:      duckdb.DuckDBPyConnection,
    decision:  SellDecision,
    dry_run:   bool = False,
) -> bool:
    """
    将 SellDecision 写入数据库（positions + trade_audit_log）。

    positions 表更新策略：
      - 全仓卖出: status → 'sold'，填充 sell_date/price/reason/profit
      - 半仓卖出: shares -= sell_shares（保留持仓），不修改 status
        同时写 audit log 留档

    返回 True 表示成功，False 表示因幂等保护跳过或失败。
    """
    if position_already_sold(conn, decision.position_id, decision.signal_date):
        logger.info(
            f"[{decision.code}] 幂等保护: position_id={decision.position_id} "
            f"在 {decision.signal_date} 已执行，跳过"
        )
        return False

    decision = compute_pnl(decision)

    if dry_run:
        _print_decision(decision)
        return True

    try:
        # ── 1. 更新 positions 表 ──────────────────────────────
        if decision.is_full_exit:
            conn.execute("""
                UPDATE positions SET
                    status       = 'sold',
                    sell_date    = ?,
                    sell_price   = ?,
                    sell_reason  = ?,
                    profit_loss  = ?,
                    profit_pct   = ?,
                    updated_at   = CURRENT_TIMESTAMP
                WHERE id = ?
            """, [
                decision.signal_date,
                decision.sell_price,
                decision.sell_reason.label,
                decision.profit_loss,
                decision.profit_pct,
                decision.position_id,
            ])
        else:
            # 半仓：扣减手数，不改 status
            remaining = decision.shares - decision.sell_shares
            conn.execute("""
                UPDATE positions SET
                    shares     = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, [remaining, decision.position_id])

        # ── 2. 写入审计日志 ───────────────────────────────────
        max_id = conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM trade_audit_log"
        ).fetchone()[0] + 1

        conn.execute("""
            INSERT INTO trade_audit_log (
                id, trade_date, position_id, code, name, strategy,
                action, sell_reason,
                sell_shares, sell_price, buy_price, stoploss_price,
                score_s1, gross_proceeds, net_proceeds, cost_basis,
                profit_loss, profit_pct, dry_run, executed_at, notes
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, FALSE, CURRENT_TIMESTAMP, ?
            )
        """, [
            max_id,
            decision.signal_date,
            decision.position_id,
            decision.code,
            decision.name,
            decision.strategy,
            'SELL_FULL' if decision.is_full_exit else 'SELL_HALF',
            decision.sell_reason.label,
            decision.sell_shares,
            decision.sell_price,
            decision.buy_price,
            decision.stoploss_price,
            decision.score_s1,
            decision.gross_proceeds,
            decision.net_proceeds,
            decision.cost_basis,
            decision.profit_loss,
            decision.profit_pct,
            f"signal_priority={decision.sell_reason.priority}",
        ])

        logger.info(
            f"✅ EXECUTED [{decision.code}] {decision.name} | "
            f"原因: {decision.sell_reason.label} | "
            f"卖出: {decision.sell_shares}股 @ {decision.sell_price:.2f} | "
            f"盈亏: {decision.profit_loss:+.2f}元 ({decision.profit_pct:+.2f}%)"
        )
        return True

    except Exception as e:
        logger.error(f"❌ EXECUTE FAILED [{decision.code}]: {e}")
        raise


# ═══════════════════════════════════════════════════════════════
# §6  主流程
# ═══════════════════════════════════════════════════════════════

def _print_decision(decision: SellDecision) -> None:
    """干跑模式下格式化打印决策"""
    d = decision
    print(
        f"  [DRY-RUN] {d.code} {d.name:<8} | "
        f"原因: {d.sell_reason.label:<8} | "
        f"卖出: {d.sell_shares}股 @ {d.sell_price:.2f} | "
        f"成本: {d.buy_price:.2f} | "
        f"盈亏: {d.profit_loss:+.2f}元 ({d.profit_pct:+.2f}%)"
    )


def run_for_date(
    trade_date:  str,
    dry_run:     bool = False,
    code_filter: Optional[str] = None,
) -> ExecutionReport:
    """
    对单个交易日执行全量卖出扫描。

    流程：
      1. 读取所有持仓（status='holding'）
      2. 读取当日 daily_signals
      3. 对每个持仓执行 evaluate_sell_decision
      4. 有决策则调用 execute_decision 写库
      5. 返回 ExecutionReport
    """
    t0 = time.perf_counter()
    report = ExecutionReport(trade_date=trade_date, dry_run=dry_run)

    conn = get_conn(read_only=False)
    try:
        _ensure_audit_table(conn)

        positions = get_holding_positions(conn, code_filter)
        if not positions:
            logger.info(f"[{trade_date}] 无持仓，跳过")
            report.elapsed_sec = time.perf_counter() - t0
            return report

        # 只取持仓代码的信号，减少 IO
        all_signals = get_sell_signals(conn, trade_date, code_filter)

        logger.info(
            f"[{trade_date}] 持仓 {len(positions)} 只, "
            f"信号 {len(all_signals)} 条"
        )

        if dry_run:
            print(f"\n{'='*60}")
            print(f"  DRY-RUN  交易日: {trade_date}")
            print(f"{'='*60}")

        for pos in positions:
            code   = pos['code']
            signal = all_signals.get(code)

            try:
                decision = evaluate_sell_decision(pos, signal, trade_date)
                if decision is None:
                    report.skipped += 1
                    continue

                report.decisions.append(decision)
                ok = execute_decision(conn, decision, dry_run=dry_run)
                if ok:
                    report.executed     += 1
                    report.total_profit += decision.profit_loss
                else:
                    report.skipped += 1

            except Exception as e:
                report.errors += 1
                logger.error(f"[{trade_date}][{code}] 处理异常: {e}", exc_info=True)

    finally:
        conn.close()

    report.elapsed_sec = time.perf_counter() - t0
    return report


def _fmt_date(d: str) -> str:
    """将 YYYYMMDD 统一转为 YYYY-MM-DD"""
    d = d.strip().replace('-', '')
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def run(
    trade_date:  Optional[str] = None,
    start_date:  Optional[str] = None,
    end_date:    Optional[str] = None,
    dry_run:     bool = False,
    code_filter: Optional[str] = None,
) -> List[ExecutionReport]:
    """
    顶层入口：支持单日 / 区间 两种模式。
    """
    conn = get_conn(read_only=True)
    try:
        if start_date:
            sd = _fmt_date(start_date)
            ed = _fmt_date(end_date) if end_date else get_latest_trading_date(conn)
            dates = get_trading_dates_in_range(conn, sd, ed)
            logger.info(f"区间模式: {sd} ~ {ed}，共 {len(dates)} 个交易日")
        else:
            td = _fmt_date(trade_date) if trade_date else get_latest_trading_date(conn)
            dates = [td]
            logger.info(f"单日模式: {td}")
    finally:
        conn.close()

    reports = []
    for td in dates:
        logger.info(f"── 开始处理: {td} ──")
        rpt = run_for_date(td, dry_run=dry_run, code_filter=code_filter)
        reports.append(rpt)
        _print_report(rpt)

    return reports


def _print_report(rpt: ExecutionReport) -> None:
    """打印单日执行报告"""
    mode = '[DRY-RUN]' if rpt.dry_run else '[EXECUTED]'
    print(
        f"\n{mode} {rpt.trade_date} | "
        f"执行: {rpt.executed} | 跳过: {rpt.skipped} | 错误: {rpt.errors} | "
        f"当日盈亏合计: {rpt.total_profit:+.2f}元 | 耗时: {rpt.elapsed_sec:.2f}s"
    )
    if rpt.errors > 0:
        print(f"  ⚠️  {rpt.errors} 笔执行错误，请检查日志")


# ═══════════════════════════════════════════════════════════════
# §7  CLI
# ═══════════════════════════════════════════════════════════════

def parse_args():
    parser = argparse.ArgumentParser(
        prog='execute_sell.py',
        description='卖出执行引擎 — 根据 daily_signals 自动执行卖出并写入 positions',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 执行今日卖出（自动取最新交易日）
  python scripts/execute_sell.py

  # 执行指定日期
  python scripts/execute_sell.py --date 20260529

  # 干跑（只打印，不写库）
  python scripts/execute_sell.py --date 20260529 --dry-run

  # 批量回填历史
  python scripts/execute_sell.py --start 20260401 --end 20260529

  # 只处理指定股票
  python scripts/execute_sell.py --code 600519
        """
    )
    parser.add_argument('--date',    type=str, help='单日，格式 YYYYMMDD（默认最新交易日）')
    parser.add_argument('--start',   type=str, help='区间开始 YYYYMMDD')
    parser.add_argument('--end',     type=str, help='区间结束 YYYYMMDD（默认最新）')
    parser.add_argument('--code',    type=str, help='只处理指定股票代码，如 600519')
    parser.add_argument('--dry-run', action='store_true',
                        help='干跑模式：只打印决策，不写数据库')
    return parser.parse_args()


def main():
    args = parse_args()

    logger.info("=" * 60)
    logger.info("卖出执行引擎启动")
    logger.info(f"  dry_run  = {args.dry_run}")
    logger.info(f"  date     = {args.date}")
    logger.info(f"  start    = {args.start}")
    logger.info(f"  end      = {args.end}")
    logger.info(f"  code     = {args.code}")
    logger.info("=" * 60)

    reports = run(
        trade_date  = args.date,
        start_date  = args.start,
        end_date    = args.end,
        dry_run     = args.dry_run,
        code_filter = args.code,
    )

    # 汇总
    total_exec   = sum(r.executed for r in reports)
    total_skip   = sum(r.skipped  for r in reports)
    total_err    = sum(r.errors   for r in reports)
    total_profit = sum(r.total_profit for r in reports)

    print(f"\n{'='*60}")
    print(f"  全部完成 | 共 {len(reports)} 天")
    print(f"  执行: {total_exec} 笔 | 跳过: {total_skip} 笔 | 错误: {total_err} 笔")
    print(f"  累计盈亏: {total_profit:+.2f} 元")
    print(f"{'='*60}\n")

    if total_err > 0:
        sys.exit(1)


if __name__ == '__main__':
    main()
