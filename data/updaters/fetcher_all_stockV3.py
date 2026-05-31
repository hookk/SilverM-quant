#!/usr/bin/env python
# coding=utf-8
#!/usr/bin/env python
# coding=utf-8
# data/updaters/fetcher_all_stockV3.py
"""
股票列表更新器 V3 - DWD层

更新 dwd_stock_info 表，从 Tushare 获取全市场股票基本信息：
  ts_code, symbol, name, area, industry, market,
  list_date, is_hs, act_name, list_status

支持模式:
- --all:      全量更新（先清空，再写入当前所有状态的股票）
- --listed:   只更新在市股票 (list_status='L')
- --delisted: 只更新退市股票 (list_status='D')
- --paused:   只更新暂停上市股票 (list_status='P')
- 不加参数默认执行 --all

更新频率建议: 每月执行一次（月初自动化任务）
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

from database.schema import CREATE_DWD_STOCK_INFO_TABLE

DB_PATH = os.path.join(project_root, 'data', 'Astock3.duckdb')

from scripts.log_utils import setup_logger
logger = setup_logger('fetcher_all_stockV3', 'pipeline')

# 支持的上市状态
LIST_STATUS_MAP = {
    'L': '在市',
    'D': '退市',
    'P': '暂停上市',
}


def _get_tushare_pro():
    """初始化并返回 tushare pro API 实例"""
    try:
        import tushare as ts
        from config.settings import Settings
        token = Settings.TUSHARE_TOKEN
        if not token:
            # 尝试从环境变量获取
            token = os.environ.get('TUSHARE_TOKEN', '')
        if not token:
            logger.error("TUSHARE_TOKEN 未设置，请在 config/settings.py 或环境变量中配置")
            return None
        ts.set_token(token)
        pro = ts.pro_api()
        logger.info("Tushare 登录成功")
        return pro
    except Exception as e:
        logger.error(f"Tushare 登录失败: {e}")
        return None


class AllStockFetcher:
    """
    股票列表更新器 V3（dwd_stock_info）

    使用示例:
        fetcher = AllStockFetcher()

        # 全量更新（所有状态）
        fetcher.update_all()

        # 只更新在市股票
        fetcher.update_by_status('L')
    """

    def __init__(self, db_path: str = DB_PATH):
        self.db_path = db_path
        self._pro = None
        self._ensure_table()

    # ------------------------------------------------------------------
    # 内部工具
    # ------------------------------------------------------------------

    def _ensure_table(self) -> None:
        """确保 dwd_stock_info 表存在（幂等）"""
        db = duckdb.connect(self.db_path)
        try:
            db.execute(CREATE_DWD_STOCK_INFO_TABLE)
            logger.info("dwd_stock_info 表检查完成")
        finally:
            db.close()

    def _get_pro(self):
        """延迟初始化 tushare pro 实例"""
        if self._pro is None:
            self._pro = _get_tushare_pro()
        return self._pro

    def _fetch_stock_basic(self, list_status: str = 'L') -> pd.DataFrame:
        """
        从 Tushare 拉取指定上市状态的股票基本信息。

        Args:
            list_status: 上市状态 'L'（在市）| 'D'（退市）| 'P'（暂停）

        Returns:
            DataFrame，为空时返回空 DataFrame
        """
        pro = self._get_pro()
        if pro is None:
            return pd.DataFrame()

        try:
            from data.fetchers.rate_limiter import tushare_limiter
            tushare_limiter.acquire()

            df = pro.stock_basic(
                exchange='',
                list_status=list_status,
                fields=(
                    'ts_code,symbol,name,area,industry,market,'
                    'list_date,is_hs,act_name,list_status'
                )
            )
            if df is None or df.empty:
                logger.warning(f"stock_basic 返回空数据, list_status={list_status}")
                return pd.DataFrame()

            logger.info(
                f"获取到 {len(df)} 只{LIST_STATUS_MAP.get(list_status, list_status)}股票"
            )
            return df

        except Exception as e:
            logger.error(f"获取 stock_basic 失败 (list_status={list_status}): {e}")
            return pd.DataFrame()

    def _preprocess(self, df: pd.DataFrame) -> pd.DataFrame:
        """标准化 DataFrame：日期格式转换、补充 data_source 字段"""
        df = df.copy()

        # list_date: YYYYMMDD -> date
        if 'list_date' in df.columns:
            df['list_date'] = pd.to_datetime(
                df['list_date'], format='%Y%m%d', errors='coerce'
            ).dt.strftime('%Y-%m-%d')

        # 补充数据来源标记
        df['data_source'] = 'tushare'

        return df

    def _save_to_db(self, df: pd.DataFrame, replace_status: Optional[str] = None) -> int:
        """
        将 DataFrame 写入 dwd_stock_info。

        Args:
            df:             待写入数据
            replace_status: 若指定，写入前先删除该 list_status 的旧数据；
                            None 表示不删除（外部已清空表）

        Returns:
            实际写入的记录数
        """
        if df is None or df.empty:
            return 0

        db = duckdb.connect(self.db_path)
        try:
            if replace_status is not None:
                deleted = db.execute(
                    "DELETE FROM dwd_stock_info WHERE list_status = ?",
                    [replace_status]
                ).rowcount
                logger.info(
                    f"已删除旧数据: list_status={replace_status}, {deleted}条"
                )

            db.execute("CREATE TEMPORARY TABLE temp_stock AS SELECT * FROM df")
            insert_cols = ', '.join(df.columns)
            db.execute(
                f"INSERT OR REPLACE INTO dwd_stock_info ({insert_cols}) "
                f"SELECT {insert_cols} FROM temp_stock"
            )
            db.execute("DROP TABLE temp_stock")
            return len(df)
        finally:
            db.close()

    def get_current_count(self) -> Dict[str, int]:
        """查询当前 dwd_stock_info 各状态股票数量"""
        db = duckdb.connect(self.db_path)
        try:
            rows = db.execute(
                "SELECT list_status, COUNT(*) FROM dwd_stock_info GROUP BY list_status"
            ).fetchall()
            return {row[0]: row[1] for row in rows}
        except Exception:
            return {}
        finally:
            db.close()

    # ------------------------------------------------------------------
    # 公开更新方法
    # ------------------------------------------------------------------

    def update_by_status(self, list_status: str) -> Dict[str, Any]:
        """
        更新指定上市状态的股票信息。
        先删除该状态的旧记录，再写入最新数据（upsert 语义）。

        Args:
            list_status: 'L' | 'D' | 'P'

        Returns:
            统计字典 {success, records, elapsed}
        """
        status_name = LIST_STATUS_MAP.get(list_status, list_status)
        logger.info(f"更新{status_name}股票信息 (list_status={list_status})")
        start_time = time.time()

        df = self._fetch_stock_basic(list_status)
        if df.empty:
            logger.warning(f"未获取到{status_name}股票数据，跳过")
            return {'success': 0, 'records': 0, 'elapsed': 0}

        df = self._preprocess(df)
        records = self._save_to_db(df, replace_status=list_status)
        elapsed = time.time() - start_time

        logger.info(f"{status_name}股票更新完成: {records}条, {elapsed:.1f}秒")
        return {'success': 1, 'records': records, 'elapsed': elapsed}

    def update_all(self) -> Dict[str, Any]:
        """
        全量更新：获取所有状态（L / D / P）的股票信息，
        清空旧表后重建。

        Returns:
            汇总统计字典
        """
        logger.info("开始全量更新 dwd_stock_info（L + D + P）")
        start_time = time.time()

        all_dfs: List[pd.DataFrame] = []
        for status in LIST_STATUS_MAP:
            df = self._fetch_stock_basic(status)
            if not df.empty:
                all_dfs.append(self._preprocess(df))

        if not all_dfs:
            logger.error("所有状态均未获取到数据，全量更新失败")
            return {'success': 0, 'records': 0, 'elapsed': 0}

        df_all = pd.concat(all_dfs, ignore_index=True)

        # 全量模式：先清空表再整体写入，避免重复
        db = duckdb.connect(self.db_path)
        try:
            db.execute("DELETE FROM dwd_stock_info")
            logger.info("已清空 dwd_stock_info 旧数据")
        finally:
            db.close()

        records = self._save_to_db(df_all, replace_status=None)
        elapsed = time.time() - start_time

        counts = self.get_current_count()
        logger.info(
            f"全量更新完成: 共{records}条 "
            f"[在市={counts.get('L', 0)}, "
            f"退市={counts.get('D', 0)}, "
            f"暂停={counts.get('P', 0)}], "
            f"耗时{elapsed:.1f}秒"
        )
        return {'success': 1, 'records': records, 'elapsed': elapsed}

    def validate(self, min_listed: int = 5000) -> bool:
        """
        简单验证：在市股票数量不低于 min_listed。

        Args:
            min_listed: 最低在市股票数量阈值，默认 5000

        Returns:
            验证通过返回 True
        """
        counts = self.get_current_count()
        listed = counts.get('L', 0)
        if listed >= min_listed:
            logger.info(f"验证通过: 在市股票 {listed} >= {min_listed}")
            return True
        else:
            logger.warning(f"验证失败: 在市股票 {listed} < {min_listed}")
            return False


# ---------------------------------------------------------------------------
# CLI 入口
# ---------------------------------------------------------------------------

def run_cli():
    parser = argparse.ArgumentParser(
        description='股票列表更新器 V3（dwd_stock_info）',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 全量更新（所有状态）
  python fetcher_all_stockV3.py --all

  # 只更新在市股票
  python fetcher_all_stockV3.py --listed

  # 只更新退市股票
  python fetcher_all_stockV3.py --delisted

  # 不加参数默认全量更新
  python fetcher_all_stockV3.py
        """
    )

    group = parser.add_mutually_exclusive_group()
    group.add_argument('--all', action='store_true',
                       help='全量更新（L + D + P 三种状态）')
    group.add_argument('--listed', action='store_true',
                       help='只更新在市股票 (list_status=L)')
    group.add_argument('--delisted', action='store_true',
                       help='只更新退市股票 (list_status=D)')
    group.add_argument('--paused', action='store_true',
                       help='只更新暂停上市股票 (list_status=P)')

    parser.add_argument('--db', type=str, default=DB_PATH, help='数据库路径')
    parser.add_argument('--no-validate', action='store_true',
                        help='跳过更新后的数量验证')

    args = parser.parse_args()

    fetcher = AllStockFetcher(db_path=args.db)

    # 默认行为：全量更新
    if args.listed:
        result = fetcher.update_by_status('L')
    elif args.delisted:
        result = fetcher.update_by_status('D')
    elif args.paused:
        result = fetcher.update_by_status('P')
    else:
        # --all 或不带参数均执行全量更新
        result = fetcher.update_all()

    print(f"\n股票列表更新完成:")
    print(f"  成功: {result['success']}")
    print(f"  记录数: {result['records']}")
    print(f"  耗时: {result['elapsed']:.1f}秒")

    if not args.no_validate:
        fetcher.validate()

    counts = fetcher.get_current_count()
    if counts:
        print(f"\n当前 dwd_stock_info 统计:")
        for status, cnt in sorted(counts.items()):
            print(f"  {LIST_STATUS_MAP.get(status, status)} ({status}): {cnt} 只")


if __name__ == '__main__':
    run_cli()
