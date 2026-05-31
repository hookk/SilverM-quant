"""
Tushare统一数据更新器 - DWD层数据仓库

集成所有Tushare适配器，提供一站式数据更新功能：
- 日线数据 (dwd_daily_price)
- 每日指标 (dwd_daily_basic)
- 复权因子 (dwd_adj_factor)
- 利润表 (dwd_income)
- 资产负债表 (dwd_balancesheet)
- 现金流量表 (dwd_cashflow)
- 指数日线 (dwd_index_daily)
- 股票信息 (dwd_stock_info)
- 交易日历 (dwd_trade_calendar)

支持模式:
- --full: 全量更新，从start_date开始
- --incremental: 增量更新，基于dwd_trade_calendar自动判断
- --date: 更新指定日期数据

API限制: Tushare标准版50次/分钟
"""
import sys
import os
from multiprocessing import Pool, cpu_count
from datetime import datetime, timedelta
from typing import Optional, List, Dict, Any
import argparse
import time
import logging

import pandas as pd
import duckdb
from tqdm import tqdm

# 添加项目路径
project_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, project_root)

from data.fetchers.tushare_adapter import (
    TushareBaseFetcher,
    TushareDailyPriceFetcher,
    TushareDailyBasicFetcher,
    TushareAdjFactorFetcher,
    TushareIncomeFetcher,
    TushareBalanceSheetFetcher,
    TushareCashFlowFetcher,
    TushareIndexFetcher,
    TushareTradeCalFetcher,
)
from data.fetchers.baostock_adapter import (
    BaostockDailyPriceFetcher,
    BaostockStockInfoFetcher,
)
from data.fetchers.baostock_adapter.code_converter import to_tushare
from data.fetchers.rate_limiter import tushare_limiter
from database.schema import (
    CREATE_DWD_DAILY_PRICE_TABLE,
    CREATE_DWD_DAILY_BASIC_TABLE,
    CREATE_DWD_ADJ_FACTOR_TABLE,
    CREATE_DWD_INCOME_TABLE,
    CREATE_DWD_BALANCESHEET_TABLE,
    CREATE_DWD_CASHFLOW_TABLE,
    CREATE_DWD_INDEX_DAILY_TABLE,
    CREATE_DWD_STOCK_INFO_TABLE,
    CREATE_DWD_TRADE_CALENDAR_TABLE,
)

DB_PATH = os.path.join(project_root, 'data', 'Astock3.duckdb')

from scripts.log_utils import setup_logger
logger = setup_logger('fetcher_dwd', 'pipeline')


# ---------------------------------------------------------------------------
# 模块级 worker 函数
# 必须定义在模块顶层，multiprocessing.Pool 才能正确 pickle/序列化。
# 不能定义为类方法或闭包，否则在 Pool.imap 时会抛出 PicklingError。
# ---------------------------------------------------------------------------

def _fetch_date_tushare(trade_date: str) -> Dict[str, Any]:
    """
    模块级 worker：使用 Tushare 获取单日全市场日线数据。

    每个 worker 进程独立初始化 fetcher，避免共享连接/状态带来的竞态问题。
    """
    try:
        fetcher = TushareDailyPriceFetcher()
        df = fetcher.fetch_by_date(trade_date)
        if df is not None and not df.empty:
            return {'success': 1, 'fail': 0, 'records': len(df), 'df': df, 'date': trade_date}
        return {'success': 0, 'fail': 1, 'records': 0, 'df': None, 'date': trade_date}
    except Exception as e:
        # worker 内部不能使用主进程 logger（可能跨进程），用 print 输出到 stderr
        print(f"[ERROR] _fetch_date_tushare {trade_date}: {e}", file=sys.stderr)
        return {'success': 0, 'fail': 1, 'records': 0, 'df': None, 'date': trade_date}


def _fetch_stock_baostock(args: tuple) -> Dict[str, Any]:
    """
    模块级 worker：使用 Baostock 获取单只股票指定日期范围的日线数据。

    Args:
        args: (code, start_date, end_date) 元组，Pool.imap 只支持单参数，
              所以将多个参数打包为元组传入。
    """
    code, start_date, end_date = args
    try:
        fetcher = BaostockDailyPriceFetcher()
        df = fetcher.fetch_by_code(code, start_date, end_date)
        if df is not None and not df.empty:
            return {'success': 1, 'fail': 0, 'records': len(df), 'df': df, 'code': code}
        return {'success': 0, 'fail': 1, 'records': 0, 'df': None, 'code': code}
    except Exception as e:
        print(f"[ERROR] _fetch_stock_baostock {code}: {e}", file=sys.stderr)
        return {'success': 0, 'fail': 1, 'records': 0, 'df': None, 'code': code}


def _process_stock_financial(code: str) -> Dict[str, Any]:
    """
    模块级 worker：拉取单只股票的三张财务报表数据并返回 DataFrame。

    [FIX] 原实现只返回记录数统计，从未将数据写入数据库，导致表始终为空。
    现改为返回实际的 DataFrame，由主进程统一批量写库，同时规避 DuckDB
    不支持多进程并发写同一文件的限制。

    每个进程独立初始化三个 fetcher，fetch_by_stock 内部已含限流逻辑。
    """
    ts_code = to_tushare(code)
    try:
        income_fetcher = TushareIncomeFetcher()
        balancesheet_fetcher = TushareBalanceSheetFetcher()
        cashflow_fetcher = TushareCashFlowFetcher()

        income_df = income_fetcher.fetch_by_stock(ts_code)
        bs_df = balancesheet_fetcher.fetch_by_stock(ts_code)
        cf_df = cashflow_fetcher.fetch_by_stock(ts_code)

        return {
            'code': code,
            'income_df': income_df if income_df is not None and not income_df.empty else None,
            'balancesheet_df': bs_df if bs_df is not None and not bs_df.empty else None,
            'cashflow_df': cf_df if cf_df is not None and not cf_df.empty else None,
            'income_records': len(income_df) if income_df is not None and not income_df.empty else 0,
            'balancesheet_records': len(bs_df) if bs_df is not None and not bs_df.empty else 0,
            'cashflow_records': len(cf_df) if cf_df is not None and not cf_df.empty else 0,
        }
    except Exception as e:
        print(f"[ERROR] _process_stock_financial {code} ({ts_code}): {e}", file=sys.stderr)
        return {
            'code': code,
            'income_df': None,
            'balancesheet_df': None,
            'cashflow_df': None,
            'income_records': 0,
            'balancesheet_records': 0,
            'cashflow_records': 0,
        }


