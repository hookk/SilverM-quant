#!/usr/bin/env python
# coding=utf-8
"""
dashboard/positions_api.py
─────────────────────────────────────────────────────────────────────────────
持仓 & 交易 CRUD 蓝图
实现前端 HomeView.vue 调用的四个缺失接口：

  POST   /api/positions          → 添加持仓 (addForm)
  POST   /api/trade/buy          → 记录买入 (buyForm，含 reason)
  POST   /api/trade/sell         → 记录卖出 (sellForm, 支持全仓/半仓)
  DELETE /api/positions/<int:id> → 删除持仓记录

注册方式（在 dashboard/app.py 末尾 register_blueprint 处追加）：

    from dashboard.positions_api import positions_bp
    app.register_blueprint(positions_bp)

依赖：Flask, duckdb（已在 app.py 中使用，无新依赖）
─────────────────────────────────────────────────────────────────────────────
"""

from flask import Blueprint, request, jsonify, g
import duckdb
import os
from datetime import datetime

positions_bp = Blueprint('positions', __name__)

# ── 数据库路径（与 app.py 保持一致） ────────────────────────────────────────
DB_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'data', 'Astock3.duckdb'
)


# ── 工具函数 ─────────────────────────────────────────────────────────────────

def _get_db() -> duckdb.DuckDBPyConnection:
    """
    复用 Flask g 对象上的数据库连接。
    如果 app.py 的 get_db() 已在同一请求内建立连接，则直接复用；
    否则新建一个读写连接。
    """
    if 'db' not in g:
        g.db = duckdb.connect(DB_PATH)
    return g.db


def _next_id(conn: duckdb.DuckDBPyConnection, table: str) -> int:
    """获取表的下一个可用 id（MAX+1）"""
    row = conn.execute(f"SELECT COALESCE(MAX(id), 0) FROM {table}").fetchone()
    return int(row[0]) + 1


def _require_fields(data: dict, fields: list) -> str | None:
    """检查必填字段，返回缺失字段名（字符串）或 None"""
    missing = [f for f in fields if not data.get(f) and data.get(f) != 0]
    return ', '.join(missing) if missing else None


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/positions  —— 添加持仓（手动录入，无 reason 字段）
# ══════════════════════════════════════════════════════════════════════════════

