#!/usr/bin/env python
# coding=utf-8
#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scripts/execute_buy_sell.py — 手动交易操作工具 v1.0

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
设计原则（华尔街量化交易规范）:

  1. 操作完整性（Operation Integrity）
       每次手动操作记录完整的交易要素：代码、名称、日期、
       价格、手数、原因，不允许字段缺失或模糊。

  2. 成交价与交易成本（Execution Price & Cost）
       买入：佣金 0.025%（双向），最低5元
       卖出：印花税 0.05% + 佣金 0.025% + 过户费 0.001% + 滑点 0.05%
       所有成本精确核算，净盈亏基于成本基础计算。

  3. 幂等性保护（Idempotency Guard）
       同一 (code, buy_date) 不重复买入。
       同一 position_id 不重复卖出（status 检查）。

  4. 审计日志（Audit Trail）
       每笔操作写入 trade_audit_log，记录操作人意图、
       执行价、成本、盈亏、时间戳，只追加不删除。

  5. 干跑模式（Dry-Run）
       --dry-run: 只打印将要执行的操作，不写数据库。

  6. 交互确认（Confirmation Gate）
       非 --yes 模式下，执行前打印完整决策摘要并要求确认，
       防止误操作。

  7. 持仓查询（Portfolio View）
       list 子命令实时查看所有持仓（holding / sold / all）。

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
用法:

  # ── 买入 ──────────────────────────────────────────────
  python scripts/execute_buy_sell.py buy \\
      --code 600519 --price 1680.00 --shares 100 \\
      --date 20260530 --reason "MA金叉+量能放大" \\
      --strategy s1 --stop-loss 3.0

  # ── 卖出（按持仓ID）───────────────────────────────────
  python scripts/execute_buy_sell.py sell \\
      --position-id 7 --price 1750.00 --shares 100 \\
      --date 20260530 --reason "S1满仓信号" \\
      --sell-type full

  # ── 卖出（按股票代码，自动匹配持仓）─────────────────────
  python scripts/execute_buy_sell.py sell \\
      --code 600519 --price 1750.00 \\
      --date 20260530 --reason "止损" --sell-type full

  # ── 半仓减持 ──────────────────────────────────────────
  python scripts/execute_buy_sell.py sell \\
      --code 600519 --price 1750.00 --shares 100 \\
      --date 20260530 --reason "S1半仓信号" --sell-type half

  # ── 查看持仓 ──────────────────────────────────────────
  python scripts/execute_buy_sell.py list
  python scripts/execute_buy_sell.py list --status sold
  python scripts/execute_buy_sell.py list --code 600519

  # ── 查看审计日志 ──────────────────────────────────────
  python scripts/execute_buy_sell.py audit --code 600519 --limit 20

  # ── 干跑（不写库） ────────────────────────────────────
  python scripts/execute_buy_sell.py buy --code 600519 \\
      --price 1680 --shares 100 --date 20260530 \\
      --reason "测试" --dry-run

  # ── 跳过确认直接执行 ──────────────────────────────────
  python scripts/execute_buy_sell.py sell --code 600519 \\
      --price 1750 --date 20260530 --reason "收盘清仓" --yes

