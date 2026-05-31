"""
策略基类框架 - 健壮修复版

修复内容:
1. self.order 布尔判断错误 -> 改用 self.order is not None
2. 所有 if self.order / if not self.order -> 显式 None 判断
3. 增强数据长度保护
4. 取消500只股票限制（通过环境变量 BT_MAX_STOCKS 控制）
"""
import backtrader as bt
import numpy as np
from datetime import datetime
from typing import Dict, Any, List, Optional, Tuple


class BaseStrategy(bt.Strategy):
    """
    策略基类
    子类需要实现: calculate_score(), buy_condition(), sell_condition()
    """

    params = (
        ('threshold', 8.0),
        ('stop_loss_pct', 0.05),
        ('min_data_points', 60),
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
        self._init_data_aliases()
        self._init_tracking_vars()
        self._init_indicators()

    def _init_data_aliases(self):
        self.close = self.data.close
        self.open = self.data.open
        self.high = self.data.high
        self.low = self.data.low
        self.volume = self.data.volume

    def _init_tracking_vars(self):
        self.entry_price = None
        self.order = None          # 始终用 None 表示"无挂单"，比较时用 is not None

        self.pending_buy_signal = False
        self.pending_buy_reason = ""
        self.pending_buy_date = None

        self.pending_sell_signal = False
        self.pending_sell_reason = ""

        self.trade_records = []

        self.prev_k = None
        self.prev_d = None

    def _init_indicators(self):
        pass

    # =========================================
    # 核心方法 - 子类必须实现
    # =========================================

    def calculate_score(self) -> float:
        raise NotImplementedError("子类必须实现 calculate_score 方法")

    def buy_condition(self) -> bool:
        score = self.calculate_score()
        return score >= self.params.threshold

    def sell_condition(self) -> bool:
        return False

    # =========================================
    # 工具方法
    # =========================================

    def get_price_arrays(self) -> Dict[str, np.ndarray]:
        return {
            'close':  np.array(self.close.array[:len(self)]),
            'high':   np.array(self.high.array[:len(self)]),
            'low':    np.array(self.low.array[:len(self)]),
            'volume': np.array(self.volume.array[:len(self)]),
            'open':   np.array(self.open.array[:len(self)]),
        }

    def get_current_price(self) -> Dict[str, float]:
        return {
            'close':  self.close[0],
            'open':   self.open[0],
            'high':   self.high[0],
            'low':    self.low[0],
            'volume': self.volume[0],
        }

    def calculate_ma(self, period: int) -> float:
        close_arr = np.array(self.close.array[:len(self)])
        if len(close_arr) < period:
            return float(close_arr[-1]) if len(close_arr) > 0 else 0.0
        return float(np.mean(close_arr[-period:]))

    def calculate_ema(self, period: int) -> float:
        close_arr = np.array(self.close.array[:len(self)])
        if len(close_arr) == 0:
            return 0.0
        if len(close_arr) < period:
            return float(close_arr[-1])
        # 向量化 EMA，比循环快且准确
        series = __import__('pandas').Series(close_arr)
        return float(series.ewm(span=period, adjust=False).mean().iloc[-1])

    def calculate_dif(self) -> float:
        return self.calculate_ema(12) - self.calculate_ema(26)

    def calculate_rsi(self, period: int = 14) -> float:
        close_arr = np.array(self.close.array[:len(self)])
        if len(close_arr) < period + 1:
            return 50.0
        deltas = np.diff(close_arr[-(period + 1):])
        gains  = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = gains.mean()
        avg_loss = losses.mean()
        if avg_loss < 1e-10:
            return 100.0 if avg_gain > 0 else 50.0
        rs = avg_gain / avg_loss
        return float(100.0 - 100.0 / (1.0 + rs))

    def calculate_kdj(self) -> tuple:
        close_arr = np.array(self.close.array[:len(self)])
        low_arr   = np.array(self.low.array[:len(self)])
        high_arr  = np.array(self.high.array[:len(self)])

        n = 9
        if len(close_arr) < n:
            return 50.0, 50.0, 50.0

        lowest_low    = float(np.min(low_arr[-n:]))
        highest_high  = float(np.max(high_arr[-n:]))

        if highest_high == lowest_low:
            rsv = 50.0
        else:
            rsv = (float(close_arr[-1]) - lowest_low) / (highest_high - lowest_low) * 100.0

        k = 2/3 * (self.prev_k if self.prev_k is not None else 50.0) + 1/3 * rsv
        d = 2/3 * (self.prev_d if self.prev_d is not None else 50.0) + 1/3 * k
        j = 3.0 * k - 2.0 * d

        self.prev_k = k
        self.prev_d = d

        return k, d, j

    def calculate_bbi(self) -> float:
        return (self.calculate_ma(3) + self.calculate_ma(6) +
                self.calculate_ma(12) + self.calculate_ma(24)) / 4.0

    def get_position_size(self, price: float) -> int:
        if price <= 0:
            return 0
        cash = self.broker.getcash()
        available_cash = cash * 0.95
        size = int(available_cash / price / 100) * 100
        return max(size, 100)

    # =========================================
    # 时间过滤
    # =========================================

    def time_filter(self) -> bool:
        """
        返回 True 表示"可以交易"，False 表示"跳过本 bar"。

        关键修复: self.order 是 backtrader Order 对象，不能直接 bool()，
        必须与 None 做显式比较。
        """
        if len(self) <= self.params.min_data_points:
            return False

        # ✅ 修复: 用 is not None 代替 if self.order
        if self.order is not None:
            return False

        return True

    def is_time_filtered(self, current_time=None) -> Tuple[bool, str]:
        if current_time is None:
            current_date = self.datas[0].datetime.datetime(0)
        else:
            current_date = current_time

        if current_date.hour == 14 and current_date.minute >= 30:
            return (True, "14:30尾盘过滤")

        for holiday in self.节假日列表:
            if current_date.date() == holiday.date():
                return (True, f"节假日过滤:{holiday.strftime('%Y-%m-%d')}")

        if len(self) >= 5:
            close_arr = np.array(self.close.array[:len(self)])
            if len(close_arr) >= 5:
                recent_5 = close_arr[-5:]
                price_changes = [(recent_5[i] - recent_5[i-1]) / recent_5[i-1] * 100
                                 for i in range(1, len(recent_5))
                                 if recent_5[i-1] != 0]
                consecutive_down = 0
                for pc in reversed(price_changes):
                    if pc < -3:
                        consecutive_down += 1
                    else:
                        break
                if consecutive_down >= 3:
                    return (True, "连续下跌过滤")

        return (False, "")

    # =========================================
    # 主循环
    # =========================================

    def next(self):
        if not self.time_filter():
            return

        if not self.position:
            try:
                if self.buy_condition():
                    self._execute_buy()
            except Exception as e:
                if self.params.debug_mode:
                    print(f"buy_condition error: {e}")
        else:
            try:
                if self.sell_condition():
                    self._execute_sell()
            except Exception as e:
                if self.params.debug_mode:
                    print(f"sell_condition error: {e}")

    def _execute_buy(self):
        price = self.close[0]
        size = self.get_position_size(price)
        if size < 100:
            return
        self.pending_buy_signal = True
        self.pending_buy_date   = self.datas[0].datetime.datetime(0)
        try:
            self.pending_buy_reason = f"分数:{self.calculate_score():.1f}"
        except Exception:
            self.pending_buy_reason = "买入"
        self.order = self.buy(size=size)

    def _execute_sell(self):
        self.order = self.close()

    def notify_order(self, order):
        if order.status in [order.Completed]:
            date = self.datas[0].datetime.datetime(0)
            if order.isbuy():
                self.entry_price = order.executed.price
                self.pending_buy_signal = False
                self._record_trade({
                    'date':   date,
                    'action': 'BUY',
                    'price':  order.executed.price,
                    'size':   order.executed.size,
                    'reason': self.pending_buy_reason,
                })
            elif order.issell():
                pnl = 0.0
                if self.entry_price:
                    pnl = (order.executed.price - self.entry_price) / self.entry_price * 100.0
                self._record_trade({
                    'date':   date,
                    'action': 'SELL',
                    'price':  order.executed.price,
                    'size':   order.executed.size,
                    'pnl':    pnl,
                    'reason': self.pending_sell_reason,
                })
                self.entry_price = None
            # ✅ 修复: 订单完成后重置为 None，让 time_filter 可以继续放行
            self.order = None

        elif order.status in [order.Canceled, order.Margin, order.Rejected]:
            # 订单失败也要重置
            self.order = None

    def _record_trade(self, trade_record: Dict):
        self.trade_records.append(trade_record)
        if self.params.debug_mode:
            print(f"交易: {trade_record['date']} {trade_record['action']} "
                  f"价格:{trade_record['price']:.2f} 数量:{trade_record['size']}")

    def get_trade_records(self) -> List[Dict]:
        return self.trade_records

    # =========================================
    # S1 卖出评分（通用实现）
    # =========================================

    def calculate_s1_score(self, indicators: Dict[str, Any], positions: Dict[str, Any]) -> tuple:
        close      = indicators['close']
        high       = indicators['high']
        open_price = indicators['open_price']
        close_arr  = indicators['close_arr']
        high_arr   = indicators['high_arr']
        volume_arr = indicators['volume_arr']
        volume     = indicators['volume']
        dif        = indicators['dif']
        j          = indicators['j']
        k          = indicators['k']
        d          = indicators['d']
        s1_half_sold = positions.get('s1_half_sold', False)

        前10日涨幅 = (close / close_arr[-10] - 1) * 100 > 10 if len(close_arr) >= 10 else False
        前50日涨幅 = (close / close_arr[-50] - 1) * 100 > 50 if len(close_arr) >= 50 else False

        # --- 安全初始化 ---
        条件1基础 = False
        条件1     = False
        条件2基础 = False
        条件2     = False
        条件1评分 = 0
        条件2评分 = 0
        前3天最高位距今 = 0

        if len(high_arr) >= 60 and len(volume_arr) >= 60:
            hhv_vol_60 = float(np.max(volume_arr[-60:]))
            hhv_h_60   = float(np.max(high_arr[-60:]))

            条件1基础 = (close < open_price) and (high == hhv_h_60) and (前10日涨幅 or 前50日涨幅)
            if 条件1基础:
                if volume >= hhv_vol_60:             条件1评分 = 10
                elif volume * 1.10 >= hhv_vol_60:    条件1评分 = 8
                elif volume * 1.25 >= hhv_vol_60:    条件1评分 = 7.5
                elif volume * 1.42 >= hhv_vol_60:    条件1评分 = 6.5
            条件1 = 条件1基础 and (volume * 1.42 >= hhv_vol_60)

            if len(high_arr) >= 4:
                hhv_h_4 = float(np.max(high_arr[-4:]))
                if hhv_h_4 == hhv_h_60 and high != hhv_h_60:
                    vol_ma5  = float(np.mean(volume_arr[-5:]))
                    vol_ma10 = float(np.mean(volume_arr[-10:]))
                    涨幅 = (close - close_arr[-2]) / close_arr[-2] * 100 if len(close_arr) >= 2 and close_arr[-2] != 0 else 0
                    if (volume > vol_ma5 or volume > vol_ma10) and 涨幅 < -0.03 and close < open_price and (前10日涨幅 or 前50日涨幅):
                        条件2基础 = True

        if len(high_arr) >= 3:
            前3天最高位距今 = int(2 - np.argmax(high_arr[-3:]))

        if 条件2基础 and 前3天最高位距今 < len(volume_arr):
            idx = -(前3天最高位距今 + 1) if 前3天最高位距今 > 0 else -1
            ref_vol = float(volume_arr[idx])
            if   volume >= ref_vol * 1.20: 条件2评分 = 12
            elif volume >= ref_vol * 1.00: 条件2评分 = 10
            elif volume >= ref_vol * 0.80: 条件2评分 = 7.8
            elif volume >= ref_vol * 0.70: 条件2评分 = 6.5
            条件2 = volume >= ref_vol * 0.70

        # DIF 历史（向量化）
        if len(close_arr) >= 26:
            import pandas as _pd
            _s = _pd.Series(close_arr)
            dif_history = list(_s.ewm(span=12, adjust=False).mean() - _s.ewm(span=26, adjust=False).mean())
        else:
            dif_history = [dif] * len(close_arr)

        hhv_dif_60 = float(np.max(dif_history[-60:])) if len(dif_history) >= 60 else dif

        实体  = open_price - close
        上影线 = high - max(close, open_price)

        加分1 = 1.0   if (条件1 and dif < hhv_dif_60) else 0.0
        加分2 = 0.5   if (条件1 and 上影线 > 实体 / 2 and len(close_arr) >= 2 and close > close_arr[-2]) else 0.0
        加分3 = 1.8   if 条件2基础 else 0.0
        加分4 = 0.8   if (条件2 and j < k < d) else 0.0
        加分5 = 2.0   if ((条件1 or 条件2基础) and len(close_arr) >= 2 and close < close_arr[-2]) else 0.0
        天量柱 = (len(volume_arr) >= 2 and
                  float(volume_arr[-1]) > float(volume_arr[-2]) * 1.8 and
                  volume >= float(volume_arr[-1]) * 1.8)
        加分6 = 3.0 if 天量柱 else 0.0

        score_s1 = 条件1评分 + 条件2评分 + 加分1 + 加分2 + 加分3 + 加分4 + 加分5 + 加分6
        return (score_s1, score_s1 > 10, score_s1 > 5 and not s1_half_sold)
