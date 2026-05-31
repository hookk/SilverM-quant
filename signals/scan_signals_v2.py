#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
量化信号系统 - scan_signals.py
全量计算每日市场信号

功能:
1. 获取全市场股票列表
2. 多进程并行计算每只股票的技术指标
3. 运行7个策略买入判断 + 卖出判断
4. 批量写入 daily_signals 表

用法:
    python scripts/scan_signals.py                    # 运行今日信号计算
    python scripts/scan_signals.py --date 20260311    # 运行指定日期
    python scripts/scan_signals.py --workers 10       # 指定进程数

参数                用途                                        示例
--date              单日扫描（原有功能不变）                    --date 20260529
--start / --end     区间扫描，--end 可省略（默认最新）          --start 20260101 --end 20260529
--last              N最近 N 个交易日                            --last 30 / --last 60 / --last 120 / --last 365
--skip-existing     增量模式，跳过已有记录的日期配合 --last 使用


"""

import os
import sys

from requests import get
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'singal_cal'))

import json
import argparse
from datetime import datetime
from typing import Optional, Dict, Any, List
from multiprocessing import Pool
from functools import partial
import subprocess

from backtrader import indicator
import numpy as np
import pandas as pd
import duckdb
from sqlalchemy import false

from basic_module import calculate_indicators

from S1_module import calculate_s1_score

from B1_strategy_module import calculate_b1_score
from B2_strategy_module import calculate_b2_score
from BLKB2_strategy_module import check_暴力K,check_倍量柱,check_J拐头向上
from SCB_strategy_module import calculate_dl_score,check_dl_basic_condition,calculate_blk_signal,calculate_scb_signal
from DZ30_strategy_module import calculate_倍量柱_arr,check_前20日非阴,check_长短期KD
from risk_module import (
    RiskManager,
    check_market_condition,
    adjust_threshold_by_market,
    build_risk_enhanced_result,
    calculate_adx,
)

def code_to_ts_code(code: str) -> str:
    """转换股票代码为tushare格式"""
    code = str(code)
    if code.startswith('6'):
        return f"{code}.SH"
    else:
        return f"{code}.SZ"


# 配置路径
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'Astock3.duckdb')


def _cleanup_orphaned_resource_trackers():
    """清理上一次运行遗留的孤儿 resource_tracker 进程"""
    try:
        result = subprocess.run(
            ['pgrep', '-f', 'multiprocessing.resource_tracker'],
            capture_output=True, text=True, timeout=5
        )
        if result.stdout.strip():
            for pid in result.stdout.strip().split('\n'):
                try:
                    subprocess.run(['kill', pid], capture_output=True, timeout=3)
                    logger.info(f"已清理残留的 resource_tracker 进程: PID {pid}")
                except Exception:
                    pass
    except Exception as e:
        logger.warning(f"清理孤儿进程时出错（不影响主流程）: {e}")

from scripts.log_utils import setup_logger
logger = setup_logger('scan_signals', 'pipeline')

DEFAULT_WORKERS = 4  # 默认进程数
DATA_DAYS = 150  # 数据天数

# ==================== daily_signals 表期望的完整列结构 ====================
# 用于自动迁移旧表：如果表已存在但缺少某列，自动 ALTER TABLE ADD COLUMN
DAILY_SIGNALS_COLUMNS = {
    'date':               'DATE',
    'code':               'VARCHAR',
    'name':               'VARCHAR',
    'open':               'DOUBLE',
    'high':               'DOUBLE',
    'low':                'DOUBLE',
    'close':              'DOUBLE',
    'volume':             'DOUBLE',
    'prev_close':         'DOUBLE',
    'change_pct':         'DOUBLE',
    'score_b1':           'DOUBLE',
    'score_b2':           'DOUBLE',
    'score_blk':          'DOUBLE',
    'score_dl':           'DOUBLE',
    'score_dz30':         'DOUBLE',
    'score_scb':          'DOUBLE',
    'score_blkB2':        'DOUBLE',   # ← 修复点：原来重复写成 score_b2
    'signal_buy_b1':      'BOOLEAN',
    'signal_buy_b2':      'BOOLEAN',
    'signal_buy_blk':     'BOOLEAN',
    'signal_buy_dl':      'BOOLEAN',
    'signal_buy_dz30':    'BOOLEAN',
    'signal_buy_scb':     'BOOLEAN',
    'signal_buy_blkB2':   'BOOLEAN',
    'signal_sell_b1':     'BOOLEAN',
    'signal_sell_b2':     'BOOLEAN',
    'signal_sell_blk':    'BOOLEAN',
    'signal_sell_dl':     'BOOLEAN',
    'signal_sell_dz30':   'BOOLEAN',
    'signal_sell_scb':    'BOOLEAN',
    'signal_sell_blkB2':  'BOOLEAN',
    'score_s1':           'DOUBLE',
    'signal_s1_full':     'BOOLEAN',
    'signal_s1_half':     'BOOLEAN',
    'signal_跌破多空线':   'BOOLEAN',
    'signal_止损':         'BOOLEAN',
    'is_observing':       'BOOLEAN',
    'indicators':         'JSON',
    # 风险管理新增列
    'risk_priority':        'INTEGER',
    'risk_stoploss_price':  'DOUBLE',
    'risk_stoploss_pct':    'DOUBLE',
    'risk_position_pct':    'DOUBLE',
    'risk_position_amt':    'DOUBLE',
    'risk_market_state':    'VARCHAR',
    'risk_composite_score': 'DOUBLE',
    'risk_should_buy':      'BOOLEAN',
    'risk_reject_reason':   'VARCHAR',
}


def _ensure_daily_signals_table(conn: duckdb.DuckDBPyConnection) -> None:
    """
    确保 daily_signals 表存在且列结构完整。

    策略：
    1. 若表不存在 → CREATE TABLE（含全部列 + PRIMARY KEY）
    2. 若表已存在 → 检查每列是否存在，缺少则 ALTER TABLE ADD COLUMN
       这样旧表不会丢数据，同时自动补全新列。
    """
    # 建表 DDL（只在表不存在时执行）
    conn.execute("""
        CREATE TABLE IF NOT EXISTS daily_signals (
            date              DATE,
            code              VARCHAR,
            name              VARCHAR,

            -- OHLC数据
            open              DOUBLE,
            high              DOUBLE,
            low               DOUBLE,
            close             DOUBLE,
            volume            DOUBLE,
            prev_close        DOUBLE,
            change_pct        DOUBLE,

            -- 买入分数
            score_b1          DOUBLE,
            score_b2          DOUBLE,
            score_blk         DOUBLE,
            score_dl          DOUBLE,
            score_dz30        DOUBLE,
            score_scb         DOUBLE,
            score_blkB2       DOUBLE,

            -- 买入信号
            signal_buy_b1     BOOLEAN,
            signal_buy_b2     BOOLEAN,
            signal_buy_blk    BOOLEAN,
            signal_buy_dl     BOOLEAN,
            signal_buy_dz30   BOOLEAN,
            signal_buy_scb    BOOLEAN,
            signal_buy_blkB2  BOOLEAN,

            -- 策略卖出信号
            signal_sell_b1    BOOLEAN,
            signal_sell_b2    BOOLEAN,
            signal_sell_blk   BOOLEAN,
            signal_sell_dl    BOOLEAN,
            signal_sell_dz30  BOOLEAN,
            signal_sell_scb   BOOLEAN,
            signal_sell_blkB2 BOOLEAN,

            -- 卖出分数
            score_s1          DOUBLE,

            -- 分数卖出信号
            signal_s1_full    BOOLEAN,
            signal_s1_half    BOOLEAN,
            signal_跌破多空线   BOOLEAN,
            signal_止损        BOOLEAN,

            is_observing      BOOLEAN,

            -- 技术指标
            indicators        JSON,

            PRIMARY KEY (date, code)
        );
    """)

    # 检查并补全缺失列（处理旧表升级场景）
    existing_cols = {
        row[0].lower()
        for row in conn.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name = 'daily_signals'"
        ).fetchall()
    }

    for col_name, col_type in DAILY_SIGNALS_COLUMNS.items():
        if col_name.lower() not in existing_cols:
            try:
                conn.execute(
                    f'ALTER TABLE daily_signals ADD COLUMN "{col_name}" {col_type}'
                )
                logger.info(f"daily_signals 表自动补列: {col_name} {col_type}")
            except Exception as e:
                logger.warning(f"补列 {col_name} 失败（可忽略）: {e}")


def convert_to_serializable(obj):
    if isinstance(obj, np.ndarray):
        return obj.tolist()
    elif isinstance(obj, np.integer):
        return int(obj)
    elif isinstance(obj, np.floating):
        return float(obj)
    elif isinstance(obj, dict):
        return {k: convert_to_serializable(v) for k, v in obj.items()}
    elif isinstance(obj, list):
        return [convert_to_serializable(item) for item in obj]
    else:
        return obj

def get_db_connection():
    """获取数据库连接"""
    return duckdb.connect(DB_PATH, read_only=True)  # 只读模式


def get_trading_date(date: Optional[str] = None) -> str:
    """获取交易日期"""
    latest = None
    if date:
        return f"{date[:4]}-{date[4:6]}-{date[6:]}"
    
    conn = get_db_connection()
    try:
        latest = conn.execute("SELECT MAX(trade_date) FROM dwd_daily_price").fetchone()[0]
        if latest:
            return str(latest)
    finally:
        conn.close()
    
    return latest if latest else datetime.now().strftime('%Y-%m-%d')


def get_stock_list() -> List[Dict]:
    """获取全市场股票列表"""
    conn = get_db_connection()
    try:
        df = conn.execute("""
            SELECT symbol AS code, name
            FROM dwd_stock_info
            WHERE list_status = 'L'
            ORDER BY symbol
        """).fetchdf()
        
        return df.to_dict('records')
    finally:
        conn.close()


def get_stock_data(code: str, trading_date: str, days: int = DATA_DAYS) -> Optional[pd.DataFrame]:
    """获取股票历史数据"""
    conn = get_db_connection()
    try:
        ts_code = code_to_ts_code(code)
        
        df = conn.execute("""
            SELECT ts_code, trade_date, open, high, low, close, vol
            FROM dwd_daily_price
            WHERE ts_code = ?
            AND trade_date <= ?
            ORDER BY trade_date DESC
            LIMIT ?
        """, [ts_code, trading_date, days]).fetchdf()
        
        if df is not None and len(df) > 0:
            df = df.rename(columns={
                'ts_code': 'code',
                'trade_date': 'date',
                'vol': 'volume'
            })
        
        if df is None or len(df) < 60:
            return None
        
        df = df.sort_values('date').reset_index(drop=True)
        return df
    except Exception as e:
        logger.error(f"获取股票数据失败 {code}: {e}")
        return None
    finally:
        conn.close()

def get_positions(code: str) -> Optional[pd.DataFrame]:
    """获取持仓信息"""
    conn = get_db_connection()
    try:
        df = conn.execute("""
            SELECT code, strategy, buy_price, status, buy_date
            FROM positions
            WHERE code = ?
            AND status = 'holding'
            ORDER BY buy_date DESC
        """, [code]).fetchdf()
        
        if df is None or len(df) < 1:
            return None
            
        df = df.sort_values('buy_date').reset_index(drop=True)
        return df
    finally:
        conn.close()

def get_positions_observation_state(code: str) -> Optional[bool]:
    """获取持仓股票的观察状态"""
    conn = get_db_connection()
    try:
        df = conn.execute("""
            SELECT current_跌破多空线
            FROM positions
            WHERE code = ? AND status = 'holding'
        """, [code]).fetchdf()
        
        if df is None or len(df) < 1:
            return None
        
        val = df['current_跌破多空线'].values[0]
        if val is None:
            return False
        return bool(val)
    finally:
        conn.close()

def get_all_positions_observation_states() -> Dict[str, bool]:
    """获取所有持仓股票的观察状态"""
    conn = get_db_connection()
    try:
        df = conn.execute("""
            SELECT code, current_跌破多空线
            FROM positions
            WHERE status = 'holding'
        """).fetchdf()
        
        if df is None or len(df) == 0:
            return {}
        
        result = {}
        for _, row in df.iterrows():
            val = row['current_跌破多空线']
            if val is None or pd.isna(val):
                result[row['code']] = False
            else:
                result[row['code']] = bool(val)
        return result
    finally:
        conn.close()

def update_all_positions_observation_states(updates: List[tuple]):
    """批量更新持仓股票的观察状态"""
    if not updates:
        return
    
    conn = duckdb.connect(DB_PATH, read_only=False)
    try:
        for code, is_observing in updates:
            conn.execute("""
                UPDATE positions
                SET current_跌破多空线 = ?
                WHERE code = ? AND status = 'holding'
            """, [is_observing, code])
        logger.info(f"批量更新 {len(updates)} 只持仓股票的观察状态")
    finally:
        conn.close()

def update_positions_observation_state(code: str, is_observing: bool):
    """更新持仓股票的观察状态到 positions 表"""
    conn = duckdb.connect(DB_PATH, read_only=False)
    try:
        conn.execute("""
            UPDATE positions
            SET current_跌破多空线 = ?
            WHERE code = ? AND status = 'holding'
        """, [is_observing, code])
    finally:
        conn.close()

# ==================== 买入信号计算模块 ====================
def get_b1_buy_signal(name: str, indicators: Dict, b1_threshold=8):
    try:
        KDJ_J低 = indicators['j'] < 13
        MACD_多头 = indicators['dif'] >= 0
        趋势线条件 = indicators['知行短期趋势线'] > indicators['知行多空线']
    
        b1_score = calculate_b1_score(indicators)
        B1总分 = b1_score
        buy_condition = (KDJ_J低 and MACD_多头 and 趋势线条件 and B1总分 >= b1_threshold)

        if buy_condition:
            logger.info(f"\n=== B1买入信号详情 ===")
            logger.info(datetime.now().strftime('%Y-%m-%d'))
            logger.info(f"股票代码code: {indicators['code']}")
            logger.info(f"B1总分: {B1总分}")
            logger.info(f"KDJ_J低于13: {KDJ_J低}")
            logger.info(f"J值: {indicators['j']}")
            logger.info(f"MACD_多头: {MACD_多头}")
            logger.info(f"短期趋势线>多空线: {趋势线条件}")

        return b1_score, buy_condition
    except Exception as e:
        logger.error(f"{indicators['code']} B1买入条件处理失败: {e}")
        logger.error(f"{name} B1买入条件处理失败: {e}")
    return 0, False

def get_b2_buy_signal(name: str, indicators: Dict, b2_threshold=8):
    try:
        MACD_多头 = indicators['dif'] >= 0
        趋势线条件 = indicators['知行短期趋势线'] > indicators['知行多空线']
    
        b2_score = calculate_b2_score(indicators)

        logger.info(f"开始处理B2策略 {indicators['code']}")
        B2总分 = b2_score
        buy_condition = (MACD_多头 and 趋势线条件 and B2总分 >= b2_threshold)

        if buy_condition:
            logger.info(f"\n=== B2买入信号详情 ===")
            logger.info(datetime.now().strftime('%Y-%m-%d'))
            logger.info(f"股票代码code: {indicators['code']}")
            logger.info(f"B2总分: {B2总分}")
            logger.info(f"MACD_多头: {MACD_多头}")
            logger.info(f"短期趋势线>多空线: {趋势线条件}")

        return b2_score, buy_condition
    except Exception as e:
        logger.error(f"{indicators['code']} B2买入条件处理失败: {e}")
        logger.error(f"{name} B2买入条件处理失败: {e}")
    return 0, False

def get_BLK_buy_signal(name: str, indicators: Dict):
    try:
        score_blk = 0
        趋势线条件 = indicators['知行短期趋势线'] > indicators['知行多空线']
        暴力K = check_暴力K(indicators)
        
        buy_condition = (趋势线条件 and 暴力K)

        if buy_condition:
            score_blk = 7
            logger.info(f"\n=== 暴力k买入信号详情 ===")
            logger.info(datetime.now().strftime('%Y-%m-%d'))
            logger.info(f"股票代码code: {indicators['code']}")
            logger.info(f"短期趋势线>多空线: {趋势线条件}")
            logger.info(f"暴力K: {暴力K}")

        return score_blk, buy_condition
    except Exception as e:
        logger.error(f"{indicators['code']} 暴力k买入条件处理失败: {e}")
        logger.error(f"{name} 暴力k买入条件处理失败: {e}")
    return 0, False

def get_BLKB2_buy_signal(name: str, indicators: Dict, b2_threshold=8, score_b2=0):
    try:
        score_blkB2 = 0  # ← 修复点：原来命名混乱，统一用 score_blkB2
        MACD_多头 = indicators['dif'] >= 0
        趋势线条件 = indicators['知行短期趋势线'] > indicators['知行多空线']
        
        b2_score = score_b2
        
        暴力K = check_暴力K(indicators)
        score_blk = 7 if 暴力K else 0
        
        倍量柱 = check_倍量柱(indicators)
        score_blz = 10 if 倍量柱 else 0
        
        J拐头向上 = check_J拐头向上(indicators)
        score_jt = 10 if J拐头向上 else 0
        
        buy_condition = (MACD_多头 and 趋势线条件 and b2_score >= b2_threshold
                         and 暴力K and 倍量柱 and J拐头向上)

        if buy_condition:
            score_blkB2 = score_blk * 0.5 + b2_score * 0.6 + score_blz * 0.2 + score_jt * 0.1
            logger.info(f"\n=== 暴力k+B2买入信号详情 ===")
            logger.info(datetime.now().strftime('%Y-%m-%d'))
            logger.info(f"股票代码code: {indicators['code']}")
            logger.info(f"B2总分: {b2_score}")
            logger.info(f"MACD_多头: {MACD_多头}")
            logger.info(f"短期趋势线>多空线: {趋势线条件}")
            logger.info(f"暴力K: {暴力K}")
            logger.info(f"倍量柱: {倍量柱}")
            logger.info(f"J拐头向上: {J拐头向上}")

        return score_blkB2, buy_condition
    except Exception as e:
        logger.error(f"{indicators['code']} 暴力k+B2买入条件处理失败: {e}")
        logger.error(f"{name} 暴力k+B2买入条件处理失败: {e}")
    return 0, False

def get_SCB_buy_signal(name: str, indicators: Dict):
    try:
        dl_basic_history = []
        for offset in range(1, 6):
            historical_indicators = {
                'code': indicators['code'],
                'close': indicators['close_arr'][-offset-1],
                'prev_close': indicators['close_arr'][-offset-2],
                'open': indicators['open_arr'][-offset-1],
                'high': indicators['high_arr'][-offset-1],
                'low': indicators['low_arr'][-offset-1],
                'volume': indicators['volume_arr'][-offset-1],
                'close_arr': indicators['close_arr'][:-(offset+1)],
                'open_arr': indicators['open_arr'][:-(offset+1)],
                'high_arr': indicators['high_arr'][:-(offset+1)],
                'low_arr': indicators['low_arr'][:-(offset+1)],
                'volume_arr': indicators['volume_arr'][:-(offset+1)],
            }
            dl_result = check_dl_basic_condition(historical_indicators)
            dl_basic_history.append(dl_result)
        
        blk_signal = calculate_blk_signal(indicators)
        scb_signal, scb_score = calculate_scb_signal(indicators, blk_signal, dl_basic_history)
        buy_condition = scb_signal

        if buy_condition:
            logger.info(f"\n=== 沙尘暴买入信号详情 ===")
            logger.info(datetime.now().strftime('%Y-%m-%d'))
            logger.info(f"股票代码code: {indicators['code']}")
            logger.info(f"地量基础条件: {dl_basic_history}")
            logger.info(f"暴力K: {blk_signal}")
            logger.info(f"SCB评分: {scb_score}")

        return scb_score, buy_condition
    except Exception as e:
        logger.error(f"{indicators['code']} 沙尘暴买入条件处理失败: {e}")
        logger.error(f"{name} 沙尘暴买入条件处理失败: {e}")
    return 0, False

def get_DZ30_buy_signal(name: str, indicators: Dict):
    try:
        score_dz30 = 0
        短期KD, 长期KD = check_长短期KD(indicators)
        今日条件 = (长期KD >= 80) and (短期KD <= 30)
        
        价格在趋势线上 = indicators['close'] > indicators['知行短期趋势线']
        趋势多头 = indicators['知行短期趋势线'] > indicators['知行多空线']
        
        倍量柱_arr = calculate_倍量柱_arr(indicators)
        倍量柱_count = np.sum(倍量柱_arr[-20:]) if len(倍量柱_arr) >= 20 else np.sum(倍量柱_arr)
        有倍量柱 = 倍量柱_count >= 1
        
        前20日非阴 = check_前20日非阴(indicators)
        
        buy_condition = (今日条件 and 价格在趋势线上 and
                         趋势多头 and 有倍量柱 and 前20日非阴)

        if buy_condition:
            score_dz30 = 5
            logger.info(f"\n=== 单针30买入信号详情 ===")
            logger.info(datetime.now().strftime('%Y-%m-%d'))
            logger.info(f"股票代码code: {indicators['code']}")
            logger.info(f"长期>85,短期<=30: {今日条件}")
            logger.info(f"价格在趋势线上: {价格在趋势线上}")
            logger.info(f"短期趋势线>多空线: {趋势多头}")
            logger.info(f"有倍量柱: {有倍量柱}")
            logger.info(f"前20日非阴: {前20日非阴}")

        return score_dz30, buy_condition
    except Exception as e:
        logger.error(f"{indicators['code']} 单针30买入条件处理失败: {e}")
        logger.error(f"{name} 单针30买入条件处理失败: {e}")
    return 0, False

# ==================== 卖出信号计算模块 ====================
def common_sell_signal(name: str, indicators: Dict, was_observing: bool = False):
    score_s1 = calculate_s1_score(indicators)

    signal_s1_full = False
    signal_s1_half = False
    signal_跌破多空线 = False
    signal_止损 = False
    is_observing = False

    if score_s1 >= 5 and score_s1 < 10:
        signal_s1_half = True
    if score_s1 >= 10:
        signal_s1_full = True

    current_close = indicators['close']
    current_line = indicators['知行多空线']
    prev_close = indicators['close_arr'][-2]

    if current_close < current_line:
        if prev_close >= current_line:
            is_observing = True
        elif was_observing:
            signal_跌破多空线 = True
            is_observing = False
    else:
        is_observing = False

    return score_s1, signal_s1_half, signal_s1_full, signal_跌破多空线, signal_止损, is_observing

def _build_sell_condition(indicators: Dict, positions_data: tuple = None) -> bool:
    """通用卖出条件判断（内部复用，避免重复代码）"""
    signal_s1_half     = indicators.get('signal_s1_half', False)
    signal_s1_full     = indicators.get('signal_s1_full', False)
    signal_跌破多空线  = indicators.get('signal_跌破多空线', False)
    signal_止损        = indicators.get('signal_止损', False)
    
    if positions_data is not None:
        buy_price, _ = positions_data
        if buy_price and buy_price > 0:
            buy_price_cost = buy_price * 1.0005
            profit_pct_low = (indicators['low'] - buy_price_cost) / buy_price_cost * 100
            if profit_pct_low < -3:
                signal_止损 = True
    
    return signal_s1_full or signal_s1_half or signal_跌破多空线 or signal_止损

def get_b1_sell_signal(name: str, indicators: Dict, positions_data: tuple = None):
    sell_condition = _build_sell_condition(indicators, positions_data)
    if sell_condition:
        logger.info(f"\n=== B1卖出信号详情 ===")
        logger.info(f"股票代码code: {indicators['code']} 名称: {name}")
        logger.info(f"S1分数: {indicators.get('score_s1',0)} 收盘: {indicators['close']} 多空线: {indicators['知行多空线']}")
    return sell_condition

def get_b2_sell_signal(name: str, indicators: Dict, positions_data: tuple = None):
    sell_condition = _build_sell_condition(indicators, positions_data)
    if sell_condition:
        logger.info(f"\n=== B2卖出信号详情 ===")
        logger.info(f"股票代码code: {indicators['code']} 名称: {name}")
        logger.info(f"S1分数: {indicators.get('score_s1',0)} 收盘: {indicators['close']} 多空线: {indicators['知行多空线']}")
    return sell_condition

def get_BLKB2_sell_signal(name: str, indicators: Dict, positions_data: tuple = None):
    sell_condition = _build_sell_condition(indicators, positions_data)
    if sell_condition:
        logger.info(f"\n=== 暴力k+B2卖出信号详情 ===")
        logger.info(f"股票代码code: {indicators['code']} 名称: {name}")
        logger.info(f"S1分数: {indicators.get('score_s1',0)} 收盘: {indicators['close']} 多空线: {indicators['知行多空线']}")
    return sell_condition

def get_BLK_sell_signal(name: str, indicators: Dict, positions_data: tuple = None):
    sell_condition = _build_sell_condition(indicators, positions_data)
    if sell_condition:
        logger.info(f"\n=== 暴力k卖出信号详情 ===")
        logger.info(f"股票代码code: {indicators['code']} 名称: {name}")
        logger.info(f"S1分数: {indicators.get('score_s1',0)} 收盘: {indicators['close']} 多空线: {indicators['知行多空线']}")
    return sell_condition

def get_SCB_sell_signal(name: str, indicators: Dict, positions_data: tuple = None):
    sell_condition = _build_sell_condition(indicators, positions_data)
    if sell_condition:
        logger.info(f"\n=== 沙尘暴卖出信号详情 ===")
        logger.info(f"股票代码code: {indicators['code']} 名称: {name}")
        logger.info(f"S1分数: {indicators.get('score_s1',0)} 收盘: {indicators['close']} 多空线: {indicators['知行多空线']}")
    return sell_condition

def get_DZ30_sell_signal(name: str, indicators: Dict, positions_data: tuple = None):
    sell_condition = _build_sell_condition(indicators, positions_data)
    if sell_condition:
        logger.info(f"\n=== 单针30卖出信号详情 ===")
        logger.info(f"股票代码code: {indicators['code']} 名称: {name}")
        logger.info(f"S1分数: {indicators.get('score_s1',0)} 收盘: {indicators['close']} 多空线: {indicators['知行多空线']}")
    return sell_condition

# ==================== 股票处理模块 ====================
def process_single_stock(args: tuple) -> Optional[Dict]:
    """处理单只股票"""
    # 解包时增加新参数
    code, name, trading_date, was_observing, b1_threshold, b2_threshold, market_state = args

    df = get_stock_data(code, trading_date)
    if df is None or len(df) < 60:
        return None

    indicators = calculate_indicators(df)

    b1_threshold = 8
    b2_threshold = 8
    
    try:
        positions = get_positions(code)
        positions_data = None
        if positions is not None and len(positions) > 0:
            buy_price = positions['buy_price'].values[0]
            strategy  = positions['strategy'].values[0]
            positions_data = (buy_price, strategy)
        
        # 买入信号（使用动态阈值）
        score_b1,  b1_buy_condition   = get_b1_buy_signal(name, indicators, b1_threshold)
        score_b2,  b2_buy_condition   = get_b2_buy_signal(name, indicators, b2_threshold)
        # ↓ 修复点：BLKB2 返回值用独立变量 score_blkB2，不再覆盖 score_b2
        score_blkB2, BLKB2_buy_condition = get_BLKB2_buy_signal(name, indicators, b2_threshold, score_b2)
        score_blk, BLK_buy_condition  = get_BLK_buy_signal(name, indicators)
        scb_score, SCB_buy_condition  = get_SCB_buy_signal(name, indicators)
        score_dz30, DZ30_buy_condition = get_DZ30_buy_signal(name, indicators)
        
        # 卖出信号（S1只算一次）
        score_s1, signal_s1_half, signal_s1_full, signal_跌破多空线, signal_止损, current_observing = \
            common_sell_signal(name, indicators, was_observing)

        indicators['score_s1']         = score_s1
        indicators['signal_s1_half']   = signal_s1_half
        indicators['signal_s1_full']   = signal_s1_full
        indicators['signal_跌破多空线'] = signal_跌破多空线
        indicators['signal_止损']       = signal_止损
        indicators['is_observing']     = current_observing

        b1_sell_condition   = get_b1_sell_signal(name, indicators, positions_data)
        b2_sell_condition   = get_b2_sell_signal(name, indicators, positions_data)
        BLKB2_sell_condition = get_BLKB2_sell_signal(name, indicators, positions_data)
        BLK_sell_condition  = get_BLK_sell_signal(name, indicators, positions_data)
        SCB_sell_condition  = get_SCB_sell_signal(name, indicators, positions_data)
        DZ30_sell_condition = get_DZ30_sell_signal(name, indicators, positions_data)
        
        indicators_serializable = convert_to_serializable(indicators)


        # ─── 新增：Risk Manager 评估 ───────────────────────────────
        any_buy = any([b1_buy_condition, b2_buy_condition, SCB_buy_condition,
                    BLK_buy_condition, DZ30_buy_condition])
        
        rm_eval = None
        if any_buy:
            # 注意：rm 是进程外的对象，多进程下需要在函数内重建
            # 或者通过 initializer 传入（见下方说明）
            from risk_module import RiskManager
            _rm = RiskManager(total_capital=1_000_000)
            rm_eval = _rm.evaluate(
                indicators      = indicators,
                signal_buy_b1   = b1_buy_condition,
                signal_buy_b2   = b2_buy_condition,
                signal_buy_scb  = SCB_buy_condition,
                signal_buy_blk  = BLK_buy_condition,
                signal_buy_dz30 = DZ30_buy_condition,
                score_b1        = score_b1,
                score_b2        = score_b2,
                score_scb       = scb_score,
                score_blk       = score_blk,
                score_dz30      = score_dz30,
                market_state    = market_state,
            )
        
        risk_fields = build_risk_enhanced_result(
            base_result   = {},
            indicators    = indicators,
            buy_condition = any_buy,
            rm_evaluation = rm_eval,
        )
        # ─── Risk Manager 评估结束 ────────────────────────────────


        # ↓ 修复点：result 字典列名与 CREATE TABLE 完全一一对应，无重复无缺失
        result = {
            'date':       trading_date,
            'code':       code,
            'name':       name,

            'open':       indicators['open'],
            'high':       indicators['high'],
            'low':        indicators['low'],
            'close':      indicators['close'],
            'volume':     indicators['volume'],
            'prev_close': indicators['prev_close'],
            'change_pct': indicators['涨幅'],

            'score_b1':    score_b1,
            'score_b2':    score_b2,
            'score_blk':   score_blk,
            'score_dl':    0,
            'score_dz30':  score_dz30,
            'score_scb':   scb_score,
            'score_blkB2': score_blkB2,   # ← 独立变量，不再和 score_b2 混淆

            'signal_buy_b1':    b1_buy_condition,
            'signal_buy_b2':    b2_buy_condition,
            'signal_buy_blk':   BLK_buy_condition,
            'signal_buy_dl':    False,
            'signal_buy_dz30':  DZ30_buy_condition,
            'signal_buy_scb':   SCB_buy_condition,
            'signal_buy_blkB2': BLKB2_buy_condition,

            'signal_sell_b1':    b1_sell_condition,
            'signal_sell_b2':    b2_sell_condition,
            'signal_sell_blk':   BLK_sell_condition,
            'signal_sell_dl':    False,
            'signal_sell_dz30':  DZ30_sell_condition,
            'signal_sell_scb':   SCB_sell_condition,
            'signal_sell_blkB2': BLKB2_sell_condition,

            'score_s1':          score_s1,
            'signal_s1_full':    signal_s1_full,
            'signal_s1_half':    signal_s1_half,
            'signal_跌破多空线':  signal_跌破多空线,
            'signal_止损':        signal_止损,
            'is_observing':      current_observing,
            'indicators':        json.dumps(indicators_serializable, ensure_ascii=False),
            # 新增 risk 字段
            **risk_fields,
        }
        return result
    except Exception as e:
        logger.error(f"处理股票 {code}({name}) 失败: {e}")
        return None


def scan_signals(trading_date: str, workers: int = DEFAULT_WORKERS) -> Dict[str, Any]:
    """扫描信号"""
    _cleanup_orphaned_resource_trackers()
    logger.info(f"开始扫描信号: {trading_date}, 进程数: {workers}")
    start_time = datetime.now()
    
    stocks = get_stock_list()
    
    # ─── 新增：获取大盘状态 ───────────────────────────────────
    market_state = 'range'  # 默认震荡
    try:
        conn = get_db_connection()
        # 用上证指数（000001.SH）或沪深300（000300.SH）
        index_df = conn.execute("""
            SELECT close, vol AS volume FROM dwd_daily_price
            WHERE ts_code = '000001.SH'
            AND trade_date <= ?
            ORDER BY trade_date DESC LIMIT 100
        """, [trading_date]).fetchdf()
        conn.close()
        
        if index_df is not None and len(index_df) >= 60:
            index_df = index_df.sort_values('trade_date').reset_index(drop=True)
            market_state = check_market_condition(
                index_close_arr  = index_df['close'].values,
                index_volume_arr = index_df['volume'].values,
            )
        logger.info(f"当前大盘状态: {market_state}")
    except Exception as e:
        logger.warning(f"大盘状态判断失败，使用默认 range: {e}")
    # ─── 大盘状态获取结束 ─────────────────────────────────────
    
    # 动态调整阈值
    b1_threshold = adjust_threshold_by_market(8.0, market_state)
    b2_threshold = adjust_threshold_by_market(8.0, market_state)
    
    # 初始化 RiskManager（整日共享一个实例）
    rm = RiskManager(
        total_capital    = 1_000_000,   # 根据实际资金修改
        kelly_fraction   = 0.5,
        max_position_pct = 0.25,
        atr_multiplier   = 2.0,
        aggressiveness   = 'normal',
    )
    
    # 获取所有持仓股票的观察状态快照（在构建 args_list 前必须先初始化）
    positions_observing_snapshot = get_all_positions_observation_states()

    # 将 rm 和 market_state 传入 args_list
    args_list = [
        (s['code'], s['name'], trading_date,
         positions_observing_snapshot.get(s['code'], False),
         b1_threshold, b2_threshold, market_state)
        for s in stocks
    ]
    
    results = []
    observation_updates = []
    success_count = 0
    fail_count = 0
    
    pool = None
    try:
        pool = Pool(processes=workers)
        for result in pool.imap_unordered(process_single_stock, args_list, chunksize=100):
            if result:
                results.append(result)
                success_count += 1
                if result['code'] in positions_observing_snapshot:
                    observation_updates.append((result['code'], result.get('is_observing', False)))
            else:
                fail_count += 1
            
            total = success_count + fail_count
            if total % 500 == 0:
                logger.info(f"进度: {total}/{len(stocks)}")
        
        pool.close()
        pool.join()
        logger.info(f"Pool 正常关闭")
        
    except KeyboardInterrupt:
        logger.warning("检测到用户中断，正在强制关闭 Pool...")
        if pool:
            pool.terminate()
            pool.join()
        raise
        
    except Exception as e:
        logger.error(f"Pool 处理异常: {e}，正在强制关闭...")
        if pool:
            try:
                pool.terminate()
                pool.join(timeout=5)
            except Exception:
                pass
        raise
        
    finally:
        if pool:
            try:
                pool.terminate()
                pool.join(timeout=3)
            except Exception:
                pass
    
    if observation_updates:
        update_all_positions_observation_states(observation_updates)
    
    logger.info(f"处理完成: 成功 {success_count}, 失败 {fail_count}")
    
    if results:
        conn = duckdb.connect(DB_PATH, read_only=False)
        try:
            # ↓ 修复点：用新函数建表/迁移，自动处理旧表缺列问题
            _ensure_daily_signals_table(conn)

            conn.execute("DELETE FROM daily_signals WHERE date = ?", [trading_date])
            
            results_db = pd.DataFrame(results)
            conn.execute("INSERT INTO daily_signals BY NAME SELECT * FROM results_db")
            
            logger.info(f"写入数据库 {len(results)} 条记录")
        except Exception as e:
            logger.error(f"写入数据库失败: {e}")
            raise
        finally:
            conn.close()
    
    signal_stats = {
        'signal_buy_b1':    sum(1 for r in results if r['signal_buy_b1']),
        'signal_buy_b2':    sum(1 for r in results if r['signal_buy_b2']),
        'signal_buy_blk':   sum(1 for r in results if r['signal_buy_blk']),
        'signal_buy_dl':    sum(1 for r in results if r['signal_buy_dl']),
        'signal_buy_dz30':  sum(1 for r in results if r['signal_buy_dz30']),
        'signal_buy_scb':   sum(1 for r in results if r['signal_buy_scb']),
        'signal_buy_blkB2': sum(1 for r in results if r['signal_buy_blkB2']),

        'signal_sell_b1':    sum(1 for r in results if r['signal_sell_b1']),
        'signal_sell_b2':    sum(1 for r in results if r['signal_sell_b2']),
        'signal_sell_blk':   sum(1 for r in results if r['signal_sell_blk']),
        'signal_sell_dl':    sum(1 for r in results if r['signal_sell_dl']),
        'signal_sell_dz30':  sum(1 for r in results if r['signal_sell_dz30']),
        'signal_sell_scb':   sum(1 for r in results if r['signal_sell_scb']),
        'signal_sell_blkB2': sum(1 for r in results if r['signal_sell_blkB2']),

        'signal_跌破多空线': sum(1 for r in results if r['signal_跌破多空线']),
        'signal_止损':       sum(1 for r in results if r['signal_止损']),
        'signal_s1_full':   sum(1 for r in results if r['signal_s1_full']),
        'signal_s1_half':   sum(1 for r in results if r['signal_s1_half']),
    }
    
    end_time = datetime.now()
    duration = (end_time - start_time).total_seconds()
    
    logger.info(f"扫描完成，耗时: {duration:.1f}秒")
    logger.info(f"信号统计: {signal_stats}")
    
    # ===== 流水线日志记录 =====
    try:
        from scripts.pipeline_manager import write_step_log
        
        buy_signals  = sum(1 for r in results if r and (r.get('signal_buy_b1') or r.get('signal_buy_b2')))
        sell_signals = sum(1 for r in results if r and (r.get('signal_sell_b1') or r.get('signal_sell_b2')))
        b1_signals   = sum(1 for r in results if r and r.get('signal_buy_b1'))
        b2_signals   = sum(1 for r in results if r and r.get('signal_buy_b2'))
        blk_signals  = sum(1 for r in results if r and r.get('signal_buy_blk'))
        
        pipeline_id = os.environ.get('PIPELINE_ID', f"manual_{datetime.now().strftime('%Y%m%d')}")
        
        write_step_log(pipeline_id, 'signals', {
            'update_type':  'daily',
            'start_time':   start_time,
            'end_time':     end_time,
            'duration_sec': duration,
            'expected_count': len(stocks),
            'actual_count':   success_count,
            'is_success':     True,
            'step_details': {
                'target_date':       trading_date,
                'buy_signals_count': buy_signals,
                'sell_signals_count': sell_signals,
                'b1_signals':  b1_signals,
                'b2_signals':  b2_signals,
                'blk_signals': blk_signals,
            }
        })
    except Exception as e:
        logger.warning(f"写入流水线日志失败: {e}")
    # ===== 流水线日志记录结束 =====
    
    return {
        'date':          trading_date,
        'total_stocks':  len(stocks),
        'success_count': success_count,
        'fail_count':    fail_count,
        'signal_stats':  signal_stats,
        'duration':      duration,
    }


def get_trading_dates_in_range(start_date: str, end_date: str) -> List[str]:
    """
    从 dwd_daily_price 获取 [start_date, end_date] 区间内所有实际交易日（YYYY-MM-DD）。
    start_date / end_date 格式: YYYYMMDD 或 YYYY-MM-DD 均可。
    """
    def _fmt(d: str) -> str:
        d = d.strip().replace('-', '')
        return f"{d[:4]}-{d[4:6]}-{d[6:]}"

    s = _fmt(start_date)
    e = _fmt(end_date)

    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT DISTINCT trade_date
            FROM dwd_daily_price
            WHERE trade_date >= ? AND trade_date <= ?
            ORDER BY trade_date
        """, [s, e]).fetchall()
        return [str(r[0]) for r in rows]
    finally:
        conn.close()


