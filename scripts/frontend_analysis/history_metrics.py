"""
历史交易综合分析模块 - history_metrics.py

从 DuckDB 数据库读取持仓记录，计算综合绩效指标：
- 核心绩效：总盈亏、累计收益率、年化收益率、夏普/索提诺/卡玛比率
- 风险指标：最大回撤、回撤持续时间、波动率
- 交易质量：胜率、盈亏比、期望值、期望值(R)
- 多维分析：按信号类型、行业、市值分组
- 时序分布：持仓时间分布、月度收益趋势

数据来源:
  - positions           : 交易记录主表
  - portfolio_daily     : 每日净值 (用于精确净值曲线指标)
  - dwd_stock_info      : 行业信息
  - dwd_daily_basic     : 市值数据 (用于市值分组)
"""
import os
import sys
from datetime import datetime, date
from typing import Optional, Dict, Any, List

import numpy as np
import pandas as pd
import duckdb

# ── 项目路径 ──────────────────────────────────────────────────────────────
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
DB_PATH = os.path.join(project_root, 'data', 'Astock3.duckdb')

INITIAL_CAPITAL = 500_000.0
RISK_PER_TRADE_PCT = 0.03   # 每笔止损3%，用于计算 R 期望值
TRADING_DAYS_PER_YEAR = 252


# ── 工具函数 ──────────────────────────────────────────────────────────────

def _get_conn(read_only: bool = True) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(DB_PATH, read_only=read_only)


def _table_exists(conn: duckdb.DuckDBPyConnection, table: str) -> bool:
    try:
        result = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
            [table]
        ).fetchone()
        return bool(result and result[0] > 0)
    except Exception:
        return False


def _safe_float(val, default=0.0) -> float:
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return default
    try:
        return float(val)
    except Exception:
        return default


# ── 核心计算 ──────────────────────────────────────────────────────────────

