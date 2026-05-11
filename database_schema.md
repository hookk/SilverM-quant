# Astock3 数据库表结构文档

**表数量**: 39
**视图数量**: 2

---

## 表 (Tables)

### agent_analysis_results

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| run_id | VARCHAR | 是 |  |
| symbol | VARCHAR | 否 |  |
| trade_date | VARCHAR | 否 |  |
| result_json | VARCHAR | 否 |  |
| created_at | TIMESTAMP | 否 |  |

### backtest_daily_pnl

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| run_id | VARCHAR | 是 |  |
| date | DATE | 是 |  |
| pnl | DOUBLE | 否 |  |
| pnl_pct | DOUBLE | 否 |  |
| total_value | DOUBLE | 否 |  |
| positions | VARCHAR | 否 |  |

### backtest_performance

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| run_id | VARCHAR | 是 |  |
| total_return | DOUBLE | 否 |  |
| annual_return | DOUBLE | 否 |  |
| max_drawdown | DOUBLE | 否 |  |
| sharpe_ratio | DOUBLE | 否 |  |
| win_rate | DOUBLE | 否 |  |
| total_trades | INTEGER | 否 |  |
| avg_holding_days | DOUBLE | 否 |  |
| industry_analysis | VARCHAR | 否 |  |
| cap_group_analysis | VARCHAR | 否 |  |
| monthly_returns | VARCHAR | 否 |  |

### backtest_run

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| run_id | VARCHAR | 是 |  |
| strategy_name | VARCHAR | 否 |  |
| strategy_params | VARCHAR | 否 |  |
| start_date | DATE | 否 |  |
| end_date | DATE | 否 |  |
| universe | VARCHAR | 否 |  |
| benchmark | VARCHAR | 否 |  |
| initial_capital | DOUBLE | 否 |  |
| status | VARCHAR | 否 |  |
| created_at | TIMESTAMP | 否 |  |
| completed_at | TIMESTAMP | 否 |  |

### backtest_trades

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| id | INTEGER | 是 |  |
| run_id | VARCHAR | 是 |  |
| datetime | TIMESTAMP | 否 |  |
| code | VARCHAR | 否 |  |
| name | VARCHAR | 否 |  |
| action | VARCHAR | 否 |  |
| price | DOUBLE | 否 |  |
| size | INTEGER | 否 |  |
| amount | DOUBLE | 否 |  |
| commission | DOUBLE | 否 |  |
| industry | VARCHAR | 否 |  |
| market_cap_group | VARCHAR | 否 |  |

### batch_backtest_daily_pnl

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| batch_id | VARCHAR | 是 |  |
| date | DATE | 是 |  |
| total_value | DOUBLE | 否 |  |
| total_pnl | DOUBLE | 否 |  |
| total_pnl_pct | DOUBLE | 否 |  |
| cumulative_return | DOUBLE | 否 |  |
| drawdown | DOUBLE | 否 |  |
| positions | JSON | 否 |  |

### batch_backtest_params

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| id | BIGINT | 是 | nextval('seq_batch_backtest_params_id') |
| batch_id | VARCHAR | 否 |  |
| param_name | VARCHAR | 否 |  |
| param_values | JSON | 否 |  |
| results | JSON | 否 |  |
| created_at | TIMESTAMP | 否 | CURRENT_TIMESTAMP |

### batch_backtest_results

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| result_id | BIGINT | 是 | nextval('batch_backtest_results_seq') |
| batch_id | VARCHAR | 否 |  |
| stock_code | VARCHAR | 否 |  |
| stock_name | VARCHAR | 否 |  |
| status | VARCHAR | 否 |  |
| total_return | FLOAT | 否 |  |
| annualized_return | FLOAT | 否 |  |
| max_drawdown | FLOAT | 否 |  |
| sharpe_ratio | FLOAT | 否 |  |
| win_rate | FLOAT | 否 |  |
| total_trades | INTEGER | 否 |  |
| final_value | FLOAT | 否 |  |
| initial_cash | FLOAT | 否 |  |
| error_message | VARCHAR | 否 |  |
| completed_at | TIMESTAMP | 否 | CURRENT_TIMESTAMP |

