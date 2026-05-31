"""
evolution/data_preparer.py — DataPreparer
=========================================

Reads data_registry.yaml, queries DuckDB (data/Astock3.duckdb),
joins multi-source data (daily + quarterly with ffill), applies the
3-segment data split, and outputs a unified DataFrame ready for signals.

Design decisions:
  • Single call returns all stocks in a split: dict[code → pd.DataFrame].
  • DataPreparer is stateless after __init__; call prepare() multiple times.
  • Missing optional datasets (enabled but table absent) are skipped with
    a warning rather than raising — lets new tables be added incrementally.
  • Required datasets (required: true) raise DataPreparerError if absent.
    If the primary table is absent but table_fallback is set, the fallback
    is tried before raising (e.g. dwd_daily_price_qfq → dwd_daily_price).
  • index_daily is special: stored as long format (one row per index_code),
    pivoted into wide format (one column per index) by DataPreparer.
  • Stock codes in DB use ts_code format (e.g. "000001.SZ"); the join_key
    field in the registry names the actual PK column.

Usage
-----
    from evolution.data_preparer import DataPreparer, Split

    dp = DataPreparer()

    # Get all stocks for the train set
    train_data = dp.prepare(split=Split.TRAIN)
    # train_data["000001.SZ"] → pd.DataFrame(index=DatetimeIndex, columns=OHLCV+...)

    # Get a single stock for quick checks
    df = dp.prepare_one("000001.SZ", split=Split.VALID)

    # Get all stocks, only price columns
    data = dp.prepare(split=Split.TRAIN, include_datasets=["price_daily"])
"""

from __future__ import annotations

import logging
import os
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yaml

logger = logging.getLogger(__name__)

# ── Paths ─────────────────────────────────────────────────────────────────────
_THIS_DIR = Path(__file__).parent
_REGISTRY = _THIS_DIR / "data_registry.yaml"
_PROJECT  = _THIS_DIR.parent               # repo root
_DB_PATH  = _PROJECT / "data" / "Astock3.duckdb"


# ── Exceptions ────────────────────────────────────────────────────────────────

class DataPreparerError(RuntimeError):
    """Raised for fatal data preparation failures."""


# ── Split enum ────────────────────────────────────────────────────────────────

class Split(str, Enum):
    TRAIN = "train"
    VALID = "valid"
    TEST  = "test"


# ─────────────────────────────────────────────────────────────────────────────
# DataPreparer
# ─────────────────────────────────────────────────────────────────────────────