# ---------------------------------------------------------------------------
# 主类
# ---------------------------------------------------------------------------

class DWDFetcher:
    """
    Tushare/Baostock 统一数据更新器

    集成所有适配器，提供 DWD 层数据仓库的一站式更新能力。

    使用示例:
        fetcher = DWDFetcher()

        # 更新单日日线数据
        fetcher.update_daily('20260403', '20260403')

        # 增量更新
        fetcher.update_incremental()

        # 全量更新日线数据
        fetcher.update_daily('20260101', '20260406')
    """

    # 默认指数列表
    DEFAULT_INDICES = [
        '000001.SH',  # 上证指数
        '399001.SZ',  # 深证成指
        '399006.SZ',  # 创业板指
        '000300.SH',  # 沪深300
        '000016.SH',  # 上证50
        '000905.SH',  # 中证500
        '000852.SH',  # 中证1000
    ]

    def __init__(self, db_path: str = DB_PATH, source: str = 'tushare'):
        """
        初始化 DWDFetcher

        Args:
            db_path: DuckDB 数据库路径
            source: 数据源 ('tushare' 或 'baostock')
        """
        self.db_path = db_path
        self.source = source
        self._ensure_tables()

        # 根据 source 初始化对应的日线 fetcher
        if source == 'tushare':
            self.daily_fetcher = TushareDailyPriceFetcher()
        elif source == 'baostock':
            self.daily_fetcher = BaostockDailyPriceFetcher()
        else:
            raise ValueError(f"不支持的数据源: {source}，请使用 'tushare' 或 'baostock'")

        # 其余适配器（财务数据等）固定使用 Tushare
        self.daily_basic_fetcher = TushareDailyBasicFetcher()
        self.adj_factor_fetcher = TushareAdjFactorFetcher()
        self.income_fetcher = TushareIncomeFetcher()
        self.balancesheet_fetcher = TushareBalanceSheetFetcher()
        self.cashflow_fetcher = TushareCashFlowFetcher()
        self.index_fetcher = TushareIndexFetcher()
        self.trade_cal_fetcher = TushareTradeCalFetcher()

        logger.info(f"DWDFetcher 初始化完成, source={source}, db={db_path}")

    # ------------------------------------------------------------------
    # 内部工具方法
    # ------------------------------------------------------------------

    def _ensure_tables(self) -> None:
        """确保所有 DWD 表存在（幂等）"""
        db = duckdb.connect(self.db_path)
        try:
            db.execute(CREATE_DWD_DAILY_PRICE_TABLE)
            db.execute(CREATE_DWD_DAILY_BASIC_TABLE)
            db.execute(CREATE_DWD_ADJ_FACTOR_TABLE)
            db.execute(CREATE_DWD_INCOME_TABLE)
            db.execute(CREATE_DWD_BALANCESHEET_TABLE)
            db.execute(CREATE_DWD_CASHFLOW_TABLE)
            db.execute(CREATE_DWD_INDEX_DAILY_TABLE)
            db.execute(CREATE_DWD_STOCK_INFO_TABLE)
            db.execute(CREATE_DWD_TRADE_CALENDAR_TABLE)
            logger.info("DWD 表检查完成")
        finally:
            db.close()

    def _get_stock_list_from_db(self) -> List[str]:
        """从 dwd_stock_info 读取在市股票列表（list_status='L'）"""
        db = duckdb.connect(self.db_path)
        try:
            result = db.execute(
                "SELECT symbol FROM dwd_stock_info WHERE list_status = 'L' ORDER BY symbol"
            ).fetchall()
            stocks = [row[0] for row in result]
            if not stocks:
                logger.warning("dwd_stock_info 中未找到在市股票，请先执行 update_stock_info")
            return stocks
        finally:
            db.close()

    def _get_latest_date(self, table: str, date_col: str = 'trade_date') -> Optional[datetime]:
        """获取表中最新日期（返回 date 对象）"""
        db = duckdb.connect(self.db_path)
        try:
            result = db.execute(f"SELECT MAX({date_col}) FROM {table}").fetchone()
            if result and result[0]:
                return pd.to_datetime(result[0]).date()
            return None
        except Exception as e:
            logger.warning(f"查询 {table}.{date_col} 最新日期失败: {e}")
            return None
        finally:
            db.close()

    def get_latest_trade_date(self, table_name: str) -> Optional[str]:
        """
        获取指定 DWD 表的最新交易日期。

        Args:
            table_name: DWD 表名

        Returns:
            最新交易日期字符串 YYYYMMDD，无数据时返回 None
        """
        db = duckdb.connect(self.db_path)
        try:
            date_col = 'list_date' if table_name == 'dwd_stock_info' else 'trade_date'
            result = db.execute(f"SELECT MAX({date_col}) FROM {table_name}").fetchone()
            if result and result[0]:
                date_val = pd.to_datetime(result[0]).strftime('%Y%m%d')
                logger.info(f"{table_name} 最新日期: {date_val}")
                return date_val
            return None
        except Exception as e:
            logger.warning(f"查询 {table_name} 最新日期失败: {e}")
            return None
        finally:
            db.close()

    def get_next_trade_date(self, from_date: str) -> Optional[str]:
        """
        从 dwd_trade_calendar 获取 from_date 之后的第一个交易日。

        Args:
            from_date: 参考日期 YYYYMMDD

        Returns:
            下一交易日字符串 YYYYMMDD，找不到时返回 None
        """
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
                next_date = pd.to_datetime(result[0]).strftime('%Y%m%d')
                logger.info(f"下一个交易日: {next_date}")
                return next_date
            return None
        except Exception as e:
            logger.warning(f"查询下一个交易日失败: {e}")
            return None
        finally:
            db.close()

    def _get_trade_dates(self, start_date: str, end_date: str) -> List[str]:
        """
        获取指定范围内的交易日列表（YYYYMMDD 格式）。

        策略：优先查 dwd_trade_calendar 库（已有数据则无需调用 API）；
        库里没有数据时才调用 TushareTradeCalFetcher，并将结果写库以备后用。
        """
        # ---- 1. 优先查库 ----
        start_fmt = f"{start_date[:4]}-{start_date[4:6]}-{start_date[6:8]}"
        end_fmt   = f"{end_date[:4]}-{end_date[4:6]}-{end_date[6:8]}"
        try:
            db = duckdb.connect(self.db_path)
            try:
                rows = db.execute(
                    """
                    SELECT trade_date FROM dwd_trade_calendar
                    WHERE is_open = TRUE
                      AND trade_date >= ?
                      AND trade_date <= ?
                      AND exchange = 'SSE'
                    ORDER BY trade_date
                    """,
                    [start_fmt, end_fmt],
                ).fetchall()
            finally:
                db.close()

            if rows:
                dates = [row[0] for row in rows]
                # trade_date 可能是 date 对象或字符串，统一转为 YYYYMMDD
                result = [
                    (d.strftime('%Y%m%d') if hasattr(d, 'strftime') else str(d).replace('-', ''))
                    for d in dates
                ]
                logger.info(f"从 dwd_trade_calendar 获取到 {len(result)} 个交易日")
                return result
        except Exception as e:
            logger.warning(f"查询 dwd_trade_calendar 失败，降级到 API: {e}")

        # ---- 2. 降级：调用 tushare API ----
        logger.warning(f"dwd_trade_calendar 无数据，尝试从 tushare 获取: {start_date} ~ {end_date}")
        df = self.trade_cal_fetcher.fetch(
            start_date=start_date, end_date=end_date, exchange='SSE'
        )
        if df is None or df.empty:
            logger.warning(f"未获取到交易日历: {start_date} ~ {end_date}")
            logger.warning("请先执行: python data/updaters/fetcher_dwd.py --data-type trade_calendar --start-date 20200101")
            return []

        # 将 API 结果顺手写库，下次直接查库
        try:
            cal_df = df.copy()
            if 'is_open' in cal_df.columns:
                cal_df['is_open'] = cal_df['is_open'].astype(str).str.lower().isin(['true', '1'])
            self._save_to_db(cal_df, 'dwd_trade_calendar')
            logger.info(f"已将 {len(cal_df)} 条交易日历写入 dwd_trade_calendar")
        except Exception as e:
            logger.warning(f"写入 dwd_trade_calendar 失败（不影响本次更新）: {e}")

        trade_dates = df[df['is_open'] == 1]['trade_date'].tolist()
        return [d.replace('-', '') for d in trade_dates]

    def _save_to_db(self, df: pd.DataFrame, table: str) -> int:
        """
        将 DataFrame 写入数据库（INSERT OR REPLACE 语义）。

        性能优化：
        - 日期列统一转换为 YYYY-MM-DD 字符串，避免类型不匹配
        - 数值列使用 pd.to_numeric 批量转换，减少逐行开销
        - 使用临时表 + INSERT OR REPLACE，单次事务完成写入

        Args:
            df: 待写入数据
            table: 目标表名

        Returns:
            实际写入的记录数；df 为空时返回 0
        """
        if df is None or df.empty:
            return 0

        df = df.copy()

        # ---- 1. 日期列统一转换 ----
        all_date_cols = [
            'trade_date', 'ann_date', 'f_ann_date', 'end_date',
            'list_date', 'listing_date', 'delist_date',
        ]
        for col in all_date_cols:
            if col in df.columns:
                df[col] = df[col].replace('', pd.NA)
                df[col] = pd.to_datetime(df[col], errors='coerce').dt.strftime('%Y-%m-%d')

        # ---- 2. 非数值列白名单（不做 to_numeric 转换）----
        skip_numeric = {
            'ts_code', 'index_code', 'symbol', 'name', 'area', 'industry',
            'market', 'is_hs', 'act_name', 'exchange', 'report_type',
            'comp_type', 'data_source', 'list_status', 'delist_date',
            # 日期列已处理，跳过
            'trade_date', 'ann_date', 'f_ann_date', 'end_date',
            'list_date', 'listing_date',
        }
        for col in df.columns:
            if col not in skip_numeric and str(df[col].dtype) == 'object':
                df[col] = pd.to_numeric(df[col], errors='coerce')

        # ---- 3. 写入数据库 ----
        db = duckdb.connect(self.db_path)
        try:
            db.execute("CREATE TEMPORARY TABLE temp_data AS SELECT * FROM df")
            cols = ', '.join(df.columns)
            db.execute(
                f"INSERT OR REPLACE INTO {table} ({cols}) SELECT {cols} FROM temp_data"
            )
            db.execute("DROP TABLE temp_data")
            return len(df)
        finally:
            db.close()

    @staticmethod
    def _aggregate_results(results: List[Dict[str, Any]]) -> tuple:
        """
        汇总 worker 返回的结果列表。

        Returns:
            (success_count, fail_count, total_records, all_dfs)
        """
        success_count = fail_count = total_records = 0
        all_dfs: List[pd.DataFrame] = []
        for r in results:
            if r.get('success'):
                success_count += 1
                total_records += r.get('records', 0)
                if r.get('df') is not None:
                    all_dfs.append(r['df'])
            else:
                fail_count += 1
        return success_count, fail_count, total_records, all_dfs

    # ------------------------------------------------------------------
    # 日线数据
    # ------------------------------------------------------------------

    def update_daily(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """
        更新日线数据 (dwd_daily_price)。

        根据初始化时指定的 source 自动选择更新策略：
        - tushare: 按日期批量拉取（fetch_by_date），逐日串行
        - baostock: 按股票逐只拉取（fetch_by_code），自动路由到
          update_daily_by_stock 以获得正确的并行效果

        Args:
            start_date: 开始日期 YYYYMMDD
            end_date:   结束日期 YYYYMMDD

        Returns:
            更新统计信息字典
        """
        # [FIX] BaostockDailyPriceFetcher 没有 fetch_by_date 接口，
        # 必须按股票维度拉取；自动路由，避免调用方感知差异。
        if self.source == 'baostock':
            logger.info(
                f"Baostock 数据源不支持 fetch_by_date，自动切换为按股票模式: "
                f"{start_date} ~ {end_date}"
            )
            return self.update_daily_by_stock(start_date, end_date)

        logger.info(f"开始更新日线数据 (tushare): {start_date} ~ {end_date}")
        start_time = time.time()

        trade_dates = self._get_trade_dates(start_date, end_date)
        if not trade_dates:
            return {'success': 0, 'fail': 0, 'records': 0, 'elapsed': 0}

        success_count = fail_count = total_records = 0

        for i, trade_date in enumerate(trade_dates):
            try:
                df = self.daily_fetcher.fetch_by_date(trade_date)
                if df is not None and not df.empty:
                    total_records += self._save_to_db(df, 'dwd_daily_price')
                    success_count += 1
                else:
                    logger.warning(f"日线无数据: {trade_date}")
                    fail_count += 1
            except Exception as e:
                logger.error(f"更新日线失败 {trade_date}: {e}")
                fail_count += 1

            if (i + 1) % 10 == 0:
                logger.info(f"日线更新进度: {i + 1}/{len(trade_dates)}")

        elapsed = time.time() - start_time
        logger.info(
            f"日线数据更新完成: 成功{success_count}天, 失败{fail_count}天, "
            f"记录{total_records}条, 耗时{elapsed:.1f}秒"
        )
        return {
            'success': success_count,
            'fail': fail_count,
            'records': total_records,
            'elapsed': elapsed,
        }

    def update_daily_parallel(
        self, start_date: str, end_date: str, num_workers: int = 4
    ) -> Dict[str, Any]:
        """
        并行更新日线数据（仅支持 Tushare，按日期维度并行）。

        [FIX] 原实现在类内部定义 fetch_single_date 闭包作为 worker，
        闭包无法被 pickle，Pool.imap 会抛出 PicklingError。
        现改为调用模块级函数 _fetch_date_tushare。

        [FIX] 若 source 为 baostock，自动路由到 update_daily_by_stock。

        Args:
            start_date:   开始日期 YYYYMMDD
            end_date:     结束日期 YYYYMMDD
            num_workers:  并行进程数（Tushare 限流，上限钳位为 4）

        Returns:
            更新统计信息字典
        """
        if self.source == 'baostock':
            logger.info("Baostock 数据源，自动切换为按股票并行模式")
            return self.update_daily_by_stock(start_date, end_date, num_workers)

        logger.info(
            f"开始并行更新日线数据 (tushare): {start_date} ~ {end_date}, "
            f"进程数: {num_workers}"
        )
        start_time = time.time()

        trade_dates = self._get_trade_dates(start_date, end_date)
        if not trade_dates:
            return {'success': 0, 'fail': 0, 'records': 0, 'elapsed': 0}

        # Tushare 标准版限流 50次/分钟，并行进程数不超过 4
        effective_workers = min(num_workers, 4)
        logger.info(f"有效并行进程数: {effective_workers}，共 {len(trade_dates)} 个交易日")

        with Pool(processes=effective_workers) as pool:
            results = list(
                tqdm(
                    pool.imap(_fetch_date_tushare, trade_dates),
                    total=len(trade_dates),
                    desc="并行更新日线",
                    unit="天",
                )
            )

        success_count, fail_count, total_records, all_dfs = self._aggregate_results(results)

        if all_dfs:
            logger.info(f"批量写入 {len(all_dfs)} 个 DataFrame 到数据库...")
            combined_df = pd.concat(all_dfs, ignore_index=True)
            self._save_to_db(combined_df, 'dwd_daily_price')

        elapsed = time.time() - start_time
        logger.info(
            f"并行日线数据更新完成: 成功{success_count}天, 失败{fail_count}天, "
            f"记录{total_records}条, 耗时{elapsed:.1f}秒"
        )
        return {
            'success': success_count,
            'fail': fail_count,
            'records': total_records,
            'elapsed': elapsed,
        }

    def update_daily_by_stock(
        self, start_date: str, end_date: str, num_workers: int = 4
    ) -> Dict[str, Any]:
        """
        按股票维度并行更新日线数据（Baostock 专用，Tushare 亦可使用）。

        Baostock 需要 query_history_k_data_plus(code, ...) 按股票拉取，
        此方法是 Baostock 日线数据的唯一正确入口。

        [FIX] 原实现同样存在闭包无法 pickle 的问题，改为模块级函数
        _fetch_stock_baostock，通过元组传递多个参数。

        Args:
            start_date:  开始日期 YYYYMMDD
            end_date:    结束日期 YYYYMMDD
            num_workers: 并行进程数

        Returns:
            更新统计信息字典
        """
        logger.info(
            f"开始按股票并行更新日线数据 ({self.source}): "
            f"{start_date} ~ {end_date}, 进程数: {num_workers}"
        )
        start_time = time.time()

        stock_list = self._get_stock_list_from_db()
        if not stock_list:
            return {'success': 0, 'fail': 0, 'records': 0, 'elapsed': 0}

        total_stocks = len(stock_list)
        effective_workers = min(num_workers, max(cpu_count() - 1, 1))
        logger.info(f"有效并行进程数: {effective_workers}，共 {total_stocks} 只股票")

        # 将多个参数打包为元组，供模块级 worker 解包
        args_list = [(code, start_date, end_date) for code in stock_list]

        with Pool(processes=effective_workers) as pool:
            results = list(
                tqdm(
                    pool.imap(_fetch_stock_baostock, args_list),
                    total=total_stocks,
                    desc="按股票更新日线",
                    unit="股",
                )
            )

        success_count, fail_count, total_records, all_dfs = self._aggregate_results(results)

        if all_dfs:
            logger.info(f"批量写入 {len(all_dfs)} 个 DataFrame 到数据库...")
            combined_df = pd.concat(all_dfs, ignore_index=True)
            self._save_to_db(combined_df, 'dwd_daily_price')

        elapsed = time.time() - start_time
        logger.info(
            f"按股票更新日线完成: 成功{success_count}只, 失败{fail_count}只, "
            f"记录{total_records}条, 耗时{elapsed:.1f}秒"
        )
        return {
            'success': success_count,
            'fail': fail_count,
            'records': total_records,
            'elapsed': elapsed,
        }

    # ------------------------------------------------------------------
    # 每日指标
    # ------------------------------------------------------------------

    def update_daily_basic(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """
        更新每日指标数据 (dwd_daily_basic)。

        Args:
            start_date: 开始日期 YYYYMMDD
            end_date:   结束日期 YYYYMMDD

        Returns:
            更新统计信息字典
        """
        logger.info(f"开始更新每日指标: {start_date} ~ {end_date}")
        start_time = time.time()

        trade_dates = self._get_trade_dates(start_date, end_date)
        if not trade_dates:
            return {'success': 0, 'fail': 0, 'records': 0, 'elapsed': 0}

        success_count = fail_count = total_records = 0

        for i, trade_date in enumerate(trade_dates):
            try:
                df = self.daily_basic_fetcher.fetch_by_date(trade_date)
                if df is not None and not df.empty:
                    total_records += self._save_to_db(df, 'dwd_daily_basic')
                    success_count += 1
                else:
                    logger.warning(f"每日指标无数据: {trade_date}")
                    fail_count += 1
            except Exception as e:
                logger.error(f"更新每日指标失败 {trade_date}: {e}")
                fail_count += 1

            if (i + 1) % 10 == 0:
                logger.info(f"每日指标更新进度: {i + 1}/{len(trade_dates)}")

        elapsed = time.time() - start_time
        logger.info(
            f"每日指标更新完成: 成功{success_count}天, 失败{fail_count}天, "
            f"记录{total_records}条, 耗时{elapsed:.1f}秒"
        )
        return {
            'success': success_count,
            'fail': fail_count,
            'records': total_records,
            'elapsed': elapsed,
        }

    # ------------------------------------------------------------------
    # 复权因子
    # ------------------------------------------------------------------

    def update_adj_factor(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """
        更新复权因子 (dwd_adj_factor)。

        Args:
            start_date: 开始日期 YYYYMMDD
            end_date:   结束日期 YYYYMMDD

        Returns:
            更新统计信息字典
        """
        logger.info(f"开始更新复权因子: {start_date} ~ {end_date}")
        start_time = time.time()

        stock_list = self._get_stock_list_from_db()
        if not stock_list:
            return {'success': 0, 'fail': 0, 'records': 0, 'elapsed': 0}

        success_count = fail_count = total_records = 0

        for i, code in enumerate(stock_list):
            try:
                ts_code = to_tushare(code)
                df = self.adj_factor_fetcher.fetch(ts_code, start_date, end_date)
                if df is not None and not df.empty:
                    total_records += self._save_to_db(df, 'dwd_adj_factor')
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                logger.error(f"更新复权因子失败 {code}: {e}")
                fail_count += 1

            if (i + 1) % 100 == 0:
                logger.info(f"复权因子更新进度: {i + 1}/{len(stock_list)}")

        elapsed = time.time() - start_time
        logger.info(
            f"复权因子更新完成: 成功{success_count}只, 失败{fail_count}只, "
            f"记录{total_records}条, 耗时{elapsed:.1f}秒"
        )
        return {
            'success': success_count,
            'fail': fail_count,
            'records': total_records,
            'elapsed': elapsed,
        }

    # ------------------------------------------------------------------
    # 财务报表（单只股票接口）
    # ------------------------------------------------------------------

    def update_income(self, ts_code: str) -> Dict[str, Any]:
        """更新单只股票利润表 (dwd_income)"""
        logger.info(f"开始更新利润表: {ts_code}")
        start_time = time.time()
        try:
            df = self.income_fetcher.fetch_by_stock(ts_code)
            if df is not None and not df.empty:
                records = self._save_to_db(df, 'dwd_income')
                elapsed = time.time() - start_time
                logger.info(f"利润表更新完成: {ts_code}, {records}条, {elapsed:.1f}秒")
                return {'success': 1, 'fail': 0, 'records': records, 'elapsed': elapsed}
            logger.warning(f"利润表无数据: {ts_code}")
            return {'success': 0, 'fail': 1, 'records': 0, 'elapsed': 0}
        except Exception as e:
            logger.error(f"更新利润表失败 {ts_code}: {e}")
            return {'success': 0, 'fail': 1, 'records': 0, 'elapsed': 0}

    def update_balancesheet(self, ts_code: str) -> Dict[str, Any]:
        """更新单只股票资产负债表 (dwd_balancesheet)"""
        logger.info(f"开始更新资产负债表: {ts_code}")
        start_time = time.time()
        try:
            df = self.balancesheet_fetcher.fetch_by_stock(ts_code)
            if df is not None and not df.empty:
                records = self._save_to_db(df, 'dwd_balancesheet')
                elapsed = time.time() - start_time
                logger.info(f"资产负债表更新完成: {ts_code}, {records}条, {elapsed:.1f}秒")
                return {'success': 1, 'fail': 0, 'records': records, 'elapsed': elapsed}
            logger.warning(f"资产负债表无数据: {ts_code}")
            return {'success': 0, 'fail': 1, 'records': 0, 'elapsed': 0}
        except Exception as e:
            logger.error(f"更新资产负债表失败 {ts_code}: {e}")
            return {'success': 0, 'fail': 1, 'records': 0, 'elapsed': 0}

    def update_cashflow(self, ts_code: str) -> Dict[str, Any]:
        """更新单只股票现金流量表 (dwd_cashflow)"""
        logger.info(f"开始更新现金流量表: {ts_code}")
        start_time = time.time()
        try:
            df = self.cashflow_fetcher.fetch_by_stock(ts_code)
            if df is not None and not df.empty:
                records = self._save_to_db(df, 'dwd_cashflow')
                elapsed = time.time() - start_time
                logger.info(f"现金流量表更新完成: {ts_code}, {records}条, {elapsed:.1f}秒")
                return {'success': 1, 'fail': 0, 'records': records, 'elapsed': elapsed}
            logger.warning(f"现金流量表无数据: {ts_code}")
            return {'success': 0, 'fail': 1, 'records': 0, 'elapsed': 0}
        except Exception as e:
            logger.error(f"更新现金流量表失败 {ts_code}: {e}")
            return {'success': 0, 'fail': 1, 'records': 0, 'elapsed': 0}

    # ------------------------------------------------------------------
    # 指数日线
    # ------------------------------------------------------------------

    def update_index(
        self, index_code: str, start_date: str, end_date: str
    ) -> Dict[str, Any]:
        """
        更新单个指数的日线数据 (dwd_index_daily)。

        Args:
            index_code: 指数代码，如 '000001.SH'
            start_date: 开始日期 YYYYMMDD
            end_date:   结束日期 YYYYMMDD

        Returns:
            更新统计信息字典
        """
        logger.info(f"开始更新指数日线: {index_code} {start_date} ~ {end_date}")
        start_time = time.time()
        try:
            df = self.index_fetcher.fetch(index_code, start_date, end_date)
            if df is not None and not df.empty:
                records = self._save_to_db(df, 'dwd_index_daily')
                elapsed = time.time() - start_time
                logger.info(f"指数日线更新完成: {index_code}, {records}条, {elapsed:.1f}秒")
                return {'success': 1, 'fail': 0, 'records': records, 'elapsed': elapsed}
            logger.warning(f"指数日线无数据: {index_code}")
            return {'success': 0, 'fail': 1, 'records': 0, 'elapsed': 0}
        except Exception as e:
            logger.error(f"更新指数日线失败 {index_code}: {e}")
            return {'success': 0, 'fail': 1, 'records': 0, 'elapsed': 0}

    def update_all_indices(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """
        更新所有默认指数的日线数据（便捷方法）。

        Returns:
            汇总统计：{'total_records': int, 'success': int, 'fail': int}
        """
        total_records = success = fail = 0
        for idx in self.DEFAULT_INDICES:
            result = self.update_index(idx, start_date, end_date)
            total_records += result['records']
            success += result['success']
            fail += result['fail']
        logger.info(f"全部指数更新完成: 共{total_records}条, 成功{success}个, 失败{fail}个")
        return {'total_records': total_records, 'success': success, 'fail': fail}

    # ------------------------------------------------------------------
    # 股票信息
    # ------------------------------------------------------------------

    def update_stock_info(self, source: Optional[str] = None) -> Dict[str, Any]:
        """
        更新股票基础信息 (dwd_stock_info)。

        Args:
            source: 数据源 ('tushare' 或 'baostock')，默认使用 self.source

        Returns:
            更新统计信息字典
        """
        effective_source = source or self.source
        logger.info(f"开始更新股票信息, 数据源: {effective_source}")
        if effective_source == 'baostock':
            return self._update_stock_info_baostock()
        return self._update_stock_info_tushare()

    def _update_stock_info_baostock(self) -> Dict[str, Any]:
        """使用 Baostock 更新股票信息"""
        start_time = time.time()
        try:
            fetcher = BaostockStockInfoFetcher()
            df = fetcher.fetch_all()
            if df is None or df.empty:
                logger.warning("Baostock 股票信息无数据")
                return {'success': 0, 'fail': 1, 'records': 0, 'elapsed': 0}

            # listing_date -> list_date（兼容 dwd_stock_info 表结构）
            df = df.rename(columns={'listing_date': 'list_date'})
            df['data_source'] = 'baostock'

            records = self._save_to_db(df, 'dwd_stock_info')
            elapsed = time.time() - start_time
            logger.info(f"股票信息更新完成 (baostock): {records}条, {elapsed:.1f}秒")
            return {'success': 1, 'fail': 0, 'records': records, 'elapsed': elapsed}
        except Exception as e:
            logger.error(f"更新股票信息失败 (baostock): {e}")
            return {'success': 0, 'fail': 1, 'records': 0, 'elapsed': 0}

    def _update_stock_info_tushare(self) -> Dict[str, Any]:
        """使用 Tushare 更新股票信息"""
        start_time = time.time()
        try:
            from data.fetchers.tushare_adapter.base import TushareBaseFetcher as _Base
            base = _Base()
            if not base._ensure_api():
                logger.error("Tushare API 未初始化")
                return {'success': 0, 'fail': 1, 'records': 0, 'elapsed': 0}

            tushare_limiter.wait_if_needed()
            df = base.api.stock_basic(
                exchange='',
                list_status='L',
                fields='ts_code,symbol,name,area,industry,market,list_date,is_hs,act_name,list_status',
            )
            if df is not None and not df.empty:
                df['data_source'] = 'tushare'
                records = self._save_to_db(df, 'dwd_stock_info')
                elapsed = time.time() - start_time
                logger.info(f"股票信息更新完成 (tushare): {records}条, {elapsed:.1f}秒")
                return {'success': 1, 'fail': 0, 'records': records, 'elapsed': elapsed}
            logger.warning("股票信息无数据 (tushare)")
            return {'success': 0, 'fail': 1, 'records': 0, 'elapsed': 0}
        except Exception as e:
            logger.error(f"更新股票信息失败 (tushare): {e}")
            return {'success': 0, 'fail': 1, 'records': 0, 'elapsed': 0}

    # ------------------------------------------------------------------
    # 交易日历
    # ------------------------------------------------------------------

    def update_trade_calendar(self, start_date: str, end_date: str) -> Dict[str, Any]:
        """
        更新交易日历 (dwd_trade_calendar)。

        Args:
            start_date: 开始日期 YYYYMMDD
            end_date:   结束日期 YYYYMMDD

        Returns:
            更新统计信息字典
        """
        logger.info(f"开始更新交易日历: {start_date} ~ {end_date}")
        start_time = time.time()
        try:
            df = self.trade_cal_fetcher.fetch(start_date, end_date)
            if df is not None and not df.empty:
                if 'is_open' in df.columns:
                    df['is_open'] = df['is_open'].astype(str).str.lower().isin(['true', '1'])
                records = self._save_to_db(df, 'dwd_trade_calendar')
                elapsed = time.time() - start_time
                logger.info(f"交易日历更新完成: {records}条, {elapsed:.1f}秒")
                return {'success': 1, 'fail': 0, 'records': records, 'elapsed': elapsed}
            logger.warning("交易日历无数据")
            return {'success': 0, 'fail': 1, 'records': 0, 'elapsed': 0}
        except Exception as e:
            logger.error(f"更新交易日历失败: {e}")
            return {'success': 0, 'fail': 1, 'records': 0, 'elapsed': 0}

    # ------------------------------------------------------------------
    # 增量更新
    # ------------------------------------------------------------------

    def update_incremental(self, data_type: str = 'daily') -> Dict[str, Any]:
        """
        增量更新：基于目标表最新日期 + dwd_trade_calendar 自动判断需要更新的范围。

        [FIX] 原实现在 index 增量更新循环中错误地调用
        fetcher.update_incremental('index') 而非 fetcher.update_index(idx, ...)，
        导致每次循环都只更新 DEFAULT_INDICES[0] 一个指数。
        现已修正：先确定增量日期范围，再对每个指数分别调用 update_index。

        Args:
            data_type: 'daily' | 'daily_basic' | 'index'

        Returns:
            更新统计信息字典
        """
        table_mapping = {
            'daily': 'dwd_daily_price',
            'daily_basic': 'dwd_daily_basic',
            'index': 'dwd_index_daily',
        }
        if data_type not in table_mapping:
            logger.error(f"不支持的增量更新类型: {data_type}，支持: {list(table_mapping)}")
            return {'success': 0, 'fail': 0, 'records': 0, 'elapsed': 0}

        table_name = table_mapping[data_type]
        latest_date = self.get_latest_trade_date(table_name)

        if latest_date is None:
            logger.warning(f"{table_name} 为空，使用默认起始日期 20200101")
            start_date = '20200101'
        else:
            next_date = self.get_next_trade_date(latest_date)
            if next_date is None:
                logger.info(f"{table_name} 已是最新，无需更新")
                return {'success': 0, 'fail': 0, 'records': 0, 'elapsed': 0}
            start_date = next_date

        end_date = datetime.now().strftime('%Y%m%d')
        logger.info(f"增量更新范围: {start_date} ~ {end_date}  ({data_type})")

        if data_type == 'daily':
            return self.update_daily(start_date, end_date)
        elif data_type == 'daily_basic':
            return self.update_daily_basic(start_date, end_date)
        elif data_type == 'index':
            # [FIX] 对所有默认指数依次增量更新，汇总结果
            total_records = total_success = total_fail = 0
            for idx in self.DEFAULT_INDICES:
                r = self.update_index(idx, start_date, end_date)
                total_records += r['records']
                total_success += r['success']
                total_fail += r['fail']
            return {
                'success': total_success,
                'fail': total_fail,
                'records': total_records,
                'elapsed': 0,  # 各子调用已单独记录耗时
            }
        # 理论上不会走到这里（已在前面 return），保留以满足类型检查
        return {'success': 0, 'fail': 0, 'records': 0, 'elapsed': 0}

    # ------------------------------------------------------------------
    # 财务数据多进程
    # ------------------------------------------------------------------

    def update_financial_multiprocess(self, num_workers: int = 4,
                                       batch_size: int = 200) -> Dict[str, Any]:
        """
        多进程批量更新三张财务报表 (income, balancesheet, cashflow)。

        [FIX] 原实现中 worker 只返回记录数统计，从未将 DataFrame 写入数据库，
        导致日志显示有数据但三张财务表始终为空。
        修复策略：
        1. worker (_process_stock_financial) 改为返回实际 DataFrame
        2. 主进程收集一批结果后合并写库（batch_size 控制内存压力）
        3. 主进程统一写库，规避 DuckDB 不支持多进程并发写同一文件的问题

        Args:
            num_workers: 并行进程数
            batch_size:  每积累多少只股票的数据就批量写库一次，默认200

        Returns:
            更新统计信息字典
        """
        logger.info(f"开始多进程更新财务数据, 进程数: {num_workers}")
        start_time = time.time()

        stock_list = self._get_stock_list_from_db()
        if not stock_list:
            return {
                'income_records': 0,
                'balancesheet_records': 0,
                'cashflow_records': 0,
                'elapsed': 0,
            }

        effective_workers = min(num_workers, max(cpu_count() - 1, 1))
        logger.info(f"有效并行进程数: {effective_workers}，共 {len(stock_list)} 只股票")

        total_income = total_bs = total_cf = 0
        income_buf: List[pd.DataFrame] = []
        bs_buf: List[pd.DataFrame] = []
        cf_buf: List[pd.DataFrame] = []

        def _flush_buffers():
            """将缓冲区中的 DataFrame 合并后写库，清空缓冲区"""
            nonlocal income_buf, bs_buf, cf_buf
            if income_buf:
                self._save_to_db(pd.concat(income_buf, ignore_index=True), 'dwd_income')
                income_buf = []
            if bs_buf:
                self._save_to_db(pd.concat(bs_buf, ignore_index=True), 'dwd_balancesheet')
                bs_buf = []
            if cf_buf:
                self._save_to_db(pd.concat(cf_buf, ignore_index=True), 'dwd_cashflow')
                cf_buf = []

        with Pool(processes=effective_workers) as pool:
            for i, result in enumerate(tqdm(
                pool.imap(_process_stock_financial, stock_list),
                total=len(stock_list),
                desc="财务数据更新",
                unit="股",
            )):
                total_income += result['income_records']
                total_bs += result['balancesheet_records']
                total_cf += result['cashflow_records']

                if result['income_df'] is not None:
                    income_buf.append(result['income_df'])
                if result['balancesheet_df'] is not None:
                    bs_buf.append(result['balancesheet_df'])
                if result['cashflow_df'] is not None:
                    cf_buf.append(result['cashflow_df'])

                # 每 batch_size 只股票批量写库一次，控制内存
                if (i + 1) % batch_size == 0:
                    logger.info(f"批量写库: 已处理 {i + 1}/{len(stock_list)} 只股票")
                    _flush_buffers()

        # 写入剩余数据
        _flush_buffers()

        elapsed = time.time() - start_time
        logger.info(
            f"财务数据更新完成: 利润表{total_income}条, "
            f"资产负债表{total_bs}条, 现金流量表{total_cf}条, "
            f"耗时{elapsed:.1f}秒"
        )
        return {
            'income_records': total_income,
            'balancesheet_records': total_bs,
            'cashflow_records': total_cf,
            'elapsed': elapsed,
        }


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def run_cli():
    """CLI 入口"""
    parser = argparse.ArgumentParser(description='DWD 层数据更新器')

    # 更新模式
    parser.add_argument('--full', action='store_true', help='全量更新模式')
    parser.add_argument('--incremental', action='store_true', help='增量更新模式')
    parser.add_argument('--date', type=str, help='更新指定日期 YYYYMMDD')

    # 数据类型
    parser.add_argument(
        '--data-type',
        type=str,
        choices=[
            'daily', 'daily_basic', 'adj_factor', 'income',
            'balancesheet', 'cashflow', 'index', 'stock_info',
            'trade_calendar', 'financial', 'all',
        ],
        default='daily',
        help='数据类型',
    )

    # 日期参数
    parser.add_argument('--start-date', type=str, help='开始日期 YYYYMMDD')
    parser.add_argument('--end-date', type=str, help='结束日期 YYYYMMDD')

    # 指数/股票代码
    parser.add_argument('--index-code', type=str, help='指数代码，如 000001.SH')
    parser.add_argument('--ts-code', type=str, help='股票代码，如 600000.SH')

    # 其他参数
    parser.add_argument('--db', type=str, default=DB_PATH, help='数据库路径')
    parser.add_argument(
        '--workers', type=int, default=max(cpu_count() - 1, 1), help='并行进程数'
    )
    parser.add_argument('--parallel', action='store_true', help='日线数据使用并行模式')
    parser.add_argument(
        '--source',
        type=str,
        choices=['tushare', 'baostock'],
        default=None,
        help='指定数据源',
    )

    args = parser.parse_args()

    source = args.source or os.environ.get('DATA_SOURCE', 'tushare')
    fetcher = DWDFetcher(db_path=args.db, source=source)

    end_date = args.end_date or datetime.now().strftime('%Y%m%d')
    start_date = args.start_date

    if args.date:
        start_date = args.date
        end_date = args.date

    # ---- 增量更新模式 ----
    if args.incremental:
        if args.data_type == 'all':
            print("开始增量更新所有数据...")
            total_records = 0
            _sd = start_date or '20200101'

            print("\n[1/5] 更新交易日历 (全量)...")
            r = fetcher.update_trade_calendar(_sd, end_date)
            print(f"  交易日历: {r['records']}条")
            total_records += r['records']

            print("\n[2/5] 更新股票信息 (全量)...")
            r = fetcher.update_stock_info(source=source)
            print(f"  股票信息: {r['records']}条")
            total_records += r['records']

            print("\n[3/5] 更新日线数据 (增量)...")
            r = fetcher.update_incremental('daily')
            print(f"  日线数据: {r['records']}条, 成功{r.get('success', 0)}天")
            total_records += r['records']

            print("\n[4/5] 更新每日指标 (增量)...")
            r = fetcher.update_incremental('daily_basic')
            print(f"  每日指标: {r['records']}条, 成功{r.get('success', 0)}天")
            total_records += r['records']

            print("\n[5/5] 更新指数日线 (增量)...")
            r = fetcher.update_incremental('index')
            print(f"  指数日线: {r['records']}条")
            total_records += r['records']

            print(f"\n增量更新完成! 总记录: {total_records}条")
        else:
            print(f"开始增量更新: {args.data_type}...")
            result = fetcher.update_incremental(args.data_type)
            print(f"\n{args.data_type} 增量更新完成:")
            print(f"  成功: {result.get('success', 0)}")
            print(f"  失败: {result.get('fail', 0)}")
            print(f"  记录数: {result.get('records', 0)}")
            print(f"  耗时: {result.get('elapsed', 0):.1f}秒")
        return

    # ---- 全量/按类型更新 ----
    if args.data_type == 'daily':
        _sd = start_date or '20260101'

        # 确保交易日历存在（日线更新依赖 dwd_trade_calendar）
        db_check = duckdb.connect(args.db)
        try:
            cal_count = db_check.execute(
                "SELECT COUNT(*) FROM dwd_trade_calendar WHERE is_open = TRUE"
            ).fetchone()[0]
        except Exception:
            cal_count = 0
        finally:
            db_check.close()

        if cal_count == 0:
            print(f"[前置] dwd_trade_calendar 为空，自动更新交易日历 {_sd} ~ {end_date}...")
            cal_result = fetcher.update_trade_calendar(_sd, end_date)
            print(f"[前置] 交易日历: {cal_result['records']}条")

        if args.parallel:
            result = fetcher.update_daily_parallel(_sd, end_date, num_workers=args.workers)
        else:
            result = fetcher.update_daily(_sd, end_date)
        print(f"\n日线数据更新完成: 成功{result['success']}天, 失败{result['fail']}天, "
              f"记录{result['records']}条, 耗时{result['elapsed']:.1f}秒")

    elif args.data_type == 'daily_basic':
        _sd = start_date or '20260101'
        result = fetcher.update_daily_basic(_sd, end_date)
        print(f"\n每日指标更新完成: 成功{result['success']}天, 失败{result['fail']}天, "
              f"记录{result['records']}条, 耗时{result['elapsed']:.1f}秒")

    elif args.data_type == 'adj_factor':
        _sd = start_date or '20200101'
        result = fetcher.update_adj_factor(_sd, end_date)
        print(f"\n复权因子更新完成: 成功{result['success']}只, 失败{result['fail']}只, "
              f"记录{result['records']}条, 耗时{result['elapsed']:.1f}秒")

    elif args.data_type == 'income':
        if not args.ts_code:
            print("错误: --ts-code 参数必需")
            return
        result = fetcher.update_income(args.ts_code)
        print(f"\n利润表更新完成: {args.ts_code}, 记录{result['records']}条")

    elif args.data_type == 'balancesheet':
        if not args.ts_code:
            print("错误: --ts-code 参数必需")
            return
        result = fetcher.update_balancesheet(args.ts_code)
        print(f"\n资产负债表更新完成: {args.ts_code}, 记录{result['records']}条")

    elif args.data_type == 'cashflow':
        if not args.ts_code:
            print("错误: --ts-code 参数必需")
            return
        result = fetcher.update_cashflow(args.ts_code)
        print(f"\n现金流量表更新完成: {args.ts_code}, 记录{result['records']}条")

    elif args.data_type == 'index':
        index_code = args.index_code or '000001.SH'
        _sd = start_date or '20260101'
        result = fetcher.update_index(index_code, _sd, end_date)
        print(f"\n指数日线更新完成: {index_code}, 记录{result['records']}条, "
              f"耗时{result['elapsed']:.1f}秒")

    elif args.data_type == 'stock_info':
        result = fetcher.update_stock_info()
        print(f"\n股票信息更新完成: 记录{result['records']}条, 耗时{result['elapsed']:.1f}秒")

    elif args.data_type == 'trade_calendar':
        _sd = start_date or '20200101'
        result = fetcher.update_trade_calendar(_sd, end_date)
        print(f"\n交易日历更新完成: 记录{result['records']}条, 耗时{result['elapsed']:.1f}秒")

    elif args.data_type == 'financial':
        result = fetcher.update_financial_multiprocess(num_workers=args.workers)
        print(f"\n财务数据更新完成: 利润表{result['income_records']}条, "
              f"资产负债表{result['balancesheet_records']}条, "
              f"现金流量表{result['cashflow_records']}条, "
              f"耗时{result['elapsed']:.1f}秒")

    elif args.data_type == 'all':
        print("开始全量更新所有数据...")
        _sd = start_date or '20240101'

        print("\n[1/8] 更新交易日历...")
        r = fetcher.update_trade_calendar(_sd, end_date)
        print(f"  交易日历: {r['records']}条")

        print("\n[2/8] 更新股票信息...")
        r = fetcher.update_stock_info(source=source)
        print(f"  股票信息: {r['records']}条")

        print("\n[3/8] 更新日线数据...")
        r = fetcher.update_daily(_sd, end_date)
        print(f"  日线数据: {r['records']}条, 成功{r['success']}天")

        print("\n[4/8] 更新每日指标...")
        r = fetcher.update_daily_basic(_sd, end_date)
        print(f"  每日指标: {r['records']}条, 成功{r['success']}天")

        print("\n[5/8] 更新复权因子...")
        r = fetcher.update_adj_factor(_sd, end_date)
        print(f"  复权因子: {r['records']}条")

        print("\n[6/8] 更新指数日线...")
        r = fetcher.update_all_indices(_sd, end_date)
        print(f"  指数日线: {r['total_records']}条")

        print("\n[7/8] 更新财务数据 (多进程)...")
        r = fetcher.update_financial_multiprocess(num_workers=args.workers)
        print(f"  财务数据: 利润表{r['income_records']}条, "
              f"资产负债表{r['balancesheet_records']}条, "
              f"现金流量表{r['cashflow_records']}条")

        print("\n全量更新完成!")


if __name__ == "__main__":
    run_cli()
