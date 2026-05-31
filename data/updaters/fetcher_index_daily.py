#!/usr/bin/env python
# coding=utf-8
# data/updaters/fetcher_index_daily.py
"""
指数日线数据更新器 - DWD层

支持模式:
- --all: 更新所有默认指数
- --code: 更新指定指数
- --start-date / --end-date: 日期范围
- --incremental: 增量更新（基于 dwd_index_daily 最新日期）
"""
import sys
import os
import argparse
import time
from datetime import datetime
from typing import Optional, List, Dict, Any

import pandas as pd
import duckdb

project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from data.fetchers.tushare_adapter import TushareIndexFetcher, TushareTradeCalFetcher
from database.schema import CREATE_DWD_INDEX_DAILY_TABLE

DB_PATH = os.path.join(project_root, 'data', 'Astock3.duckdb')

from scripts.log_utils import setup_logger
logger = setup_logger('fetcher_index_daily', 'pipeline')

DEFAULT_INDICES = [
    '000001.SH',  # 上证指数
    '399001.SZ',  # 深证成指
    '399006.SZ',  # 创业板指
    '000300.SH',  # 沪深300
    '000016.SH',  # 上证50
    '000905.SH',  # 中证500
    '000852.SH',  # 中证1000
]


class IndexDailyFetcher:
    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.index_fetcher = TushareIndexFetcher()
        self.trade_cal_fetcher = TushareTradeCalFetcher()
        self._ensure_table()

    def _ensure_table(self):
        db = duckdb.connect(self.db_path)
        try:
            db.execute(CREATE_DWD_INDEX_DAILY_TABLE)
            logger.info("dwd_index_daily 表检查完成")
        finally:
            db.close()

    def _save_to_db(self, df: pd.DataFrame, table: str = 'dwd_index_daily') -> int:
        if df is None or df.empty:
            return 0
        df = df.copy()
        date_cols = ['trade_date']
        for col in date_cols:
            if col in df.columns:
                df[col] = df[col].replace('', pd.NA)
                df[col] = pd.to_datetime(df[col], errors='coerce').dt.strftime('%Y-%m-%d')

        skip_cols = ['ts_code', 'index_code', 'exchange']
        for col in df.columns:
            if col not in skip_cols + date_cols and str(df[col].dtype) == 'object':
                df[col] = pd.to_numeric(df[col], errors='coerce')

        db = duckdb.connect(self.db_path)
        try:
            db.execute("CREATE TEMPORARY TABLE temp_idx AS SELECT * FROM df")
            cols = ', '.join(df.columns)
            db.execute(f"INSERT OR REPLACE INTO {table} ({cols}) SELECT {cols} FROM temp_idx")
            db.execute("DROP TABLE temp_idx")
            return len(df)
        finally:
            db.close()

    def get_latest_date(self) -> Optional[str]:
        db = duckdb.connect(self.db_path)
        try:
            result = db.execute("SELECT MAX(trade_date) FROM dwd_index_daily").fetchone()
            if result and result[0]:
                return pd.to_datetime(result[0]).strftime('%Y%m%d')
            return None
        finally:
            db.close()

    def get_next_trade_date(self, from_date: str) -> Optional[str]:
        db = duckdb.connect(self.db_path)
        try:
            from_date_fmt = f"{from_date[:4]}-{from_date[4:6]}-{from_date[6:8]}"
            result = db.execute("""
                SELECT MIN(trade_date) FROM dwd_trade_calendar
                WHERE is_open = TRUE AND trade_date > ?
            """, [from_date_fmt]).fetchone()
            if result and result[0]:
                return pd.to_datetime(result[0]).strftime('%Y%m%d')
            return None
        except Exception as e:
            logger.warning(f"查询下一交易日失败: {e}")
            return None
        finally:
            db.close()

    def update_index(self, index_code: str, start_date: str, end_date: str) -> Dict[str, Any]:
        logger.info(f"更新指数日线: {index_code} {start_date}~{end_date}")
        start_time = time.time()
        try:
            df = self.index_fetcher.fetch(index_code, start_date, end_date)
            if df is not None and not df.empty:
                records = self._save_to_db(df)
                elapsed = time.time() - start_time
                logger.info(f"  {index_code}: {records}条, {elapsed:.1f}秒")
                return {'success': 1, 'fail': 0, 'records': records, 'elapsed': elapsed}
            else:
                logger.warning(f"  {index_code}: 无数据")
                return {'success': 0, 'fail': 1, 'records': 0, 'elapsed': 0}
        except Exception as e:
            logger.error(f"  {index_code} 失败: {e}")
            return {'success': 0, 'fail': 1, 'records': 0, 'elapsed': 0}

    def update_all(self, start_date: str, end_date: str,
                   indices: List[str] = DEFAULT_INDICES) -> Dict[str, Any]:
        logger.info(f"更新所有指数: {start_date}~{end_date}, 共{len(indices)}个")
        total_success, total_fail, total_records = 0, 0, 0
        start_time = time.time()
        for idx in indices:
            result = self.update_index(idx, start_date, end_date)
            total_success += result['success']
            total_fail += result['fail']
            total_records += result['records']
        elapsed = time.time() - start_time
        logger.info(f"全部指数更新完成: 成功{total_success}, 失败{total_fail}, "
                    f"共{total_records}条, 耗时{elapsed:.1f}秒")
        return {'success': total_success, 'fail': total_fail,
                'records': total_records, 'elapsed': elapsed}

    def update_incremental(self, indices: List[str] = DEFAULT_INDICES) -> Dict[str, Any]:
        latest = self.get_latest_date()
        if latest is None:
            start_date = '20200101'
            logger.warning("dwd_index_daily 为空，从 20200101 开始全量更新")
        else:
            next_date = self.get_next_trade_date(latest)
            if next_date is None:
                logger.info("指数日线已是最新，无需更新")
                return {'success': 0, 'fail': 0, 'records': 0, 'elapsed': 0}
            start_date = next_date
        end_date = datetime.now().strftime('%Y%m%d')
        logger.info(f"增量更新范围: {start_date} ~ {end_date}")
        return self.update_all(start_date, end_date, indices)


def run_cli():
    parser = argparse.ArgumentParser(description='指数日线数据更新器')
    parser.add_argument('--all', action='store_true', help='更新所有默认指数')
    parser.add_argument('--code', type=str, help='更新指定指数代码，如 000001.SH')
    parser.add_argument('--start-date', type=str, help='开始日期 YYYYMMDD')
    parser.add_argument('--end-date', type=str, help='结束日期 YYYYMMDD')
    parser.add_argument('--incremental', action='store_true', help='增量更新模式')
    parser.add_argument('--db', type=str, default=DB_PATH, help='数据库路径')
    args = parser.parse_args()

    fetcher = IndexDailyFetcher(db_path=args.db)
    end_date = args.end_date or datetime.now().strftime('%Y%m%d')
    start_date = args.start_date or '20200101'

    if args.incremental:
        result = fetcher.update_incremental()
    elif args.code:
        result = fetcher.update_index(args.code, start_date, end_date)
    elif args.all:
        result = fetcher.update_all(start_date, end_date)
    else:
        parser.print_help()
        return

    print(f"\n指数日线更新完成:")
    print(f"  成功: {result['success']}")
    print(f"  失败: {result['fail']}")
    print(f"  记录数: {result['records']}")
    print(f"  耗时: {result['elapsed']:.1f}秒")


if __name__ == '__main__':
    run_cli()