### daily_basic

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| trade_date | DATE | 否 |  |
| ts_code | VARCHAR | 否 |  |
| close | DOUBLE | 否 |  |
| pe_ttm | DOUBLE | 否 |  |
| pe | DOUBLE | 否 |  |
| ps_ttm | DOUBLE | 否 |  |
| ps | DOUBLE | 否 |  |
| pcf | DOUBLE | 否 |  |
| pb | DOUBLE | 否 |  |
| total_mv | DOUBLE | 否 |  |
| circ_mv | DOUBLE | 否 |  |
| amount | DOUBLE | 否 |  |
| turn_rate | DOUBLE | 否 |  |
| data_source | VARCHAR | 否 |  |

### daily_signals

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| date | DATE | 是 |  |
| code | VARCHAR | 是 |  |
| name | VARCHAR | 否 |  |
| open | DOUBLE | 否 |  |
| high | DOUBLE | 否 |  |
| low | DOUBLE | 否 |  |
| close | DOUBLE | 否 |  |
| volume | DOUBLE | 否 |  |
| prev_close | DOUBLE | 否 |  |
| change_pct | DOUBLE | 否 |  |
| score_b1 | DOUBLE | 否 |  |
| score_b2 | DOUBLE | 否 |  |
| score_blk | DOUBLE | 否 |  |
| score_dl | DOUBLE | 否 |  |
| score_dz30 | DOUBLE | 否 |  |
| score_scb | DOUBLE | 否 |  |
| score_blkB2 | DOUBLE | 否 |  |
| signal_buy_b1 | BOOLEAN | 否 |  |
| signal_buy_b2 | BOOLEAN | 否 |  |
| signal_buy_blk | BOOLEAN | 否 |  |
| signal_buy_dl | BOOLEAN | 否 |  |
| signal_buy_dz30 | BOOLEAN | 否 |  |
| signal_buy_scb | BOOLEAN | 否 |  |
| signal_buy_blkB2 | BOOLEAN | 否 |  |
| signal_sell_b1 | BOOLEAN | 否 |  |
| signal_sell_b2 | BOOLEAN | 否 |  |
| signal_sell_blk | BOOLEAN | 否 |  |
| signal_sell_dl | BOOLEAN | 否 |  |
| signal_sell_dz30 | BOOLEAN | 否 |  |
| signal_sell_scb | BOOLEAN | 否 |  |
| signal_sell_blkB2 | BOOLEAN | 否 |  |
| score_s1 | DOUBLE | 否 |  |
| signal_s1_full | BOOLEAN | 否 |  |
| signal_s1_half | BOOLEAN | 否 |  |
| signal_跌破多空线 | BOOLEAN | 否 |  |
| signal_止损 | BOOLEAN | 否 |  |
| indicators | JSON | 否 |  |
| is_observing | BOOLEAN | 否 | CAST('f' AS BOOLEAN) |

### data_pipeline_run

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| id | INTEGER | 是 |  |
| pipeline_id | VARCHAR | 否 |  |
| pipeline_name | VARCHAR | 否 |  |
| step_name | VARCHAR | 否 |  |
| step_order | INTEGER | 否 |  |
| created_at | TIMESTAMP | 否 |  |
| started_at | TIMESTAMP | 否 |  |
| completed_at | TIMESTAMP | 否 |  |
| duration_sec | FLOAT | 否 |  |
| params | JSON | 否 |  |
| status | VARCHAR | 否 |  |
| records_count | INTEGER | 否 |  |
| error_message | VARCHAR | 否 |  |
| depends_on | VARCHAR | 否 |  |
| dependency_met | BOOLEAN | 否 |  |

### dwd_adj_factor

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| ts_code | VARCHAR | 否 |  |
| trade_date | DATE | 否 |  |
| adj_factor | DOUBLE | 否 |  |
| data_source | VARCHAR | 否 | 'tushare' |

