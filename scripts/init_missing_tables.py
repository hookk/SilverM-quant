#!/usr/bin/env python
# coding=utf-8
# scripts/init_missing_tables.py
"""
初始化 Dashboard 依赖的业务表（仅在表不存在时创建，不会覆盖数据）
"""
import sys, os
import duckdb

project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(project_root, 'data', 'Astock3.duckdb')

DDL_STATEMENTS = {

    # ── 持仓表 ──────────────────────────────────────────────────
    "positions": """
        CREATE TABLE IF NOT EXISTS positions (
            id            INTEGER PRIMARY KEY,
            code          VARCHAR NOT NULL,
            name          VARCHAR,
            buy_date      DATE,
            buy_price     DOUBLE,
            cost          DOUBLE,
            shares        INTEGER,
            current_price DOUBLE,
            market_value  DOUBLE,
            profit        DOUBLE,
            profit_pct    DOUBLE,
            strategy      VARCHAR,
            status        VARCHAR DEFAULT 'holding',  -- holding / sold
            sell_date     DATE,
            sell_price    DOUBLE,
            updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,

    # ── 每日信号表 ──────────────────────────────────────────────
    "daily_signals": """
        CREATE TABLE IF NOT EXISTS daily_signals (
            date               DATE    NOT NULL,
            code               VARCHAR NOT NULL,
            name               VARCHAR,
            open               DOUBLE,
            high               DOUBLE,
            low                DOUBLE,
            close              DOUBLE,
            volume             DOUBLE,
            prev_close         DOUBLE,
            change_pct         DOUBLE,
            score_b1           DOUBLE,
            score_b2           DOUBLE,
            score_blk          DOUBLE,
            score_dl           DOUBLE,
            score_dz30         DOUBLE,
            score_scb          DOUBLE,
            score_blkB2        DOUBLE,
            signal_buy_b1      BOOLEAN,
            signal_buy_b2      BOOLEAN,
            signal_buy_blk     BOOLEAN,
            signal_buy_dl      BOOLEAN,
            signal_buy_dz30    BOOLEAN,
            signal_buy_scb     BOOLEAN,
            signal_buy_blkB2   BOOLEAN,
            signal_sell_b1     BOOLEAN,
            signal_sell_b2     BOOLEAN,
            signal_sell_blk    BOOLEAN,
            signal_sell_dl     BOOLEAN,
            signal_sell_dz30   BOOLEAN,
            signal_sell_scb    BOOLEAN,
            signal_sell_blkB2  BOOLEAN,
            score_s1           DOUBLE,
            signal_s1_full     BOOLEAN,
            signal_s1_half     BOOLEAN,
            "signal_跌破多空线" BOOLEAN,
            signal_止损        BOOLEAN,
            indicators         JSON,
            is_observing       BOOLEAN DEFAULT FALSE,
            PRIMARY KEY (date, code)
        )
    """,

    # ── 每日组合净值快照 ────────────────────────────────────────
    "portfolio_daily": """
        CREATE TABLE IF NOT EXISTS portfolio_daily (
            date          DATE    NOT NULL,
            strategy      VARCHAR NOT NULL DEFAULT 'main',
            total_value   DOUBLE,
            cash          DOUBLE,
            market_value  DOUBLE,
            daily_pnl     DOUBLE,
            daily_pnl_pct DOUBLE,
            cum_return    DOUBLE,
            drawdown      DOUBLE,
            positions_json JSON,
            PRIMARY KEY (date, strategy)
        )
    """,

    # ── 多策略对比快照 ──────────────────────────────────────────
    "portfolio_daily_strategy": """
        CREATE TABLE IF NOT EXISTS portfolio_daily_strategy (
            date          DATE    NOT NULL,
            strategy      VARCHAR NOT NULL,
            total_value   DOUBLE,
            cash          DOUBLE,
            market_value  DOUBLE,
            daily_pnl     DOUBLE,
            daily_pnl_pct DOUBLE,
            cum_return    DOUBLE,
            drawdown      DOUBLE,
            PRIMARY KEY (date, strategy)
        )
    """,

    # ── 每日指标宽表（dashboard 用的非 dwd 版本）──────────────
    "daily_basic": """
        CREATE TABLE IF NOT EXISTS daily_basic (
            trade_date  DATE    NOT NULL,
            ts_code     VARCHAR NOT NULL,
            close       DOUBLE,
            pe_ttm      DOUBLE,
            pe          DOUBLE,
            ps_ttm      DOUBLE,
            ps          DOUBLE,
            pcf         DOUBLE,
            pb          DOUBLE,
            total_mv    DOUBLE,
            circ_mv     DOUBLE,
            amount      DOUBLE,
            turn_rate   DOUBLE,
            data_source VARCHAR,
            PRIMARY KEY (trade_date, ts_code)
        )
    """,
}


def init_tables(db_path: str = DB_PATH):
    conn = duckdb.connect(db_path)
    try:
        for table_name, ddl in DDL_STATEMENTS.items():
            try:
                conn.execute(ddl)
                print(f"  ✓ {table_name}")
            except Exception as e:
                print(f"  ✗ {table_name}: {e}")
    finally:
        conn.close()


if __name__ == '__main__':
    print(f"初始化缺失表 → {DB_PATH}")
    init_tables()
    print("完成")
