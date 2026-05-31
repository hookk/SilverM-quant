#!/usr/bin/env python
# coding=utf-8
# scripts/init_database.py
"""
数据库初始化脚本 —— 唯一建表入口

【设计原则】
  - 本文件是项目中唯一的表结构真相源（Single Source of Truth）。
  - database/schema.py 不再独立定义 DDL，直接 import 本文件的 TABLE_DDL。
  - DatabaseManager 通过 database/schema.py → 本文件 完成建表，无重复定义。

【字段对齐说明】
  portfolio_daily 字段同时满足三处代码的需求：
    - scripts/update_portfolio_daily.py 写入的字段
    - dashboard/app.py /api/equity-curve 读取的字段
    - dashboard/app.py /api/strategy-comparison 读取的字段
  portfolio_daily_strategy 同理。

用法:
    python scripts/init_database.py                   # 标准初始化（幂等）
    python scripts/init_database.py --verify          # 初始化后验证各表行数
    python scripts/init_database.py --list            # 仅列出表名
    python scripts/init_database.py --drop-recreate   # 危险：重建（需确认）
"""

import argparse
import sys
import os
import duckdb
from pathlib import Path
from datetime import datetime

_SCRIPT_DIR = Path(__file__).resolve().parent
_PROJECT_ROOT = _SCRIPT_DIR.parent

try:
    sys.path.insert(0, str(_PROJECT_ROOT))
    from config.settings import Settings
    _DEFAULT_DB = str(Settings.DATABASE_PATH)
except Exception:
    _DEFAULT_DB = str(_PROJECT_ROOT / "data" / "Astock3.duckdb")


# ═══════════════════════════════════════════════════════════════
# TABLE_DDL：所有表的建表语句
# 修改表结构时，只改这里。
# ═══════════════════════════════════════════════════════════════

