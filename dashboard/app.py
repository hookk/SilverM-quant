import os
import sys

# 添加项目根目录到sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flask import Flask, render_template, jsonify, request, send_from_directory, g
from flask_cors import CORS
import duckdb
import pandas as pd
import numpy as np
from datetime import datetime, timedelta

# Agent API路由
from dashboard.agent_api import agent_bp
from dashboard.data_update_api import data_update_bp
from dashboard.backtest_api import backtest_bp

# Vue 前端 build 产物路径
FRONTEND_DIST = os.path.join(
    os.path.dirname(__file__),
    '..', 'frontend', 'dist'
)

app = Flask(__name__)
CORS(app)

# 注册蓝图
app.register_blueprint(agent_bp)
app.register_blueprint(data_update_bp)
app.register_blueprint(backtest_bp)

DB_PATH = os.path.join(os.path.dirname(__file__), '..', 'data', 'Astock3.duckdb')

# 策略ID到中文名称映射
STRATEGY_NAME_MAP = {
    'b1': 'B1策略',
    'b2': 'B2策略',
    'blk': 'BLK策略',
    'blkB2': 'BLKB2策略',
    'dl': 'DL策略',
    'dz30': 'DZ30策略',
    'scb': 'SCB策略',
}
SELL_STRATEGY_NAME_MAP = {
    's1_full': 'S1满仓信号',
    's1_half': 'S1半仓信号',
    '跌破多空线': '跌破多空线',
    '止损': '止损信号',
}

INITIAL_CAPITAL = 500000.0


# ─────────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────────

def get_db():
    """
    获取当前请求的 DuckDB 连接。

    [FIX] 原实现使用 read_only=True，当 fetcher_dwd.py 等模块以读写模式
    打开同一数据库文件时，DuckDB 会抛出：
        ConnectionException: Can't open a connection to same database file
        with a different configuration than existing connections
    修复：去掉 read_only=True，使用普通连接。
    Dashboard 本身只做 SELECT 查询，不会产生写操作。

    使用 Flask g 对象在同一请求内复用连接，避免每次查询都重新建立连接。
    连接在请求结束时由 close_db() 统一关闭。
    """
    if 'db' not in g:
        g.db = duckdb.connect(DB_PATH)
    return g.db


@app.teardown_appcontext
def close_db(exception=None):
    """请求结束时关闭数据库连接"""
    db = g.pop('db', None)
    if db is not None:
        try:
            db.close()
        except Exception:
            pass


def table_exists(db, table_name: str) -> bool:
    """检查表是否存在"""
    try:
        result = db.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name = ?",
            [table_name]
        ).fetchone()
        return result is not None and result[0] > 0
    except Exception:
        return False


def get_latest_trading_date():
    db = get_db()
    try:
        latest = db.execute("SELECT MAX(trade_date) FROM dwd_daily_price").fetchone()[0]
        if latest:
            return latest.strftime('%Y-%m-%d') if hasattr(latest, 'strftime') else str(latest)
    except Exception:
        pass
    return datetime.now().strftime('%Y-%m-%d')


def code_to_ts_code(code: str) -> str:
    """转换股票代码为tushare格式"""
    code = str(code)
    if code.startswith('6'):
        return f"{code}.SH"
    else:
        return f"{code}.SZ"


def clean_df_for_json(df):
    for col in df.columns:
        if df[col].dtype == 'object' or str(df[col].dtype).startswith('datetime'):
            df[col] = df[col].apply(
                lambda x: None if pd.isna(x) else (
                    x.strftime('%Y-%m-%d') if hasattr(x, 'strftime') else x
                )
            )
        elif pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].replace({np.nan: None})
    return df


def map_sell_reason(reason):
    """将卖出原因映射为可读信号名称"""
    if not reason:
        return '信号卖出'

    reason_map = {
        '止损': '止损信号',
        '跌破多空线': '跌破多空线信号',
        '多空线信号': '多空线信号',
        '趋势反转': '趋势反转信号',
        '止盈': '止盈信号',
        '仓位调整': '仓位调整信号',
    }

    for key, value in reason_map.items():
        if key in str(reason):
            return value

    return f'信号卖出({reason})'


# ─────────────────────────────────────────────
# 路由：静态页面
# ─────────────────────────────────────────────

@app.route('/')
def index():
    """Vue 前端首页"""
    if os.path.exists(os.path.join(FRONTEND_DIST, 'index.html')):
        return send_from_directory(FRONTEND_DIST, 'index.html')
    return render_template('index.html')


@app.route('/<path:filename>')
def static_files(filename):
    """Vue 静态资源 (JS/CSS/图片等)"""
    if filename.startswith('api/'):
        return jsonify({'error': 'Not found'}), 404

    file_path = os.path.join(FRONTEND_DIST, filename)
    if os.path.exists(file_path) and not os.path.isdir(file_path):
        return send_from_directory(FRONTEND_DIST, filename)

    if os.path.exists(os.path.join(FRONTEND_DIST, 'index.html')):
        return send_from_directory(FRONTEND_DIST, 'index.html')

    return jsonify({'error': 'Not found'}), 404


@app.route('/agent')
def agent():
    return send_from_directory(FRONTEND_DIST, 'index.html')


@app.route('/agent/history')
def agent_history():
    return send_from_directory(FRONTEND_DIST, 'index.html')


@app.route('/data-update')
def data_update():
    return send_from_directory(FRONTEND_DIST, 'index.html')


@app.route('/multi-signal-resonance')
def multi_signal_resonance():
    return send_from_directory(FRONTEND_DIST, 'index.html')


