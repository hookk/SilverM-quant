"""
投资组合回测脚本 - True Portfolio Simulation

使用单个Cerebro实例管理整个投资组合，所有股票同时加载到Cerebro。
策略的next()方法由Cerebro自动驱动，每个交易日会遍历所有股票。
Broker统一管理账户现金和持仓，输出组合层面的绩效指标。

Key patterns:
- cereb = bt.Cerebro()
- cereb.broker.setcash(initial_cash)
- cereb.broker.setcommission(commission=0.0003)
- for data in self.datas:  # walk all stocks in next()
- self.daily_values.append(self.broker.getvalue())
- Calculate metrics from daily_values array

使用示例:
    python batch_backtest_portfolio.py -l 50 --start 20250101 --end 20251231
"""

import sys
import argparse
import os
import logging
import warnings
import json
from pathlib import Path
from datetime import datetime, date
from typing import Dict, List, Optional, Any
import inspect

project_root = Path(__file__).parent.parent.parent
sys.path.insert(0, str(project_root))

import pandas as pd
import numpy as np
import duckdb
import backtrader as bt

from strategies.registry import Registry
from scripts.log_utils import setup_logger

# Registry单例
registry = Registry()

ASTOCK3_DB_PATH = project_root / 'data' / 'Astock3.duckdb'

# 单次批量加载上限，防止内存溢出（可通过环境变量覆盖）
MAX_STOCKS_PER_BATCH = int(os.environ.get('BT_MAX_STOCKS', 10000))  # 默认不限制，可通过 BT_MAX_STOCKS 环境变量调小


def add_exchange_suffix(code: str) -> str:
    """根据股票代码前缀判断交易所后缀

    规则:
    - 688xxx, 600xxx, 601xxx, 603xxx, 605xxx -> .SH (上证)
    - 000xxx, 001xxx, 002xxx, 003xxx, 300xxx -> .SZ (深证)
    - 920xxx -> .BJ (北交所)
    """
    if code.startswith(('688', '600', '601', '603', '605')):
        return f"{code}.SH"
    elif code.startswith(('000', '001', '002', '003', '300')):
        return f"{code}.SZ"
    elif code.startswith('920'):
        return f"{code}.BJ"
    else:
        return code


def get_all_data_from_astock3_batch(codes: List[str], start_date: str, end_date: str) -> Dict[str, pd.DataFrame]:
    """
    批量一次性查询所有股票数据（单次连接，避免逐股串行连接开销）

    Returns:
        dict: {ts_code -> DataFrame}，列: datetime(index), open, high, low, close, volume, openinterest
    """
    start_date_fmt = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
    end_date_fmt = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"

    ts_codes = [add_exchange_suffix(c) for c in codes]
    # DuckDB IN 子句，最多 2000 个 code 一批
    CHUNK = 2000
    all_dfs = []
    conn = duckdb.connect(str(ASTOCK3_DB_PATH))
    try:
        for i in range(0, len(ts_codes), CHUNK):
            chunk = ts_codes[i:i + CHUNK]
            in_clause = ", ".join(f"'{c}'" for c in chunk)
            df = conn.execute(f"""
                SELECT trade_date, ts_code, open, high, low, close, vol AS volume, amount AS openinterest
                FROM dwd_daily_price
                WHERE ts_code IN ({in_clause})
                AND trade_date >= '{start_date_fmt}'
                AND trade_date <= '{end_date_fmt}'
                ORDER BY ts_code, trade_date
            """).fetchdf()
            if df is not None and len(df) > 0:
                all_dfs.append(df)
    finally:
        conn.close()

    if not all_dfs:
        return {}

    combined = pd.concat(all_dfs, ignore_index=True)

    # 反向映射 ts_code -> original code
    ts_to_code = {add_exchange_suffix(c): c for c in codes}

    result = {}
    for ts_code, group in combined.groupby('ts_code'):
        orig_code = ts_to_code.get(ts_code, ts_code)
        df = group.drop(columns=['ts_code']).copy()
        df['datetime'] = pd.to_datetime(df['trade_date'])
        df = df.drop(columns=['trade_date'])

        # 清理 NaN
        for col in ['open', 'high', 'low', 'close', 'volume']:
            if col in df.columns:
                df[col] = df[col].ffill().fillna(0)
        df = df.dropna(subset=['datetime'])
        df = df.set_index('datetime')

        if len(df) >= 60:
            result[orig_code] = df

    return result


def get_data_from_astock3(stock_code: str, start_date: str, end_date: str) -> Optional[pd.DataFrame]:
    """获取单个股票数据（保留向后兼容）"""
    data_map = get_all_data_from_astock3_batch([stock_code], start_date, end_date)
    return data_map.get(stock_code)


def get_stock_list_from_db(limit: int = None, industry: str = None) -> pd.DataFrame:
    """从数据库获取股票列表"""
    conn = duckdb.connect(str(ASTOCK3_DB_PATH))

    # 兼容 symbol 和 ts_code 两种可能的代码列名
    # dwd_stock_info 中股票代码列可能是 ts_code（如 000001.SZ）或 symbol（纯代码）
    # 统一输出为 'code' 列（纯6位代码）
    try:
        cols = conn.execute("PRAGMA table_info(dwd_stock_info)").fetchdf()['name'].tolist()
    except Exception:
        cols = []

    if 'symbol' in cols:
        code_expr = "symbol AS code"
    elif 'ts_code' in cols:
        # ts_code 形如 000001.SZ，截取前6位
        code_expr = "SUBSTR(ts_code, 1, 6) AS code"
    else:
        code_expr = "ts_code AS code"

    query = f"SELECT {code_expr}, name, industry FROM dwd_stock_info WHERE list_status = 'L'"

    if industry:
        query += f" AND industry = '{industry}'"

    if limit:
        query += f" LIMIT {limit}"

    df = conn.execute(query).df()
    conn.close()
    return df


