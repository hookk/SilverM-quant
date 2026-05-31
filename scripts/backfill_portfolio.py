#!/usr/bin/env python
# coding=utf-8
# scripts/backfill_portfolio.py
import subprocess
from datetime import date, timedelta

start = date(2025, 1, 1)   # 改为你第一笔买入日期
end = date.today()
d = start
while d <= end:
    if d.weekday() < 5:   # 跳过周末（非交易日 update_portfolio_daily.py 内部已处理）
        subprocess.run(['python', 'scripts/update_portfolio_daily.py',
                       '--date', d.strftime('%Y%m%d')])
    d += timedelta(days=1)
