#!/usr/bin/env python
# coding=utf-8
#!/usr/bin/env python
# coding=utf-8
# data/updaters/fetcher_daily_basic.py
"""
每日基本指标数据更新器 - DWD层

更新 dwd_daily_basic 表，字段包括：
  pe, pe_ttm, pb, ps, ps_ttm, dv_ratio, dv_ttm,
  total_share, float_share, free_share,
  total_mv, circ_mv 等

支持模式:
- --date:        更新指定单日
- --start-date / --end-date: 指定日期范围
- --incremental: 增量更新（基于 dwd_daily_basic 最新日期自动推进）
- --full:        全量更新（从 --start-date 或默认 20200101 开始）
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

from data.fetchers.tushare_adapter import TushareDailyBasicFetcher, TushareTradeCalFetcher
from database.schema import CREATE_DWD_DAILY_BASIC_TABLE

DB_PATH = os.path.join(project_root, 'data', 'Astock3.duckdb')

from scripts.log_utils import setup_logger
logger = setup_logger('fetcher_daily_basic', 'pipeline')


class DailyBasicFetcher:
    """
    每日基本指标更新器（dwd_daily_basic）

    使用示例:
        fetcher = DailyBasicFetcher()

        # 更新单日
        fetcher.update_date('20260522')

        # 增量更新
        fetcher.update_incremental()

        # 全量更新指定范围
        fetcher.update_range('20260101', '20260522')
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self.daily_basic_fetcher = TushareDailyBasicFetcher()
        self.trade_cal_fetcher = TushareTradeCalFetcher()
        self._ensure_table()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _ensure_table(self) -> None:
        """确保 dwd_daily_basic 表存在（幂等）"""
        db = duckdb.connect(self.db_path)
        try:
            db.execute(CREATE_DWD_DAILY_BASIC_TABLE)
            logger.info("dwd_daily_basic 表检查完成")
        finally:
            db.close()

    def _save_to_db(self, df: pd.DataFrame, table: str = 'dwd_daily_basic') -> int:
        """将 DataFrame 写入数据库（INSERT OR REPLACE 语义）"""
        if df is None or df.empty:
            return 0

        df = df.copy()

        # 日期列转换
        date_cols = ['trade_date', 'ann_date']
        for col in date_cols:
            if col in df.columns:
                df[col] = df[col].replace('', pd.NA)
                df[col] = pd.to_datetime(df[col], errors='coerce').dt.strftime('%Y-%m-%d')

        # 非数值列白名单
        skip_numeric = {'ts_code', 'trade_date', 'ann_date'}
        for col in df.columns:
            if col not in skip_numeric and str(df[col].dtype) == 'object':
                df[col] = pd.to_numeric(df[col], errors='coerce')

        db = duckdb.connect(self.db_path)
        try:
            db.execute("CREATE TEMPORARY TABLE temp_basic AS SELECT * FROM df")
            cols = ', '.join(df.columns)
            db.execute(
                f"INSERT OR REPLACE INTO {table} ({cols}) SELECT {cols} FROM temp_basic"
            )
            db.execute("DROP TABLE temp_basic")
            return len(df)
        finally:
            db.close()

    def _get_trade_dates(self, start_date: str, end_date: str) -> List[str]:
        """获取指定范围内交易日列表（YYYYMMDD 格式）"""
        df = self.trade_cal_fetcher.fetch(
            start_date=start_date, end_date=end_date, exchange='SSE'
        )
        if df is None or df.empty:
            logger.warning(f"未获取到交易日历: {start_date} ~ {end_date}")
            return []
        trade_dates = df[df['is_open'] == 1]['trade_date'].tolist()
        return [d.replace('-', '') for d in trade_dates]

    def get_latest_date(self) -> Optional[str]:
        """获取 dwd_daily_basic 表中最新交易日期（YYYYMMDD）"""
        db = duckdb.connect(self.db_path)
        try:
            result = db.execute(
                "SELECT MAX(trade_date) FROM dwd_daily_basic"
            ).fetchone()
            if result and result[0]:
                return pd.to_datetime(result[0]).strftime('%Y%m%d')
            return None
        finally:
            db.close()

    def get_next_trade_date(self, from_date: str) -> Optional[str]:
        """从 dwd_trade_calendar 获取 from_date 之后第一个交易日（YYYYMMDD）"""
        db = duckdb.connect(self.db_path)
        try:
            from_date_fmt = f"{from_date[:4]}-{from_date[4:6]}-{from_date[6:8]}"
            result = db.execute(
                """
                SELECT MIN(trade_date) FROM dwd_trade_calendar
                WHERE is_open = TRUE AND trade_date > ?
                """,
                [from_date_fmt],
            ).fetchone()
            if result and result[0]:
                return pd.to_datetime(result[0]).strftime('%Y%m%d')
            return None
        except Exception as e:
            logger.warning(f"查询下一交易日失败: {e}")
            return None
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 公开更新方法
    # ------------------------------------------------------------------

    def update_date(self, trade_date: str) -> Dict[str, Any]:
        """
        更新单日每日指标。

        Args:
            trade_date: 交易日 YYYYMMDD

        Returns:
            统计字典 {success, fail, records, elapsed}
        """
        logger.info(f"更新每日指标: {trade_date}")
        start_time = time.time()
        try:
            df = self.daily_basic_fetcher.fetch_by_date(trade_date)
            if df is not None and not df.empty:
                records = self._save_to_db(df)
                elapsed = time.time() - start_time
                logger.info(f"  {trade_date}: {records}条, {elapsed:.1f}秒")
                return {'success': 1, 'fail': 0, 'records': records, 'elapsed': elapsed}
            else:
                logger.warning(f"  {trade_date}: 无数据")
                return {'success': 0, 'fail': 1, 'records': 0, 'elapsed': 0}
        except Exception as e:
            logger.error(f"  {trade_date} 失败: {e}")
            return {'success': 0, 'fail': 1, 'records': 0, 'elapsed': 0}

    def update_range(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """
        更新指定日期范围内所有交易日的每日指标。

        Args:
            start_date: 开始日期 YYYYMMDD
            end_date:   结束日期 YYYYMMDD

        Returns:
            汇总统计字典
        """
        logger.info(f"更新每日指标范围: {start_date} ~ {end_date}")
        start_time = time.time()

        trade_dates = self._get_trade_dates(start_date, end_date)
        if not trade_dates:
            logger.warning("未获取到交易日，跳过更新")
            return {'success': 0, 'fail': 0, 'records': 0, 'elapsed': 0}

        logger.info(f"共 {len(trade_dates)} 个交易日")
        total_success = total_fail = total_records = 0

        for i, td in enumerate(trade_dates):
            result = self.update_date(td)
            total_success += result['success']
            total_fail += result['fail']
            total_records += result['records']

            if (i + 1) % 10 == 0:
                logger.info(f"进度: {i + 1}/{len(trade_dates)}, 已入库{total_records}条")

        elapsed = time.time() - start_time
        logger.info(
            f"每日指标更新完成: 成功{total_success}天, 失败{total_fail}天, "
            f"记录{total_records}条, 耗时{elapsed:.1f}秒"
        )
        return {
            'success': total_success,
            'fail': total_fail,
            'records': total_records,
            'elapsed': elapsed,
        }

    def update_incremental(self) -> Dict[str, Any]:
        """
        增量更新：从 dwd_daily_basic 最新日期的下一交易日更新至今。

        若表为空，则从 20200101 开始全量补录。

        Returns:
            汇总统计字典
        """
        latest = self.get_latest_date()
        if latest is None:
            start_date = '20200101'
            logger.warning("dwd_daily_basic 为空，从 20200101 开始全量补录")
        else:
            next_date = self.get_next_trade_date(latest)
            if next_date is None:
                logger.info("每日指标已是最新，无需更新")
                return {'success': 0, 'fail': 0, 'records': 0, 'elapsed': 0}
            start_date = next_date

        end_date = datetime.now().strftime('%Y%m%d')
        logger.info(f"增量更新范围: {start_date} ~ {end_date}")
        return self.update_range(start_date, end_date)


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def run_cli():
    parser = argparse.ArgumentParser(description='每日基本指标数据更新器 (dwd_daily_basic)')

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--incremental', action='store_true',
                       help='增量更新（基于最新日期自动推进）')
    group.add_argument('--full', action='store_true',
                       help='全量更新（从 --start-date 或默认 20200101 开始）')
    group.add_argument('--date', type=str,
                       help='更新指定单日 YYYYMMDD')

    parser.add_argument('--start-date', type=str, help='开始日期 YYYYMMDD')
    parser.add_argument('--end-date', type=str, help='结束日期 YYYYMMDD')
    parser.add_argument('--db', type=str, default=DB_PATH, help='数据库路径')

    args = parser.parse_args()

    fetcher = DailyBasicFetcher(db_path=args.db)
    end_date = args.end_date or datetime.now().strftime('%Y%m%d')

    if args.incremental:
        result = fetcher.update_incremental()

    elif args.date:
        result = fetcher.update_date(args.date)

    elif args.full:
        start_date = args.start_date or '20200101'
        result = fetcher.update_range(start_date, end_date)

    elif args.start_date:
        # 提供了 --start-date 但未指定模式，默认按范围更新
        result = fetcher.update_range(args.start_date, end_date)

    else:
        parser.print_help()
        return

    print(f"\n每日指标更新完成:")
    print(f"  成功: {result['success']}")
    print(f"  失败: {result['fail']}")
    print(f"  记录数: {result['records']}")
    print(f"  耗时: {result['elapsed']:.1f}秒")


if __name__ == '__main__':
    run_cli()