@positions_bp.route('/api/positions', methods=['POST'])
def add_position():
    """
    前端 addForm 调用。
    必填：code, name, buy_date, buy_price, shares
    可选：strategy, stop_loss_pct, notes
    """
    data = request.get_json(silent=True) or {}

    missing = _require_fields(data, ['code', 'name', 'buy_date', 'buy_price', 'shares'])
    if missing:
        return jsonify({'error': f'缺少必填字段: {missing}'}), 400

    shares = int(data['shares'])
    if shares <= 0 or shares % 100 != 0:
        return jsonify({'error': '买入数量必须是 100 的整数倍'}), 400

    buy_price = float(data['buy_price'])
    if buy_price <= 0:
        return jsonify({'error': '买入价格必须大于 0'}), 400

    conn = _get_db()
    try:
        new_id = _next_id(conn, 'positions')
        conn.execute("""
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
            data.get('notes', ''),
        ])
        return jsonify({'success': True, 'id': new_id, 'message': '持仓已添加'}), 201
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': f'数据库写入失败: {e}'}), 500


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/trade/buy  —— 记录买入（含信号原因）
# ══════════════════════════════════════════════════════════════════════════════

@positions_bp.route('/api/trade/buy', methods=['POST'])
def trade_buy():
    """
    前端 buyForm（快速买入 / 买入按钮）调用。
    必填：code, buy_date, buy_price, shares, reason
    可选：name, strategy, stop_loss_pct, notes
    """
    data = request.get_json(silent=True) or {}

    missing = _require_fields(data, ['code', 'buy_date', 'buy_price', 'shares', 'reason'])
    if missing:
        return jsonify({'error': f'缺少必填字段: {missing}'}), 400

    shares = int(data['shares'])
    if shares <= 0 or shares % 100 != 0:
        return jsonify({'error': '买入数量必须是 100 的整数倍'}), 400

    buy_price = float(data['buy_price'])
    if buy_price <= 0:
        return jsonify({'error': '买入价格必须大于 0'}), 400

    conn = _get_db()
    try:
        new_id = _next_id(conn, 'positions')
        notes_val = data.get('notes', '') or ''
        reason_val = str(data['reason'])
        if reason_val and notes_val:
            notes_combined = f"[买入原因] {reason_val} | {notes_val}"
        elif reason_val:
            notes_combined = f"[买入原因] {reason_val}"
        else:
            notes_combined = notes_val

        conn.execute("""
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
        return jsonify({'success': True, 'id': new_id, 'message': '买入记录已写入'}), 201
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': f'数据库写入失败: {e}'}), 500


# ══════════════════════════════════════════════════════════════════════════════
# POST /api/trade/sell  —— 记录卖出（全仓 / 半仓）
# ══════════════════════════════════════════════════════════════════════════════

@positions_bp.route('/api/trade/sell', methods=['POST'])
def trade_sell():
    """
    前端 sellForm 调用。
    必填：position_id, code, sell_date, sell_price, shares, sell_type, reason
    sell_type: 'full' | 'half'
    """
    data = request.get_json(silent=True) or {}

    missing = _require_fields(data, ['position_id', 'code', 'sell_date', 'sell_price', 'shares', 'reason'])
    if missing:
        return jsonify({'error': f'缺少必填字段: {missing}'}), 400

    position_id = int(data['position_id'])
    sell_price  = float(data['sell_price'])
    sell_shares = int(data['shares'])
    sell_type   = data.get('sell_type', 'full')

    if sell_price <= 0:
        return jsonify({'error': '卖出价格必须大于 0'}), 400
    if sell_shares <= 0 or sell_shares % 100 != 0:
        return jsonify({'error': '卖出数量必须是 100 的整数倍'}), 400

    conn = _get_db()
    try:
        # ── 校验持仓是否存在且未卖出 ────────────────────────────
        row = conn.execute(
            "SELECT id, code, name, strategy, buy_price, shares, status FROM positions WHERE id = ?",
            [position_id]
        ).fetchone()

        if row is None:
            return jsonify({'error': f'找不到 id={position_id} 的持仓记录'}), 404

        pos_id, pos_code, pos_name, pos_strategy, buy_price, holding_shares, status = row

        if status == 'sold':
            return jsonify({'error': '该持仓已经卖出'}), 400

        if sell_shares > holding_shares:
            return jsonify({'error': f'卖出数量({sell_shares})超过持仓({holding_shares})'}), 400

        # ── 计算盈亏 ─────────────────────────────────────────────
        cost_basis    = float(buy_price or 0) * sell_shares
        gross_proceeds = sell_price * sell_shares
        # 印花税 0.1% + 佣金约 0.025%（单边）
        fee            = gross_proceeds * 0.00125
        net_proceeds   = gross_proceeds - fee
        profit_loss    = round(net_proceeds - cost_basis, 2)
        profit_pct     = round(profit_loss / cost_basis * 100, 4) if cost_basis > 0 else 0.0

        is_full = sell_type == 'full' or sell_shares >= holding_shares

        if is_full:
            # 全仓卖出：status → 'sold'
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
                data['sell_date'],
                sell_price,
                str(data['reason']),
                profit_loss,
                profit_pct,
                position_id,
            ])
        else:
            # 半仓卖出：仅减持，保留持仓
            remaining = holding_shares - sell_shares
            conn.execute("""
                UPDATE positions SET
                    shares     = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
            """, [remaining, position_id])

        # ── 写审计日志（与 execute_sell.py 结构一致） ────────────
        try:
            audit_id = _next_id(conn, 'trade_audit_log')
            conn.execute("""
                INSERT INTO trade_audit_log (
                    id, trade_date, position_id, code, name, strategy,
                    action, sell_reason,
                    sell_shares, sell_price, buy_price,
                    gross_proceeds, net_proceeds, cost_basis,
                    profit_loss, profit_pct,
                    dry_run, executed_at, notes
                ) VALUES (
                    ?, ?, ?, ?, ?, ?,
                    ?, ?,
                    ?, ?, ?,
                    ?, ?, ?,
                    ?, ?,
                    FALSE, CURRENT_TIMESTAMP, ?
                )
            """, [
                audit_id,
                data['sell_date'],
                position_id,
                str(data['code']),
                pos_name or '',
                pos_strategy or '',
                'SELL_FULL' if is_full else 'SELL_HALF',
                str(data['reason']),
                sell_shares,
                sell_price,
                float(buy_price or 0),
                round(gross_proceeds, 2),
                round(net_proceeds, 2),
                round(cost_basis, 2),
                profit_loss,
                profit_pct,
                data.get('notes', ''),
            ])
        except Exception as audit_err:
            # 审计日志写失败不影响主流程
            print(f"[WARN] trade_audit_log 写入失败（非致命）: {audit_err}")

        return jsonify({
            'success': True,
            'message': '卖出记录已写入',
            'profit_loss': profit_loss,
            'profit_pct': profit_pct,
            'sell_type': 'full' if is_full else 'half',
        })

    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': f'数据库写入失败: {e}'}), 500


# ══════════════════════════════════════════════════════════════════════════════
# DELETE /api/positions/<id>  —— 删除持仓记录
# ══════════════════════════════════════════════════════════════════════════════

@positions_bp.route('/api/positions/<int:position_id>', methods=['DELETE'])
def delete_position(position_id: int):
    """
    前端 deleteModal 调用。
    硬删除：从 positions 表中物理移除该行。
    """
    conn = _get_db()
    try:
        row = conn.execute(
            "SELECT id, code, name FROM positions WHERE id = ?",
            [position_id]
        ).fetchone()

        if row is None:
            return jsonify({'error': f'找不到 id={position_id} 的持仓记录'}), 404

        conn.execute("DELETE FROM positions WHERE id = ?", [position_id])

        return jsonify({
            'success': True,
            'message': f'持仓记录 {row[1]} {row[2]} 已删除',
        })
    except Exception as e:
        import traceback; traceback.print_exc()
        return jsonify({'error': f'删除失败: {e}'}), 500