### dwd_balancesheet

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| ts_code | VARCHAR | 否 |  |
| ann_date | DATE | 否 |  |
| f_ann_date | DATE | 否 |  |
| end_date | DATE | 否 |  |
| report_type | VARCHAR | 否 |  |
| comp_type | VARCHAR | 否 |  |
| total_assets | DOUBLE | 否 |  |
| total_liab | DOUBLE | 否 |  |
| total_hldr_eqy_excl_min_int | DOUBLE | 否 |  |
| hldr_eqy_excl_min_int | DOUBLE | 否 |  |
| minority_int | DOUBLE | 否 |  |
| total_liab_ht_holder | DOUBLE | 否 |  |
| notes_payable | DOUBLE | 否 |  |
| accounts_payable | DOUBLE | 否 |  |
| advance_receipts | DOUBLE | 否 |  |
| total_current_assets | DOUBLE | 否 |  |
| total_non_current_assets | DOUBLE | 否 |  |
| fixed_assets | DOUBLE | 否 |  |
| cip | DOUBLE | 否 |  |
| total_current_liab | DOUBLE | 否 |  |
| total_non_current_liab | DOUBLE | 否 |  |
| lt_borrow | DOUBLE | 否 |  |
| bonds_payable | DOUBLE | 否 |  |
| data_source | VARCHAR | 否 | 'tushare' |

### dwd_cashflow

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| ts_code | VARCHAR | 否 |  |
| ann_date | DATE | 否 |  |
| f_ann_date | DATE | 否 |  |
| end_date | DATE | 否 |  |
| report_type | VARCHAR | 否 |  |
| comp_type | VARCHAR | 否 |  |
| net_profit | DOUBLE | 否 |  |
| fin_exp | DOUBLE | 否 |  |
| c_fr_oper_a | DOUBLE | 否 |  |
| c_fr_oper_a_op_ttp | DOUBLE | 否 |  |
| c_inf_fr_oper_a | DOUBLE | 否 |  |
| c_paid_goods_sold | DOUBLE | 否 |  |
| c_paid_to_for_employees | DOUBLE | 否 |  |
| c_paid_taxes | DOUBLE | 否 |  |
| other_cash_fr_oper_a | DOUBLE | 否 |  |
| n_cashflow_act | DOUBLE | 否 |  |
| c_fr_oper_b | DOUBLE | 否 |  |
| c_fr_inv_a | DOUBLE | 否 |  |
| c_to_inv_a | DOUBLE | 否 |  |
| c_fr_fin_a | DOUBLE | 否 |  |
| c_to_fin_a | DOUBLE | 否 |  |
| n_cash_in_fin_a | DOUBLE | 否 |  |
| n_cash_in_op_b | DOUBLE | 否 |  |
| n_cash_out_inv_b | DOUBLE | 否 |  |
| n_cash_out_fin_b | DOUBLE | 否 |  |
| n_cash_in_op_c | DOUBLE | 否 |  |
| n_cash_out_inv_c | DOUBLE | 否 |  |
| n_cash_out_fin_c | DOUBLE | 否 |  |
| end_cash | DOUBLE | 否 |  |
| cap_crisis_shrg | DOUBLE | 否 |  |
| data_source | VARCHAR | 否 | 'tushare' |

### dwd_daily_basic

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| trade_date | DATE | 是 |  |
| ts_code | VARCHAR | 是 |  |
| close | DOUBLE | 否 |  |
| pe_ttm | DOUBLE | 否 |  |
| pe | DOUBLE | 否 |  |
| ps_ttm | DOUBLE | 否 |  |
| ps | DOUBLE | 否 |  |
| pcf | DOUBLE | 否 |  |
| pb | DOUBLE | 否 |  |
| total_mv | DOUBLE | 否 |  |
| circ_mv | DOUBLE | 否 |  |
| amount | DOUBLE | 否 |  |
| turn_rate | DOUBLE | 否 |  |
| data_source | VARCHAR | 否 | 'tushare' |

