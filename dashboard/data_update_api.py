"""
Flask API - 数据更新接口
"""
import sys
import os
from flask import Blueprint, request, jsonify
from datetime import datetime
import threading
import duckdb

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from data.updaters.fetcher_dwd import DWDFetcher

data_update_bp = Blueprint('data_update', __name__, url_prefix='/api/data-update')

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DB_PATH = os.path.join(PROJECT_ROOT, 'data', 'Astock3.duckdb')


def get_log_path(date_str: str = None) -> str:
    """获取动态日志路径"""
    if date_str is None:
        date_str = datetime.now().strftime('%Y%m%d')
    log_dir = os.path.join(PROJECT_ROOT, 'logs', 'pipeline')
    os.makedirs(log_dir, exist_ok=True)
    return os.path.join(log_dir, f'fetcher_dwd_{date_str}.log')


def _normalize_date(date_str: str) -> str:
    """
    将前端 input[type=date] 送来的 YYYY-MM-DD 转换为 YYYYMMDD。
    已经是 YYYYMMDD 格式的原样返回。
    """
    if date_str and '-' in date_str:
        return date_str.replace('-', '')
    return date_str


# 任务状态字典，key=task_id，value=dict
update_tasks: dict = {}
# 保护 update_tasks 的锁（多线程安全）
_tasks_lock = threading.Lock()

# 正在运行的任务类型，防止同类型任务并发重复执行
# key=data_type（如 'financial'、'daily'），value=task_id
_running_types: dict = {}


def _set_task(task_id: str, **kwargs):
    """线程安全地更新任务状态"""
    with _tasks_lock:
        if task_id in update_tasks:
            update_tasks[task_id].update(kwargs)
        else:
            update_tasks[task_id] = kwargs


def _mark_type_done(data_type: str, task_id: str):
    """任务结束后释放该类型的运行锁"""
    with _tasks_lock:
        if _running_types.get(data_type) == task_id:
            del _running_types[data_type]