def _compute_summary(trades_df: pd.DataFrame, portfolio_df: Optional[pd.DataFrame]) -> Dict[str, Any]:
    """计算顶层汇总指标"""
    empty = {
        'total_trades': 0, 'total_profit': 0.0,
        'win_rate': 0.0, 'avg_holding_days': 0.0,
        'annualized_return': 0.0, 'cumulative_return': 0.0,
        'volatility': 0.0, 'sharpe_ratio': 0.0, 'sortino_ratio': 0.0,
        'max_drawdown': 0.0, 'max_drawdown_duration': 0,
        'calmar_ratio': 0.0, 'profit_loss_ratio': 0.0,
        'expectancy': 0.0, 'expectancy_r': 0.0,
        'best_trade': 0.0, 'worst_trade': 0.0,
        'avg_profit': 0.0, 'avg_loss': 0.0,
        'avg_drawdown': 0.0,
    }

    if trades_df.empty:
        return empty

    sold = trades_df[trades_df['status'] == 'sold'].copy() if 'status' in trades_df.columns else trades_df.copy()
    if sold.empty:
        return empty

    # ── 基础指标 ─────────────────────────────────────────────────────────
    total_trades = len(sold)
    total_profit = _safe_float(sold['profit_loss'].sum())
    winners = sold[sold['profit_loss'] > 0]
    losers  = sold[sold['profit_loss'] <= 0]
    win_count = len(winners)
    win_rate  = win_count / total_trades * 100 if total_trades > 0 else 0.0

    avg_profit = _safe_float(winners['profit_loss'].mean()) if not winners.empty else 0.0
    avg_loss   = _safe_float(abs(losers['profit_loss'].mean())) if not losers.empty else 0.0
    profit_loss_ratio = avg_profit / avg_loss if avg_loss > 0 else 0.0

    best_trade  = _safe_float(sold['profit_loss'].max())
    worst_trade = _safe_float(sold['profit_loss'].min())

    # 期望值（金额）
    expectancy = total_profit / total_trades if total_trades > 0 else 0.0

    # 期望值(R) = win_rate × avg_win_R - loss_rate × 1
    # 其中 avg_win_R = avg_profit / risk_per_trade
    risk_per_trade = INITIAL_CAPITAL * RISK_PER_TRADE_PCT  # 简化：以初始资金×3% 作为每笔风险
    avg_win_r = avg_profit / risk_per_trade if risk_per_trade > 0 else 0.0
    win_r_pct  = win_count / total_trades if total_trades > 0 else 0.0
    loss_r_pct = 1 - win_r_pct
    expectancy_r = win_r_pct * avg_win_r - loss_r_pct * 1.0

    # 平均持仓天数
    avg_holding_days = 0.0
    if 'buy_date' in sold.columns and 'sell_date' in sold.columns:
        try:
            buy_dates  = pd.to_datetime(sold['buy_date'],  errors='coerce')
            sell_dates = pd.to_datetime(sold['sell_date'], errors='coerce')
            days = (sell_dates - buy_dates).dt.days.dropna()
            avg_holding_days = _safe_float(days.mean())
        except Exception:
            pass

    # ── 净值曲线类指标（优先从 portfolio_daily 读取）─────────────────────
    cum_return   = 0.0
    ann_return   = 0.0
    volatility   = 0.0
    sharpe       = 0.0
    sortino      = 0.0
    max_dd       = 0.0
    max_dd_dur   = 0
    calmar       = 0.0
    avg_drawdown = 0.0

    if portfolio_df is not None and not portfolio_df.empty and 'total_value' in portfolio_df.columns:
        values = portfolio_df['total_value'].dropna().values.astype(float)
        if len(values) >= 2:
            initial_v = values[0]
            final_v   = values[-1]
            trading_days = len(values)

            cum_return = (final_v - initial_v) / initial_v * 100 if initial_v > 0 else 0.0
            years = trading_days / TRADING_DAYS_PER_YEAR
            ann_return = ((final_v / initial_v) ** (1 / years) - 1) * 100 if years > 0 and initial_v > 0 else 0.0

            daily_rets = np.diff(values) / values[:-1]
            volatility = float(np.std(daily_rets) * np.sqrt(TRADING_DAYS_PER_YEAR) * 100)

            ann_ret_dec = ann_return / 100
            vol_dec     = volatility / 100
            sharpe = ann_ret_dec / vol_dec if vol_dec > 0 else 0.0

            down_rets = daily_rets[daily_rets < 0]
            down_std  = np.std(down_rets) * np.sqrt(TRADING_DAYS_PER_YEAR) if len(down_rets) > 0 else 0.0
            sortino = ann_ret_dec / down_std if down_std > 0 else 0.0

            # 最大回撤
            peak = values[0]
            dd_list = []
            dd_dur_cur = 0
            max_dd_dur_cur = 0
            in_dd = False
            for v in values:
                if v > peak:
                    peak = v
                    dd_dur_cur = 0
                    in_dd = False
                dd = (peak - v) / peak * 100 if peak > 0 else 0.0
                dd_list.append(dd)
                if dd > 0:
                    dd_dur_cur += 1
                    max_dd_dur_cur = max(max_dd_dur_cur, dd_dur_cur)

            max_dd   = float(max(dd_list)) if dd_list else 0.0
            max_dd_dur = max_dd_dur_cur
            avg_drawdown = float(np.mean(dd_list)) if dd_list else 0.0
            calmar = ann_ret_dec / (max_dd / 100) if max_dd > 0 else 0.0
    else:
        # 从 trades 粗估
        cum_return = total_profit / INITIAL_CAPITAL * 100

    return {
        'total_trades':          total_trades,
        'total_profit':          round(total_profit, 2),
        'win_rate':              round(win_rate, 2),
        'avg_holding_days':      round(avg_holding_days, 1),
        'annualized_return':     round(ann_return, 2),
        'cumulative_return':     round(cum_return, 2),
        'volatility':            round(volatility, 2),
        'sharpe_ratio':          round(sharpe, 3),
        'sortino_ratio':         round(sortino, 3),
        'max_drawdown':          round(max_dd, 2),
        'max_drawdown_duration': int(max_dd_dur),
        'calmar_ratio':          round(calmar, 3),
        'profit_loss_ratio':     round(profit_loss_ratio, 3),
        'expectancy':            round(expectancy, 2),
        'expectancy_r':          round(expectancy_r, 4),
        'best_trade':            round(best_trade, 2),
        'worst_trade':           round(worst_trade, 2),
        'avg_profit':            round(avg_profit, 2),
        'avg_loss':              round(avg_loss, 2),
        'avg_drawdown':          round(avg_drawdown, 2),
    }