def load_strategy_class(strategy_name: str):
    """加载策略类"""
    strategy_class = registry.get(strategy_name)
    if strategy_class is not None:
        return strategy_class

    # 回退到旧方法
    warnings.warn(
        f"策略 '{strategy_name}' 未在注册表中，使用 exec() 加载已废弃。",
        DeprecationWarning,
        stacklevel=2
    )
    strategy_file = project_root / 'strategies' / f'{strategy_name}.py'

    with open(strategy_file, 'r', encoding='utf-8') as f:
        content = f.read()

    namespace = {}
    exec(content, namespace)

    for name in namespace:
        if name.endswith('Strategy') and name != 'BaseStrategy':
            return namespace[name]

    raise ValueError(f"未找到策略类 in {strategy_file}")


def get_strategy_config(strategy_file: str) -> dict:
    """获取策略配置"""
    metadata = registry.get_metadata(strategy_file)

    if metadata is not None:
        return {
            'threshold_required': metadata.threshold_required,
            'min_data_days': metadata.min_data_days,
        }
    else:
        logging.warning(f"策略 '{strategy_file}' 未在注册表中，使用默认配置")
        return {
            'threshold_required': True,
            'min_data_days': 60,
        }


def _build_strategy_kwargs(strategy_class, threshold: float, max_positions: int,
                           extra_params: Dict = None) -> Dict:
    """
    安全构建策略初始化参数。

    只传入策略 params 元组中实际声明的参数，避免因多余关键字参数导致 backtrader 报错。
    同时兼容：
      - 继承 PortfolioStrategy 的策略（有 max_positions）
      - 继承 BaseStrategy 的策略（只有 threshold，无 max_positions）
      - 完全自定义 params 的策略
    """
    # 收集策略 params 元组中已声明的所有参数名（复用安全提取函数）
    declared = {p[0] for p in _extract_params_tuple(strategy_class)}

    kwargs = {}
    if 'threshold' in declared:
        kwargs['threshold'] = threshold
    if 'max_positions' in declared:
        kwargs['max_positions'] = max_positions

    # 合并额外参数（只添加已声明的）
    if extra_params:
        for k, v in extra_params.items():
            if k in declared:
                kwargs[k] = v

    return kwargs


class PortfolioStrategy(bt.Strategy):
    """
    投资组合策略基类

    在next()中遍历所有股票数据进行策略判断。
    使用self.datas访问所有数据，self.broker.getvalue()获取组合总值。
    """

    params = (
        ('threshold', 8.0),
        ('stop_loss_pct', 0.05),
        ('min_data_points', 60),
        ('max_positions', 10),  # 最大持仓数量
        ('debug_mode', False),
    )

    节假日列表 = [
        datetime(2024, 2, 12), datetime(2024, 2, 13), datetime(2024, 2, 14),
        datetime(2024, 4, 4), datetime(2024, 4, 5), datetime(2024, 5, 1),
        datetime(2024, 5, 2), datetime(2024, 5, 3), datetime(2024, 6, 10),
        datetime(2024, 9, 16), datetime(2024, 9, 17), datetime(2024, 10, 1),
        datetime(2024, 10, 2), datetime(2024, 10, 3), datetime(2024, 10, 4),
        datetime(2024, 10, 7), datetime(2025, 1, 28), datetime(2025, 1, 29),
        datetime(2025, 1, 30), datetime(2025, 1, 31), datetime(2025, 4, 4),
        datetime(2025, 5, 1), datetime(2025, 5, 2), datetime(2025, 6, 2),
        datetime(2025, 10, 1), datetime(2025, 10, 2), datetime(2025, 10, 3),
        datetime(2025, 10, 6), datetime(2025, 10, 7), datetime(2025, 10, 8),
        datetime(2026, 1, 1), datetime(2026, 1, 2), datetime(2026, 1, 3),
        datetime(2026, 2, 15), datetime(2026, 2, 16), datetime(2026, 2, 17),
        datetime(2026, 2, 18), datetime(2026, 2, 19), datetime(2026, 2, 20),
        datetime(2026, 2, 21), datetime(2026, 2, 22), datetime(2026, 2, 23),
        datetime(2026, 4, 4), datetime(2026, 4, 5), datetime(2026, 4, 6),
        datetime(2026, 5, 1), datetime(2026, 5, 2), datetime(2026, 5, 3),
        datetime(2026, 5, 4), datetime(2026, 5, 5), datetime(2026, 6, 19),
        datetime(2026, 6, 20), datetime(2026, 6, 21), datetime(2026, 9, 25),
        datetime(2026, 9, 26), datetime(2026, 9, 27), datetime(2026, 10, 1),
        datetime(2026, 10, 2), datetime(2026, 10, 3), datetime(2026, 10, 4),
        datetime(2026, 10, 5), datetime(2026, 10, 6), datetime(2026, 10, 7)
    ]

    def __init__(self):
        self.daily_values = []  # 每日组合价值
        self.daily_dates = []  # 每日日期
        self.trade_records = []  # 交易记录
        self.pending_orders = {}  # 待处理订单 {data._name: order}

    def log(self, txt, dt=None):
        """日志"""
        if self.params.debug_mode:
            dt = dt or self.datas[0].datetime.datetime(0)
            print(f'{dt.isoformat()} - {txt}')

    def prenext(self):
        """数据不足时的回调"""
        self.next()

    def next(self):
        """
        每个交易日被Cerebro自动调用
        遍历所有股票数据进行策略判断
        """
        # 记录每日组合价值
        current_date = self.datas[0].datetime.datetime(0)
        portfolio_value = self.broker.getvalue()
        self.daily_values.append(portfolio_value)
        self.daily_dates.append(current_date)

        # 跳过预热期
        if len(self) < self.params.min_data_points:
            return

        # 时间过滤 - 14:30尾盘
        if current_date.hour == 14 and current_date.minute >= 30:
            return

        # 节假日过滤
        for holiday in self.节假日列表:
            if current_date.date() == holiday.date():
                return

        # 遍历所有股票
        for data in self.datas:
            self._process_stock(data)

    def _process_stock(self, data):
        """处理单个股票"""
        data_name = data._name

        # 检查是否有待处理订单
        if data_name in self.pending_orders:
            return  # 等待订单完成

        # 获取持仓
        position = self.getposition(data)

        if not position:
            # 无持仓，检查是否买入
            if self._should_buy(data):
                self._execute_buy(data)
        else:
            # 有持仓，检查是否卖出
            if self._should_sell(data, position):
                self._execute_sell(data)

    def _should_buy(self, data) -> bool:
        """判断是否应该买入 - 子类可重写"""
        return False

    def _should_sell(self, data, position) -> bool:
        """判断是否应该卖出 - 子类可重写"""
        return False

    def _execute_buy(self, data):
        """执行买入"""
        # 检查是否超过最大持仓数
        current_positions = len([d for d in self.datas if self.getposition(d).size > 0])
        max_pos = getattr(self.params, 'max_positions', 10)
        if current_positions >= max_pos:
            return

        price = data.close[0]
        cash = self.broker.getcash()
        available_cash = cash * 0.95  # 预留5%现金

        if available_cash < price * 100:
            return

        size = int(available_cash / price / 100) * 100
        if size < 100:
            return

        self.log(f'BUY {data._name} {size} @ {price:.2f}')
        order = self.buy(data=data, size=size)
        self.pending_orders[data._name] = order

    def _execute_sell(self, data):
        """执行卖出"""
        self.log(f'SELL {data._name}')
        order = self.close(data=data)
        self.pending_orders[data._name] = order

    def notify_order(self, order):
        """订单通知"""
        if order.status in [order.Completed, order.Canceled, order.Rejected]:
            data_name = order.data._name if hasattr(order.data, '_name') else order.data._name
            if data_name in self.pending_orders:
                del self.pending_orders[data_name]

            if order.status == order.Completed:
                current_date = self.datas[0].datetime.datetime(0)
                if order.isbuy():
                    self.trade_records.append({
                        'date': current_date,
                        'action': 'BUY',
                        'code': data_name,
                        'price': order.executed.price,
                        'size': order.executed.size,
                    })
                else:
                    self.trade_records.append({
                        'date': current_date,
                        'action': 'SELL',
                        'code': data_name,
                        'price': order.executed.price,
                        'size': order.executed.size,
                    })

    def get_daily_values(self) -> List[float]:
        """获取每日组合价值"""
        return self.daily_values

    def get_daily_dates(self) -> List[datetime]:
        """获取每日日期"""
        return self.daily_dates