### dwd_daily_price

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| trade_date | DATE | 是 |  |
| ts_code | VARCHAR | 是 |  |
| open | DOUBLE | 否 |  |
| high | DOUBLE | 否 |  |
| low | DOUBLE | 否 |  |
| close | DOUBLE | 否 |  |
| vol | BIGINT | 否 |  |
| amount | DOUBLE | 否 |  |
| pct_chg | DOUBLE | 否 |  |
| data_source | VARCHAR | 否 | 'tushare' |

### dwd_daily_price_hfq

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| ts_code | VARCHAR | 是 |  |
| trade_date | DATE | 是 |  |
| open | DOUBLE | 否 |  |
| high | DOUBLE | 否 |  |
| low | DOUBLE | 否 |  |
| close | DOUBLE | 否 |  |
| vol | BIGINT | 否 |  |
| amount | DOUBLE | 否 |  |
| pct_chg | DOUBLE | 否 |  |
| adj_factor | DOUBLE | 否 |  |
| data_source | VARCHAR | 否 | 'tushare' |

### dwd_daily_price_qfq

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| ts_code | VARCHAR | 是 |  |
| trade_date | DATE | 是 |  |
| open | DOUBLE | 否 |  |
| high | DOUBLE | 否 |  |
| low | DOUBLE | 否 |  |
| close | DOUBLE | 否 |  |
| vol | BIGINT | 否 |  |
| amount | DOUBLE | 否 |  |
| pct_chg | DOUBLE | 否 |  |
| adj_factor | DOUBLE | 否 |  |
| data_source | VARCHAR | 否 | 'tushare' |

### dwd_income

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| ts_code | VARCHAR | 否 |  |
| ann_date | DATE | 否 |  |
| f_ann_date | DATE | 否 |  |
| end_date | DATE | 否 |  |
| report_type | VARCHAR | 否 |  |
| comp_type | VARCHAR | 否 |  |
| basic_eps | DOUBLE | 否 |  |
| diluted_eps | DOUBLE | 否 |  |
| total_revenue | DOUBLE | 否 |  |
| revenue | DOUBLE | 否 |  |
| total_profit | DOUBLE | 否 |  |
| profit | DOUBLE | 否 |  |
| income_tax | DOUBLE | 否 |  |
| n_income | DOUBLE | 否 |  |
| n_income_attr_p | DOUBLE | 否 |  |
| total_cogs | DOUBLE | 否 |  |
| operate_profit | DOUBLE | 否 |  |
| invest_income | DOUBLE | 否 |  |
| non_op_income | DOUBLE | 否 |  |
| asset_impair_loss | DOUBLE | 否 |  |
| net_profit_with_non_recurring | DOUBLE | 否 |  |
| data_source | VARCHAR | 否 | 'tushare' |

### dwd_index_daily

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| index_code | VARCHAR | 是 |  |
| trade_date | DATE | 是 |  |
| open | DOUBLE | 否 |  |
| high | DOUBLE | 否 |  |
| low | DOUBLE | 否 |  |
| close | DOUBLE | 否 |  |
| pre_close | DOUBLE | 否 |  |
| change | DOUBLE | 否 |  |
| pct_change | DOUBLE | 否 |  |
| vol | BIGINT | 否 |  |
| amount | DOUBLE | 否 |  |
| data_source | VARCHAR | 否 | '''tushare''' |

### dwd_stock_info

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| ts_code | VARCHAR | 是 |  |
| symbol | VARCHAR | 否 |  |
| name | VARCHAR | 否 |  |
| area | VARCHAR | 否 |  |
| industry | VARCHAR | 否 |  |
| market | VARCHAR | 否 |  |
| list_date | DATE | 否 |  |
| is_hs | VARCHAR | 否 |  |
| act_name | VARCHAR | 否 |  |
| list_status | VARCHAR | 否 |  |
| delist_date | DATE | 否 |  |
| data_source | VARCHAR | 否 | 'tushare' |