def _compute_by_signal(trades_df: pd.DataFrame) -> List[Dict]:
    """按买入信号类型分析"""
    if trades_df.empty or 'strategy' not in trades_df.columns:
        return []

    sold = trades_df[trades_df.get('status', pd.Series(['sold'] * len(trades_df))) == 'sold'].copy()
    if sold.empty:
        return []

    results = []
    for strategy, group in sold.groupby('strategy'):
        if not strategy:
            strategy = '未知'
        win_group = group[group['profit_loss'] > 0]
        hold_days = 0.0
        if 'buy_date' in group.columns and 'sell_date' in group.columns:
            try:
                days = (pd.to_datetime(group['sell_date'], errors='coerce') -
                        pd.to_datetime(group['buy_date'],  errors='coerce')).dt.days.dropna()
                hold_days = float(days.mean()) if len(days) > 0 else 0.0
            except Exception:
                pass

        total_profit = float(group['profit_loss'].sum())
        trade_count  = len(group)
        results.append({
            'signal_name':         str(strategy),
            'signal_type':         'buy',
            'trade_count':         trade_count,
            'win_count':           len(win_group),
            'win_rate':            round(len(win_group) / trade_count * 100, 1) if trade_count > 0 else 0.0,
            'total_profit':        round(total_profit, 2),
            'avg_profit_per_trade':round(total_profit / trade_count, 2) if trade_count > 0 else 0.0,
            'avg_holding_days':    round(hold_days, 1),
        })

    results.sort(key=lambda x: x['total_profit'], reverse=True)
    return results