def _extract_params_tuple(strategy_cls):
    """
    安全地从策略类提取 params 元组。

    backtrader 在类定义完成后会将 params 从 tuple-of-tuples 替换为
    AutoInfoClass 实例，此时直接 tuple(cls.params) 会抛出
    ``TypeError: 'type' object is not iterable``。

    本函数通过遍历 __mro__ 的 __dict__ 找到原始声明的 params tuple，
    若已被替换则从 AutoInfoClass._getitems() 重建等价结构。
    """
    result = []
    seen_names = set()

    for klass in getattr(strategy_cls, '__mro__', [strategy_cls]):
        if klass is object:
            continue
        raw = klass.__dict__.get('params', None)
        if raw is None:
            continue
        # 情况 1：原始 tuple/list 声明（类刚被解析，bt 尚未替换）
        if isinstance(raw, (list, tuple)):
            for item in raw:
                if isinstance(item, (list, tuple)) and len(item) >= 1:
                    name = item[0]
                    if name not in seen_names:
                        seen_names.add(name)
                        result.append(tuple(item))
        # 情况 2：backtrader AutoInfoClass（已实例化/替换）
        elif hasattr(raw, '_getitems'):
            try:
                for name, default in raw._getitems():
                    if name not in seen_names:
                        seen_names.add(name)
                        result.append((name, default))
            except Exception:
                pass
        # 情况 3：AutoInfoClass 作为 type（bt 内部形式）
        elif isinstance(raw, type) and hasattr(raw, '_getitems'):
            try:
                for name, default in raw._getitems():
                    if name not in seen_names:
                        seen_names.add(name)
                        result.append((name, default))
            except Exception:
                pass
    return tuple(result)