### dwd_trade_calendar

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| trade_date | DATE | 是 |  |
| exchange | VARCHAR | 是 |  |
| is_open | BOOLEAN | 否 |  |

### factor_data

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| date | DATE | 是 |  |
| code | VARCHAR | 是 |  |
| pe_ttm | FLOAT | 否 |  |
| pb | FLOAT | 否 |  |
| ps_ttm | FLOAT | 否 |  |
| pcf_ttm | FLOAT | 否 |  |
| dividend_yield | FLOAT | 否 |  |
| roe | FLOAT | 否 |  |
| roa | FLOAT | 否 |  |
| gross_margin | FLOAT | 否 |  |
| net_margin | FLOAT | 否 |  |
| debt_to_asset | FLOAT | 否 |  |
| revenue_growth_yoy | FLOAT | 否 |  |
| profit_growth_yoy | FLOAT | 否 |  |
| revenue_growth_qoq | FLOAT | 否 |  |
| profit_growth_qoq | FLOAT | 否 |  |
| macd_dif | FLOAT | 否 |  |
| macd_dea | FLOAT | 否 |  |
| macd_histogram | FLOAT | 否 |  |
| kdj_k | FLOAT | 否 |  |
| kdj_d | FLOAT | 否 |  |
| kdj_j | FLOAT | 否 |  |
| rsi_6 | FLOAT | 否 |  |
| rsi_12 | FLOAT | 否 |  |
| rsi_24 | FLOAT | 否 |  |
| boll_upper | FLOAT | 否 |  |
| boll_mid | FLOAT | 否 |  |
| boll_lower | FLOAT | 否 |  |
| ma_5 | FLOAT | 否 |  |
| ma_10 | FLOAT | 否 |  |
| ma_20 | FLOAT | 否 |  |
| ma_60 | FLOAT | 否 |  |
| volatility_20d | FLOAT | 否 |  |
| turnover_20d | FLOAT | 否 |  |
| volume_ratio | FLOAT | 否 |  |
| price_momentum_20d | FLOAT | 否 |  |
| price_momentum_60d | FLOAT | 否 |  |
| custom_factor_1 | FLOAT | 否 |  |
| custom_factor_2 | FLOAT | 否 |  |
| custom_factor_3 | FLOAT | 否 |  |
| custom_factor_4 | FLOAT | 否 |  |
| custom_factor_5 | FLOAT | 否 |  |
| update_time | TIMESTAMP | 否 | CURRENT_TIMESTAMP |

### factor_ic

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| date | DATE | 是 |  |
| factor_name | VARCHAR | 是 |  |
| ic | FLOAT | 否 |  |
| ic_rank | FLOAT | 否 |  |
| ir | FLOAT | 否 |  |
| ic_positive_ratio | FLOAT | 否 |  |
| update_time | TIMESTAMP | 否 | CURRENT_TIMESTAMP |

### factor_return

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| date | DATE | 是 |  |
| factor_name | VARCHAR | 是 |  |
| long_return | FLOAT | 否 |  |
| short_return | FLOAT | 否 |  |
| long_short_return | FLOAT | 否 |  |
| quantile_returns | JSON | 否 |  |
| update_time | TIMESTAMP | 否 | CURRENT_TIMESTAMP |

### pipeline_monitor_flag

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| id | INTEGER | 是 |  |
| date | VARCHAR | 否 |  |
| completed | BOOLEAN | 否 | CAST('f' AS BOOLEAN) |
| completed_at | TIMESTAMP | 否 |  |

