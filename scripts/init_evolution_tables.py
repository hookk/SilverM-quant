#!/usr/bin/env python3
"""
scripts/init_evolution_tables.py
Create evolution_runs and evolution_iterations tables in DuckDB.
Safe to re-run (uses CREATE TABLE IF NOT EXISTS).
"""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import duckdb

DB_PATH = os.path.join(
    os.path.dirname(__file__), '..', 'data', 'Astock3.duckdb'
)

DDL_EVOLUTION_RUNS = """
CREATE SEQUENCE IF NOT EXISTS evolution_runs_seq START 1;

CREATE TABLE IF NOT EXISTS evolution_runs (
    id            INTEGER PRIMARY KEY DEFAULT nextval('evolution_runs_seq'),
    name          VARCHAR  NOT NULL UNIQUE,
    status        VARCHAR  NOT NULL DEFAULT 'created',   -- created | running | stopped | completed
    created_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at    TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    best_score    DOUBLE,
    best_iteration INTEGER,
    model         VARCHAR,
    config        JSON
);
"""

DDL_EVOLUTION_ITERATIONS = """
CREATE SEQUENCE IF NOT EXISTS evolution_iterations_seq START 1;

CREATE TABLE IF NOT EXISTS evolution_iterations (
    id             INTEGER PRIMARY KEY DEFAULT nextval('evolution_iterations_seq'),
    run_name       VARCHAR  NOT NULL,
    iteration      INTEGER  NOT NULL,
    hypothesis     VARCHAR,
    code_path      VARCHAR,
    train_metrics  JSON,
    valid_metrics  JSON,
    best_params    JSON,
    conclusion     VARCHAR,
    created_at     TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE (run_name, iteration)
);
"""

INDEX_DDL = [
    "CREATE INDEX IF NOT EXISTS idx_evo_iters_run  ON evolution_iterations (run_name);",
    "CREATE INDEX IF NOT EXISTS idx_evo_iters_iter ON evolution_iterations (run_name, iteration);",
]


def main():
    if not os.path.exists(os.path.dirname(DB_PATH)):
        print(f"[ERROR] 数据库目录不存在: {os.path.dirname(DB_PATH)}")
        sys.exit(1)

    print(f"[init_evolution_tables] 连接数据库: {DB_PATH}")
    con = duckdb.connect(DB_PATH)

    try:
        print("  创建 evolution_runs …", end=' ')
        con.execute(DDL_EVOLUTION_RUNS)
        print("OK")

        print("  创建 evolution_iterations …", end=' ')
        con.execute(DDL_EVOLUTION_ITERATIONS)
        print("OK")

        for idx in INDEX_DDL:
            print(f"  索引: {idx[:60]}…", end=' ')
            con.execute(idx)
            print("OK")

        # Verify
        tables = {r[0] for r in con.execute(
            "SELECT table_name FROM information_schema.tables "
            "WHERE table_name IN ('evolution_runs','evolution_iterations')"
        ).fetchall()}
        assert 'evolution_runs' in tables, "evolution_runs 表创建失败"
        assert 'evolution_iterations' in tables, "evolution_iterations 表创建失败"

        print("\n✅ 数据库迁移完成。")
    finally:
        con.close()


if __name__ == '__main__':
    main()