def get_trading_dates_last_n(n: int) -> List[str]:
    """
    从 dwd_daily_price 获取最近 n 个实际交易日（YYYY-MM-DD，时间正序）。
    """
    conn = get_db_connection()
    try:
        rows = conn.execute("""
            SELECT DISTINCT trade_date
            FROM dwd_daily_price
            ORDER BY trade_date DESC
            LIMIT ?
        """, [n]).fetchall()
        return sorted([str(r[0]) for r in rows])
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(
        description='量化信号扫描',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  单日扫描（默认最新交易日）:
    python scan_signals_v2.py

  指定单日:
    python scan_signals_v2.py --date 20260529

  区间扫描:
    python scan_signals_v2.py --start 20260101 --end 20260529

  最近 N 天:
    python scan_signals_v2.py --last 30
    python scan_signals_v2.py --last 60
    python scan_signals_v2.py --last 120
    python scan_signals_v2.py --last 365
""")
    parser.add_argument('--date',    type=str, help='单日扫描，格式 YYYYMMDD')
    parser.add_argument('--start',   type=str, help='区间开始日期，格式 YYYYMMDD')
    parser.add_argument('--end',     type=str, help='区间结束日期，格式 YYYYMMDD（默认最新交易日）')
    parser.add_argument('--last',    type=int, help='最近 N 个交易日（如 30/60/120/365）')
    parser.add_argument('--workers', type=int, default=DEFAULT_WORKERS,
                        help=f'进程数 (默认 {DEFAULT_WORKERS})')
    parser.add_argument('--skip-existing', action='store_true',
                        help='跳过已有扫描记录的日期（增量模式）')
    args = parser.parse_args()

    # ── 确定待扫描日期列表 ──────────────────────────────────────────────
    if args.last:
        # --last N：最近 N 个交易日
        dates = get_trading_dates_last_n(args.last)
        logger.info(f"模式: 最近 {args.last} 个交易日，共找到 {len(dates)} 天")

    elif args.start:
        # --start / --end 区间
        end = args.end or get_trading_date()   # end 默认最新交易日
        dates = get_trading_dates_in_range(args.start, end)
        logger.info(f"模式: 区间 {args.start} ~ {end}，共找到 {len(dates)} 天")

    else:
        # 单日（--date 或今日最新）
        dates = [get_trading_date(args.date)]
        logger.info(f"模式: 单日 {dates[0]}")

    if not dates:
        logger.error("未找到任何交易日，请检查数据库或参数")
        return

    # ── 跳过已有记录（增量模式）────────────────────────────────────────
    if args.skip_existing and len(dates) > 1:
        conn = get_db_connection()
        try:
            existing = {
                str(r[0])
                for r in conn.execute(
                    "SELECT DISTINCT date FROM daily_signals"
                ).fetchall()
            }
        finally:
            conn.close()
        before = len(dates)
        dates = [d for d in dates if d not in existing]
        logger.info(f"增量模式: 跳过 {before - len(dates)} 天已有记录，剩余 {len(dates)} 天待扫描")

    # ── 逐日扫描 ────────────────────────────────────────────────────────
    total_dates = len(dates)
    logger.info(f"=== 量化信号扫描启动，共 {total_dates} 天，进程数: {args.workers} ===")

    all_ok, all_fail = 0, 0
    for idx, trading_date in enumerate(dates, 1):
        logger.info(f"[{idx}/{total_dates}] 扫描日期: {trading_date}")
        try:
            result = scan_signals(trading_date, args.workers)
            all_ok += 1
            logger.info(
                f"  ✓ {trading_date} 完成 | "
                f"股票 {result['success_count']}/{result['total_stocks']} | "
                f"耗时 {result['duration']:.1f}s"
            )
        except KeyboardInterrupt:
            logger.warning("用户中断，已完成 %d/%d 天", idx - 1, total_dates)
            break
        except Exception as e:
            all_fail += 1
            logger.error(f"  ✗ {trading_date} 失败: {e}")

    logger.info(
        f"=== 全部完成 | 成功 {all_ok} 天 | 失败 {all_fail} 天 ==="
    )


if __name__ == '__main__':
    main()