def make_portfolio_wrapper(base_strategy_class):
    """
    将任意 BaseStrategy 子类（单数据源）包装为 PortfolioStrategy（多数据源）。

    BaseStrategy 的 buy_condition()/sell_condition() 绑定 self.data.close 等单数据源属性。
    在多数据 Cerebro 中，self.data 固定指向第一个 feed，导致其他股票信号从不产生。

    本 wrapper 在 next() 中为每只股票独立维护状态和指标，
    所有 BaseStrategy 子类无需改动代码即可在 Portfolio 模式下正确运行。
    """

    # 安全提取两个类的 params，避免 AutoInfoClass 不可迭代错误
    portfolio_params = _extract_params_tuple(PortfolioStrategy)
    base_params = _extract_params_tuple(base_strategy_class)
    portfolio_param_names = {p[0] for p in portfolio_params}

    # 合并：PortfolioStrategy 的参数优先，base_strategy 独有参数追加
    merged_params = portfolio_params + tuple(
        p for p in base_params if p[0] not in portfolio_param_names
    )

    class WrappedPortfolioStrategy(PortfolioStrategy):
        params = merged_params

        def __init__(self):
            super().__init__()
            self._stock_states = {}
            for data in self.datas:
                self._init_stock_state(data._name)

        def _init_stock_state(self, name: str):
            self._stock_states[name] = {
                'entry_price': None,
                'prev_k': None,
                'prev_d': None,
                's1_half_sold': False,
            }

        def _get_state(self, data) -> dict:
            name = data._name
            if name not in self._stock_states:
                self._init_stock_state(name)
            return self._stock_states[name]

        # ---- 指标计算（向量化，与 BaseStrategy 逻辑一致） ----

        def _arr(self, data, field='close'):
            raw = getattr(data, field)
            return np.array(raw.array[:len(data)])

        def _calc_ema(self, arr, period: int) -> float:
            if len(arr) == 0:
                return 0.0
            if len(arr) < period:
                return float(arr[-1])
            return float(pd.Series(arr).ewm(span=period, adjust=False).mean().iloc[-1])

        def _calc_dif(self, data) -> float:
            arr = self._arr(data)
            return self._calc_ema(arr, 12) - self._calc_ema(arr, 26)

        def _calc_rsi(self, data, period: int = 14) -> float:
            arr = self._arr(data)
            if len(arr) < period + 1:
                return 50.0
            deltas = np.diff(arr[-(period + 1):])
            avg_gain = np.where(deltas > 0, deltas, 0.0).mean()
            avg_loss = np.where(deltas < 0, -deltas, 0.0).mean()
            if avg_loss < 1e-10:
                return 100.0 if avg_gain > 0 else 50.0
            return float(100.0 - 100.0 / (1.0 + avg_gain / avg_loss))

        def _calc_kdj(self, data, state: dict):
            arr_c = self._arr(data, 'close')
            arr_l = self._arr(data, 'low')
            arr_h = self._arr(data, 'high')
            n = 9
            if len(arr_c) < n:
                return 50.0, 50.0, 50.0
            lowest = float(np.min(arr_l[-n:]))
            highest = float(np.max(arr_h[-n:]))
            rsv = (float(arr_c[-1]) - lowest) / (highest - lowest) * 100.0 if highest != lowest else 50.0
            k = 2/3 * (state['prev_k'] or 50.0) + 1/3 * rsv
            d = 2/3 * (state['prev_d'] or 50.0) + 1/3 * k
            j = 3.0 * k - 2.0 * d
            state['prev_k'], state['prev_d'] = k, d
            return k, d, j

        def _calc_ma(self, data, period: int) -> float:
            arr = self._arr(data)
            if len(arr) < period:
                return float(arr[-1]) if len(arr) > 0 else 0.0
            return float(np.mean(arr[-period:]))

        def _calc_buy_score(self, data, state: dict) -> float:
            arr = self._arr(data)
            vol_arr = self._arr(data, 'volume')
            if len(arr) < 60:
                return 0.0
            score = 0.0
            k, d, j = self._calc_kdj(data, state)
            if j < 13:
                score += 2.0
            if self._calc_dif(data) > 0:
                score += 1.0
            if self._calc_rsi(data) < 50:
                score += 2.0
            if len(vol_arr) >= 60 and data.volume[0] > self._calc_ma(data, 60):
                score += 2.0
            if len(arr) >= 2 and arr[-2] != 0:
                pct = (arr[-1] - arr[-2]) / arr[-2] * 100
                if pct > 1.0:
                    score += 1.0
            return score

        def _calc_s1_score(self, data, state: dict) -> float:
            close = data.close[0]
            high = data.high[0]
            open_price = data.open[0]
            volume = data.volume[0]
            close_arr = self._arr(data, 'close')
            high_arr = self._arr(data, 'high')
            volume_arr = self._arr(data, 'volume')
            dif = self._calc_dif(data)
            k, d, j = self._calc_kdj(data, state)

            前10涨 = (close / close_arr[-10] - 1) * 100 if len(close_arr) >= 10 else 0
            前50涨 = (close / close_arr[-50] - 1) * 100 if len(close_arr) >= 50 else 0

            条件1基础 = False; 条件1 = False; 条件2基础 = False
            条件1评分 = 0; 条件2评分 = 0

            if len(high_arr) >= 60 and len(volume_arr) >= 60:
                hhv_vol = np.max(volume_arr[-60:])
                条件1基础 = (close < open_price) and (high == np.max(high_arr[-60:])) and (前10涨 > 10 or 前50涨 > 50)
                if 条件1基础:
                    条件1评分 = 10 if volume >= hhv_vol else (6.5 if volume * 1.42 >= hhv_vol else 0)
                条件1 = 条件1基础 and (volume * 1.42 >= hhv_vol)
                hhv_h60 = np.max(high_arr[-60:])
                hhv_h4 = np.max(high_arr[-4:]) if len(high_arr) >= 4 else high
                if hhv_h4 == hhv_h60 and high != hhv_h60:
                    vol_ma5 = np.mean(volume_arr[-5:]) if len(volume_arr) >= 5 else volume
                    涨幅 = (close - close_arr[-2]) / close_arr[-2] * 100 if len(close_arr) >= 2 and close_arr[-2] != 0 else 0
                    if volume > vol_ma5 and 涨幅 < -0.03 and close < open_price:
                        条件2基础 = True

            前3高位 = (2 - int(np.argmax(high_arr[-3:]))) if len(high_arr) >= 3 else 0
            if 条件2基础:
                ref_vol = volume_arr[-前3高位-1] if 前3高位 > 0 else volume_arr[-1]
                条件2评分 = 12 if volume >= ref_vol * 1.20 else (7.8 if volume >= ref_vol * 0.80 else 0)

            if len(close_arr) >= 26:
                ema12 = pd.Series(close_arr).ewm(span=12, adjust=False).mean().values
                ema26 = pd.Series(close_arr).ewm(span=26, adjust=False).mean().values
                dif_hist = list(ema12 - ema26)
            else:
                dif_hist = [dif] * len(close_arr)
            hhv_dif = np.max(dif_hist[-60:]) if len(dif_hist) >= 60 else dif

            实体 = open_price - close
            上影 = high - max(close, open_price)
            prev_close = close_arr[-2] if len(close_arr) >= 2 else close

            加分 = (
                (1 if 条件1 and dif < hhv_dif else 0) +
                (0.5 if 条件1 and 上影 > 实体 / 2 and close > prev_close else 0) +
                (1.8 if 条件2基础 else 0) +
                (0.8 if 条件2基础 and j < k < d else 0) +
                (2 if (条件1 or 条件2基础) and close < prev_close else 0) +
                (3 if len(volume_arr) >= 2 and volume_arr[-1] > volume_arr[-2] * 1.8 and volume >= volume_arr[-1] * 1.8 else 0)
            )
            return 条件1评分 + 条件2评分 + 加分

        def _should_buy(self, data) -> bool:
            if len(data) < self.params.min_data_points:
                return False
            name = data._name
            if 'ST' in name or '*ST' in name:
                return False
            state = self._get_state(data)
            return self._calc_buy_score(data, state) >= self.params.threshold

        def _should_sell(self, data, position) -> bool:
            if not position or position.size <= 0:
                return False
            state = self._get_state(data)
            s1 = self._calc_s1_score(data, state)
            if s1 > 10:
                return True
            if s1 > 5 and not state['s1_half_sold']:
                state['s1_half_sold'] = True
                return True
            entry = state['entry_price']
            if entry and entry > 0:
                stop = getattr(self.params, 'stop_loss_pct', 0.03)
                if (data.close[0] - entry) / entry < -stop:
                    return True
            return False

        def notify_order(self, order):
            super().notify_order(order)
            if order.status == order.Completed:
                state = self._get_state(order.data)
                if order.isbuy():
                    state['entry_price'] = order.executed.price
                    state['s1_half_sold'] = False
                elif order.issell():
                    state['entry_price'] = None

    WrappedPortfolioStrategy.__name__ = f'Portfolio_{base_strategy_class.__name__}'
    WrappedPortfolioStrategy.__qualname__ = f'Portfolio_{base_strategy_class.__name__}'
    return WrappedPortfolioStrategy


