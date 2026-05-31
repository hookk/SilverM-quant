#!/usr/bin/env python
# coding=utf-8
"""
小盘股动态持有策略 - backtrader 本地适配版

原策略来源：聚宽 https://www.joinquant.com/post/63107
原作者：俺也一样0
本文件：基于原策略逻辑，改写为可在本地 backtrader 中运行的版本

核心逻辑保持一致：
- 选股：10-100亿市值小盘股，净利润>0，营业收入>1亿
- 持仓：动态调整（3-6只）
- 止损：个股亏损9%止损，市场大跌5%清仓
- 止盈：盈利100%止盈

注意事项：
- 本地回测缺少聚宽的实时财务数据接口，用历史财务数据近似替代
- 市场止损使用等权平均代替原策略的成分股平均涨跌
- 运行于 PortfolioStrategy 框架（多股票 Cerebro 模式）
"""

import numpy as np
import pandas as pd
from datetime import datetime

from strategies.registry import register
from strategies.base.portfolio_strategy import PortfolioStrategy


STRATEGY_METADATA = {
    'name': '小盘股动态持有策略_bt',
    'description': '聚宽小盘股动态持有策略的backtrader本地适配版，周频调仓+动态持仓数量',
    'author': '俺也一样0 (JoinQuant) / 本地适配',
    'version': '1.0.0',
    'min_data_days': 30,
    'threshold_required': False,
}