# ─────────────────────────────────────────────
# API: 持仓
# ─────────────────────────────────────────────

@app.route('/api/positions')
def api_positions():
    db = get_db()
    try:
        # 表不存在时返回空数据
        if not table_exists(db, 'positions'):
            return jsonify({
                'positions': [],
                'summary': {
                    'total_value': 0, 'total_cost': 0,
                    'holding_profit': 0, 'history_profit': 0,
                    'total_profit': 0, 'profit_pct': 0,
                    'count': 0, 'available_cash': INITIAL_CAPITAL
                }
            })

        sort = request.args.get('sort', 'buy_date')
        order = request.args.get('order', 'desc')

        allowed_sort_fields = {
            'buy_date', 'profit_pct', 'profit_loss',
            'current_price', 'buy_price', 'name', 'code'
        }
        if sort not in allowed_sort_fields:
            sort = 'buy_date'
        order = order.upper() if order.upper() in ('ASC', 'DESC') else 'DESC'

        df = db.execute(f"""
            SELECT
                id, code, name, strategy,
                signal_date, buy_date, shares, buy_price,
                buy_change_pct, buy_score_b1, buy_score_b2,
                current_price, profit_loss, profit_pct,
                stop_loss_pct, status, notes,
                ROUND(shares * buy_price * 0.9998, 2) as position_amount
            FROM positions
            WHERE status = 'holding'
            ORDER BY {sort} {order}
        """).df()

        latest_date = get_latest_trading_date()
        if latest_date and not df.empty:
            codes = df['code'].tolist()
            if codes:
                ts_codes = [code_to_ts_code(c) for c in codes]
                price_df = db.execute(
                    "SELECT ts_code, close FROM dwd_daily_price WHERE trade_date = ? AND ts_code IN ("
                    + ','.join(['?' for _ in ts_codes]) + ")",
                    [latest_date] + ts_codes
                ).df()

                price_map = dict(zip(price_df['ts_code'], price_df['close']))

                for idx, row in df.iterrows():
                    ts_code = code_to_ts_code(row['code'])
                    current_price = price_map.get(ts_code)
                    if current_price is not None:
                        df.at[idx, 'current_price'] = current_price
                        if row['buy_price']:
                            profit_pct = (current_price - row['buy_price']) / row['buy_price'] * 100
                            profit_loss = (current_price - row['buy_price']) * row['shares']
                            df.at[idx, 'profit_pct'] = round(profit_pct, 2)
                            df.at[idx, 'profit_loss'] = round(profit_loss, 2)

        df = clean_df_for_json(df)
        positions = df.to_dict('records')

        history_profit_result = db.execute(
            "SELECT COALESCE(SUM(profit_loss), 0) FROM positions WHERE status = 'sold'"
        ).fetchone()
        history_profit = float(history_profit_result[0]) if history_profit_result else 0.0

        # 已卖出的原始成本（用于释放出资金）
        sold_cost_result = db.execute(
            "SELECT COALESCE(SUM(buy_price * shares), 0) FROM positions WHERE status = 'sold'"
        ).fetchone()
        sold_cost = float(sold_cost_result[0]) if sold_cost_result else 0.0

        total_value = sum(
            p['current_price'] * p['shares']
            if p['current_price'] else 0
            for p in positions
        )
        total_cost = sum(
            p['buy_price'] * p['shares']
            if p['buy_price'] else 0
            for p in positions
        )
        holding_profit = total_value - total_cost
        total_profit = holding_profit + history_profit

        # available_cash = 初始资金 - 当前持仓成本 + 已卖出回款（成本+盈亏）
        available_cash = INITIAL_CAPITAL - total_cost + (sold_cost + history_profit)

        # 总资产 = 当前持仓市值 + 可用现金
        total_account_value = total_value + available_cash

        # profit_pct 基准：初始资金
        profit_pct = round(total_profit / INITIAL_CAPITAL * 100, 2)

        return jsonify({
            'positions': positions,
            'summary': {
                'total_value': round(total_value, 2),
                'total_account_value': round(total_account_value, 2),
                'total_cost': round(total_cost, 2),
                'holding_profit': round(holding_profit, 2),
                'history_profit': round(history_profit, 2),
                'total_profit': round(total_profit, 2),
                'profit_pct': profit_pct,
                'count': len(positions),
                'available_cash': round(available_cash, 2)
            }
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────
# API: 历史交易
# ─────────────────────────────────────────────

@app.route('/api/history')
def api_history():
    db = get_db()
    try:
        if not table_exists(db, 'positions'):
            return jsonify({
                'history': [],
                'summary': {
                    'total_trades': 0, 'total_profit': 0,
                    'win_count': 0, 'loss_count': 0,
                    'win_rate': 0, 'profit_loss_ratio': 0
                }
            })

        df = db.execute("""
            SELECT
                code, name, strategy,
                buy_date, buy_price, shares,
                sell_date, sell_price, sell_reason,
                profit_loss, profit_pct
            FROM positions
            WHERE status = 'sold'
            ORDER BY sell_date DESC
        """).df()

        df['buy_signal_type'] = df['strategy'].apply(lambda x: x if x else '趋势择时')
        df['sell_signal_type'] = df['sell_reason'].apply(
            lambda x: map_sell_reason(x) if x else '信号卖出'
        )

        df = clean_df_for_json(df)
        history = df.to_dict('records')

        total_profit = sum(p['profit_loss'] if p['profit_loss'] else 0 for p in history)
        win_count = len([p for p in history if p['profit_loss'] and p['profit_loss'] > 0])
        loss_count = len([p for p in history if p['profit_loss'] and p['profit_loss'] < 0])

        win_total = sum(p['profit_loss'] for p in history if p['profit_loss'] and p['profit_loss'] > 0)
        loss_total = abs(sum(p['profit_loss'] for p in history if p['profit_loss'] and p['profit_loss'] < 0))
        avg_win = win_total / win_count if win_count > 0 else 0
        avg_loss = loss_total / loss_count if loss_count > 0 else 0
        profit_loss_ratio = round(avg_win / avg_loss, 2) if avg_loss > 0 else 0

        return jsonify({
            'history': history,
            'summary': {
                'total_trades': len(history),
                'total_profit': round(total_profit, 2),
                'win_count': win_count,
                'loss_count': loss_count,
                'win_rate': round(win_count / len(history) * 100, 2) if history else 0,
                'profit_loss_ratio': profit_loss_ratio
            }
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


from scripts.frontend_analysis.history_metrics import calculate_all_metrics


@app.route('/api/history/analysis')
def api_history_analysis():
    """综合分析历史交易数据"""
    try:
        return jsonify(calculate_all_metrics())
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────
# API: 信号
# ─────────────────────────────────────────────

@app.route('/api/signals')
def api_signals():
    db = get_db()
    try:
        if not table_exists(db, 'daily_signals'):
            return jsonify({'signals': [], 'date': None, 'buy_count': 0, 'sell_count': 0})

        result = db.execute("SELECT MAX(date) FROM daily_signals").fetchone()
        latest_date = result[0] if result else None

        if not latest_date:
            return jsonify({'signals': [], 'date': None, 'buy_count': 0, 'sell_count': 0})

        date_str = latest_date.strftime('%Y-%m-%d') if hasattr(latest_date, 'strftime') else str(latest_date)

        df = db.execute("SELECT * FROM daily_signals WHERE date = ?", [latest_date]).df()

        if df.empty:
            return jsonify({'signals': [], 'date': date_str, 'buy_count': 0, 'sell_count': 0})

        buy_signal_cols = [c for c in df.columns if c.startswith('signal_buy_')]
        sell_signal_cols = [
            c for c in df.columns
            if c in ('signal_s1_full', 'signal_s1_half', 'signal_跌破多空线', 'signal_止损')
        ]

        result_list = []
        buy_count = 0
        sell_count = 0

        for _, row in df.iterrows():
            buy_signals = []
            sell_signals = []

            for col in buy_signal_cols:
                if row[col] is True or row[col] == 1:
                    strategy_id = col.replace('signal_buy_', '')
                    strategy_name = STRATEGY_NAME_MAP.get(strategy_id, strategy_id)
                    score_col = f'score_{strategy_id}'
                    score = row.get(score_col, 0) or 0
                    buy_signals.append({'strategy': strategy_name, 'score': round(score, 2)})

            for col in sell_signal_cols:
                if row[col] is True or row[col] == 1:
                    sell_key = col.replace('signal_', '')
                    strategy_name = SELL_STRATEGY_NAME_MAP.get(sell_key, sell_key)
                    sell_signals.append({
                        'strategy': strategy_name,
                        'score': round(row.get(f'score_{sell_key}', 0) or 0, 2)
                    })

            if buy_signals or sell_signals:
                if buy_signals:
                    buy_count += 1
                if sell_signals:
                    sell_count += 1
                result_list.append({
                    'code': row['code'],
                    'name': row['name'],
                    'close': round(row['close'], 2) if pd.notna(row['close']) else None,
                    'change_pct': round(row['change_pct'], 2) if pd.notna(row['change_pct']) else None,
                    'open': round(row['open'], 2) if pd.notna(row['open']) else None,
                    'high': round(row['high'], 2) if pd.notna(row['high']) else None,
                    'low': round(row['low'], 2) if pd.notna(row['low']) else None,
                    'volume': int(row['volume']) if pd.notna(row['volume']) else None,
                    'buy_signals': buy_signals,
                    'sell_signals': sell_signals,
                })

        result_list.sort(
            key=lambda x: max([s['score'] for s in x['buy_signals']] or [0], default=0),
            reverse=True
        )

        return jsonify({
            'signals': result_list,
            'date': date_str,
            'buy_count': buy_count,
            'sell_count': sell_count
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────
# API: 净值曲线
# ─────────────────────────────────────────────

@app.route('/api/equity-curve')
def api_equity_curve():
    """
    从 portfolio_daily 读取净值数据。

    表结构（由 init_missing_tables.py 创建）:
        date, strategy, total_value, cash, market_value,
        daily_pnl, daily_pnl_pct, cum_return, drawdown, positions_json
    """
    db = get_db()
    try:
        # ── 表不存在或无数据时返回 mock ──────────────────────────
        has_table = table_exists(db, 'portfolio_daily')
        portfolio_rows = []
        if has_table:
            portfolio_rows = db.execute("""
                SELECT date, total_value, cash, daily_pnl, cum_return, drawdown
                FROM portfolio_daily
                WHERE strategy = 'main'
                ORDER BY date
            """).fetchall()

        if not portfolio_rows:
            dates = [
                (datetime.now() - timedelta(days=29 - i)).strftime('%Y-%m-%d')
                for i in range(30)
            ]
            return jsonify({
                'dates': dates,
                'values': [INITIAL_CAPITAL] * 30,
                'benchmark': [INITIAL_CAPITAL] * 30,
                'total_return': 0,
                'annotations': {
                    'peak': {'date': dates[0], 'value': INITIAL_CAPITAL, 'return_pct': 0},
                    'trough': {'date': dates[-1], 'value': INITIAL_CAPITAL},
                    'max_drawdown': {'date': None, 'pct': 0}
                },
                'position_ratio': [0] * 30,
                'closed_pnl': [0] * 30,
                'available_cash': [INITIAL_CAPITAL] * 30,
                'position_pnl': [0] * 30
            })

        # ── 解析行数据 ────────────────────────────────────────────
        dates = []
        values = []
        available_cash_list = []
        position_pnl_list = []
        closed_pnl_list = []
        position_ratio_list = []

        for row in portfolio_rows:
            date, total_value, cash, daily_pnl, cum_return, drawdown = row
            d_str = date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else str(date)
            dates.append(d_str)
            tv = float(total_value) if total_value is not None else INITIAL_CAPITAL
            values.append(tv)
            c = float(cash) if cash is not None else 0.0
            available_cash_list.append(round(c, 2))
            mv = tv - c
            position_pnl_list.append(round(mv - (tv - c), 2))
            closed_pnl_list.append(round(float(daily_pnl) if daily_pnl else 0, 2))
            position_ratio_list.append(round(mv / tv * 100, 2) if tv > 0 else 0)

        # ── 基准（上证指数）────────────────────────────────────────
        benchmark_values = []
        try:
            index_data = db.execute("""
                SELECT trade_date, close FROM dwd_index_daily
                WHERE index_code = '000001.SH'
                AND trade_date >= ? AND trade_date <= ?
                ORDER BY trade_date
            """, [dates[0], dates[-1]]).fetchall()

            if index_data:
                index_map = {
                    (row[0].strftime('%Y-%m-%d') if hasattr(row[0], 'strftime') else str(row[0])): float(row[1])
                    for row in index_data
                }
                first_close = index_map.get(dates[0])
                if first_close and first_close > 0:
                    for d in dates:
                        close = index_map.get(d)
                        if close:
                            benchmark_values.append(INITIAL_CAPITAL * close / first_close)
                        else:
                            benchmark_values.append(benchmark_values[-1] if benchmark_values else INITIAL_CAPITAL)
                else:
                    benchmark_values = [INITIAL_CAPITAL] * len(dates)
            else:
                benchmark_values = [INITIAL_CAPITAL] * len(dates)
        except Exception as e:
            print(f"获取上证指数失败: {e}")
            benchmark_values = [INITIAL_CAPITAL] * len(dates)

        # ── 关键指标 ──────────────────────────────────────────────
        current_value = values[-1] if values else INITIAL_CAPITAL
        total_return = (current_value - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

        peak_value = max(values)
        peak_idx = values.index(peak_value)
        peak_date = dates[peak_idx]
        peak_return = (peak_value - INITIAL_CAPITAL) / INITIAL_CAPITAL * 100

        trough_value = min(values)
        trough_idx = values.index(trough_value)
        trough_date = dates[trough_idx]

        max_drawdown = 0
        max_drawdown_date = None
        peak_so_far = INITIAL_CAPITAL
        for d, v in zip(dates, values):
            if v > peak_so_far:
                peak_so_far = v
            dd = (peak_so_far - v) / peak_so_far * 100
            if dd > max_drawdown:
                max_drawdown = dd
                max_drawdown_date = d

        return jsonify({
            'dates': dates,
            'values': [round(v, 2) for v in values],
            'benchmark': [round(v, 2) for v in benchmark_values],
            'total_return': round(total_return, 2),
            'annotations': {
                'peak': {
                    'date': peak_date,
                    'value': round(peak_value, 2),
                    'return_pct': round(peak_return, 2)
                },
                'trough': {
                    'date': trough_date,
                    'value': round(trough_value, 2)
                },
                'max_drawdown': {
                    'date': max_drawdown_date,
                    'pct': round(max_drawdown, 2)
                }
            },
            'position_ratio': position_ratio_list,
            'closed_pnl': closed_pnl_list,
            'available_cash': available_cash_list,
            'position_pnl': position_pnl_list
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────
# API: 策略对比
# ─────────────────────────────────────────────

@app.route('/api/strategy-comparison')
def api_strategy_comparison():
    """
    从 portfolio_daily_strategy 读取各策略每日净值。

    表结构（由 init_missing_tables.py 创建）:
        date, strategy, total_value, cash, market_value,
        daily_pnl, daily_pnl_pct, cum_return, drawdown
    """
    db = get_db()
    try:
        if not table_exists(db, 'portfolio_daily_strategy'):
            return jsonify({
                'strategies': [], 'dates': [],
                'initial_value': INITIAL_CAPITAL,
                'curves': {}, 'metrics': {}
            })

        strategies = db.execute("""
            SELECT DISTINCT strategy
            FROM portfolio_daily_strategy
            WHERE strategy IS NOT NULL
            ORDER BY strategy
        """).fetchall()

        if not strategies:
            return jsonify({
                'strategies': [], 'dates': [],
                'initial_value': INITIAL_CAPITAL,
                'curves': {}, 'metrics': {}
            })

        colors = [
            '#3b82f6', '#10b981', '#f59e0b', '#ef4444',
            '#8b5cf6', '#ec4899', '#06b6d4', '#84cc16'
        ]

        strategy_data = {}

        for i, (strategy_name,) in enumerate(strategies):
            daily_rows = db.execute("""
                SELECT date, total_value, daily_pnl
                FROM portfolio_daily_strategy
                WHERE strategy = ?
                ORDER BY date
            """, [strategy_name]).fetchall()

            if not daily_rows:
                continue

            dates = []
            values = []

            for row in daily_rows:
                date, total_value, daily_pnl = row
                date_str = date.strftime('%Y-%m-%d') if hasattr(date, 'strftime') else str(date)
                dates.append(date_str)
                tv = float(total_value) if total_value is not None else INITIAL_CAPITAL
                values.append(round(tv, 2))

            if not dates:
                continue

            strategy_data[strategy_name] = {
                'dates': dates,
                'values': values,
                'color': colors[i % len(colors)]
            }

        # ── 对齐日期轴 ────────────────────────────────────────────
        all_dates = set()
        for sd in strategy_data.values():
            all_dates.update(sd['dates'])
        sorted_dates = sorted(all_dates)

        curves = {}
        metrics = {}

        for strategy_name, sd in strategy_data.items():
            date_to_value = dict(zip(sd['dates'], sd['values']))
            aligned = []
            last_v = INITIAL_CAPITAL
            for d in sorted_dates:
                v = date_to_value.get(d)
                if v is not None:
                    last_v = v
                aligned.append(last_v)

            valid = [v for v in aligned if v is not None]
            if not valid:
                continue

            final_value = valid[-1]
            total_return = (final_value - INITIAL_CAPITAL) / INITIAL_CAPITAL

            peak = INITIAL_CAPITAL
            max_drawdown = 0
            for v in valid:
                if v > peak:
                    peak = v
                dd = (peak - v) / peak
                if dd > max_drawdown:
                    max_drawdown = dd

            if len(valid) > 1:
                rets = [
                    (valid[j] - valid[j - 1]) / valid[j - 1]
                    for j in range(1, len(valid))
                    if valid[j - 1] != 0
                ]
                if rets:
                    avg_r = sum(rets) / len(rets)
                    variance = sum((r - avg_r) ** 2 for r in rets) / len(rets)
                    std_dev = variance ** 0.5
                    sharpe = avg_r / std_dev * (252 ** 0.5) if std_dev > 0 else 0
                else:
                    sharpe = 0
            else:
                sharpe = 0

            curves[strategy_name] = {
                'data': aligned,
                'initial_value': INITIAL_CAPITAL,
                'color': sd['color']
            }
            metrics[strategy_name] = {
                'total_return': round(total_return, 4),
                'annualized_return': round(total_return * 252 / len(valid), 4) if valid else 0,
                'sharpe_ratio': round(sharpe, 2),
                'max_drawdown': round(max_drawdown, 4),
                'win_rate': 0,
                'total_trades': 0
            }

        return jsonify({
            'strategies': [s[0] for s in strategies if s[0] in curves],
            'dates': sorted_dates,
            'initial_value': INITIAL_CAPITAL,
            'curves': curves,
            'metrics': metrics
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────
# API: 统计概览
# ─────────────────────────────────────────────

@app.route('/api/stats')
def api_stats():
    db = get_db()
    try:
        holding_count = 0
        sold_count = 0
        if table_exists(db, 'positions'):
            holding_count = db.execute(
                "SELECT COUNT(*) FROM positions WHERE status = 'holding'"
            ).fetchone()[0]
            sold_count = db.execute(
                "SELECT COUNT(*) FROM positions WHERE status = 'sold'"
            ).fetchone()[0]

        latest_date = None
        buy_signals_count = 0
        if table_exists(db, 'daily_signals'):
            result = db.execute("SELECT MAX(date) FROM daily_signals").fetchone()
            latest_date = result[0] if result else None
            if latest_date:
                buy_signals_count = db.execute("""
                    SELECT COUNT(*) FROM daily_signals
                    WHERE date = ? AND (signal_buy_b1 = true OR signal_buy_b2 = true)
                """, [latest_date]).fetchone()[0]

        return jsonify({
            'holding_count': holding_count,
            'sold_count': sold_count,
            'today_buy_signals': buy_signals_count,
            'latest_date': (
                latest_date.strftime('%Y-%m-%d')
                if latest_date and hasattr(latest_date, 'strftime')
                else str(latest_date) if latest_date else None
            )
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


# ─────────────────────────────────────────────
# API: 多信号共振
# ─────────────────────────────────────────────

@app.route('/api/multi-signal-resonance')
def api_multi_signal_resonance():
    """获取多信号共振股票"""
    date_str = request.args.get('date')
    if not date_str:
        date_str = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')

    db = get_db()
    try:
        if not table_exists(db, 'daily_signals'):
            return jsonify({'date': date_str, 'stocks': [], 'count': 0})

        result = db.execute("""
            SELECT code, name,
                   signal_buy_b1, signal_buy_b2, signal_buy_blk, signal_buy_dl,
                   signal_buy_dz30, signal_buy_scb, signal_buy_blkB2,
                   close, change_pct
            FROM daily_signals
            WHERE date = ?
            AND (CAST(signal_buy_b1 AS INT) + CAST(signal_buy_b2 AS INT) +
                 CAST(signal_buy_blk AS INT) + CAST(signal_buy_dl AS INT) +
                 CAST(signal_buy_dz30 AS INT) + CAST(signal_buy_scb AS INT) +
                 CAST(signal_buy_blkB2 AS INT)) >= 2
            ORDER BY (CAST(signal_buy_b1 AS INT) + CAST(signal_buy_b2 AS INT) +
                      CAST(signal_buy_blk AS INT) + CAST(signal_buy_dl AS INT) +
                      CAST(signal_buy_dz30 AS INT) + CAST(signal_buy_scb AS INT) +
                      CAST(signal_buy_blkB2 AS INT)) DESC,
                     close DESC
        """, [date_str]).fetchall()

        signal_names = ['B1', 'B2', 'BLK', 'DL', 'DZ30', 'SCB', 'BLKB2']
        data = []
        for row in result:
            signals = [signal_names[i] for i, v in enumerate(row[2:9]) if v]
            data.append({
                'code': row[0],
                'name': row[1],
                'signal_count': len(signals),
                'signals': signals,
                'close': float(row[9]) if row[9] else 0,
                'change_pct': float(row[10]) if row[10] else 0
            })

        return jsonify({'date': date_str, 'stocks': data, 'count': len(data)})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/multi-signal-trend')
def api_multi_signal_trend():
    """
    获取多信号趋势数据。

    修复说明：
    1. 原来 WHERE 条件限制 >= 2，导致没有共振信号的日期被完全过滤掉，
       数据库中只有 1 天扫描记录时趋势图退化为单条竖线。
       修复：去掉 WHERE 过滤，取所有有扫描记录的交易日，各信号为 0 时正常显示。
    2. total_count 原来用 COUNT(*)（全部扫描股票数），改为统计共振股票数
       (即至少触发 2 个买入信号的股票数)，与 resonance 接口语义一致。
    3. 支持 days 参数控制返回天数（默认 60 天，最多 365 天），
       避免全量历史数据过多导致图表拥挤。
    """
    db = get_db()
    try:
        if not table_exists(db, 'daily_signals'):
            return jsonify({'dates': [], 'total_counts': [], 'signal_data': {}})

        days = min(int(request.args.get('days', 60)), 365)

        # 先取最新 N 个有扫描记录的交易日
        date_rows = db.execute("""
            SELECT DISTINCT date FROM daily_signals
            ORDER BY date DESC
            LIMIT ?
        """, [days]).fetchall()

        if not date_rows:
            return jsonify({'dates': [], 'total_counts': [], 'signal_data': {}})

        # 时间正序排列
        date_list = sorted(
            [r[0].strftime('%Y-%m-%d') if hasattr(r[0], 'strftime') else str(r[0])
             for r in date_rows]
        )
        placeholders = ', '.join(['?' for _ in date_list])

        result = db.execute(f"""
            SELECT
                date,
                -- 各信号触发股票数（不要求共振，单信号也计入）
                SUM(CAST(signal_buy_b1   AS INT)) AS b1_count,
                SUM(CAST(signal_buy_b2   AS INT)) AS b2_count,
                SUM(CAST(signal_buy_blk  AS INT)) AS blk_count,
                SUM(CAST(signal_buy_dl   AS INT)) AS dl_count,
                SUM(CAST(signal_buy_dz30 AS INT)) AS dz30_count,
                SUM(CAST(signal_buy_scb  AS INT)) AS scb_count,
                SUM(CAST(signal_buy_blkB2 AS INT)) AS blkb2_count,
                -- 共振股票数：至少触发 2 个买入信号
                SUM(CASE WHEN (
                    CAST(signal_buy_b1   AS INT) +
                    CAST(signal_buy_b2   AS INT) +
                    CAST(signal_buy_blk  AS INT) +
                    CAST(signal_buy_dl   AS INT) +
                    CAST(signal_buy_dz30 AS INT) +
                    CAST(signal_buy_scb  AS INT) +
                    CAST(signal_buy_blkB2 AS INT)
                ) >= 2 THEN 1 ELSE 0 END) AS resonance_count
            FROM daily_signals
            WHERE date IN ({placeholders})
            GROUP BY date
            ORDER BY date
        """, date_list).fetchall()

        dates, total_counts = [], []
        signal_data = {k: [] for k in ['B1', 'B2', 'BLK', 'DL', 'DZ30', 'SCB', 'BLKB2']}

        for row in result:
            d_str = row[0].strftime('%Y-%m-%d') if hasattr(row[0], 'strftime') else str(row[0])
            dates.append(d_str)
            # row[8] = resonance_count（共振股票数）
            total_counts.append(int(row[8]) if row[8] is not None else 0)
            for j, k in enumerate(['B1', 'B2', 'BLK', 'DL', 'DZ30', 'SCB', 'BLKB2']):
                signal_data[k].append(int(row[j + 1]) if row[j + 1] is not None else 0)

        return jsonify({'dates': dates, 'total_counts': total_counts, 'signal_data': signal_data})
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500


@app.route('/api/multi-signal-resonance/dates')
def api_multi_signal_resonance_dates():
    """API: 获取多策略共振可用的日期列表"""
    db = get_db()
    try:
        if not table_exists(db, 'daily_signals'):
            return jsonify({'dates': []})

        dates = db.execute(
            "SELECT DISTINCT date FROM daily_signals ORDER BY date DESC"
        ).fetchall()
        return jsonify({
            'dates': [
                {
                    'value': d[0].strftime('%Y-%m-%d') if hasattr(d[0], 'strftime') else str(d[0]),
                    'label': d[0].strftime('%Y-%m-%d') if hasattr(d[0], 'strftime') else str(d[0])
                }
                for d in dates
            ]
        })
    except Exception as e:
        import traceback
        traceback.print_exc()
        return jsonify({'error': str(e)}), 500



# ─────────────────────────────────────────────
# API: 持仓写入 / 交易记录  (POST/DELETE)
# ─────────────────────────────────────────────
import logging as _logging

_trade_logger = _logging.getLogger('trade_api')
if not _trade_logger.handlers:
    _h = _logging.StreamHandler()
    _h.setFormatter(_logging.Formatter('%(asctime)s [TRADE] %(levelname)s: %(message)s'))
    _trade_logger.addHandler(_h)
    _trade_logger.setLevel(_logging.DEBUG)


def _next_pos_id(db):
    row = db.execute("SELECT COALESCE(MAX(id), 0) FROM positions").fetchone()
    return int(row[0]) + 1


def _require(data, fields):
    missing = [f for f in fields if data.get(f) in (None, '', [])]
    return ', '.join(missing) if missing else None


# ── 诊断接口：检查 positions 表和数据库连接 ───────────────────────
@app.route('/api/debug/positions-check')
def debug_positions_check():
    """快速诊断接口，返回表存在性和行数"""
    try:
        db = get_db()
        has_table = table_exists(db, 'positions')
        count = 0
        holding = 0
        cols = []
        if has_table:
            count = db.execute("SELECT COUNT(*) FROM positions").fetchone()[0]
            holding = db.execute("SELECT COUNT(*) FROM positions WHERE status='holding'").fetchone()[0]
            cols = [r[0] for r in db.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_name='positions' ORDER BY ordinal_position"
            ).fetchall()]
        return jsonify({
            'db_path': DB_PATH,
            'table_exists': has_table,
            'total_rows': count,
            'holding_rows': holding,
            'columns': cols,
        })
    except Exception as e:
        import traceback
        return jsonify({'error': str(e), 'traceback': traceback.format_exc()}), 500


# ── POST /api/positions  添加持仓 ────────────────────────────────
@app.route('/api/positions', methods=['POST'])
def add_position():
    data = request.get_json(silent=True) or {}
    _trade_logger.info(f"POST /api/positions  payload={data}")

    missing = _require(data, ['code', 'name', 'buy_date', 'buy_price', 'shares'])
    if missing:
        _trade_logger.warning(f"缺少字段: {missing}")
        return jsonify({'error': f'缺少必填字段: {missing}'}), 400

    try:
        shares = int(data['shares'])
        buy_price = float(data['buy_price'])
    except (ValueError, TypeError) as e:
        return jsonify({'error': f'数值格式错误: {e}'}), 400

    if shares <= 0 or shares % 100 != 0:
        return jsonify({'error': '买入数量必须是 100 的整数倍且 > 0'}), 400
    if buy_price <= 0:
        return jsonify({'error': '买入价格必须 > 0'}), 400

    db = get_db()
    try:
        new_id = _next_pos_id(db)
        _trade_logger.info(f"INSERT positions id={new_id} code={data['code']} shares={shares} price={buy_price}")
        db.execute("""
            INSERT INTO positions (
                id, code, name, strategy,
                buy_date, shares, buy_price,
                stop_loss_pct, status, notes,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'holding', ?,
                      CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, [
            new_id,
            str(data['code']).strip(),
            str(data['name']).strip(),
            data.get('strategy', ''),
            data['buy_date'],
            shares,
            buy_price,
            float(data.get('stop_loss_pct', 0.03)),
            data.get('notes', '') or '',
        ])
        _trade_logger.info(f"持仓写入成功 id={new_id}")
        return jsonify({'success': True, 'id': new_id, 'message': '持仓已添加'}), 201
    except Exception as e:
        import traceback
        _trade_logger.error(f"持仓写入失败: {e}\n{traceback.format_exc()}")
        return jsonify({'error': f'数据库写入失败: {e}'}), 500


# ── POST /api/trade/buy  记录买入 ────────────────────────────────
@app.route('/api/trade/buy', methods=['POST'])
def trade_buy():
    data = request.get_json(silent=True) or {}
    _trade_logger.info(f"POST /api/trade/buy  payload={data}")

    missing = _require(data, ['code', 'buy_date', 'buy_price', 'shares', 'reason'])
    if missing:
        _trade_logger.warning(f"缺少字段: {missing}")
        return jsonify({'error': f'缺少必填字段: {missing}'}), 400

    try:
        shares = int(data['shares'])
        buy_price = float(data['buy_price'])
    except (ValueError, TypeError) as e:
        return jsonify({'error': f'数值格式错误: {e}'}), 400

    if shares <= 0 or shares % 100 != 0:
        return jsonify({'error': '买入数量必须是 100 的整数倍且 > 0'}), 400
    if buy_price <= 0:
        return jsonify({'error': '买入价格必须 > 0'}), 400

    db = get_db()
    try:
        new_id = _next_pos_id(db)
        reason_val = str(data.get('reason', ''))
        notes_val = data.get('notes', '') or ''
        notes_combined = (f"[买入原因] {reason_val} | {notes_val}").strip(' |') if reason_val else notes_val

        _trade_logger.info(f"INSERT positions(buy) id={new_id} code={data['code']}")
        db.execute("""
            INSERT INTO positions (
                id, code, name, strategy,
                buy_date, shares, buy_price,
                stop_loss_pct, status, notes,
                created_at, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'holding', ?,
                      CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
        """, [
            new_id,
            str(data['code']).strip(),
            str(data.get('name', '')).strip(),
            data.get('strategy', ''),
            data['buy_date'],
            shares,
            buy_price,
            float(data.get('stop_loss_pct', 0.03)),
            notes_combined,
        ])
        _trade_logger.info(f"买入记录写入成功 id={new_id}")
        return jsonify({'success': True, 'id': new_id, 'message': '买入记录已写入'}), 201
    except Exception as e:
        import traceback
        _trade_logger.error(f"买入记录写入失败: {e}\n{traceback.format_exc()}")
        return jsonify({'error': f'数据库写入失败: {e}'}), 500


# ── POST /api/trade/sell  记录卖出 ───────────────────────────────
@app.route('/api/trade/sell', methods=['POST'])
def trade_sell():
    data = request.get_json(silent=True) or {}
    _trade_logger.info(f"POST /api/trade/sell  payload={data}")

    missing = _require(data, ['position_id', 'code', 'sell_date', 'sell_price', 'shares', 'reason'])
    if missing:
        return jsonify({'error': f'缺少必填字段: {missing}'}), 400

    try:
        position_id = int(data['position_id'])
        sell_price  = float(data['sell_price'])
        sell_shares = int(data['shares'])
    except (ValueError, TypeError) as e:
        return jsonify({'error': f'数值格式错误: {e}'}), 400

    if sell_price <= 0:
        return jsonify({'error': '卖出价格必须 > 0'}), 400
    if sell_shares <= 0 or sell_shares % 100 != 0:
        return jsonify({'error': '卖出数量必须是 100 的整数倍且 > 0'}), 400

    db = get_db()
    try:
        row = db.execute(
            "SELECT id, code, name, strategy, buy_price, shares, status FROM positions WHERE id = ?",
            [position_id]
        ).fetchone()

        if row is None:
            return jsonify({'error': f'找不到 id={position_id} 的持仓'}), 404

        _, pos_code, pos_name, pos_strategy, buy_price, holding_shares, status = row

        if status == 'sold':
            return jsonify({'error': '该持仓已经卖出'}), 400
        if sell_shares > holding_shares:
            return jsonify({'error': f'卖出数量({sell_shares})超过持仓({holding_shares})'}), 400

        cost_basis     = float(buy_price or 0) * sell_shares
        gross_proceeds = sell_price * sell_shares
        fee            = gross_proceeds * 0.00125
        net_proceeds   = gross_proceeds - fee
        profit_loss    = round(net_proceeds - cost_basis, 2)
        profit_pct     = round(profit_loss / cost_basis * 100, 4) if cost_basis > 0 else 0.0

        sell_type = data.get('sell_type', 'full')
        is_full = sell_type == 'full' or sell_shares >= holding_shares

        if is_full:
            db.execute("""
                UPDATE positions SET
                    status      = 'sold',
                    sell_date   = ?,
                    sell_price  = ?,
                    sell_reason = ?,
                    profit_loss = ?,
                    profit_pct  = ?,
                    updated_at  = CURRENT_TIMESTAMP
                WHERE id = ?
            """, [data['sell_date'], sell_price, str(data['reason']),
                  profit_loss, profit_pct, position_id])
        else:
            remaining = holding_shares - sell_shares
            db.execute(
                "UPDATE positions SET shares = ?, updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                [remaining, position_id]
            )

        # 审计日志（失败不阻断主流程）
        try:
            audit_id = int(db.execute("SELECT COALESCE(MAX(id),0) FROM trade_audit_log").fetchone()[0]) + 1
            db.execute("""
                INSERT INTO trade_audit_log (
                    id, trade_date, position_id, code, name, strategy,
                    action, sell_reason,
                    sell_shares, sell_price, buy_price,
                    gross_proceeds, net_proceeds, cost_basis,
                    profit_loss, profit_pct,
                    dry_run, executed_at, notes
                ) VALUES (?,?,?,?,?,?, ?,?, ?,?,?, ?,?,?, ?,?, FALSE, CURRENT_TIMESTAMP, ?)
            """, [
                audit_id, data['sell_date'], position_id,
                str(data['code']), pos_name or '', pos_strategy or '',
                'SELL_FULL' if is_full else 'SELL_HALF', str(data['reason']),
                sell_shares, sell_price, float(buy_price or 0),
                round(gross_proceeds,2), round(net_proceeds,2), round(cost_basis,2),
                profit_loss, profit_pct,
                data.get('notes', '') or '',
            ])
        except Exception as ae:
            _trade_logger.warning(f"audit_log 写入失败(非致命): {ae}")

        _trade_logger.info(f"卖出成功 id={position_id} pnl={profit_loss}")
        return jsonify({'success': True, 'message': '卖出记录已写入',
                        'profit_loss': profit_loss, 'profit_pct': profit_pct})
    except Exception as e:
        import traceback
        _trade_logger.error(f"卖出失败: {e}\n{traceback.format_exc()}")
        return jsonify({'error': f'数据库写入失败: {e}'}), 500


# ── DELETE /api/positions/<id>  删除持仓 ─────────────────────────
@app.route('/api/positions/<int:position_id>', methods=['DELETE'])
def delete_position(position_id: int):
    _trade_logger.info(f"DELETE /api/positions/{position_id}")
    db = get_db()
    try:
        row = db.execute(
            "SELECT id, code, name FROM positions WHERE id = ?", [position_id]
        ).fetchone()
        if row is None:
            return jsonify({'error': f'找不到 id={position_id} 的持仓'}), 404
        db.execute("DELETE FROM positions WHERE id = ?", [position_id])
        _trade_logger.info(f"删除持仓 id={position_id} code={row[1]}")
        return jsonify({'success': True, 'message': f'持仓 {row[1]} {row[2]} 已删除'})
    except Exception as e:
        import traceback
        _trade_logger.error(f"删除失败: {e}\n{traceback.format_exc()}")
        return jsonify({'error': f'删除失败: {e}'}), 500


# ─────────────────────────────────────────────
# 启动
# ─────────────────────────────────────────────

if __name__ == '__main__':
    template_dir = os.path.join(os.path.dirname(__file__), 'templates')
    os.makedirs(template_dir, exist_ok=True)

    print("启动Dashboard服务: http://localhost:5004")
    app.run(debug=True, port=5004, host='0.0.0.0')