def _is_portfolio_strategy(strategy_class) -> bool:
    """
    检查策略类是否已经是 PortfolioStrategy 子类。
    同时检测两个来源：
    1. 本文件内部定义的 PortfolioStrategy（batch_backtest_portfolio.py）
    2. strategies.base.portfolio_strategy 中的 PortfolioStrategy
    两者是不同的类对象，issubclass 对其中一个返回 False 不代表不是组合策略。
    """
    try:
        if issubclass(strategy_class, PortfolioStrategy):
            return True
    except TypeError:
        pass
    # 兼容从 strategies.base.portfolio_strategy 导入的 PortfolioStrategy
    try:
        from strategies.base.portfolio_strategy import PortfolioStrategy as ExtPortfolioStrategy
        if issubclass(strategy_class, ExtPortfolioStrategy):
            return True
    except (ImportError, TypeError):
        pass
    return False


def _auto_wrap_strategy(strategy_class):
    """
    自动包装策略类：
    - 已经是 PortfolioStrategy → 直接返回
    - 是 BaseStrategy 子类 → 用 make_portfolio_wrapper 包装
    - 其他 bt.Strategy 子类 → 尝试直接使用（可能缺少 daily_values，回测后降级）
    """
    if _is_portfolio_strategy(strategy_class):
        return strategy_class

    # 检查是否为 BaseStrategy 子类（两处来源均检测，防止 Python 模块缓存导致同名不同对象）
    def _is_base_strategy(cls):
        try:
            from strategies.base.framework_strategy import BaseStrategy as _BS1
            if issubclass(cls, _BS1):
                return True
        except (ImportError, TypeError):
            pass
        # 按类名兜底：检查 MRO 里有没有叫 BaseStrategy 的类
        try:
            for parent in cls.__mro__:
                if parent.__name__ == 'BaseStrategy' and parent is not cls:
                    return True
        except AttributeError:
            pass
        return False

    if _is_base_strategy(strategy_class):
        logging.info(
            f"[auto_wrap] {strategy_class.__name__} 是 BaseStrategy 子类，"
            "自动包装为 WrappedPortfolioStrategy 以支持多股票 Portfolio 模式"
        )
        return make_portfolio_wrapper(strategy_class)

    # 按类名兜底：检查 MRO 里有没有叫 PortfolioStrategy 的类（不同模块同名类）
    try:
        for parent in strategy_class.__mro__:
            if parent.__name__ == 'PortfolioStrategy' and parent is not strategy_class:
                logging.info(
                    f"[auto_wrap] {strategy_class.__name__} 的 MRO 中含 PortfolioStrategy，直接使用"
                )
                return strategy_class
    except AttributeError:
        pass

    logging.warning(
        f"[auto_wrap] {strategy_class.__name__} 既不是 PortfolioStrategy 也不是 BaseStrategy，"
        "直接使用。如果指标全零，请让策略继承 PortfolioStrategy 或 BaseStrategy。"
    )
    return strategy_class



class SimpleBuyAndHoldPortfolio(PortfolioStrategy):
    """
    简单的买入持有策略 - 用于基准对比
    """

    def __init__(self):
        super().__init__()
        self.initialized = False

    def next(self):
        """每个股票只买入一次，之后持有"""
        # 记录每日价值
        current_date = self.datas[0].datetime.datetime(0)
        portfolio_value = self.broker.getvalue()
        self.daily_values.append(portfolio_value)
        self.daily_dates.append(current_date)

        # 跳过预热期
        if len(self) < self.params.min_data_points:
            return

        # 时间过滤
        if current_date.hour == 14 and current_date.minute >= 30:
            return

        # 节假日过滤
        for holiday in self.节假日列表:
            if current_date.date() == holiday.date():
                return

        # 每个股票只买入一次
        for data in self.datas:
            position = self.getposition(data)
            if not position:
                # 检查是否超过最大持仓
                current_positions = len([d for d in self.datas if self.getposition(d).size > 0])
                max_pos = getattr(self.params, 'max_positions', 10)
                if current_positions >= max_pos:
                    continue

                price = data.close[0]
                cash = self.broker.getcash()
                available_cash = cash * 0.95

                if available_cash < price * 100:
                    continue

                size = int(available_cash / price / 100) * 100
                if size >= 100:
                    self.log(f'BUY {data._name} {size} @ {price:.2f}')
                    self.buy(data=data, size=size)