### portfolio_daily

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| id | INTEGER | 是 |  |
| date | DATE | 否 |  |
| init_cash | DECIMAL(12,2) | 否 |  |
| position_cost | DECIMAL(12,2) | 否 |  |
| position_value | DECIMAL(12,2) | 否 |  |
| position_pnl | DECIMAL(12,2) | 否 |  |
| closed_pnl | DECIMAL(12,2) | 否 | 0 |
| total_pnl | DECIMAL(12,2) | 否 |  |
| available_cash | DECIMAL(12,2) | 否 |  |
| position_ratio | DECIMAL(5,2) | 否 |  |
| notes | VARCHAR | 否 |  |
| created_at | TIMESTAMP | 否 | CURRENT_TIMESTAMP |
| updated_at | TIMESTAMP | 否 | CURRENT_TIMESTAMP |
| total_value | DECIMAL(12,2) | 否 |  |

### portfolio_daily_strategy

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| id | INTEGER | 是 |  |
| date | DATE | 否 |  |
| strategy | VARCHAR | 否 |  |
| position_cost | DECIMAL(12,2) | 否 |  |
| position_value | DECIMAL(12,2) | 否 |  |
| position_pnl | DECIMAL(12,2) | 否 |  |
| closed_pnl | DECIMAL(12,2) | 否 | 0 |
| total_pnl | DECIMAL(12,2) | 否 |  |
| trade_count | INTEGER | 否 | 0 |
| notes | VARCHAR | 否 |  |
| created_at | TIMESTAMP | 否 | CURRENT_TIMESTAMP |

### positions

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| id | INTEGER | 是 |  |
| code | VARCHAR | 否 |  |
| name | VARCHAR | 否 |  |
| strategy | VARCHAR | 否 |  |
| signal_date | DATE | 否 |  |
| buy_date | DATE | 否 |  |
| shares | INTEGER | 否 |  |
| buy_price | DOUBLE | 否 |  |
| buy_change_pct | DOUBLE | 否 |  |
| buy_score_b1 | DOUBLE | 否 |  |
| buy_score_b2 | DOUBLE | 否 |  |
| buy_dif | DOUBLE | 否 |  |
| buy_j_value | DOUBLE | 否 |  |
| buy_知行短期趋势线 | DOUBLE | 否 |  |
| buy_知行多空线 | DOUBLE | 否 |  |
| current_price | DOUBLE | 否 |  |
| current_score_s1 | DOUBLE | 否 |  |
| current_跌破多空线 | BOOLEAN | 否 |  |
| stop_loss_pct | DOUBLE | 否 | 0.03 |
| status | VARCHAR | 否 | 'holding' |
| sell_date | DATE | 否 |  |
| sell_price | DOUBLE | 否 |  |
| sell_reason | VARCHAR | 否 |  |
| profit_loss | DOUBLE | 否 |  |
| profit_pct | DOUBLE | 否 |  |
| notes | VARCHAR | 否 |  |
| created_at | TIMESTAMP | 否 | CURRENT_TIMESTAMP |
| updated_at | TIMESTAMP | 否 | CURRENT_TIMESTAMP |

### signal_events

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| id | BIGINT | 是 | nextval('seq_signal_events_id') |
| date | DATE | 否 |  |
| code | VARCHAR | 否 |  |
| name | VARCHAR | 否 |  |
| signal_abbrev | VARCHAR | 否 |  |
| version | VARCHAR | 否 |  |
| signal_type | VARCHAR | 否 |  |
| score | DOUBLE | 否 |  |
| signal_field | VARCHAR | 否 |  |
| created_at | TIMESTAMP | 否 | CURRENT_TIMESTAMP |

### step_update_log

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| id | INTEGER | 是 |  |
| pipeline_id | VARCHAR | 否 |  |
| step_name | VARCHAR | 否 |  |
| update_type | VARCHAR | 否 |  |
| update_time | TIMESTAMP | 否 |  |
| start_time | TIMESTAMP | 否 |  |
| end_time | TIMESTAMP | 否 |  |
| duration_sec | FLOAT | 否 |  |
| expected_count | INTEGER | 否 |  |
| actual_count | INTEGER | 否 |  |
| is_success | BOOLEAN | 否 |  |
| error_message | VARCHAR | 否 |  |
| error_details | JSON | 否 |  |
| step_details | JSON | 否 |  |
| validation_results | JSON | 否 |  |
| check_time | TIMESTAMP | 否 | CURRENT_TIMESTAMP |

