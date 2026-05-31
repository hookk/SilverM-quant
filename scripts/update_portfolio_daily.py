#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
每日持仓快照更新脚本 - update_portfolio_daily.py

用途：每日定时插入/更新 portfolio_daily 和 portfolio_daily_strategy 表
处理中国节假日和周末：非交易日沿用最近交易日数据

字段对齐说明：
  写入 portfolio_daily 的字段与 app.py /api/equity-curve 读取的字段完全一致：
    - cash          : 可用现金
    - total_value   : 账户总资产 = position_value + cash
    - daily_pnl     : 当日盈亏（与前一日 total_pnl 之差）
    - cum_return    : 累计收益率 = total_pnl / init_cash
    - drawdown      : 历史最高净值以来的回撤（需读前序数据计算）
    - strategy      : 固定写 'main'，供 app.py WHERE strategy='main' 查询

用法：
    python scripts/update_portfolio_daily.py              # 更新今日
    python scripts/update_portfolio_daily.py --date 20260327
    python scripts/update_portfolio_daily.py --fix        # 强制覆盖已有记录
"""

import os
import sys
import argparse
from datetime import datetime, date

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'Astock3.duckdb')
INIT_CASH = 500000.0
FEE_RATE = 0.0005  # 买入手续费 0.05%


def is_trading_day(check_date: date) -> bool:
    conn = duckdb.connect(DB_PATH, read_only=True)
    try:
        result = conn.execute(
            "SELECT COUNT(*) FROM dwd_daily_price WHERE trade_date = ?",
            [check_date.strftime('%Y-%m-%d')]
        ).fetchone()
        return result is not None and result[0] > 0
    finally:
        conn.close()


def get_latest_trading_day(before_date: date) -> date:
    conn = duckdb.connect(DB_PATH, read_only=True)
    try:
        result = conn.execute(
            "SELECT MAX(trade_date) FROM dwd_daily_price WHERE trade_date < ?",
            [before_date.strftime('%Y-%m-%d')]
        ).fetchone()
        if result and result[0]:
            val = result[0]
            return date.fromisoformat(str(val)) if isinstance(val, str) else val
        return before_date
    finally:
        conn.close()


def get_target_date(target: date) -> tuple:
    """返回 (actual_date, is_holiday)"""
    if target.weekday() >= 5:
        latest = get_latest_trading_day(target)
        if latest != target:
            return latest, True
    if not is_trading_day(target):
        latest = get_latest_trading_day(target)
        if latest != target:
            return latest, True
    return target, False


def add_market_suffix(code: str) -> str:
    if code.startswith('6') or code.startswith('9') or code.startswith('5'):
        return f"{code}.SH"
    elif code.startswith('0') or code.startswith('3') or code.startswith('2'):
        return f"{code}.SZ"
    elif code.startswith('4') or code.startswith('8'):
        return f"{code}.BJ"
    return code


def _get_prev_total_value(conn: duckdb.DuckDBPyConnection, date_str: str) -> float:
    """获取最近一条 portfolio_daily 记录的 total_value，用于计算 daily_pnl"""
    row = conn.execute("""
        SELECT total_value FROM portfolio_daily
        WHERE strategy = 'main' AND date < ?
        ORDER BY date DESC
        LIMIT 1
    """, [date_str]).fetchone()
    return float(row[0]) if row and row[0] is not None else INIT_CASH


def _get_peak_total_value(conn: duckdb.DuckDBPyConnection, date_str: str) -> float:
    """获取截至当日（含）历史最高 total_value，用于计算 drawdown"""
    row = conn.execute("""
        SELECT MAX(total_value) FROM portfolio_daily
        WHERE strategy = 'main' AND date <= ?
    """, [date_str]).fetchone()
    return float(row[0]) if row and row[0] is not None else INIT_CASH


def update_portfolio_daily(target_date: date, force: bool = False) -> bool:
    """插入/更新 portfolio_daily 和 portfolio_daily_strategy"""
    conn = duckdb.connect(DB_PATH, read_only=False)
    try:
        date_str = target_date.strftime('%Y-%m-%d')

        existing = conn.execute(
            "SELECT id FROM portfolio_daily WHERE date = ?", [date_str]
        ).fetchone()

        if existing and not force:
            print(f"portfolio_daily {date_str} 已存在，跳过（用 --fix 强制覆盖）")
            return False

        # ── 持仓数据 ────────────────────────────────────────────
        holding = conn.execute(
            "SELECT code, name, shares, buy_price, strategy FROM positions WHERE status = 'holding'"
        ).fetchall()
        sold = conn.execute(
            "SELECT code, name, profit_loss FROM positions WHERE status = 'sold'"
        ).fetchall()

        # ── 计算持仓市值 ─────────────────────────────────────────
        total_cost = 0.0
        total_value = 0.0
        holding_names = []

        for code, name, shares, buy_price, strategy in holding:
            cost = shares * buy_price * (1 + FEE_RATE)
            total_cost += cost

            price_row = conn.execute(
                "SELECT close FROM dwd_daily_price WHERE ts_code = ? AND trade_date = ?",
                [add_market_suffix(code), date_str]
            ).fetchone()
            if price_row and price_row[0]:
                total_value += shares * float(price_row[0])
            holding_names.append(name)

        # ── 计算已卖出盈亏 ───────────────────────────────────────
        closed_pnl = 0.0
        sold_names = []
        for code, name, pl in sold:
            if pl is not None:
                closed_pnl += float(pl)
            sold_names.append(name)

        # ── 汇总账户数据 ─────────────────────────────────────────
        position_pnl = total_value - total_cost
        total_pnl = position_pnl + closed_pnl
        cash = INIT_CASH - total_cost + closed_pnl
        total_value_account = total_value + cash
        position_ratio = (total_value / INIT_CASH * 100) if INIT_CASH > 0 else 0.0

        # ── 计算增量指标（app.py equity-curve 读取这三个字段） ───
        prev_total_value = _get_prev_total_value(conn, date_str)
        daily_pnl = total_value_account - prev_total_value

        cum_return = total_pnl / INIT_CASH if INIT_CASH > 0 else 0.0

        # drawdown：相对于历史峰值的回撤（负数表示亏损，0表示在峰值）
        # 先更新当日数据再算 peak，避免当日是新高时 drawdown=0 被遗漏
        peak = max(_get_peak_total_value(conn, date_str), total_value_account)
        drawdown = (total_value_account - peak) / peak if peak > 0 else 0.0

        notes = "持仓%d只: %s" % (len(holding_names), ','.join(holding_names)) if holding_names else "空仓"
        if sold_names:
            notes += " | 已卖%d只: %s" % (len(sold_names), ','.join(sold_names[:5]))

        # ── 写入 portfolio_daily ─────────────────────────────────
        if existing:
            conn.execute("DELETE FROM portfolio_daily WHERE date = ?", [date_str])
            print(f"覆盖已有记录: {date_str}")

        next_id = conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM portfolio_daily"
        ).fetchone()[0] + 1

        conn.execute("""
            INSERT INTO portfolio_daily (
                id, date, strategy,
                init_cash, position_cost, position_value, position_pnl,
                closed_pnl, total_pnl,
                cash, position_ratio, total_value, market_value,
                daily_pnl, daily_pnl_pct, cum_return, drawdown,
                notes
            ) VALUES (
                ?, ?, 'main',
                ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?,
                ?
            )
        """, [
            next_id, date_str,
            INIT_CASH, total_cost, total_value, position_pnl,
            closed_pnl, total_pnl,
            cash, position_ratio, total_value_account, total_value,
            daily_pnl,
            daily_pnl / prev_total_value if prev_total_value else 0.0,
            cum_return,
            drawdown,
            notes,
        ])

        print("✅ portfolio_daily %s: 总资产=%.0f 仓位=%.1f%% 日盈亏=%.0f 累计收益=%.2f%%" % (
            date_str, total_value_account, position_ratio, daily_pnl, cum_return * 100))

        # ── 写入 portfolio_daily_strategy ────────────────────────
        strategies: dict = {}

        for code, name, shares, buy_price, strategy in holding:
            if strategy not in strategies:
                strategies[strategy] = {'cost': 0.0, 'value': 0.0, 'count': 0, 'closed': 0.0}
            cost = shares * buy_price * (1 + FEE_RATE)
            strategies[strategy]['cost'] += cost
            strategies[strategy]['count'] += 1

            price_row = conn.execute(
                "SELECT close FROM dwd_daily_price WHERE ts_code = ? AND trade_date = ?",
                [add_market_suffix(code), date_str]
            ).fetchone()
            if price_row and price_row[0]:
                strategies[strategy]['value'] += shares * float(price_row[0])

        for code, name, pl, strategy in conn.execute(
            "SELECT code, name, profit_loss, strategy FROM positions WHERE status = 'sold'"
        ).fetchall():
            if strategy not in strategies:
                strategies[strategy] = {'cost': 0.0, 'value': 0.0, 'count': 0, 'closed': 0.0}
            if pl is not None:
                strategies[strategy]['closed'] += float(pl)

        next_id_strat = conn.execute(
            "SELECT COALESCE(MAX(id), 0) FROM portfolio_daily_strategy"
        ).fetchone()[0]

        count = 0
        for strat_name, data in strategies.items():
            if force:
                conn.execute(
                    "DELETE FROM portfolio_daily_strategy WHERE date = ? AND strategy = ?",
                    [date_str, strat_name]
                )

            cost = data['cost']
            value = data['value']
            pnl = value - cost
            closed = data['closed']
            strat_total_pnl = pnl + closed
            strat_total_value = value + (INIT_CASH - cost + closed)
            strat_cum_return = strat_total_pnl / INIT_CASH if INIT_CASH > 0 else 0.0

            next_id_strat += 1
            conn.execute("""
                INSERT INTO portfolio_daily_strategy (
                    id, date, strategy,
                    position_cost, position_value, position_pnl,
                    closed_pnl, total_pnl,
                    total_value, daily_pnl, cum_return,
                    trade_count, notes
                ) VALUES (
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?
                )
            """, [
                next_id_strat, date_str, strat_name,
                cost, value, pnl,
                closed, strat_total_pnl,
                strat_total_value,
                strat_total_pnl,      # daily_pnl 近似用 total_pnl（单策略粒度无前日对比）
                strat_cum_return,
                data['count'],
                "持仓市值=%.0f，成本=%.0f" % (value, cost),
            ])
            count += 1

        print("✅ portfolio_daily_strategy %s: 新增 %d 条策略记录" % (date_str, count))
        return True

    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description='每日持仓快照更新')
    parser.add_argument('--date', type=str, help='目标日期 YYYYMMDD（默认今日）')
    parser.add_argument('--fix', action='store_true', help='强制覆盖已有记录')
    args = parser.parse_args()

    target = datetime.strptime(args.date, '%Y%m%d').date() if args.date else date.today()
    actual, is_holiday = get_target_date(target)

    print("=" * 50)
    print("目标日期: %s" % target)
    print("实际更新: %s%s" % (actual, " (节假日/周末沿用)" if is_holiday else ""))
    print("=" * 50)

    update_portfolio_daily(actual, force=args.fix)
    print()
    print("✅ 完成 %s 的持仓快照更新" % actual)


if __name__ == '__main__':
    main()