def calculate_metrics(daily_values: List[float], initial_cash: float) -> Dict[str, Any]:
    """
    从每日组合价值计算绩效指标

    Args:
        daily_values: 每日组合价值列表
        initial_cash: 初始资金

    Returns:
        绩效指标字典
    """
    if not daily_values:
        return {}

    values = np.array(daily_values, dtype=float)
    valid_mask = np.isfinite(values)
    values = values[valid_mask]

    if len(values) == 0:
        return {}

    final_value = values[-1]
    total_return = (final_value - initial_cash) / initial_cash

    num_days = len(values)
    years = num_days / 252
    annualized_return = (final_value / initial_cash) ** (1 / years) - 1 if years > 0 else 0

    peak = values[0]
    max_drawdown = 0
    max_drawdown_duration = 0
    current_drawdown_duration = 0

    for value in values:
        if value > peak:
            peak = value
            current_drawdown_duration = 0
        else:
            drawdown = (peak - value) / peak
            if drawdown > max_drawdown:
                max_drawdown = drawdown
            current_drawdown_duration += 1
            if current_drawdown_duration > max_drawdown_duration:
                max_drawdown_duration = current_drawdown_duration

    daily_returns = np.diff(values) / values[:-1]
    daily_returns = daily_returns[np.isfinite(daily_returns)]
    volatility = np.std(daily_returns) * np.sqrt(252) if len(daily_returns) > 1 else 0

    risk_free_rate = 0.03
    excess_return = annualized_return - risk_free_rate
    sharpe_ratio = excess_return / volatility if volatility > 0 else 0

    negative_returns = daily_returns[daily_returns < 0]
    downside_volatility = np.std(negative_returns) * np.sqrt(252) if len(negative_returns) > 1 else 0.01
    sortino_ratio = excess_return / downside_volatility if downside_volatility > 0 else 0

    calmar_ratio = annualized_return / max_drawdown if max_drawdown > 0 else 0

    return {
        'initial_cash': initial_cash,
        'final_value': final_value,
        'total_return': total_return,
        'annualized_return': annualized_return,
        'max_drawdown': max_drawdown,
        'max_drawdown_duration': max_drawdown_duration,
        'volatility': volatility,
        'sharpe_ratio': sharpe_ratio,
        'sortino_ratio': sortino_ratio,
        'calmar_ratio': calmar_ratio,
        'num_days': num_days,
        'daily_values': values.tolist(),
    }