### stock_info

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| code | VARCHAR | 是 |  |
| name | VARCHAR | 否 |  |
| industry | VARCHAR | 否 |  |
| market_cap | DOUBLE | 否 |  |
| circulating_cap | DOUBLE | 否 |  |
| listing_date | DATE | 否 |  |
| market_type | VARCHAR | 否 |  |
| is_st | BOOLEAN | 否 |  |
| update_time | TIMESTAMP | 否 |  |
| is_delisted | BOOLEAN | 否 | CAST('f' AS BOOLEAN) |

### strategy_metadata

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| name | VARCHAR | 是 |  |
| signal_abbrev | VARCHAR | 否 |  |
| class_name | VARCHAR | 否 |  |
| description | VARCHAR | 否 |  |
| status | VARCHAR | 否 | 'draft' |
| current_version | VARCHAR | 否 |  |
| promotion_config | JSON | 否 |  |
| latest_backtest | JSON | 否 |  |
| created_at | TIMESTAMP | 否 | CURRENT_TIMESTAMP |
| updated_at | TIMESTAMP | 否 | CURRENT_TIMESTAMP |

### strategy_params

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| id | INTEGER | 是 |  |
| strategy_name | VARCHAR | 否 |  |
| param_name | VARCHAR | 否 |  |
| param_type | VARCHAR | 否 |  |
| default_value | JSON | 否 |  |
| current_value | JSON | 否 |  |
| description | VARCHAR | 否 |  |
| constraints | JSON | 否 |  |
| is_required | BOOLEAN | 否 | CAST('f' AS BOOLEAN) |
| updated_at | TIMESTAMP | 否 | CURRENT_TIMESTAMP |

### strategy_params_history

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| id | INTEGER | 是 |  |
| strategy_name | VARCHAR | 否 |  |
| param_name | VARCHAR | 否 |  |
| old_value | JSON | 否 |  |
| new_value | JSON | 否 |  |
| changed_at | TIMESTAMP | 否 | CURRENT_TIMESTAMP |
| changed_by | VARCHAR | 否 |  |

### strategy_registry

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| id | VARCHAR | 是 |  |
| name | VARCHAR | 否 |  |
| display_name | VARCHAR | 否 |  |
| class_path | VARCHAR | 否 |  |
| source_file | VARCHAR | 否 |  |
| description | VARCHAR | 否 |  |
| version | VARCHAR | 否 | '1.0.0' |
| author | VARCHAR | 否 |  |
| status | VARCHAR | 否 | 'active' |
| strategy_type | VARCHAR | 否 |  |
| threshold_required | BOOLEAN | 否 | CAST('f' AS BOOLEAN) |
| min_data_days | INTEGER | 否 | 0 |
| param_schema | JSON | 否 |  |
| created_at | TIMESTAMP | 否 | CURRENT_TIMESTAMP |
| updated_at | TIMESTAMP | 否 | CURRENT_TIMESTAMP |

### strategy_versions

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| id | INTEGER | 是 | nextval('seq_strategy_versions_id') |
| strategy_name | VARCHAR | 否 |  |
| signal_abbrev | VARCHAR | 否 |  |
| version | VARCHAR | 否 |  |
| backtest_metrics | JSON | 否 |  |
| backtest_params | JSON | 否 |  |
| run_id | VARCHAR | 否 |  |
| status | VARCHAR | 否 | 'tested' |
| promoted_at | TIMESTAMP | 否 |  |
| created_at | TIMESTAMP | 否 | CURRENT_TIMESTAMP |