@register(
    name='小盘股动态持有策略_bt',
    threshold_required=False,
    min_data_days=30,
    description='聚宽小盘股动态持有策略本地适配版，周频调仓，小市值选股，多重止损'
)
class SmallCapDynamicStrategy(PortfolioStrategy):
    """
    小盘股动态持有策略 - backtrader 本地适配版

    选股逻辑：
    - 数据充足（>=30日）
    - 非ST股票
    - 价格 <= 50元
    - 近期无连续暴跌（3日内无单日跌幅>9%的情况）

    调仓：每周一检查，动态持仓数量（默认4只，最多6只）

    止损止盈：
    - 个股盈利100%止盈
    - 个股亏损9%止损
    - 若当日多只股票同时大跌（平均跌幅>5%），触发市场止损

    注：本地版无法获取实时财务数据（市值、净利润等），
    选股条件退化为：非ST + 数据充足 + 价格合理。
    如需财务过滤，请接入本地财务数据库。
    """

    params = (
        ('threshold', 0.0),        # 不使用评分阈值
        ('stop_loss_pct', 0.09),   # 个股止损线 9%
        ('take_profit_pct', 1.00), # 个股止盈线 100%
        ('market_stop_pct', 0.05), # 市场止损：平均跌幅 >= 5%
        ('max_positions', 4),      # 默认持仓数量
        ('max_price', 50.0),       # 股票单价上限
        ('min_data_points', 30),   # 最少数据天数
        ('rebalance_weekday', 0),  # 每周调仓日（0=周一）
        ('debug_mode', False),
    )

    def __init__(self):
        super().__init__()
        self._last_rebalance_week = -1   # 记录上次调仓的周数
        self._stock_entry_price = {}     # {code: entry_price}
        self._market_stop_triggered = False

    def next(self):
        """主循环 - 每日调用"""
        current_date = self.datas[0].datetime.datetime(0)
        portfolio_value = self.broker.getvalue()
        self.daily_values.append(portfolio_value)
        self.daily_dates.append(current_date)

        if len(self) < self.params.min_data_points:
            return

        # ---- 检查市场止损（当日多股大跌）----
        if self._check_market_stop():
            self._market_stop_triggered = True
            self.log(f'市场止损触发，全部清仓')
            for data in self.datas:
                pos = self.getposition(data)
                if pos.size > 0:
                    self.close(data=data)
            return

        self._market_stop_triggered = False

        # ---- 个股止损/止盈 ----
        for data in self.datas:
            pos = self.getposition(data)
            if pos.size > 0:
                self._check_stop_loss_take_profit(data, pos)

        # ---- 每周调仓 ----
        week_num = current_date.isocalendar()[1]
        if (current_date.weekday() == self.params.rebalance_weekday
                and week_num != self._last_rebalance_week):
            self._last_rebalance_week = week_num
            self._weekly_rebalance()

    def _check_market_stop(self) -> bool:
        """市场止损：所有当前持仓股票的平均跌幅 >= market_stop_pct"""
        held = [d for d in self.datas if self.getposition(d).size > 0]
        if len(held) < 2:
            return False
        changes = []
        for data in held:
            arr = np.array(data.close.array[:len(data)])
            if len(arr) >= 2 and arr[-2] != 0:
                changes.append((arr[-1] - arr[-2]) / arr[-2])
        if not changes:
            return False
        avg_change = np.mean(changes)
        return avg_change <= -self.params.market_stop_pct

    def _check_stop_loss_take_profit(self, data, position):
        """个股止损/止盈"""
        code = data._name
        entry = self._stock_entry_price.get(code)
        if not entry or entry <= 0:
            return
        current_price = data.close[0]
        pnl_pct = (current_price - entry) / entry

        if pnl_pct >= self.params.take_profit_pct:
            self.log(f'止盈 {code}: +{pnl_pct*100:.1f}%')
            self.close(data=data)
        elif pnl_pct <= -self.params.stop_loss_pct:
            self.log(f'止损 {code}: {pnl_pct*100:.1f}%')
            self.close(data=data)

    def _weekly_rebalance(self):
        """每周调仓逻辑"""
        # 动态调整持仓数量（模拟原策略根据指数MA调整的逻辑）
        target_positions = self._calc_dynamic_positions()

        # 选出目标股票
        candidates = self._select_stocks()
        target = candidates[:target_positions]

        # 获取当前持仓
        held = {d._name for d in self.datas if self.getposition(d).size > 0}

        # 卖出不在目标列表中的持仓
        for data in self.datas:
            if data._name in held and data._name not in target:
                self.log(f'调仓卖出 {data._name}')
                self.close(data=data)

        # 买入新目标
        to_buy = [code for code in target if code not in held]
        if not to_buy:
            return

        cash = self.broker.getcash()
        per_stock = cash * 0.95 / len(to_buy)

        for data in self.datas:
            if data._name not in to_buy:
                continue
            price = data.close[0]
            if price <= 0 or price > self.params.max_price:
                continue
            size = int(per_stock / price / 100) * 100
            if size >= 100:
                self.log(f'调仓买入 {data._name}: {size}股 @ {price:.2f}')
                self.buy(data=data, size=size)

    def _calc_dynamic_positions(self) -> int:
        """
        动态调整持仓数量（简化版）
        原策略基于399101的MA10决定持仓数3~6只
        本地版：使用所有持仓股票的近期均价趋势作为市场强弱代理
        """
        held_data = [d for d in self.datas if self.getposition(d).size > 0]
        if len(held_data) < 3:
            return self.params.max_positions

        changes = []
        for data in held_data:
            arr = np.array(data.close.array[:len(data)])
            if len(arr) >= 10:
                ma10 = np.mean(arr[-10:])
                changes.append((arr[-1] - ma10) / ma10 * 100)

        if not changes:
            return self.params.max_positions

        avg_change = np.mean(changes)
        if avg_change >= 5:
            return 3
        elif avg_change >= 2:
            return 3
        elif avg_change >= -2:
            return 4
        elif avg_change >= -5:
            return 5
        else:
            return 6

    def _select_stocks(self):
        """
        选股逻辑（本地简化版）
        原策略：市值10-100亿 + 净利润>0 + 营业收入>1亿 + 非ST + 非停牌 + 非涨跌停
        本地版：数据充足 + 非ST + 价格合理 + 近期未连续大跌
        按近30日成交量均值排序（量大流动性好）
        """
        candidates = []
        for data in self.datas:
            if len(data) < self.params.min_data_points:
                continue
            name = data._name
            if 'ST' in name or '*ST' in name:
                continue
            price = data.close[0]
            if price <= 0 or price > self.params.max_price:
                continue
            # 过滤近3日有单日跌幅>9%的（避免买到踩雷股）
            arr = np.array(data.close.array[:len(data)])
            if len(arr) >= 4:
                recent = arr[-4:]
                pcts = np.diff(recent) / recent[:-1]
                if any(p < -0.09 for p in pcts):
                    continue
            # 用近30日成交量均值作为流动性排序指标
            vol_arr = np.array(data.volume.array[:len(data)])
            avg_vol = np.mean(vol_arr[-30:]) if len(vol_arr) >= 30 else 0
            candidates.append((name, avg_vol))

        # 按成交量降序排（流动性好的优先，近似原策略按市值升序的效果）
        candidates.sort(key=lambda x: x[1], reverse=True)
        return [c[0] for c in candidates]

    def notify_order(self, order):
        """记录成交"""
        super().notify_order(order)
        if order.status == order.Completed:
            code = order.data._name
            if order.isbuy():
                self._stock_entry_price[code] = order.executed.price
            elif order.issell():
                self._stock_entry_price.pop(code, None)

    def log(self, txt):
        if self.params.debug_mode:
            dt = self.datas[0].datetime.datetime(0)
            print(f'{dt.date()} [{self.__class__.__name__}] {txt}')