def run_portfolio_backtest(
    stocks: List[Dict],
    start_date: str,
    end_date: str,
    strategy_class,
    initial_cash: float = 1000000.0,
    commission: float = 0.0003,
    threshold: float = 8.0,
    max_positions: int = 10,
    save_to_db: bool = True,
    strategy_params: Dict = None,
    progress_callback=None,  # 新增：进度回调 callback(pct: int, msg: str)
) -> Dict[str, Any]:
    """
    运行投资组合回测

    修复内容：
    1. 批量查询 DuckDB（单次连接），避免逐股串行连接性能极差的问题
    2. 安全传参给策略类（_build_strategy_kwargs），兼容任何策略
    3. 限制单次加载股票数量上限（MAX_STOCKS_PER_BATCH），防止 OOM
    4. 支持 progress_callback 实时上报进度
    """
    import backtrader as bt

    def _progress(pct: int, msg: str):
        if progress_callback:
            try:
                progress_callback(pct, msg)
            except Exception:
                pass

    # 解析日期
    from_date = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:]}"
    to_date = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:]}"

    total_stocks = len(stocks)
    _progress(10, f'批量加载 {total_stocks} 只股票数据...')

    # ---- 限制股票数量，防止 OOM ----
    if total_stocks > MAX_STOCKS_PER_BATCH:
        logging.info(
            f"[INFO] 股票数量 {total_stocks} 超过配置上限 {MAX_STOCKS_PER_BATCH}，"
            f"截取前 {MAX_STOCKS_PER_BATCH} 只。如需更多，设置 BT_MAX_STOCKS=<数量>。"
        )
        stocks = stocks[:MAX_STOCKS_PER_BATCH]
        total_stocks = len(stocks)
        _progress(10, f'[注意] 股票数量已截至 {MAX_STOCKS_PER_BATCH} 只（原{len(stocks)+MAX_STOCKS_PER_BATCH}只），批量加载数据...')

    codes = [s.get('code', '') for s in stocks if s.get('code')]

    # ---- 批量查询所有股票数据（单次 DuckDB 连接） ----
    try:
        data_map = get_all_data_from_astock3_batch(codes, start_date, end_date)
    except Exception as e:
        import traceback
        return {
            'status': 'error',
            'error': f'批量查询数据失败: {e}',
            'traceback': traceback.format_exc()
        }

    _progress(25, f'数据加载完成，有效股票 {len(data_map)} 只，开始构建回测...')

    # 创建Cerebro
    cereb = bt.Cerebro()
    cereb.broker.setcash(initial_cash)
    cereb.broker.setcommission(commission=commission)

    # ---- 自动包装策略：BaseStrategy → WrappedPortfolioStrategy ----
    original_class_name = strategy_class.__name__
    strategy_class = _auto_wrap_strategy(strategy_class)
    if strategy_class.__name__ != original_class_name:
        logging.info(f"策略已自动包装: {original_class_name} → {strategy_class.__name__}")
        _progress(28, f'策略 [{original_class_name}] 已自动适配为 Portfolio 模式')

    # ---- 安全构建策略参数（关键修复：不强传 max_positions 给不认识它的策略） ----
    safe_kwargs = _build_strategy_kwargs(
        strategy_class, threshold, max_positions, strategy_params
    )
    cereb.addstrategy(strategy_class, **safe_kwargs)

    # 添加数据
    valid_stocks = []
    name_map = {s.get('code', ''): s.get('name', '') for s in stocks}

    bt_fromdate = datetime.strptime(from_date, '%Y-%m-%d').date()
    bt_todate = datetime.strptime(to_date, '%Y-%m-%d').date()

    for code, df in data_map.items():
        data = bt.feeds.PandasData(
            dataname=df,
            name=code,
            fromdate=bt_fromdate,
            todate=bt_todate
        )
        cereb.adddata(data, name=code)
        valid_stocks.append({'code': code, 'name': name_map.get(code, ''), 'data_len': len(df)})

    if not valid_stocks:
        return {
            'status': 'no_data',
            'error': '没有有效股票数据（数据不足60条或日期范围内无数据）'
        }

    _progress(35, f'已添加 {len(valid_stocks)} 只股票，开始运行回测...')

    # 添加分析器
    cereb.addanalyzer(bt.analyzers.Returns, _name='returns')
    cereb.addanalyzer(bt.analyzers.SharpeRatio, _name='sharpe')
    cereb.addanalyzer(bt.analyzers.DrawDown, _name='drawdown')
    cereb.addanalyzer(bt.analyzers.TradeAnalyzer, _name='trades')

    # 运行回测
    try:
        results = cereb.run()
    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        logging.error(f"[run_portfolio_backtest] Cerebro 运行失败: {e}\n{tb}")
        print(f"[run_portfolio_backtest] Cerebro 运行失败: {e}\n{tb}")
        return {
            'status': 'error',
            'error': f'Cerebro 运行失败: {e}',
            'traceback': tb
        }

    # results 是 [[strat]] 格式（cerebro.run() 返回策略实例列表的列表）
    strat = results[0][0] if (results and isinstance(results[0], list)) else results[0]

    _progress(85, '回测完成，计算指标...')

    # 获取每日价值：优先调用 PortfolioStrategy 的方法，否则 fallback 到属性
    if hasattr(strat, 'get_daily_values') and callable(strat.get_daily_values):
        daily_values = strat.get_daily_values()
        daily_dates = strat.get_daily_dates()
    elif hasattr(strat, 'daily_values') and hasattr(strat, 'daily_dates'):
        daily_values = list(strat.daily_values)
        daily_dates = list(strat.daily_dates)
    else:
        logging.warning(
            "策略未实现 get_daily_values()，也无 daily_values 属性，"
            "将使用初始资金+Returns分析器估算最终价值，每日曲线不可用。"
            "建议让策略继承 PortfolioStrategy 或自行维护 self.daily_values / self.daily_dates。"
        )
        try:
            returns_analysis = strat.analyzers.returns.get_analysis()
            total_return = returns_analysis.get('rtot', 0) or 0
            final_value = initial_cash * (1 + total_return)
        except Exception:
            final_value = initial_cash
        from datetime import datetime as _dt
        daily_values = [initial_cash, final_value]
        daily_dates = [_dt.strptime(from_date, '%Y-%m-%d'), _dt.strptime(to_date, '%Y-%m-%d')]

    # 计算指标
    metrics = calculate_metrics(daily_values, initial_cash)

    # 获取交易统计
    trades_analyzer = strat.analyzers.trades.get_analysis()

    total_trades = 0
    won_trades = 0
    lost_trades = 0

    if trades_analyzer:
        total_trades = trades_analyzer.get('total', {}).get('total', 0) or 0
        won_trades = trades_analyzer.get('won', {}).get('total', 0) or 0
        lost_trades = trades_analyzer.get('lost', {}).get('total', 0) or 0

    metrics['total_trades'] = total_trades
    metrics['winning_trades'] = won_trades
    metrics['losing_trades'] = lost_trades
    metrics['win_rate'] = won_trades / total_trades if total_trades > 0 else 0

    # 清理交易记录中的datetime对象，转换为ISO格式字符串
    # 注意：backtrader 重写了 __getattr__，会把未知属性代理给 self.lines 并抛 AttributeError，
    # 因此不能用 getattr(strat, 'trade_records', None) 作为安全访问。
    # 正确做法：先检查实例 __dict__ 再取值，绕过 backtrader 的代理。
    cleaned_trade_records = []
    raw_trade_records = strat.__dict__.get('trade_records', []) or []
    for record in raw_trade_records:
        cleaned_record = {}
        for k, v in record.items():
            if hasattr(v, 'isoformat'):  # datetime对象
                cleaned_record[k] = v.isoformat()
            elif isinstance(v, (np.integer, np.floating)):  # numpy数值类型
                cleaned_record[k] = float(v) if isinstance(v, np.floating) else int(v)
            else:
                cleaned_record[k] = v
        cleaned_trade_records.append(cleaned_record)

    # 清理每日values中的numpy类型
    cleaned_daily_values = [float(v) if isinstance(v, (np.integer, np.floating)) else v for v in daily_values]

    _progress(95, '整理结果...')

    # 构建结果
    result = {
        'status': 'success',
        'initial_cash': float(initial_cash),
        'final_value': float(metrics.get('final_value', initial_cash)),
        'total_return': float(metrics.get('total_return', 0)),
        'annualized_return': float(metrics.get('annualized_return', 0)),
        'max_drawdown': float(metrics.get('max_drawdown', 0)),
        'max_drawdown_duration': int(metrics.get('max_drawdown_duration', 0)),
        'sharpe_ratio': float(metrics.get('sharpe_ratio', 0)),
        'sortino_ratio': float(metrics.get('sortino_ratio', 0)),
        'calmar_ratio': float(metrics.get('calmar_ratio', 0)),
        'volatility': float(metrics.get('volatility', 0)),
        'total_trades': int(total_trades),
        'winning_trades': int(won_trades),
        'losing_trades': int(lost_trades),
        'win_rate': float(metrics.get('win_rate', 0)),
        'num_days': int(metrics.get('num_days', 0)),
        'valid_stocks': int(len(valid_stocks)),
        'trade_records': cleaned_trade_records,
        'daily_values': cleaned_daily_values,
        'daily_dates': [d.isoformat() for d in daily_dates],
        'equity_curve': [
            (d.isoformat() if hasattr(d, 'isoformat') else d,
             float(v) if isinstance(v, (np.integer, np.floating)) else v)
            for d, v in zip(daily_dates, daily_values)
        ],
    }

    return result