def run_update_task(task_id: str, data_type: str,
                    start_date: str = None, end_date: str = None,
                    ts_code: str = None, index_code: str = None,
                    workers: int = 4, source: str = 'tushare'):
    """后台线程：运行更新任务，持续写入 update_tasks[task_id]"""
    try:
        _set_task(task_id, status='running', progress=5, message='初始化更新器...')

        fetcher = DWDFetcher(source=source)
        result = None

        if data_type == 'daily':
            sd = start_date or '20240101'
            ed = end_date or datetime.now().strftime('%Y%m%d')
            _set_task(task_id, progress=10, message=f'更新日线数据 {sd}~{ed}...')
            result = fetcher.update_daily(sd, ed)

        elif data_type == 'daily_basic':
            sd = start_date or '20240101'
            ed = end_date or datetime.now().strftime('%Y%m%d')
            _set_task(task_id, progress=10, message=f'更新每日指标 {sd}~{ed}...')
            result = fetcher.update_daily_basic(sd, ed)

        elif data_type == 'adj_factor':
            sd = start_date or '20200101'
            ed = end_date or datetime.now().strftime('%Y%m%d')
            _set_task(task_id, progress=10, message=f'更新复权因子 {sd}~{ed}...')
            result = fetcher.update_adj_factor(sd, ed)

        elif data_type == 'index':
            sd = start_date or '20240101'
            ed = end_date or datetime.now().strftime('%Y%m%d')
            _set_task(task_id, progress=10, message=f'更新指数日线 {sd}~{ed}...')
            # 更新全部默认指数，而非单个
            result = fetcher.update_all_indices(sd, ed)
            # update_all_indices 返回 {total_records, ...}，统一为 {records}
            if result and 'total_records' in result:
                result['records'] = result.pop('total_records')

        elif data_type == 'stock_info':
            _set_task(task_id, progress=10, message='更新股票基础信息...')
            result = fetcher.update_stock_info(source=source)

        elif data_type == 'trade_calendar':
            sd = start_date or '20200101'
            ed = end_date or datetime.now().strftime('%Y%m%d')
            _set_task(task_id, progress=10, message=f'更新交易日历 {sd}~{ed}...')
            result = fetcher.update_trade_calendar(sd, ed)

        elif data_type == 'financial':
            _set_task(task_id, progress=10,
                      message=f'更新财务数据（多进程 {workers} 个进程，耗时较长）...')
            result = fetcher.update_financial_multiprocess(num_workers=workers)
            if result:
                _set_task(task_id, progress=80,
                          message=(
                              f'财务数据写入完成：利润表 {result.get("income_records", 0)} 条，'
                              f'资产负债表 {result.get("balancesheet_records", 0)} 条，'
                              f'现金流量表 {result.get("cashflow_records", 0)} 条，收尾中...'
                          ))
                result['records'] = (
                    result.get('income_records', 0)
                    + result.get('balancesheet_records', 0)
                    + result.get('cashflow_records', 0)
                )

        elif data_type == 'all':
            sd = start_date or '20240101'
            ed = end_date or datetime.now().strftime('%Y%m%d')
            total_records = 0
            results = {}

            steps = [
                (10,  '更新交易日历...'),
                (20,  '更新股票信息...'),
                (35,  '更新日线数据...'),
                (55,  '更新每日指标...'),
                (65,  '更新复权因子...'),
                (78,  '更新指数日线...'),
                (90,  f'更新财务数据（多进程 {workers} 个进程）...'),
            ]

            # 1. 交易日历
            _set_task(task_id, progress=steps[0][0], message=steps[0][1])
            r = fetcher.update_trade_calendar(sd, ed)
            results['trade_calendar'] = r
            total_records += r.get('records', 0)

            # 2. 股票信息
            _set_task(task_id, progress=steps[1][0], message=steps[1][1])
            r = fetcher.update_stock_info(source=source)
            results['stock_info'] = r
            total_records += r.get('records', 0)

            # 3. 日线数据
            _set_task(task_id, progress=steps[2][0], message=steps[2][1])
            r = fetcher.update_daily(sd, ed)
            results['daily'] = r
            total_records += r.get('records', 0)

            # 4. 每日指标
            _set_task(task_id, progress=steps[3][0], message=steps[3][1])
            r = fetcher.update_daily_basic(sd, ed)
            results['daily_basic'] = r
            total_records += r.get('records', 0)

            # 5. 复权因子
            _set_task(task_id, progress=steps[4][0], message=steps[4][1])
            r = fetcher.update_adj_factor(sd, ed)
            results['adj_factor'] = r
            total_records += r.get('records', 0)

            # 6. 指数数据
            _set_task(task_id, progress=steps[5][0], message=steps[5][1])
            r = fetcher.update_all_indices(sd, ed)
            results['index'] = {'records': r.get('total_records', r.get('records', 0))}
            total_records += results['index']['records']

            # 7. 财务数据
            _set_task(task_id, progress=steps[6][0], message=steps[6][1])
            r = fetcher.update_financial_multiprocess(num_workers=workers)
            results['financial'] = r
            fin_records = (
                r.get('income_records', 0)
                + r.get('balancesheet_records', 0)
                + r.get('cashflow_records', 0)
            )
            total_records += fin_records
            _set_task(task_id, progress=95,
                      message=f'财务数据完成（{fin_records} 条），收尾中...')

            result = {'records': total_records, 'results': results}

        else:
            _set_task(task_id, status='error', progress=0,
                      message=f'不支持的数据类型: {data_type}')
            _mark_type_done(data_type, task_id)
            return

        records = result.get('records', 0) if result else 0
        _set_task(task_id,
                  status='completed',
                  progress=100,
                  message=f'更新完成，共 {records} 条记录',
                  result=result)

    except Exception as e:
        import traceback
        tb = traceback.format_exc()
        _set_task(task_id, status='error', progress=0,
                  message=f'更新失败: {str(e)}',
                  traceback=tb)
    finally:
        # 无论成功或失败，都释放该类型的运行锁
        _mark_type_done(data_type, task_id)