━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
"""

import os
import sys
import argparse
import time
from datetime import datetime, date
from typing import Optional, List, Dict, Any
from dataclasses import dataclass, field

import duckdb

# ── 路径初始化 ────────────────────────────────────────────────────
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT_ROOT)

try:
    from scripts.log_utils import setup_logger
    logger = setup_logger('execute_buy_sell', 'pipeline')
except ImportError:
    import logging
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s [%(levelname)s] %(name)s — %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )
    logger = logging.getLogger('execute_buy_sell')

DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'Astock3.duckdb')

# ── 颜色终端输出 ──────────────────────────────────────────────────
_USE_COLOR = sys.stdout.isatty()

def _c(text: str, code: str) -> str:
    return f"\033[{code}m{text}\033[0m" if _USE_COLOR else text

def green(t):  return _c(t, '32')
def red(t):    return _c(t, '31')
def yellow(t): return _c(t, '33')
def cyan(t):   return _c(t, '36')
def bold(t):   return _c(t, '1')
def dim(t):    return _c(t, '2')

# ── 交易成本常量 ──────────────────────────────────────────────────
STAMP_DUTY_RATE   = 0.0005    # 印花税 0.05%（卖出单边）
COMMISSION_RATE   = 0.00025   # 佣金 0.025%
MIN_COMMISSION    = 5.0       # 最低佣金 5 元
TRANSFER_RATE     = 0.00001   # 过户费 0.001%（沪市）
SLIPPAGE_RATE     = 0.0005    # 滑点 0.05%（双边各半）

TOTAL_SELL_COST   = STAMP_DUTY_RATE + COMMISSION_RATE + TRANSFER_RATE + SLIPPAGE_RATE
# 合计卖出摩擦：0.076%

BUY_COST_RATE     = COMMISSION_RATE   # 买入仅佣金
# 合计买入摩擦：0.025%

FIXED_STOPLOSS_PCT = 0.03  # 默认止损阈值 3%


# ═══════════════════════════════════════════════════════════════
# §1  数据模型
# ═══════════════════════════════════════════════════════════════

@dataclass
class BuyOrder:
    """手动买入指令"""
    code:          str
    name:          str
    buy_date:      str           # YYYY-MM-DD
    buy_price:     float
    shares:        int
    reason:        str
    strategy:      str  = 's1'
    stop_loss_pct: float = FIXED_STOPLOSS_PCT
    notes:         str  = ''
    # 计算字段
    commission:    float = 0.0
    cost_basis:    float = 0.0   # 买入总成本（含佣金）


@dataclass
class SellOrder:
    """手动卖出指令"""
    position_id:    int
    code:           str
    name:           str
    sell_date:      str          # YYYY-MM-DD
    sell_price:     float
    sell_shares:    int
    sell_type:      str          # 'full' | 'half'
    reason:         str
    notes:          str  = ''
    # 从持仓载入
    buy_price:      float = 0.0
    buy_date:       str   = ''
    total_shares:   int   = 0
    strategy:       str   = ''
    # 计算字段
    gross_proceeds: float = 0.0
    net_proceeds:   float = 0.0
    cost_basis:     float = 0.0
    profit_loss:    float = 0.0
    profit_pct:     float = 0.0
    is_full_exit:   bool  = True


# ═══════════════════════════════════════════════════════════════
# §2  数据库工具
# ═══════════════════════════════════════════════════════════════

def get_conn(read_only: bool = False) -> duckdb.DuckDBPyConnection:
    return duckdb.connect(DB_PATH, read_only=read_only)


def _ensure_audit_table(conn: duckdb.DuckDBPyConnection) -> None:
    """确保 trade_audit_log 表存在（与 execute_sell.py 共用同一张表）"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trade_audit_log (
            id              INTEGER NOT NULL PRIMARY KEY,
            trade_date      DATE    NOT NULL,
            position_id     INTEGER,
            code            VARCHAR NOT NULL,
            name            VARCHAR,
            strategy        VARCHAR,
            action          VARCHAR NOT NULL,
            sell_reason     VARCHAR NOT NULL,
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
            dry_run         BOOLEAN DEFAULT FALSE,
            executed_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            notes           VARCHAR
        )
    """)


def _ensure_positions_table(conn: duckdb.DuckDBPyConnection) -> None:
    """确保 positions 表存在并含必要列"""
    conn.execute("""
        CREATE TABLE IF NOT EXISTS positions (
            id             INTEGER NOT NULL PRIMARY KEY,
            code           VARCHAR NOT NULL,
            name           VARCHAR,
            strategy       VARCHAR,
            buy_date       DATE    NOT NULL,
            buy_price      DOUBLE  NOT NULL,
            shares         INTEGER NOT NULL,
            stop_loss_pct  DOUBLE  DEFAULT 0.03,
            status         VARCHAR DEFAULT 'holding',
            sell_date      DATE,
            sell_price     DOUBLE,
            sell_reason    VARCHAR,
            profit_loss    DOUBLE,
            profit_pct     DOUBLE,
            notes          VARCHAR,
            created_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at     TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)