class DataPreparer:
    """
    Loads data from DuckDB according to data_registry.yaml.

    Args:
        registry_path: Path to data_registry.yaml. Default: adjacent file.
        db_path:       Path to Astock3.duckdb. Default: data/Astock3.duckdb.
    """

    def __init__(
        self,
        registry_path: Optional[Path] = None,
        db_path: Optional[Path] = None,
    ):
        self._registry_path = Path(registry_path or _REGISTRY)
        self._db_path = Path(db_path or _DB_PATH)
        self._registry = self._load_registry()
        logger.info(
            "DataPreparer init | registry=%s | db=%s",
            self._registry_path, self._db_path,
        )

    # ── Registry loading ──────────────────────────────────────────────────────

    def _load_registry(self) -> dict:
        if not self._registry_path.exists():
            raise DataPreparerError(
                f"data_registry.yaml not found at {self._registry_path}"
            )
        with open(self._registry_path, "r", encoding="utf-8") as f:
            reg = yaml.safe_load(f)
        logger.debug(
            "Loaded registry with datasets: %s", list(reg.get("datasets", {}).keys())
        )
        return reg

    # ── Split date resolution ─────────────────────────────────────────────────

    def _split_dates(self, split: Split) -> tuple[str, str]:
        """Return (start_date, end_date) strings for the given split."""
        splits = self._registry["splits"]
        cfg = splits[split.value]
        start = cfg["start"]
        end   = cfg["end"] or date.today().isoformat()
        return start, end

    # ── DuckDB connection ─────────────────────────────────────────────────────

    def _connect(self):
        """Return a read-only DuckDB connection."""
        try:
            import duckdb
        except ImportError as exc:
            raise DataPreparerError(
                "duckdb is not installed. Run: pip install duckdb"
            ) from exc
        if not self._db_path.exists():
            raise DataPreparerError(
                f"DuckDB file not found: {self._db_path}. "
                "Run the data pipeline first."
            )
        return duckdb.connect(str(self._db_path), read_only=True)

    # ── Table existence check ─────────────────────────────────────────────────

    def _table_exists(self, conn, table: str) -> bool:
        try:
            row = conn.execute(
                "SELECT COUNT(*) FROM information_schema.tables "
                "WHERE table_name = ?", [table]
            ).fetchone()
            return row is not None and row[0] > 0
        except Exception:
            return False

    def _resolve_table(self, conn, ds_cfg: dict) -> Optional[str]:
        """
        Return the actual table name to use, trying fallback if primary is absent.
        Returns None if neither primary nor fallback exists.
        """
        primary  = ds_cfg.get("table")
        fallback = ds_cfg.get("table_fallback")
        if primary and self._table_exists(conn, primary):
            return primary
        if fallback and self._table_exists(conn, fallback):
            logger.info(
                "Primary table '%s' absent; using fallback '%s'.", primary, fallback
            )
            return fallback
        return None

    # ── Core: load one standard dataset ──────────────────────────────────────

    def _load_dataset(
        self,
        conn,
        ds_name: str,
        ds_cfg: dict,
        start: str,
        end: str,
        codes: Optional[List[str]] = None,
    ) -> Optional[pd.DataFrame]:
        """
        Load a single dataset from DuckDB.

        Special case: index_daily uses a pivot (see _load_index_daily).

        Returns:
            pd.DataFrame with columns renamed per registry, or None if skipped.
        """
        if not ds_cfg.get("enabled", False):
            logger.debug("Dataset '%s' disabled, skipping.", ds_name)
            return None

        required = ds_cfg.get("required", False)

        # ── index_daily needs special pivot handling ──────────────────────────
        if ds_name == "index_daily" and ds_cfg.get("index_codes"):
            return self._load_index_daily(conn, ds_cfg, start, end)

        # ── Standard dataset ──────────────────────────────────────────────────
        table = self._resolve_table(conn, ds_cfg)
        if table is None:
            msg = (
                f"Table '{ds_cfg.get('table')}' (dataset '{ds_name}') "
                "not found in DuckDB."
            )
            if required:
                raise DataPreparerError(msg)
            logger.warning("%s Skipping (not required).", msg)
            return None

        col_map:  dict = ds_cfg.get("columns", {})   # DB col → exposed col
        src_cols: list = list(col_map.keys())
        date_col: str  = ds_cfg["date_column"]
        join_key: str  = ds_cfg.get("join_key")      # actual DB column name

        # Discover which columns actually exist in the table
        try:
            existing_cols = {
                r[0] for r in conn.execute(
                    f"SELECT column_name FROM information_schema.columns "
                    f"WHERE table_name = '{table}'"
                ).fetchall()
            }
        except Exception:
            existing_cols = set(src_cols) | {date_col} | (
                {join_key} if join_key else set()
            )

        select_parts = [date_col]
        if join_key and join_key in existing_cols:
            select_parts.append(join_key)
        for col in src_cols:
            if col in existing_cols:
                select_parts.append(col)
            else:
                logger.warning(
                    "Column '%s' not found in table '%s', skipping.", col, table
                )

        select_sql = ", ".join(select_parts)

        where_parts = [
            f"{date_col} >= '{start}'",
            f"{date_col} <= '{end}'",
        ]
        if join_key and codes:
            codes_str = ", ".join(f"'{c}'" for c in codes)
            where_parts.append(f"{join_key} IN ({codes_str})")

        where_sql = " AND ".join(where_parts)
        sql = (
            f"SELECT {select_sql} FROM {table} "
            f"WHERE {where_sql} ORDER BY {date_col}"
        )

        logger.debug("SQL: %s", sql)
        try:
            df = conn.execute(sql).df()
        except Exception as exc:
            msg = f"Failed to query table '{table}': {exc}"
            if required:
                raise DataPreparerError(msg) from exc
            logger.warning("%s Skipping.", msg)
            return None

        if df.empty:
            logger.warning(
                "Dataset '%s' returned 0 rows for %s–%s.", ds_name, start, end
            )
            return None

        # Rename: DB column → exposed name
        rename_map = {src: dst for src, dst in col_map.items() if src in df.columns}
        df = df.rename(columns=rename_map)

        # Normalise join_key column to "ts_code" so _build_stock_df can rely on it
        if join_key and join_key in df.columns and join_key != "ts_code":
            df = df.rename(columns={join_key: "ts_code"})

        # Parse date column → uniform column name "trade_date"
        df[date_col] = pd.to_datetime(df[date_col])
        if date_col != "trade_date":
            df = df.rename(columns={date_col: "trade_date"})

        logger.info(
            "Loaded dataset '%s': %d rows, %d columns, %s–%s",
            ds_name, len(df), len(df.columns), start, end,
        )
        return df

    # ── Special: index_daily pivot ────────────────────────────────────────────

    def _load_index_daily(
        self,
        conn,
        ds_cfg: dict,
        start: str,
        end: str,
    ) -> Optional[pd.DataFrame]:
        """
        Load dwd_index_daily and pivot into wide format.

        dwd_index_daily schema:
            index_code  VARCHAR  (e.g. "000300.SH")
            trade_date  DATE
            close       DOUBLE
            ...

        Output:
            pd.DataFrame indexed by trade_date with columns hs300, zz500, ...
            (one column per index code as declared in registry index_codes map)
        """
        table = ds_cfg.get("table", "dwd_index_daily")
        if not self._table_exists(conn, table):
            logger.warning(
                "index_daily table '%s' not found in DuckDB. Skipping.", table
            )
            return None

        index_codes: dict = ds_cfg.get("index_codes", {})
        if not index_codes:
            return None

        codes_str = ", ".join(f"'{c}'" for c in index_codes.keys())
        sql = (
            f"SELECT trade_date, index_code, close "
            f"FROM {table} "
            f"WHERE trade_date >= '{start}' AND trade_date <= '{end}' "
            f"  AND index_code IN ({codes_str}) "
            f"ORDER BY trade_date"
        )
        logger.debug("index_daily SQL: %s", sql)
        try:
            raw = conn.execute(sql).df()
        except Exception as exc:
            logger.warning("Failed to query index_daily: %s. Skipping.", exc)
            return None

        if raw.empty:
            logger.warning("index_daily returned 0 rows for %s–%s.", start, end)
            return None

        # Pivot: rows=(trade_date), cols=(index_code) → one col per index
        raw["trade_date"] = pd.to_datetime(raw["trade_date"])
        wide = raw.pivot_table(
            index="trade_date", columns="index_code", values="close", aggfunc="first"
        )
        wide.columns.name = None

        # Rename index_code → user-facing column name
        rename_map = {code: name for code, name in index_codes.items()}
        wide = wide.rename(columns=rename_map)
        wide = wide.reset_index()   # trade_date becomes a regular column

        logger.info(
            "Loaded index_daily (pivot): %d rows, cols=%s",
            len(wide), list(wide.columns),
        )
        return wide

    # ── Per-stock DataFrame assembly ──────────────────────────────────────────

    def _build_stock_df(
        self,
        datasets: Dict[str, Optional[pd.DataFrame]],
        code: str,
    ) -> Optional[pd.DataFrame]:
        """
        Merge all loaded datasets for a single stock code.

        The join key in all standard datasets is normalised to 'ts_code'
        by _load_dataset.  index_daily has no stock join key (broadcast).
        Multi-frequency (quarterly) data is forward-filled to daily.
        """
        price_ds = datasets.get("price_daily")
        if price_ds is None:
            return None

        # Filter to this stock — join key is 'ts_code' after normalisation
        if "ts_code" in price_ds.columns:
            stock_price = price_ds[price_ds["ts_code"] == code].copy()
        else:
            # Fallback: if somehow ts_code column is absent, try using all rows
            logger.warning(
                "price_daily missing 'ts_code' column; "
                "cannot filter to code=%s", code
            )
            return None

        if stock_price.empty:
            return None

        stock_price = (
            stock_price
            .set_index("trade_date")
            .sort_index()
            .drop(columns=["ts_code"], errors="ignore")
        )

        # Merge optional datasets
        for ds_name, ds_df in datasets.items():
            if ds_name == "price_daily" or ds_df is None:
                continue

            ds_cfg  = self._registry["datasets"].get(ds_name, {})
            join_key = ds_cfg.get("join_key")
            freq     = ds_cfg.get("frequency", "D")

            # index_daily: broadcast (no per-stock join key)
            if ds_name == "index_daily" or join_key is None:
                sub = ds_df.copy()
                if "trade_date" in sub.columns:
                    sub = sub.set_index("trade_date").sort_index()
            elif "ts_code" in ds_df.columns:
                sub = ds_df[ds_df["ts_code"] == code].copy()
                if sub.empty:
                    continue
                sub = (
                    sub
                    .set_index("trade_date")
                    .sort_index()
                    .drop(columns=["ts_code"], errors="ignore")
                )
            else:
                continue

            # Forward-fill quarterly data to daily frequency
            if freq == "Q":
                sub = sub.reindex(stock_price.index, method="ffill")
            else:
                sub = sub.reindex(stock_price.index)

            # Drop columns already present to avoid conflicts
            overlap = set(sub.columns) & set(stock_price.columns)
            if overlap:
                sub = sub.drop(columns=list(overlap))

            stock_price = stock_price.join(sub, how="left")

        # Coerce all columns to float64
        for col in stock_price.columns:
            try:
                stock_price[col] = pd.to_numeric(stock_price[col], errors="coerce")
            except Exception:
                pass

        return stock_price

    # ── Universe derivation ───────────────────────────────────────────────────

    def _get_universe(
        self,
        conn,
        start: str,
        end: str,
        include_codes: Optional[List[str]] = None,
    ) -> List[str]:
        """Return list of stock ts_codes that pass universe filters."""
        universe_cfg  = self._registry.get("universe", {})
        min_days      = universe_cfg.get("filters", {}).get("min_trading_days", 0)
        # Use the table and column names declared in the registry
        source_table  = universe_cfg.get("source_table", "dwd_daily_price")
        code_col      = universe_cfg.get("code_column",  "ts_code")

        if not self._table_exists(conn, source_table):
            logger.warning(
                "Universe source table '%s' not found; returning empty universe.",
                source_table,
            )
            return include_codes or []

        try:
            sql = (
                f"SELECT {code_col}, COUNT(*) AS n_days "
                f"FROM {source_table} "
                f"WHERE trade_date >= '{start}' AND trade_date <= '{end}' "
                f"GROUP BY {code_col} HAVING n_days >= {min_days} "
                f"ORDER BY {code_col}"
            )
            rows  = conn.execute(sql).fetchall()
            codes = [r[0] for r in rows]
        except Exception as exc:
            logger.warning("Universe query failed: %s. Using empty list.", exc)
            codes = []

        if include_codes:
            include_set = set(include_codes)
            codes = [c for c in codes if c in include_set]

        logger.info("Universe for %s–%s: %d stocks", start, end, len(codes))
        return codes

    # ── Public API ────────────────────────────────────────────────────────────

    def prepare(
        self,
        split: Split = Split.TRAIN,
        include_datasets: Optional[List[str]] = None,
        codes: Optional[List[str]] = None,
        max_stocks: Optional[int] = None,
    ) -> Dict[str, pd.DataFrame]:
        """
        Load and join all enabled datasets for the given split.

        Args:
            split:            Which data segment to load (TRAIN/VALID/TEST).
            include_datasets: Whitelist of dataset names. None = all enabled.
            codes:            Restrict to specific stock ts_codes
                              (e.g. ["000001.SZ", "600519.SH"]).
            max_stocks:       Cap the number of stocks (useful for quick tests).

        Returns:
            dict mapping ts_code → pd.DataFrame(DatetimeIndex, columns=OHLCV+...)
        """
        start, end = self._split_dates(split)
        logger.info(
            "DataPreparer.prepare | split=%s | %s → %s", split.value, start, end
        )

        conn = self._connect()
        try:
            universe = self._get_universe(conn, start, end, include_codes=codes)
            if max_stocks:
                universe = universe[:max_stocks]
            if not universe:
                logger.warning("Empty universe for split=%s.", split.value)
                return {}

            # Load all requested datasets as wide DataFrames
            raw_datasets: Dict[str, Optional[pd.DataFrame]] = {}
            for ds_name, ds_cfg in self._registry["datasets"].items():
                if include_datasets and ds_name not in include_datasets:
                    continue
                raw_datasets[ds_name] = self._load_dataset(
                    conn, ds_name, ds_cfg, start, end, codes=universe
                )

            # Per-stock merge
            result: Dict[str, pd.DataFrame] = {}
            for code in universe:
                df = self._build_stock_df(raw_datasets, code)
                if df is not None and not df.empty:
                    result[code] = df

            logger.info(
                "DataPreparer.prepare complete: %d stocks loaded for split=%s",
                len(result), split.value,
            )
            return result

        finally:
            conn.close()

    def prepare_one(
        self,
        code: str,
        split: Split = Split.TRAIN,
        include_datasets: Optional[List[str]] = None,
    ) -> Optional[pd.DataFrame]:
        """
        Load a single stock's data for the given split.

        Args:
            code:  Stock ts_code, e.g. "600519.SH".
            split: Data split to use.

        Returns:
            pd.DataFrame or None if the stock has no data in the split.
        """
        result = self.prepare(
            split=split,
            include_datasets=include_datasets,
            codes=[code],
        )
        return result.get(code)

    def split_dates(self, split: Split) -> tuple[str, str]:
        """Public accessor for (start_date, end_date) of a split."""
        return self._split_dates(split)

    def registry(self) -> dict:
        """Return the raw parsed registry dict (read-only)."""
        return self._registry

    def list_datasets(self) -> List[dict]:
        """
        Return a summary list of all datasets with their enabled status.

        Returns:
            List of dicts: [{name, enabled, table, frequency, description}]
        """
        return [
            {
                "name":        name,
                "enabled":     cfg.get("enabled", False),
                "table":       cfg.get("table", "?"),
                "frequency":   cfg.get("frequency", "D"),
                "description": cfg.get("description", ""),
                "required":    cfg.get("required", False),
            }
            for name, cfg in self._registry.get("datasets", {}).items()
        ]
