#!/usr/bin/env python
# coding=utf-8
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/backfill_portfolio_accurate.py
═══════════════════════════════════════════════════════════════════════════════
方案B：精准历史回填 —— 按每天的真实持仓快照重建 portfolio_daily

与原始 backfill_portfolio.py 的区别：
  原版：每天都用"当前持仓状态"（已卖出的股票不算），导致历史日期资产严重低估
  本版：根据每笔交易的 buy_date / sell_date，推算出每个历史日期实际持有哪些股票，
        再用那天的 close 价计算市值，得到真实的历史净值曲线

用法：
    python scripts/backfill_portfolio_accurate.py
    python scripts/backfill_portfolio_accurate.py --start 20250101
    python scripts/backfill_portfolio_accurate.py --start 20250101 --end 20260101
    python scripts/backfill_portfolio_accurate.py --fix       # 强制覆盖已有记录

依赖：duckdb（项目已有）
═══════════════════════════════════════════════════════════════════════════════
"""

import os
import sys
import argparse
from datetime import date, timedelta, datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'Astock3.duckdb')

INIT_CASH = 500_000.0
FEE_RATE  = 0.0005   # 买入手续费 0.05%
SELL_FEE  = 0.00125  # 卖出手续费（含印花税）


# ─────────────────────────────────────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────────────────────────────────────

def ts_code(code: str) -> str:
    """000001 → 000001.SZ  /  600001 → 600001.SH"""
    c = str(code).strip()
    if c.startswith('6') or c.startswith('9') or c.startswith('5'):
        return f"{c}.SH"
    elif c.startswith('0') or c.startswith('3') or c.startswith('2'):
        return f"{c}.SZ"
    elif c.startswith('4') or c.startswith('8'):
        return f"{c}.BJ"
    return c


def get_trading_days(conn: duckdb.DuckDBPyConnection,
                     start: date, end: date) -> list[date]:
    """从 dwd_daily_price 获取区间内所有交易日（有价格数据的日期）"""
    rows = conn.execute("""
        SELECT DISTINCT trade_date
        FROM dwd_daily_price
        WHERE trade_date >= ? AND trade_date <= ?
        ORDER BY trade_date
    """, [start.isoformat(), end.isoformat()]).fetchall()
    result = []
    for (d,) in rows:
        result.append(d if isinstance(d, date) else date.fromisoformat(str(d)))
    return result


def load_all_positions(conn: duckdb.DuckDBPyConnection) -> list[dict]:
    """
    加载所有持仓记录（含已卖出），构建每笔交易的时间区间。
    返回 list of dict，每条记录包含：
        code, name, strategy, shares, buy_price, buy_date,
        sell_date (None表示仍持有), profit_loss (卖出时已计算)
    """
    rows = conn.execute("""
        SELECT
            id, code, name, strategy,
            shares, buy_price,
            buy_date, sell_date, sell_price,
            profit_loss, status
        FROM positions
        ORDER BY buy_date
    """).fetchall()

    positions = []
    for row in rows:
        (pid, code, name, strategy,
         shares, buy_price,
         buy_date, sell_date, sell_price,
         profit_loss, status) = row

        # 统一为 date 对象
        if isinstance(buy_date, str):
            buy_date = date.fromisoformat(buy_date)
        if sell_date and isinstance(sell_date, str):
            sell_date = date.fromisoformat(sell_date)

        positions.append({
            'id':          pid,
            'code':        str(code).strip(),
            'name':        name or '',
            'strategy':    strategy or '',
            'shares':      int(shares or 0),
            'buy_price':   float(buy_price or 0),
            'buy_date':    buy_date,
            'sell_date':   sell_date,
            'sell_price':  float(sell_price or 0) if sell_price else None,
            'profit_loss': float(profit_loss or 0) if profit_loss is not None else None,
            'status':      status,
        })
    return positions


def get_close_price(conn: duckdb.DuckDBPyConnection,
                    code: str, d: date,
                    cache: dict) -> float | None:
    """带缓存的收盘价查询，找不到时向前回溯最多5个交易日"""
    key = (code, d)
    if key in cache:
        return cache[key]

    # 精确匹配
    row = conn.execute(
        "SELECT close FROM dwd_daily_price WHERE ts_code = ? AND trade_date = ?",
        [ts_code(code), d.isoformat()]
    ).fetchone()
    if row and row[0]:
        cache[key] = float(row[0])
        return cache[key]

    # 向前回溯5个交易日（应对节假日停牌等）
    row = conn.execute("""
        SELECT close FROM dwd_daily_price
        WHERE ts_code = ? AND trade_date <= ? AND trade_date >= ?
        ORDER BY trade_date DESC LIMIT 1
    """, [ts_code(code), d.isoformat(),
          (d - timedelta(days=7)).isoformat()]).fetchone()

    cache[key] = float(row[0]) if row and row[0] else None
    return cache[key]


# ─────────────────────────────────────────────────────────────────────────────
# 核心：按日期计算净值快照
# ─────────────────────────────────────────────────────────────────────────────

def compute_daily_snapshot(
        d: date,
        positions: list[dict],
        conn: duckdb.DuckDBPyConnection,
        price_cache: dict,
) -> dict:
    """
    计算 d 这天的账户快照。

    逻辑：
    1. 找出 d 这天 "正在持有" 的股票：buy_date <= d < sell_date（或未卖）
    2. 已卖出（sell_date <= d）的股票，累加其 profit_loss 作为 closed_pnl
    3. 持仓市值 = Σ shares * close_price(d)
    4. cash = INIT_CASH - Σ持仓成本 + closed_pnl（含回款成本）
    5. total_value = 持仓市值 + cash
    """

    holding   = []  # 当天持有的股票
    closed_pnl = 0.0
    sold_cost  = 0.0  # 已卖出的原始成本，用于归还现金

    for p in positions:
        buy_d  = p['buy_date']
        sell_d = p['sell_date']

        if buy_d is None or buy_d > d:
            continue   # 还没买入

        if sell_d is not None and sell_d <= d:
            # 已卖出：回收现金
            cost = p['shares'] * p['buy_price'] * (1 + FEE_RATE)
            sold_cost  += cost
            closed_pnl += p['profit_loss'] if p['profit_loss'] is not None else 0.0
        else:
            # 仍持有
            holding.append(p)

    # 持仓成本（当前持有）
    holding_cost  = sum(p['shares'] * p['buy_price'] * (1 + FEE_RATE) for p in holding)

    # 持仓市值
    holding_value = 0.0
    for p in holding:
        price = get_close_price(conn, p['code'], d, price_cache)
        if price:
            holding_value += p['shares'] * price
        else:
            # 找不到价格时用买入价保底（不失真太多）
            holding_value += p['shares'] * p['buy_price']

    # 可用现金：初始资金 - 当前持仓成本 + 已卖出回款（成本 + 盈亏）
    cash = INIT_CASH - holding_cost + sold_cost + closed_pnl

    # 账户总资产
    total_value = holding_value + cash

    # 盈亏汇总
    holding_pnl = holding_value - holding_cost
    total_pnl   = holding_pnl + closed_pnl

    # 仓位比例
    position_ratio = (holding_value / total_value * 100) if total_value > 0 else 0.0

    return {
        'date':            d.isoformat(),
        'holding':         holding,
        'holding_cost':    round(holding_cost,  2),
        'holding_value':   round(holding_value, 2),
        'holding_pnl':     round(holding_pnl,   2),
        'closed_pnl':      round(closed_pnl,    2),
        'total_pnl':       round(total_pnl,     2),
        'cash':            round(cash,           2),
        'total_value':     round(total_value,    2),
        'position_ratio':  round(position_ratio, 2),
        'cum_return':      round(total_pnl / INIT_CASH, 6) if INIT_CASH > 0 else 0.0,
    }


# ─────────────────────────────────────────────────────────────────────────────
# 主写入逻辑
# ─────────────────────────────────────────────────────────────────────────────

def backfill(start: date, end: date, force: bool = False) -> None:
    conn = duckdb.connect(DB_PATH, read_only=False)
    try:
        # 1. 加载所有持仓历史
        all_positions = load_all_positions(conn)
        if not all_positions:
            print("❌ positions 表为空，无法 backfill")
            return

        earliest_buy = min(p['buy_date'] for p in all_positions if p['buy_date'])
        effective_start = max(start, earliest_buy)
        print(f"持仓最早买入日期: {earliest_buy}")
        print(f"回填区间: {effective_start} → {end}")

        # 2. 获取区间内所有交易日
        trading_days = get_trading_days(conn, effective_start, end)
        if not trading_days:
            print("❌ 该区间内无交易日数据，请先更新 dwd_daily_price")
            return
        print(f"交易日共 {len(trading_days)} 天")

        # 3. 获取已有记录（避免重复写）
        existing_dates = set()
        if not force:
            rows = conn.execute(
                "SELECT date FROM portfolio_daily WHERE strategy = 'main'"
            ).fetchall()
            for (d,) in rows:
                existing_dates.add(d if isinstance(d, date) else date.fromisoformat(str(d)))

        # 4. 价格缓存（减少 DB 查询）
        price_cache: dict = {}

        # 5. 按时间顺序处理每个交易日
        prev_total_value = INIT_CASH
        peak_value       = INIT_CASH
        inserted = 0
        skipped  = 0

        # 先把已有数据的 peak 计算出来（fix 模式下从头算）
        if not force and existing_dates:
            row = conn.execute(
                "SELECT MAX(total_value) FROM portfolio_daily WHERE strategy = 'main'"
            ).fetchone()
            if row and row[0]:
                peak_value = max(peak_value, float(row[0]))
            row2 = conn.execute("""
                SELECT total_value FROM portfolio_daily
                WHERE strategy = 'main'
                ORDER BY date DESC LIMIT 1
            """).fetchone()
            if row2 and row2[0]:
                prev_total_value = float(row2[0])

        for d in trading_days:
            if d in existing_dates:
                skipped += 1
                continue

            snap = compute_daily_snapshot(d, all_positions, conn, price_cache)

            tv           = snap['total_value']
            daily_pnl    = tv - prev_total_value
            daily_pnl_pct = daily_pnl / prev_total_value if prev_total_value else 0.0

            peak_value   = max(peak_value, tv)
            drawdown     = (tv - peak_value) / peak_value if peak_value > 0 else 0.0

            prev_total_value = tv

            # 生成持仓备注
            names = [p['name'] for p in snap['holding']]
            notes = f"持仓{len(names)}只: {','.join(names[:8])}" if names else "空仓"

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
                next_id,   snap['date'],
                INIT_CASH, snap['holding_cost'], snap['holding_value'], snap['holding_pnl'],
                snap['closed_pnl'], snap['total_pnl'],
                snap['cash'], snap['position_ratio'], tv, snap['holding_value'],
                round(daily_pnl, 2), round(daily_pnl_pct, 6),
                snap['cum_return'], round(drawdown, 6),
                notes,
            ])
            inserted += 1

            if inserted % 20 == 0:
                print(f"  {snap['date']}  总资产={tv:,.0f}  日盈亏={daily_pnl:+,.0f}"
                      f"  累计={snap['cum_return']*100:+.2f}%  已写{inserted}条...")

        print(f"\n✅ 完成！写入 {inserted} 条，跳过 {skipped} 条（已存在）")
        print(f"   区间: {trading_days[0]} → {trading_days[-1]}")

    finally:
        conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# 入口
# ─────────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description='精准历史回填 portfolio_daily（方案B）'
    )
    parser.add_argument(
        '--start', type=str, default=None,
        help='起始日期 YYYYMMDD（默认：最早买入日期）'
    )
    parser.add_argument(
        '--end', type=str, default=None,
        help='截止日期 YYYYMMDD（默认：今天）'
    )
    parser.add_argument(
        '--fix', action='store_true',
        help='强制覆盖已有记录（从头重算所有日期）'
    )
    args = parser.parse_args()

    start_d = (datetime.strptime(args.start, '%Y%m%d').date()
               if args.start else date(2024, 1, 1))
    end_d   = (datetime.strptime(args.end,   '%Y%m%d').date()
               if args.end   else date.today())

    if args.fix:
        print("⚠️  --fix 模式：将删除区间内已有记录并重新计算")
        conn_tmp = duckdb.connect(DB_PATH, read_only=False)
        conn_tmp.execute(
            "DELETE FROM portfolio_daily WHERE strategy = 'main' AND date >= ? AND date <= ?",
            [start_d.isoformat(), end_d.isoformat()]
        )
        conn_tmp.close()
        print(f"   已删除 {start_d} ~ {end_d} 的旧记录")

    print("=" * 60)
    print("  portfolio_daily 精准历史回填")
    print(f"  DB  : {DB_PATH}")
    print(f"  区间: {start_d} → {end_d}")
    print("=" * 60)

    backfill(start_d, end_d, force=args.fix)


if __name__ == '__main__':
    main()