def _next_id(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    row = conn.execute(f"SELECT COALESCE(MAX(id), 0) + 1 FROM {table}").fetchone()
    return int(row[0])


def _fmt_date(d: str) -> str:
    """统一转为 YYYY-MM-DD"""
    d = d.strip().replace('-', '')
    return f"{d[:4]}-{d[4:6]}-{d[6:]}"


def _lookup_name(conn: duckdb.DuckDBPyConnection, code: str) -> str:
    """从 daily_signals 或 positions 尝试查名称"""
    row = conn.execute(
        "SELECT name FROM daily_signals WHERE code = ? LIMIT 1", [code]
    ).fetchone()
    if row and row[0]:
        return row[0]
    row = conn.execute(
        "SELECT name FROM positions WHERE code = ? LIMIT 1", [code]
    ).fetchone()
    return row[0] if row and row[0] else code


def get_holding_by_code(
    conn: duckdb.DuckDBPyConnection, code: str
) -> Optional[Dict[str, Any]]:
    """按代码取持仓中的最新一条（多笔取最近买入）"""
    row = conn.execute("""
        SELECT id, code, name, strategy, buy_date, buy_price, shares,
               stop_loss_pct, status, notes
        FROM positions
        WHERE code = ? AND status = 'holding'
        ORDER BY buy_date DESC
        LIMIT 1
    """, [code]).fetchone()
    if not row:
        return None
    cols = ['id', 'code', 'name', 'strategy', 'buy_date', 'buy_price',
            'shares', 'stop_loss_pct', 'status', 'notes']
    return dict(zip(cols, row))


def get_position_by_id(
    conn: duckdb.DuckDBPyConnection, pid: int
) -> Optional[Dict[str, Any]]:
    row = conn.execute("""
        SELECT id, code, name, strategy, buy_date, buy_price, shares,
               stop_loss_pct, status, notes
        FROM positions WHERE id = ?
    """, [pid]).fetchone()
    if not row:
        return None
    cols = ['id', 'code', 'name', 'strategy', 'buy_date', 'buy_price',
            'shares', 'stop_loss_pct', 'status', 'notes']
    return dict(zip(cols, row))


def duplicate_buy_exists(
    conn: duckdb.DuckDBPyConnection, code: str, buy_date: str
) -> bool:
    """幂等性：同一 (code, buy_date) 是否已存在 holding 记录"""
    row = conn.execute(
        "SELECT COUNT(*) FROM positions WHERE code = ? AND buy_date = ? AND status = 'holding'",
        [code, buy_date]
    ).fetchone()
    return row is not None and row[0] > 0


def position_is_sold(
    conn: duckdb.DuckDBPyConnection, position_id: int
) -> bool:
    row = conn.execute(
        "SELECT status FROM positions WHERE id = ?", [position_id]
    ).fetchone()
    return row is not None and row[0] == 'sold'


# ═══════════════════════════════════════════════════════════════
# §3  P&L 计算
# ═══════════════════════════════════════════════════════════════

def compute_buy_cost(order: BuyOrder) -> BuyOrder:
    """
    计算买入成本。

    commission   = max(buy_price × shares × COMMISSION_RATE, MIN_COMMISSION)
    cost_basis   = buy_price × shares + commission
    """
    raw_commission  = order.buy_price * order.shares * COMMISSION_RATE
    commission      = max(raw_commission, MIN_COMMISSION)
    order.commission = round(commission, 2)
    order.cost_basis = round(order.buy_price * order.shares + commission, 2)
    return order


def compute_sell_pnl(order: SellOrder) -> SellOrder:
    """
    填充卖出盈亏字段。

    cost_basis      = buy_price × sell_shares × (1 + BUY_COST_RATE)
    gross_proceeds  = sell_price × sell_shares
    net_proceeds    = gross_proceeds × (1 - TOTAL_SELL_COST)
    profit_loss     = net_proceeds - cost_basis
    profit_pct      = profit_loss / cost_basis × 100
    """
    cost_basis      = order.buy_price * order.sell_shares * (1 + BUY_COST_RATE)
    gross_proceeds  = order.sell_price * order.sell_shares
    net_proceeds    = gross_proceeds * (1 - TOTAL_SELL_COST)
    profit_loss     = net_proceeds - cost_basis
    profit_pct      = (profit_loss / cost_basis * 100) if cost_basis > 0 else 0.0

    order.cost_basis      = round(cost_basis, 2)
    order.gross_proceeds  = round(gross_proceeds, 2)
    order.net_proceeds    = round(net_proceeds, 2)
    order.profit_loss     = round(profit_loss, 2)
    order.profit_pct      = round(profit_pct, 4)
    return order


# ═══════════════════════════════════════════════════════════════
# §4  打印摘要
# ═══════════════════════════════════════════════════════════════

SEP = "─" * 62

def _print_buy_summary(order: BuyOrder, dry_run: bool) -> None:
    tag = yellow("  [DRY-RUN]") if dry_run else green("  [BUY]   ")
    print(f"\n{bold(SEP)}")
    print(f"{tag}  {bold(order.code)}  {order.name}")
    print(SEP)
    print(f"  {'买入日期':12s}  {order.buy_date}")
    print(f"  {'买入价格':12s}  {order.buy_price:.3f} 元")
    print(f"  {'买入手数':12s}  {order.shares:,} 股")
    print(f"  {'佣金':12s}  {order.commission:.2f} 元")
    print(f"  {'成本基础':12s}  {bold(f'{order.cost_basis:,.2f}')} 元")
    print(f"  {'止损阈值':12s}  {order.stop_loss_pct*100:.1f}%  → "
          f"止损价 {order.buy_price*(1-order.stop_loss_pct):.3f} 元")
    print(f"  {'策略':12s}  {order.strategy}")
    print(f"  {'原因':12s}  {order.reason}")
    if order.notes:
        print(f"  {'备注':12s}  {order.notes}")
    print(f"{bold(SEP)}\n")


def _print_sell_summary(order: SellOrder, dry_run: bool) -> None:
    pnl_str  = (green if order.profit_loss >= 0 else red)(
        f"{order.profit_loss:+,.2f} 元  ({order.profit_pct:+.2f}%)"
    )
    stype    = "全仓清出" if order.is_full_exit else "半仓减持"
    tag      = yellow("  [DRY-RUN]") if dry_run else red("  [SELL]  ")
    print(f"\n{bold(SEP)}")
    print(f"{tag}  {bold(order.code)}  {order.name}  ({stype})")
    print(SEP)
    print(f"  {'持仓ID':12s}  #{order.position_id}")
    print(f"  {'买入日期':12s}  {order.buy_date}")
    print(f"  {'买入价格':12s}  {order.buy_price:.3f} 元")
    print(f"  {'持仓手数':12s}  {order.total_shares:,} 股")
    print(f"  {'卖出手数':12s}  {order.sell_shares:,} 股")
    print(f"  {'卖出日期':12s}  {order.sell_date}")
    print(f"  {'卖出价格':12s}  {order.sell_price:.3f} 元")
    print(f"  {'毛收入':12s}  {order.gross_proceeds:,.2f} 元")
    print(f"  {'净收入':12s}  {order.net_proceeds:,.2f} 元")
    print(f"  {'成本基础':12s}  {order.cost_basis:,.2f} 元")
    print(f"  {'净盈亏':12s}  {pnl_str}")
    print(f"  {'原因':12s}  {order.reason}")
    if order.notes:
        print(f"  {'备注':12s}  {order.notes}")
    print(f"{bold(SEP)}\n")


def _confirm(prompt: str) -> bool:
    """终端确认（非 TTY 时默认拒绝）"""
    if not sys.stdin.isatty():
        print(red("  ⚠️  非交互模式，需加 --yes 参数才能执行写库操作"))
        return False
    ans = input(f"  {bold(prompt)} [y/N] ").strip().lower()
    return ans in ('y', 'yes')


# ═══════════════════════════════════════════════════════════════
# §5  执行写库
# ═══════════════════════════════════════════════════════════════

def execute_buy(
    order:   BuyOrder,
    dry_run: bool = False,
    yes:     bool = False,
) -> bool:
    """
    写入买入记录：
      1. 幂等检查（同一 code+buy_date 不重复）
      2. 插入 positions（status='holding'）
      3. 写入 trade_audit_log
    """
    order = compute_buy_cost(order)
    _print_buy_summary(order, dry_run)

    if dry_run:
        return True

    if not yes and not _confirm("确认执行买入并写入数据库？"):
        print(dim("  已取消。"))
        return False

    conn = get_conn(read_only=False)
    t0   = time.perf_counter()
    try:
        _ensure_positions_table(conn)
        _ensure_audit_table(conn)

        # ── 幂等检查 ─────────────────────────────────────────
        if duplicate_buy_exists(conn, order.code, order.buy_date):
            print(yellow(f"  ⚠️  幂等保护: {order.code} 在 {order.buy_date} 已存在持仓记录，跳过。"))
            return False

        # ── 插入 positions ────────────────────────────────────
        pos_id = _next_id(conn, 'positions')
        conn.execute("""
            INSERT INTO positions (
                id, code, name, strategy,
                buy_date, buy_price, shares,
                stop_loss_pct, status, notes,
                created_at, updated_at
            ) VALUES (
                ?, ?, ?, ?,
                ?, ?, ?,
                ?, 'holding', ?,
                CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
            )
        """, [
            pos_id,
            order.code, order.name, order.strategy,
            order.buy_date, order.buy_price, order.shares,
            order.stop_loss_pct, order.notes or '',
        ])

        # ── 写审计日志 ────────────────────────────────────────
        audit_id = _next_id(conn, 'trade_audit_log')
        conn.execute("""
            INSERT INTO trade_audit_log (
                id, trade_date, position_id, code, name, strategy,
                action, sell_reason,
                sell_shares, sell_price, buy_price, stoploss_price,
                score_s1, gross_proceeds, net_proceeds, cost_basis,
                profit_loss, profit_pct, dry_run, executed_at, notes
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                'BUY', ?,
                ?, ?, ?, ?,
                NULL, NULL, NULL, ?,
                NULL, NULL, FALSE, CURRENT_TIMESTAMP, ?
            )
        """, [
            audit_id,
            order.buy_date, pos_id, order.code, order.name, order.strategy,
            order.reason,
            order.shares, order.buy_price, order.buy_price,
            round(order.buy_price * (1 - order.stop_loss_pct), 3),
            order.cost_basis, order.notes or '',
        ])

        elapsed = time.perf_counter() - t0
        print(green(
            f"  ✅ 买入记录已写入 | positions.id={pos_id} | "
            f"耗时 {elapsed*1000:.1f}ms"
        ))
        logger.info(
            f"BUY  [{order.code}] {order.name} "
            f"{order.shares}股 @ {order.buy_price:.3f} "
            f"成本 {order.cost_basis:.2f}元  pos_id={pos_id}"
        )
        return True

    except Exception as e:
        logger.error(f"买入写库失败 [{order.code}]: {e}", exc_info=True)
        print(red(f"  ❌ 写库失败: {e}"))
        raise
    finally:
        conn.close()


def execute_sell(
    order:   SellOrder,
    dry_run: bool = False,
    yes:     bool = False,
) -> bool:
    """
    写入卖出记录：
      1. 幂等检查（position 未已卖出）
      2. 计算盈亏
      3. 全仓: positions.status → 'sold'
         半仓: positions.shares -= sell_shares
      4. 写审计日志
    """
    order = compute_sell_pnl(order)
    _print_sell_summary(order, dry_run)

    if dry_run:
        return True

    if not yes and not _confirm("确认执行卖出并写入数据库？"):
        print(dim("  已取消。"))
        return False

    conn = get_conn(read_only=False)
    t0   = time.perf_counter()
    try:
        _ensure_audit_table(conn)

        # ── 幂等检查 ─────────────────────────────────────────
        if position_is_sold(conn, order.position_id):
            print(yellow(
                f"  ⚠️  幂等保护: position_id={order.position_id} 已是 sold 状态，跳过。"
            ))
            return False

        # ── 更新 positions ────────────────────────────────────
        if order.is_full_exit:
            conn.execute("""
                UPDATE positions SET
                    status      = 'sold',
                    sell_date   = ?,
                    sell_price  = ?,
                    sell_reason = ?,
                    profit_loss = ?,
                    profit_pct  = ?,
                    updated_at  = CURRENT_TIMESTAMP
                WHERE id = ?
            """, [
                order.sell_date, order.sell_price, order.reason,
                order.profit_loss, order.profit_pct,
                order.position_id,
            ])
        else:
            remaining = order.total_shares - order.sell_shares
            conn.execute("""
                UPDATE positions SET
                    shares     = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, [remaining, order.position_id])

        # ── 写审计日志 ────────────────────────────────────────
        audit_id = _next_id(conn, 'trade_audit_log')
        action   = 'SELL_FULL' if order.is_full_exit else 'SELL_HALF'
        conn.execute("""
            INSERT INTO trade_audit_log (
                id, trade_date, position_id, code, name, strategy,
                action, sell_reason,
                sell_shares, sell_price, buy_price, stoploss_price,
                score_s1, gross_proceeds, net_proceeds, cost_basis,
                profit_loss, profit_pct, dry_run, executed_at, notes
            ) VALUES (
                ?, ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, NULL,
                NULL, ?, ?, ?,
                ?, ?, FALSE, CURRENT_TIMESTAMP, ?
            )
        """, [
            audit_id,
            order.sell_date, order.position_id, order.code, order.name, order.strategy,
            action, order.reason,
            order.sell_shares, order.sell_price, order.buy_price,
            order.gross_proceeds, order.net_proceeds, order.cost_basis,
            order.profit_loss, order.profit_pct,
            order.notes or '',
        ])

        elapsed = time.perf_counter() - t0
        pnl_col = green if order.profit_loss >= 0 else red
        print(pnl_col(
            f"  ✅ 卖出记录已写入 | positions.id={order.position_id} | "
            f"盈亏 {order.profit_loss:+,.2f}元 ({order.profit_pct:+.2f}%) | "
            f"耗时 {elapsed*1000:.1f}ms"
        ))
        logger.info(
            f"SELL [{order.code}] {order.name} "
            f"{order.sell_shares}股 @ {order.sell_price:.3f} "
            f"盈亏 {order.profit_loss:+.2f}元 ({order.profit_pct:+.2f}%)  "
            f"pos_id={order.position_id} action={action}"
        )
        return True

    except Exception as e:
        logger.error(f"卖出写库失败 [{order.code}]: {e}", exc_info=True)
        print(red(f"  ❌ 写库失败: {e}"))
        raise
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
# §6  持仓列表展示
# ═══════════════════════════════════════════════════════════════

def cmd_list(args) -> None:
    """list 子命令：展示持仓明细"""
    conn = get_conn(read_only=True)
    try:
        where_parts = []
        params      = []

        if args.status and args.status != 'all':
            where_parts.append("status = ?")
            params.append(args.status)

        if args.code:
            where_parts.append("code = ?")
            params.append(args.code)

        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""

        rows = conn.execute(f"""
            SELECT
                id, code, name, strategy,
                buy_date, buy_price, shares,
                stop_loss_pct, status,
                sell_date, sell_price, sell_reason,
                profit_loss, profit_pct
            FROM positions
            {where_sql}
            ORDER BY status, buy_date DESC
        """, params).fetchall()

    finally:
        conn.close()

    if not rows:
        print(dim("  （无持仓记录）"))
        return

    # ── 表头 ──────────────────────────────────────────────────
    H = f"{'ID':>4}  {'代码':<8}  {'名称':<10}  {'策略':<6}  {'买入日':>10}  " \
        f"{'买入价':>8}  {'手数':>6}  {'状态':<8}  " \
        f"{'卖出日':>10}  {'卖出价':>8}  {'盈亏(元)':>12}  {'收益率':>8}"
    print(f"\n{bold(H)}")
    print(dim("─" * len(H)))

    for r in rows:
        (pid, code, name, strategy, buy_date, buy_price, shares,
         sl_pct, status, sell_date, sell_price, sell_reason,
         profit_loss, profit_pct) = r

        status_str = (green("持仓中") if status == 'holding'
                      else red("已卖出") if status == 'sold'
                      else dim(status or ''))

        pnl_str   = ''
        pct_str   = ''
        if profit_loss is not None:
            col     = green if profit_loss >= 0 else red
            pnl_str = col(f"{profit_loss:+,.2f}")
            pct_str = col(f"{profit_pct:+.2f}%")

        sd_str = str(sell_date) if sell_date else ''
        sp_str = f"{sell_price:.3f}" if sell_price else ''

        print(
            f"  {pid:>3d}  {code:<8}  {(name or ''):<10}  {(strategy or ''):<6}  "
            f"{str(buy_date):>10}  {buy_price:>8.3f}  {shares:>6,d}  "
            f"{status_str:<8}  "
            f"{sd_str:>10}  {sp_str:>8}  "
            f"{pnl_str:>12}  {pct_str:>8}"
        )

    print(dim("─" * len(H)))
    holding_rows = [r for r in rows if r[8] == 'holding']
    sold_rows    = [r for r in rows if r[8] == 'sold']
    total_pnl    = sum((r[12] or 0) for r in sold_rows)
    pnl_col      = green if total_pnl >= 0 else red
    print(
        f"\n  持仓 {len(holding_rows)} 只  已平仓 {len(sold_rows)} 只  "
        f"历史总盈亏: {pnl_col(f'{total_pnl:+,.2f}')} 元\n"
    )


# ═══════════════════════════════════════════════════════════════
# §7  审计日志展示
# ═══════════════════════════════════════════════════════════════

def cmd_audit(args) -> None:
    """audit 子命令：查看 trade_audit_log"""
    conn = get_conn(read_only=True)
    try:
        # 判断表是否存在
        tbl = conn.execute(
            "SELECT COUNT(*) FROM information_schema.tables WHERE table_name='trade_audit_log'"
        ).fetchone()
        if not tbl or tbl[0] == 0:
            print(dim("  （trade_audit_log 表不存在，尚无操作记录）"))
            return

        where_parts = []
        params      = []
        if args.code:
            where_parts.append("code = ?")
            params.append(args.code)
        if getattr(args, 'action', None):
            where_parts.append("action = ?")
            params.append(args.action.upper())

        where_sql = ("WHERE " + " AND ".join(where_parts)) if where_parts else ""
        limit     = getattr(args, 'limit', 30) or 30

        rows = conn.execute(f"""
            SELECT
                id, trade_date, code, name, action, sell_reason,
                sell_shares, sell_price, buy_price,
                profit_loss, profit_pct,
                dry_run, executed_at, notes
            FROM trade_audit_log
            {where_sql}
            ORDER BY id DESC
            LIMIT {int(limit)}
        """, params).fetchall()

    finally:
        conn.close()

    if not rows:
        print(dim("  （无审计记录）"))
        return

    H = (f"{'ID':>5}  {'日期':>10}  {'代码':<8}  {'名称':<10}  {'操作':<12}  "
         f"{'原因':<12}  {'手数':>6}  {'成交价':>8}  {'盈亏(元)':>12}  {'收益率':>8}  {'执行时间'}")
    print(f"\n{bold(H)}")
    print(dim("─" * max(len(H), 100)))

    for r in rows:
        (rid, trade_date, code, name, action, reason,
         shares, sell_price, buy_price,
         profit_loss, profit_pct,
         dry_run, executed_at, notes) = r

        action_str = (green(action) if action == 'BUY'
                      else red(action) if 'SELL' in (action or '')
                      else dim(action or ''))

        pnl_str = pct_str = ''
        if profit_loss is not None:
            col     = green if profit_loss >= 0 else red
            pnl_str = col(f"{profit_loss:+,.2f}")
            pct_str = col(f"{profit_pct:+.2f}%")

        dr_tag = yellow(" [DR]") if dry_run else ''

        ts_str = str(executed_at)[:19] if executed_at else ''

        print(
            f"  {rid:>4d}  {str(trade_date):>10}  {code:<8}  {(name or ''):<10}  "
            f"{action_str:<12}  {(reason or ''):<12}  "
            f"{(shares or 0):>6,d}  {(sell_price or 0):>8.3f}  "
            f"{pnl_str:>12}  {pct_str:>8}  {ts_str}{dr_tag}"
        )

    print(dim(f"\n  显示最近 {len(rows)} 条记录（共 limit={limit}）\n"))


# ═══════════════════════════════════════════════════════════════
# §8  子命令处理器
# ═══════════════════════════════════════════════════════════════

def cmd_buy(args) -> None:
    """buy 子命令入口"""
    conn = get_conn(read_only=True)
    try:
        name = args.name or _lookup_name(conn, args.code)
    finally:
        conn.close()

    buy_date = _fmt_date(args.date)

    order = BuyOrder(
        code          = args.code.strip(),
        name          = name,
        buy_date      = buy_date,
        buy_price     = float(args.price),
        shares        = int(args.shares),
        reason        = args.reason or '',
        strategy      = args.strategy or 's1',
        stop_loss_pct = float(args.stop_loss) / 100 if args.stop_loss else FIXED_STOPLOSS_PCT,
        notes         = args.notes or '',
    )

    # 基本校验
    if order.buy_price <= 0:
        sys.exit(red("  ❌ 买入价格必须 > 0"))
    if order.shares <= 0 or order.shares % 100 != 0:
        sys.exit(red("  ❌ 手数必须是 100 的整数倍"))

    execute_buy(order, dry_run=args.dry_run, yes=args.yes)


def cmd_sell(args) -> None:
    """sell 子命令入口"""
    conn = get_conn(read_only=True)
    try:
        # ── 查找持仓 ─────────────────────────────────────────
        pos = None
        if args.position_id:
            pos = get_position_by_id(conn, int(args.position_id))
            if not pos:
                sys.exit(red(f"  ❌ 未找到 position_id={args.position_id}"))
        elif args.code:
            pos = get_holding_by_code(conn, args.code.strip())
            if not pos:
                sys.exit(red(f"  ❌ 未找到 code={args.code} 的持仓（status=holding）"))
        else:
            sys.exit(red("  ❌ 必须指定 --position-id 或 --code"))

        if pos['status'] == 'sold':
            sys.exit(yellow(f"  ⚠️  position_id={pos['id']} 已是 sold 状态，无需再次卖出"))

    finally:
        conn.close()

    total_shares = int(pos['shares'])
    sell_type    = (args.sell_type or 'full').lower()

    # ── 确定卖出手数 ─────────────────────────────────────────
    if args.shares:
        sell_shares = int(args.shares)
    elif sell_type == 'half':
        sell_shares = max(100, (total_shares // 2 // 100) * 100)
    else:
        sell_shares = total_shares

    if sell_shares <= 0 or sell_shares > total_shares:
        sys.exit(red(f"  ❌ 卖出手数 {sell_shares} 超过持仓 {total_shares}"))
    if sell_shares % 100 != 0:
        sys.exit(red(f"  ❌ 卖出手数 {sell_shares} 必须是 100 的整数倍"))

    is_full_exit = (sell_shares >= total_shares)
    sell_date    = _fmt_date(args.date)

    order = SellOrder(
        position_id  = pos['id'],
        code         = pos['code'],
        name         = pos['name'] or pos['code'],
        sell_date    = sell_date,
        sell_price   = float(args.price),
        sell_shares  = sell_shares,
        sell_type    = sell_type,
        reason       = args.reason or '',
        notes        = args.notes or '',
        buy_price    = float(pos['buy_price']),
        buy_date     = str(pos['buy_date']),
        total_shares = total_shares,
        strategy     = pos['strategy'] or '',
        is_full_exit = is_full_exit,
    )

    if order.sell_price <= 0:
        sys.exit(red("  ❌ 卖出价格必须 > 0"))

    execute_sell(order, dry_run=args.dry_run, yes=args.yes)


# ═══════════════════════════════════════════════════════════════
# §9  CLI 定义
# ═══════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog='execute_buy_sell.py',
        description=bold('手动交易操作工具 — 记录买入/卖出，更新持仓，写审计日志'),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  买入:   python scripts/execute_buy_sell.py buy --code 600519 --price 1680 --shares 100 --date 20260530 --reason "MA金叉"
  卖出:   python scripts/execute_buy_sell.py sell --code 600519 --price 1750 --date 20260530 --reason "止损" --sell-type full
  持仓:   python scripts/execute_buy_sell.py list
  日志:   python scripts/execute_buy_sell.py audit --limit 20
        """
    )

    # ── 全局参数 ──────────────────────────────────────────────
    parser.add_argument('--db', type=str, default=None,
                        help='数据库路径（默认 data/Astock3.duckdb）')

    subparsers = parser.add_subparsers(dest='command', metavar='命令')
    subparsers.required = True

    # ┌─────────────────────────────────────────────────────────
    # │  buy
    # └─────────────────────────────────────────────────────────
    buy_p = subparsers.add_parser('buy', help='手动记录买入')
    buy_p.add_argument('--code',      required=True,  type=str,   help='股票代码，如 600519')
    buy_p.add_argument('--price',     required=True,  type=float, help='买入成交价（元）')
    buy_p.add_argument('--shares',    required=True,  type=int,   help='买入手数（必须是100的整数倍）')
    buy_p.add_argument('--date',      required=True,  type=str,   help='买入日期 YYYYMMDD 或 YYYY-MM-DD')
    buy_p.add_argument('--reason',    required=True,  type=str,   help='买入原因（自由文本）')
    buy_p.add_argument('--name',      type=str,   default=None,   help='股票名称（可选，自动查询）')
    buy_p.add_argument('--strategy',  type=str,   default='s1',   help='策略标签（默认 s1）')
    buy_p.add_argument('--stop-loss', type=float, default=3.0,    help='止损阈值%%，如 3.0 表示3%%（默认3%%）')
    buy_p.add_argument('--notes',     type=str,   default='',     help='备注')
    buy_p.add_argument('--dry-run',   action='store_true',         help='干跑模式，不写库')
    buy_p.add_argument('--yes',       action='store_true',         help='跳过确认直接执行')

    # ┌─────────────────────────────────────────────────────────
    # │  sell
    # └─────────────────────────────────────────────────────────
    sell_p = subparsers.add_parser('sell', help='手动记录卖出')
    sell_src = sell_p.add_mutually_exclusive_group()
    sell_src.add_argument('--position-id', type=int, help='精确指定持仓ID（positions.id）')
    sell_src.add_argument('--code',        type=str, help='股票代码（自动匹配最新持仓）')
    sell_p.add_argument('--price',     required=True,  type=float, help='卖出成交价（元）')
    sell_p.add_argument('--date',      required=True,  type=str,   help='卖出日期 YYYYMMDD 或 YYYY-MM-DD')
    sell_p.add_argument('--reason',    required=True,  type=str,   help='卖出原因（自由文本）')
    sell_p.add_argument('--shares',    type=int,   default=None,   help='卖出手数（默认按 sell-type 计算）')
    sell_p.add_argument('--sell-type', type=str,   default='full',
                        choices=['full', 'half'],                   help='full=全仓（默认），half=半仓')
    sell_p.add_argument('--notes',     type=str,   default='',     help='备注')
    sell_p.add_argument('--dry-run',   action='store_true',         help='干跑模式，不写库')
    sell_p.add_argument('--yes',       action='store_true',         help='跳过确认直接执行')

    # ┌─────────────────────────────────────────────────────────
    # │  list
    # └─────────────────────────────────────────────────────────
    list_p = subparsers.add_parser('list', help='查看持仓明细')
    list_p.add_argument('--status', type=str, default='all',
                        choices=['holding', 'sold', 'all'],    help='筛选状态（默认 all）')
    list_p.add_argument('--code',   type=str, default=None,    help='筛选股票代码')

    # ┌─────────────────────────────────────────────────────────
    # │  audit
    # └─────────────────────────────────────────────────────────
    audit_p = subparsers.add_parser('audit', help='查看审计日志')
    audit_p.add_argument('--code',   type=str, default=None, help='筛选股票代码')
    audit_p.add_argument('--action', type=str, default=None,
                         choices=['BUY', 'SELL_FULL', 'SELL_HALF'], help='筛选操作类型')
    audit_p.add_argument('--limit',  type=int, default=30,   help='最多显示条数（默认30）')

    return parser


# ═══════════════════════════════════════════════════════════════
# §10  主入口
# ═══════════════════════════════════════════════════════════════

def main():
    parser = build_parser()
    args   = parser.parse_args()

    # 支持覆盖数据库路径
    global DB_PATH
    if getattr(args, 'db', None):
        DB_PATH = args.db

    logger.info(f"execute_buy_sell 启动 | command={args.command} | db={DB_PATH}")

    dispatch = {
        'buy':   cmd_buy,
        'sell':  cmd_sell,
        'list':  cmd_list,
        'audit': cmd_audit,
    }
    handler = dispatch.get(args.command)
    if handler:
        handler(args)
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == '__main__':
    main()