# ---------------------------------------------------------------------------
# 路由
# ---------------------------------------------------------------------------

@data_update_bp.route('/status', methods=['GET'])
def get_status():
    """获取所有DWD表状态"""
    try:
        db = duckdb.connect(DB_PATH)
        try:
            tables_status = {}
            table_queries = {
                'dwd_daily_price':   "SELECT COUNT(*) as cnt, MAX(trade_date) as latest FROM dwd_daily_price",
                'dwd_daily_basic':   "SELECT COUNT(*) as cnt, MAX(trade_date) as latest FROM dwd_daily_basic",
                'dwd_adj_factor':    "SELECT COUNT(*) as cnt, MAX(trade_date) as latest FROM dwd_adj_factor",
                'dwd_income':        "SELECT COUNT(*) as cnt, MAX(end_date) as latest FROM dwd_income",
                'dwd_balancesheet':  "SELECT COUNT(*) as cnt, MAX(end_date) as latest FROM dwd_balancesheet",
                'dwd_cashflow':      "SELECT COUNT(*) as cnt, MAX(end_date) as latest FROM dwd_cashflow",
                'dwd_index_daily':   "SELECT COUNT(*) as cnt, MAX(trade_date) as latest FROM dwd_index_daily",
                'dwd_stock_info':    "SELECT COUNT(*) as cnt, MAX(list_date) as latest FROM dwd_stock_info",
                'dwd_trade_calendar':"SELECT COUNT(*) as cnt, MAX(trade_date) as latest FROM dwd_trade_calendar WHERE is_open = TRUE",
            }
            for table, query in table_queries.items():
                try:
                    row = db.execute(query).fetchone()
                    latest = row[1] if row and row[1] else None
                    if latest and hasattr(latest, 'strftime'):
                        latest = latest.strftime('%Y-%m-%d')
                    tables_status[table] = {
                        'count':  row[0] if row else 0,
                        'latest': latest,
                    }
                except Exception as e:
                    tables_status[table] = {'count': 0, 'latest': None, 'error': str(e)}

            return jsonify({
                'success':   True,
                'data':      tables_status,
                'timestamp': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            })
        finally:
            db.close()

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@data_update_bp.route('/update', methods=['POST'])
def trigger_update():
    """触发数据更新，立即返回 task_id，前端轮询进度"""
    try:
        data = request.get_json() or {}

        data_type = data.get('data_type')
        if not data_type:
            return jsonify({'success': False, 'error': '缺少 data_type 参数'}), 400

        # 日期格式标准化：YYYY-MM-DD -> YYYYMMDD
        start_date = _normalize_date(data.get('start_date') or '')
        end_date   = _normalize_date(data.get('end_date') or '')
        start_date = start_date or None
        end_date   = end_date   or None

        ts_code    = data.get('ts_code')
        index_code = data.get('index_code')
        workers    = int(data.get('workers', 4))
        source     = data.get('source', 'tushare')

        # ── 去重：同类型任务运行中时拒绝重复启动 ──────────────────────
        with _tasks_lock:
            existing_task_id = _running_types.get(data_type)
            if existing_task_id:
                existing_task = update_tasks.get(existing_task_id, {})
                if existing_task.get('status') == 'running':
                    return jsonify({
                        'success': False,
                        'error': f'任务 {data_type} 已在运行中，请等待完成后再试',
                        'running_task_id': existing_task_id,
                        'progress': existing_task.get('progress', 0),
                        'message': existing_task.get('message', ''),
                    }), 409

            task_id = f"update_{datetime.now().strftime('%Y%m%d%H%M%S')}"
            _running_types[data_type] = task_id

            # 先写入初始状态，再启动线程，确保轮询能立刻拿到状态
            update_tasks[task_id] = {
                'status':   'running',
                'progress': 0,
                'message':  '任务已接收，准备启动...',
            }
        # ── 去重结束 ──────────────────────────────────────────────────

        thread = threading.Thread(
            target=run_update_task,
            args=(task_id, data_type, start_date, end_date,
                  ts_code, index_code, workers, source),
            daemon=True,
        )
        thread.start()

        return jsonify({
            'success': True,
            'task_id': task_id,
            'message': f'更新任务已启动: {data_type}',
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@data_update_bp.route('/task/<task_id>', methods=['GET'])
def get_task_status(task_id):
    """查询任务进度"""
    with _tasks_lock:
        task = update_tasks.get(task_id)

    if task is not None:
        return jsonify({'success': True, 'data': task})
    return jsonify({'success': False, 'error': '任务不存在或已过期'}), 404


@data_update_bp.route('/log', methods=['GET'])
def get_update_log():
    """获取更新日志最后 N 行"""
    try:
        lines    = int(request.args.get('lines', 100))
        date_str = request.args.get('date', datetime.now().strftime('%Y%m%d'))
        log_path = get_log_path(date_str)

        if os.path.exists(log_path):
            with open(log_path, 'r', encoding='utf-8') as f:
                all_lines = f.readlines()
            log_lines = all_lines[-lines:] if len(all_lines) > lines else all_lines
            content   = ''.join(log_lines)
            num_lines = len(log_lines)
        else:
            content   = f'日志文件不存在: {log_path}'
            num_lines = 0

        return jsonify({
            'success':  True,
            'log':      content,
            'lines':    num_lines,
            'log_path': log_path,
        })

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@data_update_bp.route('/indices', methods=['GET'])
def get_default_indices():
    """获取默认指数列表"""
    return jsonify({'success': True, 'data': DWDFetcher.DEFAULT_INDICES})


@data_update_bp.route('/calendar/<table_name>', methods=['GET'])
def get_table_calendar(table_name):
    """获取指定表的数据日历统计"""
    date_col_map = {
        'dwd_daily_price':    'trade_date',
        'dwd_daily_basic':    'trade_date',
        'dwd_adj_factor':     'trade_date',
        'dwd_income':         'end_date',
        'dwd_balancesheet':   'end_date',
        'dwd_cashflow':       'end_date',
        'dwd_index_daily':    'trade_date',
        'dwd_trade_calendar': 'trade_date',
        'dwd_stock_info':     'list_date',
    }
    date_col = date_col_map.get(table_name, 'trade_date')

    try:
        db = duckdb.connect(DB_PATH)
        try:
            rows = db.execute(f"""
                SELECT {date_col}, COUNT(*) as cnt
                FROM {table_name}
                GROUP BY {date_col}
                ORDER BY {date_col}
            """).fetchall()

            year_stats, month_stats, day_stats = {}, {}, {}
            for row in rows:
                d = row[0].strftime('%Y-%m-%d') if hasattr(row[0], 'strftime') else str(row[0])
                cnt = row[1]
                year_stats[d[:4]]  = year_stats.get(d[:4], 0)  + cnt
                month_stats[d[:7]] = month_stats.get(d[:7], 0) + cnt
                day_stats[d]       = cnt

            table_names = {
                'dwd_daily_price':    '日线行情',
                'dwd_daily_basic':    '每日指标',
                'dwd_adj_factor':     '复权因子',
                'dwd_income':         '利润表',
                'dwd_balancesheet':   '资产负债表',
                'dwd_cashflow':       '现金流量表',
                'dwd_index_daily':    '指数日线',
                'dwd_stock_info':     '股票基础信息',
                'dwd_trade_calendar': '交易日历',
            }
            return jsonify({
                'success': True,
                'data': {
                    'table_name':  table_name,
                    'table_desc':  table_names.get(table_name, table_name),
                    'date_col':    date_col,
                    'year_stats':  [{'period': k, 'count': v} for k, v in sorted(year_stats.items(),  reverse=True)],
                    'month_stats': [{'period': k, 'count': v} for k, v in sorted(month_stats.items(), reverse=True)],
                    'day_stats':   [{'period': k, 'count': v} for k, v in sorted(day_stats.items(),   reverse=True)],
                },
            })
        finally:
            db.close()

    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500