def _compute_by_industry(trades_df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> List[Dict]:
    """按行业分析"""
    if trades_df.empty:
        return []

    sold = trades_df[trades_df.get('status', pd.Series(['sold'] * len(trades_df))) == 'sold'].copy()
    if sold.empty:
        return []

    # 尝试关联行业信息
    if _table_exists(conn, 'dwd_stock_info') and 'code' in sold.columns:
        try:
            industry_df = conn.execute(
                "SELECT symbol, industry FROM dwd_stock_info"
            ).fetchdf()
            sold = sold.merge(industry_df, left_on='code', right_on='symbol', how='left')
        except Exception:
            pass

    if 'industry' not in sold.columns:
        sold['industry'] = '未知'
    sold['industry'] = sold['industry'].fillna('未知')

    results = []
    for industry, group in sold.groupby('industry'):
        win_group  = group[group['profit_loss'] > 0]
        trade_count = len(group)
        results.append({
            'industry':    str(industry),
            'trade_count': trade_count,
            'win_count':   len(win_group),
            'win_rate':    round(len(win_group) / trade_count * 100, 1) if trade_count > 0 else 0.0,
            'total_profit':round(float(group['profit_loss'].sum()), 2),
            'avg_holding_days': 0.0,
        })

    results.sort(key=lambda x: x['total_profit'], reverse=True)
    return results


def _compute_by_market_cap(trades_df: pd.DataFrame, conn: duckdb.DuckDBPyConnection) -> List[Dict]:
    """按市值分组分析（小/中/大盘）"""
    GROUPS = [
        ('小盘', 'small',  0,          5e9),
        ('中盘', 'mid',    5e9,        20e9),
        ('大盘', 'large',  20e9,       100e9),
        ('超大', 'xlarge', 100e9,      float('inf')),
    ]

    if trades_df.empty:
        return []

    sold = trades_df[trades_df.get('status', pd.Series(['sold'] * len(trades_df))) == 'sold'].copy()
    if sold.empty:
        return []

    # 尝试关联市值
    total_mv_col = None
    if _table_exists(conn, 'dwd_daily_basic') and 'code' in sold.columns and 'buy_date' in sold.columns:
        try:
            mv_df = conn.execute("""
                SELECT ts_code, trade_date, total_mv
                FROM dwd_daily_basic
                WHERE total_mv IS NOT NULL
            """).fetchdf()
            # ts_code 格式 600000.SH，code 格式 600000
            mv_df['symbol'] = mv_df['ts_code'].str[:6]
            mv_df['trade_date'] = pd.to_datetime(mv_df['trade_date'], errors='coerce').dt.strftime('%Y-%m-%d')
            sold['buy_date_str'] = pd.to_datetime(sold['buy_date'], errors='coerce').dt.strftime('%Y-%m-%d')
            merged = sold.merge(
                mv_df[['symbol', 'trade_date', 'total_mv']],
                left_on=['code', 'buy_date_str'],
                right_on=['symbol', 'trade_date'],
                how='left'
            )
            if 'total_mv' in merged.columns:
                sold['total_mv'] = merged['total_mv'].values
                total_mv_col = 'total_mv'
        except Exception:
            pass

    results = []
    for group_name, group_code, low, high in GROUPS:
        if total_mv_col and total_mv_col in sold.columns:
            # total_mv 单位：万元
            mask = (sold[total_mv_col] * 10000 >= low) & (sold[total_mv_col] * 10000 < high)
            group = sold[mask]
        else:
            group = pd.DataFrame()  # 无市值数据时，分组为空

        win_group   = group[group['profit_loss'] > 0] if not group.empty else pd.DataFrame()
        trade_count = len(group)
        results.append({
            'group':       group_name,
            'group_code':  group_code,
            'trade_count': trade_count,
            'win_count':   len(win_group),
            'win_rate':    round(len(win_group) / trade_count * 100, 1) if trade_count > 0 else 0.0,
            'total_profit':round(float(group['profit_loss'].sum()), 2) if not group.empty else 0.0,
        })

    return results


def _compute_holding_distribution(trades_df: pd.DataFrame) -> Dict[str, int]:
    """持仓时间分布"""
    dist = {'0-5天': 0, '6-10天': 0, '11-20天': 0, '21-30天': 0, '30天以上': 0}

    if trades_df.empty or 'buy_date' not in trades_df.columns or 'sell_date' not in trades_df.columns:
        return dist

    sold = trades_df[trades_df.get('status', pd.Series(['sold'] * len(trades_df))) == 'sold'].copy()
    if sold.empty:
        return dist

    try:
        days = (pd.to_datetime(sold['sell_date'], errors='coerce') -
                pd.to_datetime(sold['buy_date'],  errors='coerce')).dt.days.dropna()
        for d in days:
            if d <= 5:
                dist['0-5天']   += 1
            elif d <= 10:
                dist['6-10天']  += 1
            elif d <= 20:
                dist['11-20天'] += 1
            elif d <= 30:
                dist['21-30天'] += 1
            else:
                dist['30天以上'] += 1
    except Exception:
        pass

    return dist


def _compute_monthly_returns(trades_df: pd.DataFrame) -> List[Dict]:
    """月度收益趋势"""
    if trades_df.empty or 'sell_date' not in trades_df.columns:
        return []

    sold = trades_df[trades_df.get('status', pd.Series(['sold'] * len(trades_df))) == 'sold'].copy()
    if sold.empty:
        return []

    try:
        sold['sell_month'] = pd.to_datetime(sold['sell_date'], errors='coerce').dt.strftime('%Y-%m')
        sold = sold.dropna(subset=['sell_month'])
        results = []
        for month, group in sorted(sold.groupby('sell_month')):
            win_g = group[group['profit_loss'] > 0]
            results.append({
                'month':       month,
                'trade_count': len(group),
                'profit':      round(float(group['profit_loss'].sum()), 2),
                'win_rate':    round(len(win_g) / len(group) * 100, 1) if len(group) > 0 else 0.0,
            })
        return results
    except Exception:
        return []


# ── 公开接口 ──────────────────────────────────────────────────────────────

def calculate_all_metrics() -> Dict[str, Any]:
    """
    计算所有历史交易分析指标。
    被 dashboard/app.py 的 /api/history/analysis 调用。
    """
    conn = _get_conn(read_only=True)
    try:
        # ── 读取交易记录 ──────────────────────────────────────────────────
        trades_df = pd.DataFrame()
        if _table_exists(conn, 'positions'):
            try:
                trades_df = conn.execute("""
                    SELECT
                        code, name, strategy,
                        buy_date, sell_date,
                        buy_price, sell_price, shares,
                        profit_loss, profit_pct,
                        stop_loss_pct,
                        status
                    FROM positions
                    WHERE status = 'sold'
                      AND profit_loss IS NOT NULL
                """).fetchdf()
            except Exception as e:
                print(f"[history_metrics] 读取 positions 失败: {e}")

        # ── 读取净值曲线 ──────────────────────────────────────────────────
        portfolio_df = None
        if _table_exists(conn, 'portfolio_daily'):
            try:
                portfolio_df = conn.execute("""
                    SELECT date, total_value
                    FROM portfolio_daily
                    WHERE strategy = 'main'
                    ORDER BY date
                """).fetchdf()
            except Exception as e:
                print(f"[history_metrics] 读取 portfolio_daily 失败: {e}")

        # ── 计算各维度指标 ────────────────────────────────────────────────
        summary          = _compute_summary(trades_df, portfolio_df)
        by_signal_type   = _compute_by_signal(trades_df)
        by_industry      = _compute_by_industry(trades_df, conn)
        by_market_cap    = _compute_by_market_cap(trades_df, conn)
        holding_dist     = _compute_holding_distribution(trades_df)
        monthly_returns  = _compute_monthly_returns(trades_df)

        return {
            'summary':                    summary,
            'by_signal_type':             by_signal_type,
            'by_industry':                by_industry,
            'by_market_cap':              by_market_cap,
            'holding_period_distribution':holding_dist,
            'monthly_returns':            monthly_returns,
        }

    finally:
        conn.close()