TABLE_DDL: dict[str, str] = {

    # ── DWD 市场数据层 ──────────────────────────────────────────

    "dwd_stock_info": """
        CREATE TABLE IF NOT EXISTS dwd_stock_info (
            ts_code      VARCHAR NOT NULL PRIMARY KEY,
            symbol       VARCHAR,
            name         VARCHAR,
            area         VARCHAR,
            industry     VARCHAR,
            market       VARCHAR,
            list_date    DATE,
            delist_date  DATE,
            is_hs        VARCHAR,
            act_name     VARCHAR,
            list_status  VARCHAR,
            data_source  VARCHAR DEFAULT 'tushare'
        )
    """,

    "dwd_trade_calendar": """
        CREATE TABLE IF NOT EXISTS dwd_trade_calendar (
            trade_date   DATE    NOT NULL,
            exchange     VARCHAR NOT NULL,
            is_open      BOOLEAN NOT NULL DEFAULT FALSE,
            PRIMARY KEY (trade_date, exchange)
        )
    """,

    "dwd_daily_price": """
        CREATE TABLE IF NOT EXISTS dwd_daily_price (
            trade_date   DATE    NOT NULL,
            ts_code      VARCHAR NOT NULL,
            open         DOUBLE,
            high         DOUBLE,
            low          DOUBLE,
            close        DOUBLE,
            vol          BIGINT,
            amount       DOUBLE,
            pct_chg      DOUBLE,
            data_source  VARCHAR DEFAULT 'tushare',
            PRIMARY KEY (trade_date, ts_code)
        )
    """,

    "dwd_daily_price_qfq": """
        CREATE TABLE IF NOT EXISTS dwd_daily_price_qfq (
            trade_date   DATE    NOT NULL,
            ts_code      VARCHAR NOT NULL,
            open         DOUBLE,
            high         DOUBLE,
            low          DOUBLE,
            close        DOUBLE,
            vol          BIGINT,
            amount       DOUBLE,
            pct_chg      DOUBLE,
            adj_factor   DOUBLE,
            data_source  VARCHAR DEFAULT 'tushare',
            PRIMARY KEY (trade_date, ts_code)
        )
    """,

    "dwd_daily_price_hfq": """
        CREATE TABLE IF NOT EXISTS dwd_daily_price_hfq (
            trade_date   DATE    NOT NULL,
            ts_code      VARCHAR NOT NULL,
            open         DOUBLE,
            high         DOUBLE,
            low          DOUBLE,
            close        DOUBLE,
            vol          BIGINT,
            amount       DOUBLE,
            pct_chg      DOUBLE,
            adj_factor   DOUBLE,
            data_source  VARCHAR DEFAULT 'tushare',
            PRIMARY KEY (trade_date, ts_code)
        )
    """,

    "dwd_adj_factor": """
        CREATE TABLE IF NOT EXISTS dwd_adj_factor (
            ts_code      VARCHAR NOT NULL,
            trade_date   DATE    NOT NULL,
            adj_factor   DOUBLE,
            data_source  VARCHAR DEFAULT 'tushare',
            PRIMARY KEY (ts_code, trade_date)
        )
    """,

    "dwd_daily_basic": """
        CREATE TABLE IF NOT EXISTS dwd_daily_basic (
            trade_date   DATE    NOT NULL,
            ts_code      VARCHAR NOT NULL,
            close        DOUBLE,
            pe_ttm       DOUBLE,
            pe           DOUBLE,
            ps_ttm       DOUBLE,
            ps           DOUBLE,
            pcf          DOUBLE,
            pb           DOUBLE,
            total_mv     DOUBLE,
            circ_mv      DOUBLE,
            total_share  DOUBLE,
            float_share  DOUBLE,
            free_share   DOUBLE,
            dv_ratio     DOUBLE,
            dv_ttm       DOUBLE,
            amount       DOUBLE,
            turn_rate    DOUBLE,
            data_source  VARCHAR DEFAULT 'tushare',
            PRIMARY KEY (trade_date, ts_code)
        )
    """,

    "dwd_index_daily": """
        CREATE TABLE IF NOT EXISTS dwd_index_daily (
            index_code   VARCHAR NOT NULL,
            trade_date   DATE    NOT NULL,
            open         DOUBLE,
            high         DOUBLE,
            low          DOUBLE,
            close        DOUBLE,
            pre_close    DOUBLE,
            change       DOUBLE,
            pct_change   DOUBLE,
            vol          BIGINT,
            amount       DOUBLE,
            data_source  VARCHAR DEFAULT 'tushare',
            PRIMARY KEY (index_code, trade_date)
        )
    """,

    "dwd_income": """
        CREATE TABLE IF NOT EXISTS dwd_income (
            ts_code                       VARCHAR,
            ann_date                      DATE,
            f_ann_date                    DATE,
            end_date                      DATE,
            report_type                   VARCHAR,
            comp_type                     VARCHAR,
            basic_eps                     DOUBLE,
            diluted_eps                   DOUBLE,
            total_revenue                 DOUBLE,
            revenue                       DOUBLE,
            total_profit                  DOUBLE,
            profit                        DOUBLE,
            income_tax                    DOUBLE,
            n_income                      DOUBLE,
            n_income_attr_p               DOUBLE,
            total_cogs                    DOUBLE,
            operate_profit                DOUBLE,
            invest_income                 DOUBLE,
            non_op_income                 DOUBLE,
            asset_impair_loss             DOUBLE,
            net_profit_with_non_recurring DOUBLE,
            data_source                   VARCHAR DEFAULT 'tushare',
            PRIMARY KEY (ts_code, end_date, report_type)
        )
    """,

    "dwd_balancesheet": """
        CREATE TABLE IF NOT EXISTS dwd_balancesheet (
            ts_code                          VARCHAR,
            ann_date                         DATE,
            f_ann_date                       DATE,
            end_date                         DATE,
            report_type                      VARCHAR,
            comp_type                        VARCHAR,
            total_assets                     DOUBLE,
            total_liab                       DOUBLE,
            total_hldr_eqy_excl_min_int      DOUBLE,
            hldr_eqy_excl_min_int            DOUBLE,
            minority_int                     DOUBLE,
            total_liab_ht_holder             DOUBLE,
            notes_payable                    DOUBLE,
            accounts_payable                 DOUBLE,
            advance_receipts                 DOUBLE,
            total_current_assets             DOUBLE,
            total_non_current_assets         DOUBLE,
            fixed_assets                     DOUBLE,
            cip                              DOUBLE,
            total_current_liab               DOUBLE,
            total_non_current_liab           DOUBLE,
            lt_borrow                        DOUBLE,
            bonds_payable                    DOUBLE,
            data_source                      VARCHAR DEFAULT 'tushare',
            PRIMARY KEY (ts_code, end_date, report_type)
        )
    """,

    "dwd_cashflow": """
        CREATE TABLE IF NOT EXISTS dwd_cashflow (
            ts_code          VARCHAR,
            ann_date         DATE,
            f_ann_date       DATE,
            end_date         DATE,
            report_type      VARCHAR,
            comp_type        VARCHAR,
            net_profit       DOUBLE,
            fin_exp          DOUBLE,
            c_fr_oper_a      DOUBLE,
            n_cashflow_act   DOUBLE,
            end_cash         DOUBLE,
            data_source      VARCHAR DEFAULT 'tushare',
            PRIMARY KEY (ts_code, end_date, report_type)
        )
    """,

    # ── 信号与持仓 ───────────────────────────────────────────────

    "daily_signals": """
        CREATE TABLE IF NOT EXISTS daily_signals (
            date                DATE    NOT NULL,
            code                VARCHAR NOT NULL,
            name                VARCHAR,
            open                DOUBLE,
            high                DOUBLE,
            low                 DOUBLE,
            close               DOUBLE,
            volume              DOUBLE,
            prev_close          DOUBLE,
            change_pct          DOUBLE,
            score_b1            DOUBLE,
            score_b2            DOUBLE,
            score_blk           DOUBLE,
            score_dl            DOUBLE,
            score_dz30          DOUBLE,
            score_scb           DOUBLE,
            score_blkB2         DOUBLE,
            signal_buy_b1       BOOLEAN,
            signal_buy_b2       BOOLEAN,
            signal_buy_blk      BOOLEAN,
            signal_buy_dl       BOOLEAN,
            signal_buy_dz30     BOOLEAN,
            signal_buy_scb      BOOLEAN,
            signal_buy_blkB2    BOOLEAN,
            signal_sell_b1      BOOLEAN,
            signal_sell_b2      BOOLEAN,
            signal_sell_blk     BOOLEAN,
            signal_sell_dl      BOOLEAN,
            signal_sell_dz30    BOOLEAN,
            signal_sell_scb     BOOLEAN,
            signal_sell_blkB2   BOOLEAN,
            score_s1            DOUBLE,
            signal_s1_full      BOOLEAN,
            signal_s1_half      BOOLEAN,
            "signal_跌破多空线"  BOOLEAN,
            signal_止损          BOOLEAN,
            indicators          JSON,
            is_observing        BOOLEAN DEFAULT FALSE,
            -- 风险管理列（scan_signals_v2 写入）
            risk_priority        INTEGER,
            risk_stoploss_price  DOUBLE,
            risk_stoploss_pct    DOUBLE,
            risk_position_pct    DOUBLE,
            risk_position_amt    DOUBLE,
            risk_market_state    VARCHAR,
            risk_composite_score DOUBLE,
            risk_should_buy      BOOLEAN,
            risk_reject_reason   VARCHAR,
            PRIMARY KEY (date, code)
        )
    """,

    "signal_events": """
        CREATE TABLE IF NOT EXISTS signal_events (
            id            BIGINT  NOT NULL PRIMARY KEY,
            date          DATE,
            code          VARCHAR,
            name          VARCHAR,
            signal_abbrev VARCHAR,
            version       VARCHAR,
            signal_type   VARCHAR,
            score         DOUBLE,
            signal_field  VARCHAR,
            created_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,

    # ── 持仓表 ──────────────────────────────────────────────────
    # app.py /api/positions 和 /api/history 读取
    # 需要字段：code, name, strategy, signal_date, buy_date, shares, buy_price,
    #           buy_change_pct, buy_score_b1, buy_score_b2,
    #           current_price, profit_loss, profit_pct,
    #           stop_loss_pct, status, notes,
    #           sell_date, sell_price, sell_reason

    "positions": """
        CREATE TABLE IF NOT EXISTS positions (
            id                   INTEGER NOT NULL PRIMARY KEY,
            code                 VARCHAR NOT NULL,
            name                 VARCHAR,
            strategy             VARCHAR,
            signal_date          DATE,
            buy_date             DATE,
            shares               INTEGER,
            buy_price            DOUBLE,
            buy_change_pct       DOUBLE,
            buy_score_b1         DOUBLE,
            buy_score_b2         DOUBLE,
            buy_dif              DOUBLE,
            buy_j_value          DOUBLE,
            "buy_知行短期趋势线"  DOUBLE,
            "buy_知行多空线"      DOUBLE,
            cost                 DOUBLE,
            current_price        DOUBLE,
            market_value         DOUBLE,
            current_score_s1     DOUBLE,
            "current_跌破多空线" BOOLEAN,
            stop_loss_pct        DOUBLE  DEFAULT 0.03,
            status               VARCHAR DEFAULT 'holding',
            sell_date            DATE,
            sell_price           DOUBLE,
            sell_reason          VARCHAR,
            profit               DOUBLE,
            profit_pct           DOUBLE,
            profit_loss          DOUBLE,
            notes                VARCHAR,
            created_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at           TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,

    # ── 组合净值快照 ────────────────────────────────────────────
    #
    # 字段对齐说明（三处代码的需求统一在此）：
    #
    # update_portfolio_daily.py 写入：
    #   id, date, init_cash, position_cost, position_value, position_pnl,
    #   closed_pnl, total_pnl, cash, position_ratio, total_value, notes,
    #   daily_pnl, cum_return, drawdown  ← 本次新增写入，见该文件
    #
    # app.py /api/equity-curve 读取（WHERE strategy = 'main'）：
    #   date, total_value, cash, daily_pnl, cum_return, drawdown
    #
    # strategy 列默认值 'main'，update_portfolio_daily.py 不传时自动填充。

    "portfolio_daily": """
        CREATE TABLE IF NOT EXISTS portfolio_daily (
            id             INTEGER      NOT NULL PRIMARY KEY,
            date           DATE         NOT NULL UNIQUE,
            strategy       VARCHAR      NOT NULL DEFAULT 'main',
            init_cash      DECIMAL(12,2),
            position_cost  DECIMAL(12,2),
            position_value DECIMAL(12,2),
            position_pnl   DECIMAL(12,2),
            closed_pnl     DECIMAL(12,2) DEFAULT 0,
            total_pnl      DECIMAL(12,2),
            cash           DECIMAL(12,2),           -- 可用现金（前称 available_cash）
            position_ratio DECIMAL(5,2),
            total_value    DECIMAL(12,2),            -- 账户总资产 = position_value + cash
            market_value   DECIMAL(12,2),            -- 同 position_value，供兼容
            daily_pnl      DECIMAL(12,2),            -- 当日盈亏（app.py equity-curve 读取）
            daily_pnl_pct  DECIMAL(8,4),
            cum_return     DECIMAL(8,4),             -- 累计收益率（app.py equity-curve 读取）
            drawdown       DECIMAL(8,4),             -- 最大回撤（app.py equity-curve 读取）
            positions_json JSON,
            notes          VARCHAR,
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,

    # ── 策略级净值快照 ───────────────────────────────────────────
    #
    # app.py /api/strategy-comparison 读取：
    #   date, strategy, total_value, daily_pnl
    #
    # update_portfolio_daily.py 写入：
    #   id, date, strategy, position_cost, position_value, position_pnl,
    #   closed_pnl, total_pnl, total_value, daily_pnl, trade_count, notes

    "portfolio_daily_strategy": """
        CREATE TABLE IF NOT EXISTS portfolio_daily_strategy (
            id             INTEGER      NOT NULL PRIMARY KEY,
            date           DATE         NOT NULL,
            strategy       VARCHAR      NOT NULL,
            position_cost  DECIMAL(12,2),
            position_value DECIMAL(12,2),
            position_pnl   DECIMAL(12,2),
            closed_pnl     DECIMAL(12,2) DEFAULT 0,
            total_pnl      DECIMAL(12,2),
            total_value    DECIMAL(12,2),            -- app.py strategy-comparison 读取
            cash           DECIMAL(12,2),
            market_value   DECIMAL(12,2),
            daily_pnl      DECIMAL(12,2),            -- app.py strategy-comparison 读取
            daily_pnl_pct  DECIMAL(8,4),
            cum_return     DECIMAL(8,4),
            drawdown       DECIMAL(8,4),
            trade_count    INTEGER DEFAULT 0,
            notes          VARCHAR,
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,

    # ── 流水线管理 ───────────────────────────────────────────────

    "data_pipeline_run": """
        CREATE TABLE IF NOT EXISTS data_pipeline_run (
            id             INTEGER  NOT NULL PRIMARY KEY,
            pipeline_id    VARCHAR,
            pipeline_name  VARCHAR,
            step_name      VARCHAR,
            step_order     INTEGER,
            status         VARCHAR DEFAULT 'pending',
            depends_on     VARCHAR,
            dependency_met BOOLEAN,
            params         JSON,
            records_count  INTEGER,
            error_message  VARCHAR,
            created_at     TIMESTAMP,
            started_at     TIMESTAMP,
            completed_at   TIMESTAMP,
            duration_sec   FLOAT,
            UNIQUE (pipeline_id, step_name)
        )
    """,

    "step_update_log": """
        CREATE TABLE IF NOT EXISTS step_update_log (
            id                 INTEGER  NOT NULL PRIMARY KEY,
            pipeline_id        VARCHAR,
            step_name          VARCHAR,
            update_type        VARCHAR,
            start_time         TIMESTAMP,
            end_time           TIMESTAMP,
            duration_sec       FLOAT,
            expected_count     INTEGER,
            actual_count       INTEGER,
            is_success         BOOLEAN,
            error_message      VARCHAR,
            error_details      JSON,
            step_details       JSON,
            validation_results JSON,
            check_time         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,

    "pipeline_monitor_flag": """
        CREATE TABLE IF NOT EXISTS pipeline_monitor_flag (
            id           INTEGER  NOT NULL PRIMARY KEY,
            date         VARCHAR  NOT NULL,
            completed    BOOLEAN  DEFAULT FALSE,
            completed_at TIMESTAMP
        )
    """,

    # ── 回测 ────────────────────────────────────────────────────

    "backtest_run": """
        CREATE TABLE IF NOT EXISTS backtest_run (
            run_id         VARCHAR NOT NULL PRIMARY KEY,
            strategy_name  VARCHAR NOT NULL,
            strategy_params JSON,
            start_date     DATE,
            end_date       DATE,
            universe       VARCHAR,
            benchmark      VARCHAR,
            initial_capital FLOAT,
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            completed_at   TIMESTAMP,
            status         VARCHAR DEFAULT 'running',
            error_message  VARCHAR
        )
    """,

    "backtest_trades": """
        CREATE TABLE IF NOT EXISTS backtest_trades (
            id             BIGINT  NOT NULL,
            run_id         VARCHAR NOT NULL,
            date           DATE,
            datetime       TIMESTAMP,
            code           VARCHAR,
            name           VARCHAR,
            industry       VARCHAR,
            market_cap_group VARCHAR,
            action         VARCHAR,
            price          FLOAT,
            volume         INTEGER,
            amount         FLOAT,
            commission     FLOAT,
            tax            FLOAT,
            total_cost     FLOAT,
            signal_type    VARCHAR,
            PRIMARY KEY (run_id, id)
        )
    """,

    "backtest_daily_pnl": """
        CREATE TABLE IF NOT EXISTS backtest_daily_pnl (
            run_id           VARCHAR NOT NULL,
            date             DATE    NOT NULL,
            total_value      FLOAT,
            cash             FLOAT,
            market_value     FLOAT,
            daily_pnl        FLOAT,
            daily_return     FLOAT,
            cumulative_return FLOAT,
            benchmark_return FLOAT,
            excess_return    FLOAT,
            drawdown         FLOAT,
            positions        JSON,
            PRIMARY KEY (run_id, date)
        )
    """,

    "backtest_performance": """
        CREATE TABLE IF NOT EXISTS backtest_performance (
            run_id                VARCHAR NOT NULL PRIMARY KEY,
            total_return          FLOAT,
            annualized_return     FLOAT,
            benchmark_return      FLOAT,
            excess_return         FLOAT,
            volatility            FLOAT,
            max_drawdown          FLOAT,
            max_drawdown_duration INTEGER,
            var_95                FLOAT,
            sharpe_ratio          FLOAT,
            sortino_ratio         FLOAT,
            calmar_ratio          FLOAT,
            information_ratio     FLOAT,
            total_trades          INTEGER,
            winning_trades        INTEGER,
            losing_trades         INTEGER,
            win_rate              FLOAT,
            avg_profit            FLOAT,
            avg_loss              FLOAT,
            profit_loss_ratio     FLOAT,
            industry_analysis     JSON,
            cap_group_analysis    JSON,
            monthly_returns       JSON,
            created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,

    "batch_backtest_results": """
        CREATE TABLE IF NOT EXISTS batch_backtest_results (
            result_id        BIGINT  NOT NULL PRIMARY KEY,
            batch_id         VARCHAR,
            stock_code       VARCHAR,
            stock_name       VARCHAR,
            status           VARCHAR,
            total_return      FLOAT,
            annualized_return FLOAT,
            max_drawdown      FLOAT,
            sharpe_ratio      FLOAT,
            win_rate          FLOAT,
            total_trades      INTEGER,
            final_value       FLOAT,
            initial_cash      FLOAT,
            error_message     VARCHAR,
            completed_at      TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            strategy_name     VARCHAR,
            start_date        DATE,
            end_date          DATE,
            initial_capital   FLOAT,
            total_stocks      INTEGER,
            valid_stocks      INTEGER
        )
    """,

    "batch_backtest_daily_pnl": """
        CREATE TABLE IF NOT EXISTS batch_backtest_daily_pnl (
            batch_id          VARCHAR NOT NULL,
            date              DATE    NOT NULL,
            total_value       DOUBLE,
            total_pnl         DOUBLE,
            total_pnl_pct     DOUBLE,
            cumulative_return DOUBLE,
            drawdown          DOUBLE,
            positions         JSON,
            PRIMARY KEY (batch_id, date)
        )
    """,

    "batch_backtest_params": """
        CREATE TABLE IF NOT EXISTS batch_backtest_params (
            id           BIGINT  NOT NULL PRIMARY KEY,
            batch_id     VARCHAR NOT NULL,
            param_name   VARCHAR NOT NULL,
            param_values JSON    NOT NULL,
            results      JSON    NOT NULL,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,

    # ── 因子 ────────────────────────────────────────────────────

    "factor_data": """
        CREATE TABLE IF NOT EXISTS factor_data (
            date         DATE    NOT NULL,
            code         VARCHAR NOT NULL,
            pe_ttm       FLOAT,
            pb           FLOAT,
            ps_ttm       FLOAT,
            pcf_ttm      FLOAT,
            dividend_yield FLOAT,
            roe          FLOAT,
            roa          FLOAT,
            gross_margin FLOAT,
            net_margin   FLOAT,
            debt_to_asset FLOAT,
            revenue_growth_yoy FLOAT,
            profit_growth_yoy  FLOAT,
            macd_dif     FLOAT,
            macd_dea     FLOAT,
            kdj_k        FLOAT,
            kdj_d        FLOAT,
            kdj_j        FLOAT,
            rsi_6        FLOAT,
            rsi_14       FLOAT,
            ma_5         FLOAT,
            ma_20        FLOAT,
            ma_60        FLOAT,
            volatility_20d FLOAT,
            volume_ratio FLOAT,
            price_momentum_20d FLOAT,
            update_time  TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (date, code)
        )
    """,

    "factor_ic": """
        CREATE TABLE IF NOT EXISTS factor_ic (
            date              DATE    NOT NULL,
            factor_name       VARCHAR NOT NULL,
            ic                FLOAT,
            ic_rank           FLOAT,
            ir                FLOAT,
            ic_positive_ratio FLOAT,
            update_time       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (date, factor_name)
        )
    """,

    # ── AI/Agent ────────────────────────────────────────────────

    "agent_analysis_results": """
        CREATE TABLE IF NOT EXISTS agent_analysis_results (
            run_id       VARCHAR  NOT NULL PRIMARY KEY,
            symbol       VARCHAR,
            trade_date   VARCHAR,
            result_json  VARCHAR,
            created_at   TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,

    # ── 策略注册表 ───────────────────────────────────────────────

    "strategy_registry": """
        CREATE TABLE IF NOT EXISTS strategy_registry (
            id                 VARCHAR NOT NULL PRIMARY KEY,
            name               VARCHAR NOT NULL,
            display_name       VARCHAR,
            class_path         VARCHAR NOT NULL,
            source_file        VARCHAR,
            description        VARCHAR,
            version            VARCHAR DEFAULT '1.0.0',
            author             VARCHAR,
            status             VARCHAR DEFAULT 'active',
            strategy_type      VARCHAR,
            threshold_required BOOLEAN DEFAULT FALSE,
            min_data_days      INTEGER DEFAULT 0,
            param_schema       JSON,
            created_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at         TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,

    "strategy_metadata": """
        CREATE TABLE IF NOT EXISTS strategy_metadata (
            name             VARCHAR NOT NULL PRIMARY KEY,
            signal_abbrev    VARCHAR,
            class_name       VARCHAR,
            description      VARCHAR,
            status           VARCHAR DEFAULT 'draft',
            current_version  VARCHAR,
            promotion_config JSON,
            latest_backtest  JSON,
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,

    "strategy_versions": """
        CREATE TABLE IF NOT EXISTS strategy_versions (
            id               INTEGER  NOT NULL PRIMARY KEY,
            strategy_name    VARCHAR  NOT NULL,
            signal_abbrev    VARCHAR,
            version          VARCHAR  NOT NULL,
            backtest_metrics JSON,
            backtest_params  JSON,
            run_id           VARCHAR,
            status           VARCHAR DEFAULT 'tested',
            promoted_at      TIMESTAMP,
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,

    "strategy_params": """
        CREATE TABLE IF NOT EXISTS strategy_params (
            id            INTEGER  NOT NULL PRIMARY KEY,
            strategy_name VARCHAR  NOT NULL,
            param_name    VARCHAR  NOT NULL,
            param_type    VARCHAR,
            default_value JSON,
            current_value JSON,
            description   VARCHAR,
            constraints   JSON,
            is_required   BOOLEAN  DEFAULT FALSE,
            updated_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """,

    "strategy_params_history": """
        CREATE TABLE IF NOT EXISTS strategy_params_history (
            id            INTEGER  NOT NULL PRIMARY KEY,
            strategy_name VARCHAR  NOT NULL,
            param_name    VARCHAR  NOT NULL,
            old_value     JSON,
            new_value     JSON,
            changed_at    TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            changed_by    VARCHAR
        )
    """,

    # ── 审计 ────────────────────────────────────────────────────

    "trade_audit_log": """
        CREATE TABLE IF NOT EXISTS trade_audit_log (
            id              INTEGER  NOT NULL PRIMARY KEY,
            trade_date      DATE     NOT NULL,
            position_id     INTEGER,
            code            VARCHAR  NOT NULL,
            name            VARCHAR,
            strategy        VARCHAR,
            action          VARCHAR  NOT NULL,
            sell_reason     VARCHAR  NOT NULL,
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
            dry_run         BOOLEAN  DEFAULT FALSE,
            executed_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes           VARCHAR
        )
    """,

    # ── 兼容旧版 ────────────────────────────────────────────────

    "stock_info": """
        CREATE TABLE IF NOT EXISTS stock_info (
            code             VARCHAR NOT NULL PRIMARY KEY,
            name             VARCHAR,
            industry         VARCHAR,
            market_cap       DOUBLE,
            circulating_cap  DOUBLE,
            listing_date     DATE,
            market_type      VARCHAR,
            is_st            BOOLEAN DEFAULT FALSE,
            update_time      TIMESTAMP,
            is_delisted      BOOLEAN DEFAULT FALSE
        )
    """,
}


# ═══════════════════════════════════════════════════════════════
# VIEW_DDL：视图定义
# ═══════════════════════════════════════════════════════════════

VIEW_DDL: dict[str, str] = {

    "v_position_analysis": """
        CREATE OR REPLACE VIEW v_position_analysis AS
        SELECT
            p.*,
            s.industry,
            s.market,
            d.pe_ttm   AS buy_pe_ttm,
            d.pb       AS buy_pb,
            d.total_mv AS buy_total_mv,
            d.turn_rate AS buy_turn_rate
        FROM positions p
        LEFT JOIN dwd_stock_info  s ON p.code = s.symbol
        LEFT JOIN dwd_daily_basic d ON p.code = d.ts_code
                                    AND d.trade_date = p.buy_date
    """,

    "v_holding_summary": """
        CREATE OR REPLACE VIEW v_holding_summary AS
        SELECT
            strategy,
            COUNT(*)                    AS stock_count,
            SUM(shares * buy_price)     AS total_cost,
            SUM(shares * current_price) AS total_market_value,
            SUM(profit)                 AS total_unrealized_pnl,
            SUM(profit_loss)            AS total_realized_pnl
        FROM positions
        WHERE status = 'holding'
        GROUP BY strategy
    """,
}


# ═══════════════════════════════════════════════════════════════
# INDEX_DDL：索引定义
# ═══════════════════════════════════════════════════════════════

INDEX_DDL: list[str] = [
    "CREATE INDEX IF NOT EXISTS idx_dwd_daily_price_date     ON dwd_daily_price (trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_dwd_daily_price_code     ON dwd_daily_price (ts_code)",
    "CREATE INDEX IF NOT EXISTS idx_dwd_daily_basic_date     ON dwd_daily_basic (trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_dwd_daily_basic_code     ON dwd_daily_basic (ts_code)",
    "CREATE INDEX IF NOT EXISTS idx_dwd_index_daily_date     ON dwd_index_daily (trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_daily_signals_date       ON daily_signals (date)",
    "CREATE INDEX IF NOT EXISTS idx_daily_signals_observing  ON daily_signals (is_observing)",
    "CREATE INDEX IF NOT EXISTS idx_positions_status         ON positions (status)",
    "CREATE INDEX IF NOT EXISTS idx_positions_code           ON positions (code)",
    "CREATE INDEX IF NOT EXISTS idx_portfolio_daily_date     ON portfolio_daily (date)",
    "CREATE INDEX IF NOT EXISTS idx_portfolio_daily_strategy ON portfolio_daily (strategy)",
    "CREATE INDEX IF NOT EXISTS idx_portfolio_strat_date     ON portfolio_daily_strategy (date)",
    "CREATE INDEX IF NOT EXISTS idx_portfolio_strat_name     ON portfolio_daily_strategy (strategy)",
    "CREATE INDEX IF NOT EXISTS idx_pipeline_run_pid         ON data_pipeline_run (pipeline_id)",
    "CREATE INDEX IF NOT EXISTS idx_pipeline_run_status      ON data_pipeline_run (status)",
    "CREATE INDEX IF NOT EXISTS idx_backtest_trades_run      ON backtest_trades (run_id)",
    "CREATE INDEX IF NOT EXISTS idx_batch_results_batch      ON batch_backtest_results (batch_id)",
]


# ═══════════════════════════════════════════════════════════════
# 核心函数
# ═══════════════════════════════════════════════════════════════

def _parse_ddl_columns(ddl: str) -> dict[str, str]:
    """
    从 CREATE TABLE DDL 字符串中解析列名和类型。
    返回 {列名: 类型字符串}，忽略 PRIMARY KEY / UNIQUE / CONSTRAINT 行。
    """
    import re
    cols: dict[str, str] = {}
    # 取括号内的内容
    m = re.search(r'\(\s*(.*)\s*\)', ddl, re.DOTALL)
    if not m:
        return cols
    body = m.group(1)
    for line in body.splitlines():
        line = line.strip().rstrip(',').strip()
        if not line:
            continue
        # 跳过约束行
        upper = line.upper()
        if any(upper.startswith(k) for k in ('PRIMARY', 'UNIQUE', 'CONSTRAINT', 'CHECK', 'FOREIGN', '--')):
            continue
        # 去掉行内注释
        line = re.sub(r'--.*$', '', line).strip()
        if not line:
            continue
        # 支持带引号的列名，如 "buy_知行多空线"
        m2 = re.match(r'^"([^"]+)"\s+(\S+.*)', line)
        if m2:
            col_name = m2.group(1)
            col_type = m2.group(2).split()[0]
        else:
            parts = line.split()
            if len(parts) < 2:
                continue
            col_name = parts[0]
            col_type = parts[1]
        # 保留完整类型（含括号，如 DECIMAL(12,2)）
        rest = line[len(col_name):].strip()
        type_m = re.match(r'(\w+(?:\s*\([^)]*\))?)', rest)
        col_type = type_m.group(1) if type_m else col_type
        cols[col_name] = col_type
    return cols


def migrate_tables(conn: duckdb.DuckDBPyConnection, verbose: bool = True) -> None:
    """
    对已存在的表，按 TABLE_DDL 定义自动补齐缺失的列（幂等）。
    只新增列，不删除也不修改已有列的类型，保证数据安全。
    """
    added_any = False
    for table_name, ddl in TABLE_DDL.items():
        # 查实际列
        try:
            actual_cols = {
                row[0].lower()
                for row in conn.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_name = ?",
                    [table_name]
                ).fetchall()
            }
        except Exception:
            continue  # 表不存在，跳过（建表步骤会处理）

        expected_cols = _parse_ddl_columns(ddl)
        for col_name, col_type in expected_cols.items():
            if col_name.lower() not in actual_cols:
                try:
                    quoted = f'"{col_name}"' if any(c > '\x7f' or c == ' ' for c in col_name) else col_name
                    conn.execute(
                        f"ALTER TABLE {table_name} ADD COLUMN IF NOT EXISTS {quoted} {col_type}"
                    )
                    if verbose:
                        print(f"  + {table_name}.{col_name} ({col_type})")
                    added_any = True
                except Exception as e:
                    print(f"  ✗ migrate {table_name}.{col_name}: {e}")

    if not added_any and verbose:
        print("  所有表列已是最新，无需补列")


def init_database(db_path: str = _DEFAULT_DB, verbose: bool = True) -> duckdb.DuckDBPyConnection:
    """
    初始化数据库：创建所有表、视图、索引（幂等，可重复执行）。

    Returns:
        已连接的 DuckDBPyConnection（调用方负责关闭）
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if verbose:
        print(f"\n{'='*60}")
        print(f"  初始化数据库: {db_path}")
        print(f"  时间: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        print(f"{'='*60}")

    conn = duckdb.connect(str(db_path))

    if verbose:
        print(f"\n[1/3] 建表（共 {len(TABLE_DDL)} 张）...")
    ok, fail = 0, 0
    for table_name, ddl in TABLE_DDL.items():
        try:
            conn.execute(ddl)
            if verbose:
                print(f"  ✓ {table_name}")
            ok += 1
        except Exception as e:
            print(f"  ✗ {table_name}: {e}")
            fail += 1

    if verbose:
        print(f"\n[1b] 补齐缺失列（迁移已有旧表）...")
    migrate_tables(conn, verbose=verbose)

    if verbose:
        print(f"\n[2/3] 建视图（共 {len(VIEW_DDL)} 个）...")
    for view_name, ddl in VIEW_DDL.items():
        try:
            conn.execute(ddl)
            if verbose:
                print(f"  ✓ {view_name}")
        except Exception as e:
            print(f"  ✗ {view_name}: {e}")

    if verbose:
        print(f"\n[3/3] 建索引（共 {len(INDEX_DDL)} 个）...")
    for idx_sql in INDEX_DDL:
        try:
            conn.execute(idx_sql)
            if verbose:
                idx_name = idx_sql.split("INDEX IF NOT EXISTS ")[1].split(" ")[0]
                print(f"  ✓ {idx_name}")
        except Exception as e:
            print(f"  ✗ 索引: {e}")

    conn.commit()

    if verbose:
        print(f"\n{'='*60}")
        print(f"  完成！建表 {ok} 成功 / {fail} 失败")
        print(f"{'='*60}\n")

    return conn


def verify_database(db_path: str = _DEFAULT_DB):
    """验证数据库各表存在并打印行数"""
    conn = duckdb.connect(db_path, read_only=True)
    try:
        print(f"\n{'='*60}")
        print(f"  {'表名':<42} {'行数':>10}")
        print(f"  {'-'*42} {'-'*10}")
        for table_name in TABLE_DDL:
            try:
                cnt = conn.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
                flag = "✓" if cnt > 0 else "○"
                print(f"  {flag} {table_name:<40} {cnt:>10,}")
            except Exception:
                print(f"  ✗ {table_name:<40} {'(不存在)':>10}")
        print(f"\n  视图:")
        for view_name in VIEW_DDL:
            try:
                conn.execute(f"SELECT 1 FROM {view_name} LIMIT 0")
                print(f"  ✓ {view_name}")
            except Exception:
                print(f"  ✗ {view_name}")
        print()
    finally:
        conn.close()


def drop_and_recreate(db_path: str = _DEFAULT_DB):
    """危险：删除所有表后重建（需二次确认）"""
    confirm = input(
        f"\n⚠️  警告：将删除 {db_path} 中的所有表和数据！\n"
        "请输入 'YES' 确认: "
    ).strip()
    if confirm != "YES":
        print("已取消。")
        return

    conn = duckdb.connect(db_path)
    try:
        for view_name in VIEW_DDL:
            try:
                conn.execute(f"DROP VIEW IF EXISTS {view_name}")
            except Exception:
                pass
        for table_name in TABLE_DDL:
            try:
                conn.execute(f"DROP TABLE IF EXISTS {table_name}")
            except Exception:
                pass
        conn.commit()
    finally:
        conn.close()

    print("重建中...")
    conn = init_database(db_path)
    conn.close()


# ═══════════════════════════════════════════════════════════════
# CLI
# ═══════════════════════════════════════════════════════════════

def main():
    parser = argparse.ArgumentParser(
        description="数据库初始化工具（幂等，可重复执行）",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  python scripts/init_database.py
  python scripts/init_database.py --verify
  python scripts/init_database.py --list
  python scripts/init_database.py --drop-recreate
        """
    )
    parser.add_argument("--db", default=_DEFAULT_DB)
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--drop-recreate", action="store_true")
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args()

    if args.list:
        print(f"\n表（共 {len(TABLE_DDL)} 张）:")
        for name in TABLE_DDL:
            print(f"  - {name}")
        print(f"\n视图（共 {len(VIEW_DDL)} 个）:")
        for name in VIEW_DDL:
            print(f"  - {name}")
        return

    if args.drop_recreate:
        drop_and_recreate(args.db)
        return

    conn = init_database(args.db, verbose=not args.quiet)
    conn.close()

    if args.verify:
        verify_database(args.db)


if __name__ == "__main__":
    main()