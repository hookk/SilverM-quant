#!/usr/bin/env python
# coding=utf-8
#!/usr/bin/env python3
"""修复 portfolio_daily 和 portfolio_daily_strategy 表结构，补充 app.py 读取所需的列"""
import duckdb, os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'data', 'Astock3.duckdb')

conn = duckdb.connect(DB_PATH)

# ── 修复 portfolio_daily ──────────────────────────────────────────────────────
# app.py 查询: date, total_value, cash, daily_pnl, cum_return, drawdown
# 现有表字段: id, date, init_cash, position_cost, position_value,
#             position_pnl, closed_pnl, total_pnl, available_cash,
#             position_ratio, notes, created_at, updated_at
# 缺少: strategy, total_value, cash, daily_pnl, cum_return, drawdown

existing = conn.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'portfolio_daily'
""").fetchdf()['column_name'].tolist()
print("portfolio_daily 现有列:", existing)

# 用 CREATE TABLE AS 重建，补充缺失列（含 strategy 默认 'main'）
conn.execute("""
    CREATE TABLE portfolio_daily_v2 AS
    SELECT
        id,
        date,
        'main'          AS strategy,
        init_cash,
        position_cost,
        position_value,
        position_pnl,
        closed_pnl,
        total_pnl,
        available_cash  AS cash,
        -- total_value = position_value + available_cash
        (position_value + available_cash)   AS total_value,
        position_pnl    AS daily_pnl,       -- 近似：当日持仓盈亏
        total_pnl / init_cash               AS cum_return,   -- 累计收益率
        CAST(0.0 AS DOUBLE)                 AS drawdown,
        position_ratio,
        notes,
        created_at,
        updated_at
    FROM portfolio_daily
""")
conn.execute("DROP TABLE portfolio_daily")
conn.execute("ALTER TABLE portfolio_daily_v2 RENAME TO portfolio_daily")
print("✅ portfolio_daily 重建完成")

# ── 修复 portfolio_daily_strategy ────────────────────────────────────────────
# app.py 查询: date, total_value, daily_pnl
# 现有表字段: id, date, strategy, position_cost, position_value,
#             position_pnl, closed_pnl, total_pnl, trade_count, notes, created_at
# 缺少: total_value, daily_pnl

existing2 = conn.execute("""
    SELECT column_name FROM information_schema.columns
    WHERE table_name = 'portfolio_daily_strategy'
""").fetchdf()['column_name'].tolist()
print("portfolio_daily_strategy 现有列:", existing2)

conn.execute("""
    CREATE TABLE portfolio_daily_strategy_v2 AS
    SELECT
        id, date, strategy,
        position_cost, position_value, position_pnl,
        closed_pnl, total_pnl,
        -- total_value = position_value + (init_cash - position_cost + closed_pnl) 近似
        -- 简化：total_value = total_pnl + 500000
        (total_pnl + 500000.0)  AS total_value,
        position_pnl            AS daily_pnl,
        trade_count, notes, created_at
    FROM portfolio_daily_strategy
""")
conn.execute("DROP TABLE portfolio_daily_strategy")
conn.execute("ALTER TABLE portfolio_daily_strategy_v2 RENAME TO portfolio_daily_strategy")
print("✅ portfolio_daily_strategy 重建完成")

conn.close()
print("\n全部修复完成。")