def print_portfolio_results(result: Dict[str, Any]):
    """打印投资组合回测结果"""
    print("\n" + "=" * 60)
    print("投资组合回测结果")
    print("=" * 60)
    print(f"初始资金:      {result['initial_cash']:>15,.2f}")
    print(f"最终价值:      {result['final_value']:>15,.2f}")
    print(f"总收益率:      {result['total_return']*100:>15.2f}%")
    print(f"年化收益率:    {result['annualized_return']*100:>15.2f}%")
    print("-" * 60)
    print(f"夏普比率:      {result['sharpe_ratio']:>15.2f}")
    print(f"索提比率:      {result['sortino_ratio']:>15.2f}")
    print(f"卡玛比率:      {result['calmar_ratio']:>15.2f}")
    print(f"波动率:        {result['volatility']*100:>15.2f}%")
    print(f"最大回撤:      {result['max_drawdown']*100:>15.2f}%")
    print(f"最大回撤天数:  {result['max_drawdown_duration']:>15}")
    print("-" * 60)
    print(f"总交易次数:    {result['total_trades']:>15}")
    print(f"盈利交易:      {result['winning_trades']:>15}")
    print(f"亏损交易:      {result['losing_trades']:>15}")
    print(f"胜率:          {result['win_rate']*100:>15.2f}%")
    print("-" * 60)
    print(f"有效股票数:    {result['valid_stocks']:>15}")
    print(f"回测天数:      {result['num_days']:>15}")
    print("=" * 60)


def save_results_to_csv(result: Dict[str, Any], output_path: str):
    """保存结果到CSV"""
    # 保存权益曲线
    equity_df = pd.DataFrame(result['equity_curve'], columns=['date', 'value'])
    equity_path = output_path.replace('.csv', '_equity.csv')
    equity_df.to_csv(equity_path, index=False)
    print(f"权益曲线已保存到: {equity_path}")

    # 保存交易记录
    if result.get('trade_records'):
        trades_df = pd.DataFrame(result['trade_records'])
        trades_path = output_path.replace('.csv', '_trades.csv')
        trades_df.to_csv(trades_path, index=False, encoding='utf-8-sig')
        print(f"交易记录已保存到: {trades_path}")


def main():
    parser = argparse.ArgumentParser(description='投资组合回测 - True Portfolio Simulation')

    parser.add_argument('--stocks', '-s', nargs='+', default=None,
                       help='股票代码列表')
    parser.add_argument('--stock-file', '-f', default=None,
                       help='股票代码文件')
    parser.add_argument('--limit', '-l', type=int, default=None,
                       help='从数据库获取的股票数量')
    parser.add_argument('--industry', '-i', type=str, default=None,
                       help='行业过滤')
    parser.add_argument('--start', default='20250101', help='开始日期 YYYYMMDD')
    parser.add_argument('--end', default='20251231', help='结束日期 YYYYMMDD')
    parser.add_argument('--cash', type=float, default=1000000, help='初始资金')
    parser.add_argument('--strategy', default='天宫B1策略v1', help='策略文件名')
    parser.add_argument('--threshold', type=float, default=8.0, help='策略阈值')
    parser.add_argument('--max-positions', type=int, default=10, help='最大持仓数')
    parser.add_argument('--no-save', action='store_true', help='不保存到CSV')
    parser.add_argument('--output', '-o', default=None, help='结果输出路径')
    parser.add_argument('--benchmark', action='store_true', help='同时运行基准测试(买入持有)')

    args = parser.parse_args()

    # 获取股票列表
    stocks = None

    if args.stocks:
        stocks = [{'code': code, 'name': ''} for code in args.stocks]
    elif args.stock_file:
        with open(args.stock_file, 'r') as f:
            codes = [line.strip() for line in f if line.strip()]
        stocks = [{'code': code, 'name': ''} for code in codes]
    elif args.limit or args.industry:
        stock_df = get_stock_list_from_db(limit=args.limit, industry=args.industry)
        stocks = stock_df.to_dict('records')
    else:
        # 默认从数据库获取50只
        stock_df = get_stock_list_from_db(limit=50)
        stocks = stock_df.to_dict('records')

    print(f"股票数量: {len(stocks)}")
    print(f"回测期间: {args.start} - {args.end}")
    print(f"初始资金: {args.cash:,.0f}")
    print(f"策略: {args.strategy}")

    # 加载策略
    try:
        strategy_class = load_strategy_class(args.strategy)
    except Exception as e:
        print(f"加载策略失败: {e}")
        # 使用默认策略
        strategy_class = SimpleBuyAndHoldPortfolio
        print("使用默认策略: SimpleBuyAndHoldPortfolio")

    # 运行投资组合回测
    result = run_portfolio_backtest(
        stocks=stocks,
        start_date=args.start,
        end_date=args.end,
        strategy_class=strategy_class,
        initial_cash=args.cash,
        commission=0.0003,
        threshold=args.threshold,
        max_positions=args.max_positions,
        save_to_db=not args.no_save,
    )

    if result['status'] == 'success':
        print_portfolio_results(result)

        # 基准测试
        if args.benchmark:
            print("\n运行基准测试 (买入持有)...")
            benchmark_result = run_portfolio_backtest(
                stocks=stocks,
                start_date=args.start,
                end_date=args.end,
                strategy_class=SimpleBuyAndHoldPortfolio,
                initial_cash=args.cash,
                commission=0.0003,
                threshold=0,
                max_positions=args.max_positions,
                save_to_db=False,
            )

            if benchmark_result['status'] == 'success':
                print("\n" + "-" * 40)
                print("基准测试结果 (买入持有)")
                print("-" * 40)
                print(f"总收益率: {benchmark_result['total_return']*100:.2f}%")
                print(f"年化收益率: {benchmark_result['annualized_return']*100:.2f}%")
                print(f"夏普比率: {benchmark_result['sharpe_ratio']:.2f}")
                print(f"最大回撤: {benchmark_result['max_drawdown']*100:.2f}%")

                # 计算超额收益
                excess_return = result['total_return'] - benchmark_result['total_return']
                print(f"\n超额收益: {excess_return*100:.2f}%")

        # 保存结果
        if not args.no_save:
            output_path = args.output or f"portfolio_backtest_{args.start}_{args.end}.csv"
            save_results_to_csv(result, output_path)

    else:
        print(f"回测失败: {result.get('error', '未知错误')}")


if __name__ == '__main__':
    main()