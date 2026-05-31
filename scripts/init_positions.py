#!/usr/bin/env python
# coding=utf-8
#!/usr/bin/env python3
"""初始化 positions 表 - 录入实盘持仓和历史交易"""
import duckdb, os
from datetime import date

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       'data', 'Astock3.duckdb')
conn = duckdb.connect(DB_PATH)

# ── 当前持仓（status='holding'） ─────────────────────────────────────────────
# 按实际情况修改以下数据
holding_positions = [
    # (id, code, name, strategy, buy_date, shares, buy_price, stop_loss_pct, notes)
    (1, '600519', '贵州茅台', 'b1', date(2026, 4, 1), 100, 1580.00, 0.03, ''),
    # 继续添加...
]

max_id = conn.execute("SELECT COALESCE(MAX(id),0) FROM positions").fetchone()[0]
for i, (pid, code, name, strategy, buy_date, shares, buy_price, sl, notes) in enumerate(holding_positions):
    conn.execute("""
        INSERT OR IGNORE INTO positions
        (id, code, name, strategy, buy_date, shares, buy_price, stop_loss_pct, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'holding', ?)
    """, [max_id + i + 1, code, name, strategy, buy_date, shares, buy_price, sl, notes])

print(f"✅ 插入 {len(holding_positions)} 条持仓记录")

# ── 历史交易（status='sold'） ─────────────────────────────────────────────────
# (id, code, name, strategy, buy_date, shares, buy_price,
#  sell_date, sell_price, sell_reason, profit_loss, profit_pct)
sold_positions = [
    # 按实际情况填写...
]
# 同样插入...

conn.close()