### trade_audit_log

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| id | INTEGER | 是 |  |
| audit_date | DATE | 否 |  |
| check_item | VARCHAR | 否 |  |
| check_type | VARCHAR | 否 |  |
| severity | VARCHAR | 否 |  |
| status | VARCHAR | 否 |  |
| detail | VARCHAR | 否 |  |
| fix_action | VARCHAR | 否 |  |
| before_val | VARCHAR | 否 |  |
| after_val | VARCHAR | 否 |  |
| auditor | VARCHAR | 否 | 'audit_trade.py' |
| created_at | TIMESTAMP | 否 | CURRENT_TIMESTAMP |

### v_position_analysis

| 列名 | 类型 | 非空 | 默认值 |
|------|------|------|--------|
| id | INTEGER | 否 |  |
| code | VARCHAR | 否 |  |
| name | VARCHAR | 否 |  |
| strategy | VARCHAR | 否 |  |
| signal_date | DATE | 否 |  |
| buy_date | DATE | 否 |  |
| shares | INTEGER | 否 |  |
| buy_price | DOUBLE | 否 |  |
| buy_change_pct | DOUBLE | 否 |  |
| buy_score_b1 | DOUBLE | 否 |  |
| buy_score_b2 | DOUBLE | 否 |  |
| buy_dif | DOUBLE | 否 |  |
| buy_j_value | DOUBLE | 否 |  |
| buy_知行短期趋势线 | DOUBLE | 否 |  |
| buy_知行多空线 | DOUBLE | 否 |  |
| current_price | DOUBLE | 否 |  |
| current_score_s1 | DOUBLE | 否 |  |
| current_跌破多空线 | BOOLEAN | 否 |  |
| stop_loss_pct | DOUBLE | 否 |  |
| status | VARCHAR | 否 |  |
| sell_date | DATE | 否 |  |
| sell_price | DOUBLE | 否 |  |
| sell_reason | VARCHAR | 否 |  |
| profit_loss | DOUBLE | 否 |  |
| profit_pct | DOUBLE | 否 |  |
| notes | VARCHAR | 否 |  |
| created_at | TIMESTAMP | 否 |  |
| updated_at | TIMESTAMP | 否 |  |
| industry | VARCHAR | 否 |  |
| buy_pe | DOUBLE | 否 |  |
| buy_pb | DOUBLE | 否 |  |
| buy_turnover_rate | DOUBLE | 否 |  |

---

## 视图 (Views)

### daily_basic

| 列名 | 类型 |
|------|------|
| trade_date | DATE |
| ts_code | VARCHAR |
| close | DOUBLE |
| pe_ttm | DOUBLE |
| pe | DOUBLE |
| ps_ttm | DOUBLE |
| ps | DOUBLE |
| pcf | DOUBLE |
| pb | DOUBLE |
| total_mv | DOUBLE |
| circ_mv | DOUBLE |
| amount | DOUBLE |
| turn_rate | DOUBLE |
| data_source | VARCHAR |

### v_position_analysis

| 列名 | 类型 |
|------|------|
| id | INTEGER |
| code | VARCHAR |
| name | VARCHAR |
| strategy | VARCHAR |
| signal_date | DATE |
| buy_date | DATE |
| shares | INTEGER |
| buy_price | DOUBLE |
| buy_change_pct | DOUBLE |
| buy_score_b1 | DOUBLE |
| buy_score_b2 | DOUBLE |
| buy_dif | DOUBLE |
| buy_j_value | DOUBLE |
| buy_知行短期趋势线 | DOUBLE |
| buy_知行多空线 | DOUBLE |
| current_price | DOUBLE |
| current_score_s1 | DOUBLE |
| current_跌破多空线 | BOOLEAN |
| stop_loss_pct | DOUBLE |
| status | VARCHAR |
| sell_date | DATE |
| sell_price | DOUBLE |
| sell_reason | VARCHAR |
| profit_loss | DOUBLE |
| profit_pct | DOUBLE |
| notes | VARCHAR |
| created_at | TIMESTAMP |
| updated_at | TIMESTAMP |
| industry | VARCHAR |
| buy_pe | DOUBLE |
| buy_pb | DOUBLE |
| buy_turnover_rate | DOUBLE |
