"""
Flask API - Backtest回测接口
"""
import sys
import os
import re
import importlib
import importlib.util
from flask import Blueprint, request, jsonify, Response
from datetime import datetime
from pathlib import Path
import pandas as pd
import numpy as np
import io
import threading

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backtest.engine import BacktestEngine
from strategies.registry import Registry
from database.db_manager import DatabaseManager

backtest_bp = Blueprint('backtest', __name__, url_prefix='/api/backtest')

batch_tasks = {}

# 统一数据库路径常量，避免 DatabaseManager 单例路径不一致
DB_PATH = str(Path(__file__).parent.parent / 'data' / 'Astock3.duckdb')
STRATEGIES_DIR = Path(__file__).parent.parent / 'strategies'
PROJECT_ROOT = Path(__file__).parent.parent


def get_db():
    """获取数据库连接（主线程/请求上下文使用单例）"""
    return DatabaseManager(DB_PATH)


def get_db_for_thread():
    """
    为后台线程创建独立的数据库连接实例。

    DatabaseManager 是单例，多线程共用同一个 duckdb.Connection 对象会产生
    竞态冲突（主线程请求和后台回测线程同时读写同一 conn），导致"保存回测
    结果失败"。后台线程必须使用此函数获取独立连接，不与主线程单例共享。
    """
    import duckdb as _duckdb
    from database.schema import create_tables as _create_tables
    # 绕过单例，直接构造新实例
    inst = object.__new__(DatabaseManager)
    inst._initialized = False
    inst.db_path = Path(DB_PATH)
    inst.db_path.parent.mkdir(parents=True, exist_ok=True)

    # WAL 安全检查（与 DatabaseManager.__init__ 保持一致）
    wal_path = inst.db_path.parent / (inst.db_path.name + '.wal')
    if wal_path.exists():
        try:
            _test = _duckdb.connect(str(inst.db_path))
            _test.execute("CHECKPOINT")
            _test.close()
        except Exception as wal_err:
            print(f"[WAL/thread] WAL 文件疑似损坏 ({wal_err})，删除后重建: {wal_path}")
            try:
                wal_path.unlink()
            except Exception:
                pass

    inst.conn = _duckdb.connect(str(inst.db_path))
    _create_tables(inst.conn)
    inst._initialized = True
    return inst


def clean_df_for_json(df):
    """清理DataFrame以便JSON序列化"""
    if df is None or df.empty:
        return []
    df = df.copy()
    for col in df.columns:
        if df[col].dtype == 'object' or str(df[col].dtype).startswith('datetime'):
            df[col] = df[col].apply(lambda x: None if pd.isna(x) else (x.strftime('%Y-%m-%d') if hasattr(x, 'strftime') else x))
        elif pd.api.types.is_numeric_dtype(df[col]):
            df[col] = df[col].replace({np.nan: None})
    return df.to_dict('records')


# ---------------------------------------------------------------------------
# 策略解析：三路 fallback
# ---------------------------------------------------------------------------

def _resolve_strategy_class(strategy_name: str):
    """
    三路 fallback 解析策略类：
      1. Registry 内存缓存 / 按文件名加载
      2. 递归扫描 strategies/ 目录，按 @register(name=...) 注册名匹配
      3. 数据库 class_path 字段动态 import

    Returns:
        策略类 or None
    """
    registry = Registry()

    # 路径1：registry.get() 内部已含内存缓存 + 文件名精确匹配 + 注册名扫描 + class_path
    cls = registry.get(strategy_name)
    if cls is not None:
        return cls

    # 路径2（额外保障）：直接递归扫描，宽松匹配
    for py_file in STRATEGIES_DIR.rglob('*.py'):
        if py_file.name.startswith('_'):
            continue
        try:
            source = py_file.read_text(encoding='utf-8', errors='ignore')
        except Exception:
            continue
        if strategy_name not in source:
            continue

        rel = py_file.relative_to(PROJECT_ROOT)
        module_name = '.'.join(rel.with_suffix('').parts)

        try:
            if module_name in sys.modules:
                module = sys.modules[module_name]
            else:
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if not spec or not spec.loader:
                    continue
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)

            # 尝试导入基类，失败时退化为只检查 bt.Strategy 子类
            try:
                from strategies.base.framework_strategy import BaseStrategy
                from strategies.base.portfolio_strategy import PortfolioStrategy
                base_classes = (BaseStrategy, PortfolioStrategy)
                skip_names = ('BaseStrategy', 'FrameworkStrategy', 'PortfolioStrategy')
            except Exception:
                import backtrader as bt
                base_classes = (bt.Strategy,)
                skip_names = ()

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and attr_name not in skip_names
                    and issubclass(attr, base_classes)
                ):
                    registry._classes[strategy_name] = attr
                    return attr
        except Exception:
            continue

    return None


@backtest_bp.route('/strategies', methods=['GET'])
def get_strategies():
    """获取可用策略列表

    GET /api/backtest/strategies
    GET /api/backtest/strategies?detail=true   → 返回完整元数据对象列表

    Returns: JSON格式策略列表（从数据库获取）
    """
    try:
        detail = request.args.get('detail', 'false').lower() == 'true'
        db = get_db()
        db_strategies = db.list_strategies(status='active')

        if detail:
            # 返回完整对象列表供前端使用
            result = []
            for s in db_strategies:
                result.append({
                    'name': s.get('name', ''),
                    'description': s.get('description', ''),
                    'threshold_required': bool(s.get('threshold_required', True)),
                    'min_data_days': s.get('min_data_days', 60),
                    'version': s.get('version', ''),
                    'author': s.get('author', ''),
                    'status': s.get('status', 'active'),
                    'class_path': s.get('class_path', ''),
                })
            return jsonify({'strategies': sorted(result, key=lambda x: x['name'])})
        else:
            # 向后兼容：返回名称列表
            strategy_list = [s['name'] for s in db_strategies]
            return jsonify({'strategies': sorted(strategy_list)})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@backtest_bp.route('/strategies/<path:strategy_name>', methods=['DELETE'])
def delete_strategy(strategy_name):
    """删除策略

    DELETE /api/backtest/strategies/<strategy_name>

    Returns: {
        "success": true
    }
    """
    try:
        registry = Registry()
        # soft_delete 内部已检查数据库，不再依赖内存 _metadata
        success = registry.soft_delete(strategy_name)
        if success:
            return jsonify({'success': True})
        else:
            return jsonify({'error': f'策略 {strategy_name} 不存在'}), 404
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@backtest_bp.route('/browse-strategies', methods=['GET'])
def browse_strategies():
    """浏览 strategies/ 目录下的可注册策略文件

    GET /api/backtest/browse-strategies

    Returns: {
        "files": [
            {
                "path": "strategies/b1/b1_strategy.py",   # 相对项目根目录
                "name": "b1_strategy.py",
                "size": 1234,
                "has_register": true,                      # 是否含 @register(
                "is_jukuan": false,                        # 是否是聚宽格式（不可直接import）
                "registered_name": "天宫B1策略v2.1",       # @register(name=...) 里的名字（如能解析）
                "already_registered": false               # 是否已在数据库中
            },
            ...
        ]
    }
    """
    try:
        BASE_DIR = STRATEGIES_DIR / 'base'
        db = get_db()
        # 只有 active 状态的策略才算"已注册"，archived/deleted 策略应允许重新注册
        registered_names = {s['name'] for s in db.list_strategies(status='active') or []}

        files = []
        for py_file in sorted(STRATEGIES_DIR.rglob('*.py')):
            if py_file.name.startswith('_'):
                continue
            # 跳过 base/ 目录
            try:
                py_file.relative_to(BASE_DIR)
                continue
            except ValueError:
                pass

            try:
                source = py_file.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue

            has_register = '@register(' in source
            is_jukuan = 'from jqdata import' in source or 'import jqdata' in source

            # 尝试提取注册名
            registered_name = None
            import re
            m = re.search(r'@register\s*\(.*?name\s*=\s*["\'](.+?)["\']', source, re.DOTALL)
            if m:
                registered_name = m.group(1)

            rel_path = str(py_file.relative_to(PROJECT_ROOT))
            files.append({
                'path': rel_path,
                'name': py_file.name,
                'size': py_file.stat().st_size,
                'has_register': has_register,
                'is_jukuan': is_jukuan,
                'registered_name': registered_name,
                'already_registered': registered_name in registered_names if registered_name else False,
            })

        return jsonify({'files': files})
    except Exception as e:
        import traceback
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@backtest_bp.route('/register-strategy-file', methods=['POST'])
def register_strategy_file():
    """注册指定路径的策略文件

    POST /api/backtest/register-strategy-file
    Body: {"path": "strategies/b1/b1_strategy.py"}

    Returns: {
        "success": true,
        "name": "天宫B1策略v2.1",
        "message": "..."
    }
    """
    data = request.get_json()
    if not data or not data.get('path'):
        return jsonify({'error': '缺少 path 参数'}), 400

    rel_path = data['path']
    py_file = PROJECT_ROOT / rel_path

    if not py_file.exists() or not py_file.suffix == '.py':
        return jsonify({'error': f'文件不存在或不是 .py 文件: {rel_path}'}), 400

    # 安全检查：确保在 strategies/ 目录下
    try:
        py_file.resolve().relative_to(STRATEGIES_DIR.resolve())
    except ValueError:
        return jsonify({'error': '不允许注册 strategies/ 目录以外的文件'}), 403

    try:
        source = py_file.read_text(encoding='utf-8', errors='ignore')
    except Exception as e:
        return jsonify({'error': f'读取文件失败: {e}'}), 500

    is_jukuan = 'from jqdata import' in source or 'import jqdata' in source

    if is_jukuan:
        _register_jukuan_metadata(py_file, source)
        import re
        m = re.search(r'@register\s*\(.*?name\s*=\s*["\'](.+?)["\']', source, re.DOTALL)
        name = m.group(1) if m else py_file.stem
        return jsonify({'success': True, 'name': name, 'message': f'聚宽策略元数据已注册: {name}'})

    rel = py_file.relative_to(PROJECT_ROOT)
    module_name = '.'.join(rel.with_suffix('').parts)

    # 强制重新加载让 @register 装饰器重新执行
    if module_name in sys.modules:
        del sys.modules[module_name]

    spec = importlib.util.spec_from_file_location(module_name, py_file)
    if not spec or not spec.loader:
        return jsonify({'error': f'无法加载模块: {rel_path}'}), 500

    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception as e:
        import traceback
        return jsonify({'error': f'导入失败: {e}', 'traceback': traceback.format_exc()}), 500

    # 确保数据库中所有 archived 状态的策略被复活为 active
    # （应对"删除后重新注册"场景：save_strategy 的 UPSERT 可能不会覆盖 status 字段）
    try:
        _db_reactivate = get_db()
        _all_in_file = []
        import re as _re
        _src = py_file.read_text(encoding='utf-8', errors='ignore')
        for _m in _re.finditer(r'@register\s*\(.*?name\s*=\s*["\'](.+?)["\']', _src, _re.DOTALL):
            _all_in_file.append(_m.group(1))
        if not _all_in_file:
            _all_in_file.append(py_file.stem)
        for _sname in _all_in_file:
            _info = _db_reactivate.get_strategy(_sname)
            if _info and _info.get('status') != 'active':
                _db_reactivate.update_strategy_status(_sname, 'active')
    except Exception:
        pass  # 复活失败不影响主流程

    # 重置 Registry 单例，与数据库重新同步
    from strategies.registry import Registry
    Registry._initialized = False
    Registry._instance = None
    Registry()

    db = get_db()
    db_strategies = db.list_strategies(status='active')
    strategy_list = [
        {
            'name': s.get('name', ''),
            'description': s.get('description', ''),
            'threshold_required': bool(s.get('threshold_required', True)),
            'min_data_days': s.get('min_data_days', 60),
            'version': s.get('version', ''),
            'author': s.get('author', ''),
            'status': s.get('status', 'active'),
            'class_path': s.get('class_path', ''),
        }
        for s in db_strategies
    ]

    return jsonify({
        'success': True,
        'name': py_file.stem,
        'message': f'策略文件 {py_file.name} 注册成功',
        'strategies': sorted(strategy_list, key=lambda x: x['name'])
    })


@backtest_bp.route('/register-strategies', methods=['POST'])
def register_strategies():
    """扫描并注册所有策略文件（含子目录，跳过base/抽象基类）

    POST /api/backtest/register-strategies

    Returns: {
        "success": true,
        "loaded": N,
        "failed": N,
        "strategies": [...]
    }
    """
    try:
        loaded = []
        failed = []

        BASE_DIR = STRATEGIES_DIR / 'base'

        for py_file in STRATEGIES_DIR.rglob('*.py'):
            if py_file.name.startswith('_'):
                continue

            # ✅ 修复1：跳过 strategies/base/ 目录（全是抽象基类，无 @register）
            try:
                py_file.relative_to(BASE_DIR)
                continue  # 在 base/ 下，跳过
            except ValueError:
                pass  # 不在 base/ 下，继续

            try:
                source = py_file.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue

            # ✅ 修复2：只处理含 @register( 的文件，跳过纯工具/模板文件
            if '@register(' not in source:
                continue

            is_jukuan = 'from jqdata import' in source or 'import jqdata' in source

            if is_jukuan:
                # 聚宽文件：只注册元数据，不实际 import
                _register_jukuan_metadata(py_file, source)
                loaded.append(py_file.name + ' [元数据]')
                continue

            rel = py_file.relative_to(PROJECT_ROOT)
            module_name = '.'.join(rel.with_suffix('').parts)

            # 强制重新加载，让 @register 装饰器重新执行
            if module_name in sys.modules:
                del sys.modules[module_name]

            spec = importlib.util.spec_from_file_location(module_name, py_file)
            if not spec or not spec.loader:
                failed.append(py_file.name)
                continue

            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
                loaded.append(py_file.name)
                # 确保本文件中所有 archived 策略被复活为 active
                try:
                    import re as _re
                    _db_r = get_db()
                    for _m in _re.finditer(r'@register\s*\(.*?name\s*=\s*["\'](.+?)["\']', source, _re.DOTALL):
                        _sname = _m.group(1)
                        _info = _db_r.get_strategy(_sname)
                        if _info and _info.get('status') != 'active':
                            _db_r.update_strategy_status(_sname, 'active')
                except Exception:
                    pass
            except Exception as e:
                failed.append((py_file.name, str(e)))

        # ✅ 修复3：重置 Registry 单例内存缓存，与数据库重新同步
        # @register 装饰器已将新策略写入数据库；现在让单例从数据库重新加载
        from strategies.registry import Registry
        Registry._initialized = False
        Registry._instance = None
        # 重新初始化（触发 _load_from_database）
        Registry()

        # 从数据库读取最新策略列表
        db = get_db()
        db_strategies = db.list_strategies(status='active')
        strategy_list = []
        for s in db_strategies:
            strategy_list.append({
                'name': s.get('name', ''),
                'description': s.get('description', ''),
                'threshold_required': bool(s.get('threshold_required', True)),
                'min_data_days': s.get('min_data_days', 60),
                'version': s.get('version', ''),
                'author': s.get('author', ''),
                'status': s.get('status', 'active'),
                'class_path': s.get('class_path', ''),
            })

        return jsonify({
            'success': True,
            'loaded': len(loaded),
            'failed': len(failed),
            'strategies': sorted(strategy_list, key=lambda x: x['name'])
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500



def _register_jukuan_metadata(py_file: Path, source: str):
    """
    从聚宽格式文件中提取注释元数据并写入数据库。
    聚宽文件无法真正 import，但元数据可以注册。
    """
    try:
        # 尝试从文件名提取名称
        name = py_file.stem  # e.g. "天宫B1策略v2.1-聚宽"

        # 从 @register 装饰器提取（如果有）
        m = re.search(r'@register\s*\(.*?name\s*=\s*["\'](.+?)["\']', source)
        if m:
            name = m.group(1)

        # 提取 threshold
        threshold_required = True
        if 'threshold' in source.lower():
            threshold_required = True

        # 提取 min_data_days
        min_data_days = 60

        db = get_db()
        strategy_data = {
            'name': name,
            'class_path': f'strategies.jukuan.{py_file.stem}',
            'source_file': str(py_file.relative_to(PROJECT_ROOT)),
            'description': f'聚宽策略（不可直接回测）: {py_file.name}',
            'threshold_required': threshold_required,
            'min_data_days': min_data_days,
            'status': 'active',
        }
        db.save_strategy(strategy_data)

        # 同步到 Registry 内存（元数据，无类引用）
        from strategies.registry import Registry, StrategyMetadata
        registry = Registry()
        registry._metadata[name] = StrategyMetadata(
            name=name,
            threshold_required=threshold_required,
            min_data_days=min_data_days,
            description=strategy_data['description'],
        )
    except Exception as e:
        print(f"注册聚宽策略元数据失败 {py_file.name}: {e}")


def _load_strategy_files():
    """加载所有策略文件以触发 @register 装饰器（含子目录）"""
    for py_file in STRATEGIES_DIR.rglob('*.py'):
        if py_file.name.startswith('_'):
            continue
        rel = py_file.relative_to(PROJECT_ROOT)
        module_name = '.'.join(rel.with_suffix('').parts)
        if module_name in sys.modules:
            continue
        spec = importlib.util.spec_from_file_location(module_name, py_file)
        if spec and spec.loader:
            module = importlib.util.module_from_spec(spec)
            sys.modules[module_name] = module
            try:
                spec.loader.exec_module(module)
            except Exception:
                pass


@backtest_bp.route('/run', methods=['POST'])
def run_backtest():
    """运行回测

    POST /api/backtest/run
    Body: {
        "strategy_name": "天宫B1策略v2.1",
        "start_date": "20240101",
        "end_date": "20240601",
        "stock_list": ["000001", "000002"],  // optional
        "initial_capital": 100000  // optional
    }

    Returns: {
        "run_id": "abc123",
        "status": "completed",
        "metrics": {...}
    }
    """
    data = request.get_json()

    if not data:
        return jsonify({'error': '请求体不能为空'}), 400

    strategy_name = data.get('strategy_name')
    start_date = data.get('start_date')
    end_date = data.get('end_date')
    stock_list = data.get('stock_list')
    initial_capital = data.get('initial_capital', 100000.0)

    if not strategy_name:
        return jsonify({'error': '缺少strategy_name参数'}), 400
    if not start_date:
        return jsonify({'error': '缺少start_date参数'}), 400
    if not end_date:
        return jsonify({'error': '缺少end_date参数'}), 400

    try:
        from datetime import date as _date
        def _parse_date(s: str) -> _date:
            """YYYYMMDD 或 YYYY-MM-DD → date 对象"""
            s = s.replace('-', '')
            return _date(int(s[:4]), int(s[4:6]), int(s[6:8]))

        start_date_obj = _parse_date(start_date)
        end_date_obj   = _parse_date(end_date)

        engine_params = {
            'initial_cash': initial_capital,
            'start_date': start_date_obj,
            'end_date': end_date_obj,
        }
        if stock_list:
            engine_params['stock_list'] = stock_list

        engine = BacktestEngine(**engine_params)

        # 使用三路 fallback 解析策略类
        strategy_class = _resolve_strategy_class(strategy_name)

        if strategy_class is None:
            return jsonify({'error': f'策略 {strategy_name} 未找到，请先注册策略'}), 404

        engine.add_strategy(strategy_class)

        if not stock_list:
            db = get_db()
            stock_df = db.conn.execute("""
                SELECT DISTINCT symbol FROM dwd_stock_info
                WHERE delist_date IS NULL
            """).fetchdf()
            stock_list = stock_df['symbol'].tolist() if not stock_df.empty else []

        added = 0
        for stock_code in stock_list:
            try:
                engine.add_data_from_db(stock_code, fromdate=start_date_obj, todate=end_date_obj)
                added += 1
            except Exception as e:
                print(f"添加股票 {stock_code} 数据失败: {e}")
                continue

        if added == 0:
            return jsonify({'error': f'所选股票在 {start_date}~{end_date} 期间无有效数据'}), 400

        result = engine.run(strategy_name=strategy_name, save_results=True)

        run_id = engine.get_run_id()
        metrics = result.get('metrics', {})

        return jsonify({
            'run_id': run_id,
            'status': 'completed',
            'metrics': {
                'total_return':          float(result.get('total_return', 0) or 0),
                'annual_return':         float(metrics.get('annualized_return', 0) or 0),
                'sharpe_ratio':          float(metrics.get('sharpe_ratio', 0) or 0),
                'sortino_ratio':         float(metrics.get('sortino_ratio', 0) or 0),
                'calmar_ratio':          float(metrics.get('calmar_ratio', 0) or 0),
                'max_drawdown':          float(metrics.get('max_drawdown', 0) or 0),
                'max_drawdown_duration': int(metrics.get('max_drawdown_duration', 0) or 0),
                'volatility':            float(metrics.get('volatility', 0) or 0),
                'win_rate':              float(metrics.get('win_rate', 0) or 0),
                'profit_loss_ratio':     float(metrics.get('profit_loss_ratio', 0) or 0),
                'total_trades':          int(metrics.get('total_trades', 0) or 0),
            }
        })

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@backtest_bp.route('/history', methods=['GET'])
def get_history():
    """获取回测历史

    GET /api/backtest/history?page=1&limit=10&strategy_name=xxx

    同时查询 backtest_run（单次回测）和 batch_backtest_results（批量回测），合并排序。

    Returns: {
        "runs": [...],
        "total": 100
    }
    """
    page = request.args.get('page', 1, type=int)
    limit = request.args.get('limit', 10, type=int)
    # 空字符串视为"不过滤"，与 None 等价
    strategy_name = request.args.get('strategy_name') or None

    if page < 1:
        page = 1
    if limit < 1 or limit > 100:
        limit = 10

    offset = (page - 1) * limit

    print(f"[get_history] page={page}, limit={limit}, strategy_name={strategy_name!r}")

    try:
        db = get_db()
        all_runs = []

        # ---- 工具函数：将 pandas NaT / NaN / None 统一转为 Python None ----
        def _to_str(val):
            """将 pandas Timestamp / NaT 转为字符串，None/NaT 返回 None"""
            if val is None:
                return None
            try:
                if pd.isna(val):
                    return None
            except (TypeError, ValueError):
                pass
            return str(val)

        def _to_float(val):
            if val is None:
                return None
            try:
                if pd.isna(val):
                    return None
            except (TypeError, ValueError):
                pass
            try:
                return float(val)
            except Exception:
                return None

        def _to_int(val, default=0):
            if val is None:
                return default
            try:
                if pd.isna(val):
                    return default
            except (TypeError, ValueError):
                pass
            try:
                return int(val)
            except Exception:
                return default

        # ---- 1. 查询单次回测（backtest_run 表） ----
        try:
            # 将策略名筛选下推到 SQL，避免 Python 层过滤遗漏
            if strategy_name:
                single_query = """
                    SELECT run_id, strategy_name, start_date, end_date,
                           initial_capital, status, completed_at
                    FROM backtest_run
                    WHERE strategy_name = ?
                    ORDER BY completed_at DESC NULLS LAST
                    LIMIT 200
                """
                single_df = db.conn.execute(single_query, [strategy_name]).fetchdf()
            else:
                single_query = """
                    SELECT run_id, strategy_name, start_date, end_date,
                           initial_capital, status, completed_at
                    FROM backtest_run
                    ORDER BY completed_at DESC NULLS LAST
                    LIMIT 200
                """
                single_df = db.conn.execute(single_query).fetchdf()

            print(f"[get_history] backtest_run 查询返回 {len(single_df)} 行")

            for _, row in single_df.iterrows():
                run_strategy = row.get('strategy_name') or '未知'

                # 从 backtest_performance 获取指标
                try:
                    perf_df = db.conn.execute("""
                        SELECT total_return, sharpe_ratio, max_drawdown, win_rate, total_trades
                        FROM backtest_performance
                        WHERE run_id = ?
                        LIMIT 1
                    """, [row['run_id']]).fetchdf()
                except Exception:
                    perf_df = pd.DataFrame()

                perf = perf_df.iloc[0].to_dict() if not perf_df.empty else {}

                all_runs.append({
                    'run_id': row['run_id'],
                    'type': 'single',
                    'strategy_name': run_strategy,
                    'start_date': _to_str(row.get('start_date')),
                    'end_date':   _to_str(row.get('end_date')),
                    'initial_capital': _to_float(row.get('initial_capital')),
                    'status': row.get('status') or 'completed',
                    'completed_at': _to_str(row.get('completed_at')),
                    'total_return':  _to_float(perf.get('total_return')),
                    'sharpe_ratio':  _to_float(perf.get('sharpe_ratio')),
                    'max_drawdown':  _to_float(perf.get('max_drawdown')),
                    'win_rate':      _to_float(perf.get('win_rate')),
                    'total_trades':  _to_int(perf.get('total_trades')),
                })
        except Exception as e:
            import traceback as _tb
            print(f"[get_history] 查询 backtest_run 表失败: {e}")
            print(_tb.format_exc())

        # ---- 2. 查询批量回测（batch_backtest_results 表），按 batch_id 分组 ----
        try:
            # 策略名筛选同样下推到 SQL
            if strategy_name:
                batch_query = """
                    SELECT
                        batch_id,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN strategy_name END) AS strategy_name,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN start_date END)    AS start_date,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN end_date END)      AS end_date,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN initial_capital END) AS initial_capital,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN total_stocks END)  AS total_stocks,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN valid_stocks END)  AS valid_stocks,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN total_return END)  AS avg_return,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN sharpe_ratio END)  AS avg_sharpe,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN max_drawdown END)  AS avg_max_drawdown,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN total_trades END)  AS total_trades,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN final_value END)   AS final_value,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN initial_cash END)  AS initial_cash,
                        MAX(completed_at) AS completed_at
                    FROM batch_backtest_results
                    GROUP BY batch_id
                    HAVING MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN strategy_name END) = ?
                    ORDER BY completed_at DESC NULLS LAST
                    LIMIT 50
                """
                batch_df = db.conn.execute(batch_query, [strategy_name]).fetchdf()
            else:
                batch_query = """
                    SELECT
                        batch_id,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN strategy_name END) AS strategy_name,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN start_date END)    AS start_date,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN end_date END)      AS end_date,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN initial_capital END) AS initial_capital,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN total_stocks END)  AS total_stocks,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN valid_stocks END)  AS valid_stocks,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN total_return END)  AS avg_return,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN sharpe_ratio END)  AS avg_sharpe,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN max_drawdown END)  AS avg_max_drawdown,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN total_trades END)  AS total_trades,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN final_value END)   AS final_value,
                        MAX(CASE WHEN stock_code = 'PORTFOLIO' THEN initial_cash END)  AS initial_cash,
                        MAX(completed_at) AS completed_at
                    FROM batch_backtest_results
                    GROUP BY batch_id
                    ORDER BY completed_at DESC NULLS LAST
                    LIMIT 50
                """
                batch_df = db.conn.execute(batch_query).fetchdf()

            print(f"[get_history] batch_backtest_results 查询返回 {len(batch_df)} 行")

            for _, row in batch_df.iterrows():
                batch_id = row['batch_id']

                # 从 batch_tasks 内存补充（进程未重启时有效）
                mem_params = {}
                if batch_id in batch_tasks and 'params' in batch_tasks[batch_id]:
                    mem_params = batch_tasks[batch_id]['params']

                db_strategy = _to_str(row.get('strategy_name'))
                batch_strategy = db_strategy or mem_params.get('strategy_name', '未知')

                db_start    = row.get('start_date')
                db_end      = row.get('end_date')
                db_capital  = row.get('initial_capital')
                db_total    = row.get('total_stocks')
                db_valid    = row.get('valid_stocks')

                batch_start   = (_to_str(db_start) or '')[:10] or mem_params.get('start_date')
                batch_end     = (_to_str(db_end)   or '')[:10] or mem_params.get('end_date')
                batch_capital = _to_float(db_capital) if _to_float(db_capital) is not None else mem_params.get('initial_capital')
                batch_total   = _to_int(db_total, 0)
                batch_valid   = _to_int(db_valid, 0)

                all_runs.append({
                    'run_id': batch_id,
                    'type': 'batch',
                    'strategy_name': batch_strategy,
                    'start_date':    batch_start or None,
                    'end_date':      batch_end   or None,
                    'initial_capital': batch_capital,
                    'status': 'completed',
                    'completed_at':  _to_str(row.get('completed_at')),
                    'total_return':  _to_float(row.get('avg_return')),
                    'sharpe_ratio':  _to_float(row.get('avg_sharpe')),
                    'max_drawdown':  _to_float(row.get('avg_max_drawdown')),
                    'final_value':   _to_float(row.get('final_value')),
                    'initial_cash':  _to_float(row.get('initial_cash')),
                    'total_stocks':  batch_total,
                    'valid_stocks':  batch_valid,
                    'success_count': batch_valid,
                    'success_rate':  batch_valid / batch_total if batch_total > 0 else 0,
                    'total_trades':  _to_int(row.get('total_trades'), 0),
                })
        except Exception as e:
            import traceback as _tb
            print(f"[get_history] 查询 batch_backtest_results 表失败: {e}")
            print(_tb.format_exc())

        # ---- 3. 合并后按 completed_at 降序排序（None 排最后） ----
        all_runs.sort(key=lambda x: x.get('completed_at') or '', reverse=True)

        # ---- 4. 分页 ----
        total = len(all_runs)
        paginated_runs = all_runs[offset:offset + limit]

        print(f"[get_history] 合计 {total} 条，返回第{page}页 {len(paginated_runs)} 条")

        return jsonify({
            'runs': paginated_runs,
            'total': total,
            'page': page,
            'limit': limit
        })

    except Exception as e:
        import traceback
        print(f"[get_history] 顶层异常: {e}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


def calculate_metrics_from_daily_pnl(daily_pnl_df):
    """Calculate annualized metrics from daily PnL data."""
    if daily_pnl_df is None or len(daily_pnl_df) == 0:
        return None

    daily_returns = daily_pnl_df['daily_return'].dropna().values

    if len(daily_returns) == 0:
        return None

    cumulative_return = daily_pnl_df['cumulative_return'].iloc[-1] if 'cumulative_return' in daily_pnl_df.columns else 0
    trading_days = len(daily_pnl_df)
    if trading_days > 0 and cumulative_return > -1:
        years = trading_days / 252
        annualized_return = (1 + cumulative_return) ** (1 / years) - 1 if years > 0 else 0
    else:
        annualized_return = 0

    volatility = np.std(daily_returns) * np.sqrt(252) if len(daily_returns) > 0 else 0
    sharpe_ratio = annualized_return / volatility if volatility > 0 else 0

    downside_returns = daily_returns[daily_returns < 0]
    downside_deviation = np.std(downside_returns) * np.sqrt(252) if len(downside_returns) > 0 else 0
    sortino_ratio = annualized_return / downside_deviation if downside_deviation > 0 else 0

    max_drawdown = daily_pnl_df['drawdown'].max() if 'drawdown' in daily_pnl_df.columns else 0

    return {
        'annualized_return': annualized_return,
        'volatility': volatility,
        'sharpe_ratio': sharpe_ratio,
        'sortino_ratio': sortino_ratio,
        'max_drawdown': max_drawdown
    }


def calculate_batch_metrics(results_df, daily_pnl_df=None):
    """Compute metrics from batch backtest aggregated data."""
    metrics = {
        'total_profit': 0,
        'cumulative_return': 0,
        'annualized_return': 0,
        'sharpe_ratio': 0,
        'sortino_ratio': 0,
        'calmar_ratio': 0,
        'max_drawdown': 0,
        'avg_drawdown': 0,
        'volatility': 0,
        'win_rate': 0,
        'total_trades': 0,
        'profit_loss_ratio': 0,
        'expectancy': 0,
        'expectancy_r': None,
        'best_trade': None,
        'worst_trade': None,
        'avg_profit': None,
        'avg_loss': None,
        'avg_holding_days': None
    }

    if results_df is None or results_df.empty:
        return metrics

    success_df = results_df[(results_df['status'] == 'success') & (results_df['total_trades'] > 0)]

    if success_df.empty:
        return metrics

    if 'final_value' in success_df.columns and 'initial_cash' in success_df.columns:
        metrics['total_profit'] = (success_df['final_value'] - success_df['initial_cash']).sum()

    if metrics['total_profit'] != 0:
        total_initial_cash = success_df['initial_cash'].sum() if 'initial_cash' in success_df.columns else 0
        if total_initial_cash > 0:
            metrics['cumulative_return'] = metrics['total_profit'] / total_initial_cash

    if 'annualized_return' in success_df.columns:
        val = success_df['annualized_return'].mean()
        if pd.notna(val):
            metrics['annualized_return'] = val

    if 'sharpe_ratio' in success_df.columns:
        val = success_df['sharpe_ratio'].mean()
        if pd.notna(val):
            metrics['sharpe_ratio'] = val

    if 'max_drawdown' in success_df.columns:
        val = success_df['max_drawdown'].max()
        if pd.notna(val):
            metrics['max_drawdown'] = val / 100 if val > 1 else val

    if 'max_drawdown' in success_df.columns:
        val = success_df['max_drawdown'].mean()
        if pd.notna(val):
            metrics['avg_drawdown'] = val / 100

    if 'win_rate' in success_df.columns and 'total_trades' in success_df.columns:
        total_trades = success_df['total_trades'].sum()
        if total_trades > 0:
            weighted_win_rate = (success_df['win_rate'] * success_df['total_trades']).sum() / total_trades
            if pd.notna(weighted_win_rate):
                metrics['win_rate'] = weighted_win_rate
                metrics['total_trades'] = int(total_trades)

    avg_return = metrics['annualized_return']
    avg_dd = metrics['avg_drawdown']
    if avg_dd != 0 and avg_return != 0:
        metrics['calmar_ratio'] = avg_return / avg_dd

    if 'total_return' in success_df.columns:
        val = success_df['total_return'].mean()
        if pd.notna(val):
            metrics['expectancy'] = val

    wr = metrics['win_rate']
    if wr > 0 and wr < 1:
        metrics['profit_loss_ratio'] = wr / (1 - wr)

    for k, v in metrics.items():
        if v is None:
            continue
        if isinstance(v, np.floating):
            metrics[k] = float(v)
        elif isinstance(v, np.integer):
            metrics[k] = int(v)

    return metrics


@backtest_bp.route('/<run_id>', methods=['GET'])
def get_backtest_detail(run_id):
    """获取回测详情

    GET /api/backtest/<run_id>

    Returns: {
        "run_id": "abc123",
        "trades": [...],
        "daily_pnl": [...],
        "metrics": {...}
    }
    
    如果run_id以batch_开头，则返回批量回测汇总结果
    """
    if not run_id:
        return jsonify({'error': '缺少run_id参数'}), 400

    try:
        if run_id.startswith('batch_'):
            db = get_db()
            results_df = db.get_batch_backtest_results(run_id)

            if results_df is None or results_df.empty:
                return jsonify({'error': '未找到批量回测结果'}), 404

            total_stocks = len(results_df)
            success_df = results_df[(results_df['status'] == 'success') & (results_df['total_trades'] > 0)]
            success_count = len(success_df)
            success_rate = success_count / total_stocks if total_stocks > 0 else 0
            total_trades = int(results_df['total_trades'].sum()) if 'total_trades' in results_df.columns else 0
            completed_at = results_df['completed_at'].max() if 'completed_at' in results_df.columns and results_df['completed_at'].notna().any() else None

            batch_daily_pnl_raw = db.get_batch_daily_pnl(run_id)
            if batch_daily_pnl_raw is not None and len(batch_daily_pnl_raw) > 0:
                daily_pnl_for_metrics = batch_daily_pnl_raw.copy()
                if 'total_pnl_pct' in daily_pnl_for_metrics.columns:
                    daily_pnl_for_metrics['daily_return'] = daily_pnl_for_metrics['total_pnl_pct'] / 100
            else:
                daily_pnl_for_metrics = None

            batch_metrics = calculate_batch_metrics(results_df, daily_pnl_for_metrics)
            batch_daily_pnl_transformed = []
            if batch_daily_pnl_raw is not None:
                for _, row in batch_daily_pnl_raw.iterrows():
                    batch_daily_pnl_transformed.append({
                        'date': row.get('date'),
                        'total_value': row.get('total_value'),
                        'daily_pnl': row.get('total_pnl'),
                        'daily_return': row.get('total_pnl_pct', 0) / 100 if row.get('total_pnl_pct') else 0,
                        'cumulative_return': row.get('cumulative_return', 0),
                        'benchmark_return': 0,
                        'excess_return': 0,
                        'drawdown': row.get('drawdown', 0),
                        'cash': 0,
                        'market_value': 0
                    })
            batch_daily_pnl = clean_df_for_json(pd.DataFrame(batch_daily_pnl_transformed)) if batch_daily_pnl_transformed else []

            return jsonify({
                'run_id': run_id,
                'type': 'batch',
                'status': 'completed',
                'total_stocks': total_stocks,
                'success_count': success_count,
                'success_rate': success_rate,
                'fail_count': total_stocks - success_count,
                'total_trades': total_trades,
                'completed_at': str(completed_at) if completed_at else None,
                'metrics': batch_metrics,
                'stocks': clean_df_for_json(results_df),
                'daily_pnl': batch_daily_pnl
            })

        db = get_db()
        result = db.get_backtest_result(run_id)

        trades = clean_df_for_json(result.get('trades'))
        daily_pnl = clean_df_for_json(result.get('daily_pnl'))

        perf_df = result.get('performance')
        metrics = {}
        if perf_df is not None and not perf_df.empty:
            perf_row = perf_df.iloc[0].to_dict()
            perf_row.pop('run_id', None)
            metrics = {k: v for k, v in perf_row.items() if v is not None and not pd.isna(v)}

        return jsonify({
            'run_id': run_id,
            'type': 'single',
            'trades': trades,
            'daily_pnl': daily_pnl,
            'metrics': metrics
        })

    except Exception as e:
        import traceback
        print(f"获取回测详情失败: {e}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@backtest_bp.route('/<run_id>/trades', methods=['GET'])
def get_backtest_trades(run_id):
    """获取回测交易记录"""
    if not run_id:
        return jsonify({'error': '缺少run_id参数'}), 400

    try:
        db = get_db()
        result = db.get_backtest_result(run_id)
        trades = clean_df_for_json(result.get('trades'))
        return jsonify({'trades': trades})
    except Exception as e:
        return jsonify({'error': str(e)}), 500


@backtest_bp.route('/<run_id>/export', methods=['GET'])
def export_backtest(run_id):
    """导出回测数据"""
    if not run_id:
        return jsonify({'error': '缺少run_id参数'}), 400

    format_type = request.args.get('format', 'csv').lower()
    if format_type not in ('csv', 'json'):
        return jsonify({'error': '不支持的格式，请使用 csv 或 json'}), 400

    try:
        db = get_db()
        result = db.get_backtest_result(run_id)

        run_info = db.conn.execute("""
            SELECT run_id, strategy_name, start_date, end_date, 
                   initial_capital, status, completed_at
            FROM backtest_run WHERE run_id = ?
        """, [run_id]).fetchdf()

        if run_info.empty:
            return jsonify({'error': '回测记录不存在'}), 404

        date_str = datetime.now().strftime('%Y%m%d')
        filename = f'backtest_{run_id}_{date_str}'

        if format_type == 'csv':
            trades_df = result.get('trades')
            if trades_df is None or trades_df.empty:
                return jsonify({'error': '没有交易记录可导出'}), 404

            csv_df = trades_df.copy()
            for col in csv_df.columns:
                if csv_df[col].dtype == 'object' or str(csv_df[col].dtype).startswith('datetime'):
                    csv_df[col] = csv_df[col].apply(
                        lambda x: '' if pd.isna(x) else (x.strftime('%Y-%m-%d') if hasattr(x, 'strftime') else x)
                    )
                elif pd.api.types.is_numeric_dtype(csv_df[col]):
                    csv_df[col] = csv_df[col].fillna('').replace({np.nan: ''})

            csv_output = io.StringIO()
            csv_df.to_csv(csv_output, index=False, encoding='utf-8-sig')
            csv_content = csv_output.getvalue()

            return Response(
                csv_content,
                mimetype='text/csv',
                headers={
                    'Content-Disposition': f'attachment; filename={filename}.csv',
                    'Content-Type': 'text/csv; charset=utf-8-sig'
                }
            )

        else:
            trades = clean_df_for_json(result.get('trades'))
            daily_pnl = clean_df_for_json(result.get('daily_pnl'))

            perf_df = result.get('performance')
            metrics = {}
            if perf_df is not None and not perf_df.empty:
                perf_row = perf_df.iloc[0].to_dict()
                sensitive_fields = {'run_id'}
                metrics = {k: v for k, v in perf_row.items()
                           if v is not None and not pd.isna(v) and k not in sensitive_fields}

            export_data = {
                'run_id': run_id,
                'strategy_name': run_info.iloc[0]['strategy_name'],
                'start_date': str(run_info.iloc[0]['start_date']),
                'end_date': str(run_info.iloc[0]['end_date']),
                'initial_capital': float(run_info.iloc[0]['initial_capital']) if run_info.iloc[0]['initial_capital'] else None,
                'status': run_info.iloc[0]['status'],
                'completed_at': run_info.iloc[0]['completed_at'].strftime('%Y-%m-%d %H:%M:%S') if run_info.iloc[0]['completed_at'] else None,
                'metrics': metrics,
                'trades': trades,
                'daily_pnl': daily_pnl
            }

            json_output = io.StringIO()
            import json as json_module
            json_module.dump(export_data, json_output, ensure_ascii=False, indent=2)
            json_content = json_output.getvalue()

            return Response(
                json_content,
                mimetype='application/json',
                headers={
                    'Content-Disposition': f'attachment; filename={filename}.json',
                    'Content-Type': 'application/json; charset=utf-8'
                }
            )

    except Exception as e:
        return jsonify({'error': str(e)}), 500


@backtest_bp.route('/batch-run', methods=['POST'])
def batch_run():
    """触发批量回测任务"""
    data = request.get_json()

    if not data:
        return jsonify({'error': '请求体不能为空'}), 400

    strategy_name = data.get('strategy_name')
    start_date = data.get('start_date')
    end_date = data.get('end_date')
    stock_list = data.get('stock_list')
    initial_capital = data.get('initial_capital', 100000.0)
    param_grid = data.get('param_grid')

    if not strategy_name:
        return jsonify({'error': '缺少strategy_name参数'}), 400
    if not start_date:
        return jsonify({'error': '缺少start_date参数'}), 400
    if not end_date:
        return jsonify({'error': '缺少end_date参数'}), 400

    task_id = f"batch_{datetime.now().strftime('%Y%m%d%H%M%S')}"

    batch_tasks[task_id] = {
        'status': 'pending',
        'progress': 0,
        'message': '任务已创建',
        'started_at': datetime.now().isoformat(),
        'completed_at': None,
        'error_message': None,
        'params': {
            'strategy_name': strategy_name,
            'start_date': start_date,
            'end_date': end_date,
            'stock_list': stock_list,
            'initial_capital': initial_capital,
            'param_grid': param_grid
        }
    }

    thread = threading.Thread(
        target=run_batch_task,
        args=(task_id, strategy_name, start_date, end_date, stock_list, initial_capital, param_grid)
    )
    thread.daemon = True
    thread.start()

    return jsonify({'task_id': task_id, 'status': 'pending'})


def run_batch_task(task_id: str, strategy_name: str, start_date: str, end_date: str,
                   stock_list: list = None, initial_capital: float = 100000.0,
                   param_grid: dict = None):
    """后台运行批量回测任务"""
    from backtest.strategy_backtest.batch_backtest_portfolio import (
        get_stock_list_from_db, run_portfolio_backtest
    )

    def _update(pct: int, msg: str):
        """统一进度更新，确保线程安全写入"""
        batch_tasks[task_id]['progress'] = pct
        batch_tasks[task_id]['message'] = msg

    try:
        batch_tasks[task_id]['status'] = 'running'
        batch_tasks[task_id]['started_at'] = datetime.now().isoformat()
        _update(5, '加载股票列表...')

        if stock_list is None or len(stock_list) == 0:
            stocks_df = get_stock_list_from_db()
            stocks = [{'code': row['code'], 'name': row['name']} for _, row in stocks_df.iterrows()]
        else:
            db = get_db_for_thread()
            stocks = []
            for code in stock_list:
                try:
                    result = db.conn.execute(
                        "SELECT name FROM dwd_stock_info WHERE symbol = ?",
                        [code]
                    ).fetchone()
                    if result:
                        stocks.append({'code': code, 'name': result[0]})
                    else:
                        stocks.append({'code': code, 'name': code})
                except Exception:
                    stocks.append({'code': code, 'name': code})

        total_stocks = len(stocks)
        _update(8, f'已获取 {total_stocks} 只股票，解析策略...')

        if param_grid:
            _update(10, '运行参数扫描...')
            batch_tasks[task_id]['status'] = 'completed'
            _update(100, '参数扫描完成')
            batch_tasks[task_id]['completed_at'] = datetime.now().isoformat()
            return

        # 三路 fallback 解析策略类
        strategy_class = _resolve_strategy_class(strategy_name)
        if strategy_class is None:
            batch_tasks[task_id]['status'] = 'failed'
            batch_tasks[task_id]['message'] = f'策略 {strategy_name} 未找到，请先注册策略'
            batch_tasks[task_id]['error_message'] = f'策略 {strategy_name} 未找到'
            batch_tasks[task_id]['completed_at'] = datetime.now().isoformat()
            return

        db = get_db_for_thread()
        threshold = 8.0

        _update(10, f'开始批量加载数据并运行回测 ({total_stocks}只股票)...')

        # progress_callback 将 run_portfolio_backtest 内部进度（10-95）
        # 映射到任务总进度（10-90）
        def _portfolio_progress(pct: int, msg: str):
            # pct: 10~95 from run_portfolio_backtest -> mapped to 10~90
            mapped = 10 + int((pct - 10) / 85 * 80)
            _update(min(mapped, 90), msg)

        result = run_portfolio_backtest(
            stocks=stocks,
            strategy_class=strategy_class,
            start_date=start_date,
            end_date=end_date,
            initial_cash=initial_capital,
            threshold=threshold,
            progress_callback=_portfolio_progress,
        )

        # ---- 检查回测是否真正成功 ----
        if result.get('status') != 'success':
            err_msg = result.get('error', '回测返回非success状态')
            tb = result.get('traceback', '')
            batch_tasks[task_id]['status'] = 'failed'
            batch_tasks[task_id]['error_message'] = err_msg
            batch_tasks[task_id]['completed_at'] = datetime.now().isoformat()
            print(f"[run_batch_task] 回测失败 task_id={task_id}: {err_msg}")
            if tb:
                print(tb)
            return

        _update(92, '保存回测结果...')

        portfolio_result = {
            'code': 'PORTFOLIO',
            'name': '投资组合',
            'status': 'success',
            'total_return': float(result.get('total_return', 0)) if result.get('total_return') is not None else 0,
            'annualized_return': float(result.get('annualized_return', 0)) if result.get('annualized_return') is not None else 0,
            'max_drawdown': float(result.get('max_drawdown', 0)) if result.get('max_drawdown') is not None else 0,
            'sharpe_ratio': float(result.get('sharpe_ratio', 0)) if result.get('sharpe_ratio') is not None else 0,
            'win_rate': float(result.get('win_rate', 0)) if result.get('win_rate') is not None else 0,
            'total_trades': int(result.get('total_trades', 0)) if result.get('total_trades') is not None else 0,
            'final_value': float(result.get('final_value', 0)) if result.get('final_value') is not None else 0,
            'initial_cash': float(result.get('initial_cash', 0)) if result.get('initial_cash') is not None else 0,
            'error': result.get('error'),
            # ── 批次元数据（持久化到DB，history接口不再依赖内存batch_tasks） ──
            'strategy_name': strategy_name,
            'start_date': start_date,
            'end_date': end_date,
            'initial_capital': initial_capital,
            'total_stocks': total_stocks,
            'valid_stocks': int(result.get('valid_stocks', 0)) if result.get('valid_stocks') is not None else 0,
        }
        db.save_batch_backtest_result(task_id, portfolio_result)

        if 'daily_values' in result and 'daily_dates' in result:
            daily_records = []
            initial_value = float(result.get('initial_cash', 0)) if result.get('initial_cash') else 0
            peak_value = initial_value
            cumulative_return = 0
            max_drawdown = 0

            for date_str, value in zip(result['daily_dates'], result['daily_values']):
                if not isinstance(value, (int, float)) or not np.isfinite(value):
                    continue
                date = date_str[:10] if 'T' in date_str else date_str
                pnl = value - initial_value
                cumulative_return = (value - initial_value) / initial_value if initial_value > 0 else 0

                if value > peak_value:
                    peak_value = value
                drawdown = (peak_value - value) / peak_value if peak_value > 0 else 0
                if drawdown > max_drawdown:
                    max_drawdown = drawdown

                daily_records.append({
                    'date': date,
                    'total_value': float(value),
                    'total_pnl': float(pnl),
                    'total_pnl_pct': float((pnl / value * 100) if value > 0 else 0),
                    'cumulative_return': float(cumulative_return),
                    'drawdown': float(drawdown),
                    'positions': None
                })

            if daily_records:
                db.save_batch_daily_pnl(task_id, daily_records)
                print(f"每日汇总已保存: {len(daily_records)}天")

        batch_tasks[task_id]['status'] = 'completed'
        batch_tasks[task_id]['progress'] = 100
        batch_tasks[task_id]['message'] = f'批量回测完成 (有效股票{result.get("valid_stocks", 0)}只 / 共{total_stocks}只)'
        batch_tasks[task_id]['completed_at'] = datetime.now().isoformat()

    except Exception as e:
        import traceback
        batch_tasks[task_id]['status'] = 'failed'
        batch_tasks[task_id]['error_message'] = str(e)
        batch_tasks[task_id]['completed_at'] = datetime.now().isoformat()
        print(f"批量回测任务失败: {task_id}")
        print(traceback.format_exc())
    finally:
        # 关闭线程独立连接，避免连接泄漏
        try:
            if 'db' in dir() and db is not None:
                db.conn.close()
        except Exception:
            pass


@backtest_bp.route('/batch-task/<task_id>', methods=['GET'])
def get_batch_task_status(task_id):
    """获取批量回测任务状态"""
    if task_id in batch_tasks:
        return jsonify({
            'task_id': task_id,
            'status': batch_tasks[task_id]['status'],
            'progress': batch_tasks[task_id]['progress'],
            'message': batch_tasks[task_id]['message'],
            'started_at': batch_tasks[task_id]['started_at'],
            'completed_at': batch_tasks[task_id]['completed_at'],
            'error_message': batch_tasks[task_id]['error_message']
        })
    else:
        return jsonify({'error': '任务不存在或已过期'}), 404


@backtest_bp.route('/batch-results/<task_id>', methods=['GET'])
def get_batch_results(task_id):
    """获取批量回测任务结果"""
    try:
        db = get_db()
        results_df = db.get_batch_backtest_results(task_id)

        if results_df is None or len(results_df) == 0:
            return jsonify({'error': '未找到回测结果', 'task_id': task_id}), 404

        # ── PORTFOLIO 行包含整体组合指标（也是唯一一行） ──
        portfolio_row = results_df[results_df['stock_code'] == 'PORTFOLIO']
        has_portfolio = not portfolio_row.empty

        if has_portfolio:
            pr = portfolio_row.iloc[0]
            # total_stocks 从持久化的元数据字段读（不依赖内存batch_tasks）
            total_stocks = int(pr.get('total_stocks', 0) or 0)
            valid_stocks = int(pr.get('valid_stocks', 0) or 0)
            success_count = valid_stocks  # 有效股票即为成功
            fail_count = total_stocks - valid_stocks if total_stocks > valid_stocks else 0
            no_data_count = total_stocks - valid_stocks if total_stocks >= valid_stocks else 0

            avg_return = float(pr.get('total_return', 0) or 0)
            avg_sharpe = float(pr.get('sharpe_ratio', 0) or 0)
            avg_win_rate = float(pr.get('win_rate', 0) or 0)
            avg_annual_return = float(pr.get('annualized_return', 0) or 0)
            avg_max_drawdown = float(pr.get('max_drawdown', 0) or 0)
            total_trades = int(pr.get('total_trades', 0) or 0)

            # top5/bottom5 只能显示 PORTFOLIO 自身，无法排名
            top5 = [{'stock_code': 'PORTFOLIO', 'stock_name': '投资组合',
                      'total_return': avg_return, 'sharpe_ratio': avg_sharpe, 'win_rate': avg_win_rate}]
            bottom5 = top5
        else:
            # 非组合模式（按股票逐个回测），保持原逻辑
            success_df = results_df[(results_df['status'] == 'success') & (results_df['total_trades'] > 0)]
            total_stocks = len(results_df)
            success_count = len(success_df)
            fail_count = len(results_df[results_df['status'].isin(['error', 'insufficient_data'])]) if 'status' in results_df.columns else 0
            no_data_count = len(results_df[results_df['status'] == 'no_data']) if 'status' in results_df.columns else 0

            if success_count > 0:
                avg_return = float(success_df['total_return'].mean()) if 'total_return' in success_df.columns else 0
                avg_sharpe = float(success_df['sharpe_ratio'].mean()) if 'sharpe_ratio' in success_df.columns else 0
                avg_win_rate = float(success_df['win_rate'].mean()) if 'win_rate' in success_df.columns else 0
                avg_annual_return = float(success_df['annualized_return'].mean()) if 'annualized_return' in success_df.columns else 0
                avg_max_drawdown = float(success_df['max_drawdown'].mean()) if 'max_drawdown' in success_df.columns else 0
                total_trades = int(success_df['total_trades'].sum()) if 'total_trades' in success_df.columns else 0
                top5 = success_df.nlargest(5, 'total_return')[['stock_code', 'stock_name', 'total_return', 'sharpe_ratio', 'win_rate']].to_dict('records')
                bottom5 = success_df.nsmallest(5, 'total_return')[['stock_code', 'stock_name', 'total_return', 'sharpe_ratio', 'win_rate']].to_dict('records')
            else:
                avg_return = avg_sharpe = avg_win_rate = avg_annual_return = avg_max_drawdown = 0
                total_trades = 0
                top5 = bottom5 = []

        param_results = None
        param_df = db.get_batch_param_results(task_id)
        if param_df is not None and len(param_df) > 0:
            param_results = param_df.to_dict('records')

        error_stocks = results_df[results_df['status'].isin(['error', 'insufficient_data'])]
        error_stocks_list = error_stocks[['stock_code', 'stock_name', 'status', 'error_message']].to_dict('records') if len(error_stocks) > 0 else []

        # ── 从 PORTFOLIO 行读取 final_value / initial_cash（真实资产值） ──
        final_value = 0
        initial_cash = 0
        if has_portfolio:
            pr = portfolio_row.iloc[0]
            final_value = float(pr.get('final_value', 0) or 0)
            initial_cash = float(pr.get('initial_cash', 0) or 0)

        # 安全列集合（排除新增元数据列避免 KeyError）
        stock_cols = [c for c in ['stock_code', 'stock_name', 'status', 'total_return',
                                   'annualized_return', 'sharpe_ratio', 'win_rate',
                                   'total_trades', 'final_value', 'initial_cash', 'error_message']
                      if c in results_df.columns]

        return jsonify({
            'task_id': task_id,
            'total_stocks': total_stocks,
            'valid_stocks': valid_stocks if has_portfolio else success_count,
            'success_count': success_count,
            'fail_count': fail_count,
            'no_data_count': no_data_count,
            'success_rate': success_count / total_stocks if total_stocks > 0 else 0,
            'avg_return': avg_return,
            'avg_sharpe': avg_sharpe,
            'avg_win_rate': avg_win_rate,
            'avg_annual_return': avg_annual_return,
            'avg_max_drawdown': avg_max_drawdown,
            'total_trades': total_trades,
            'final_value': final_value,
            'initial_cash': initial_cash,
            'top5_stocks': top5,
            'bottom5_stocks': bottom5,
            'param_results': param_results,
            'error_stocks': error_stocks_list,
            'stocks': results_df[stock_cols].to_dict('records')
        })

    except Exception as e:
        import traceback
        print(f"获取批量回测结果失败: {e}")
        print(traceback.format_exc())
        return jsonify({'error': str(e)}), 500


@backtest_bp.route('/batch-task/<task_id>', methods=['DELETE'])
def cancel_batch_task(task_id):
    """取消批量回测任务"""
    if task_id in batch_tasks:
        batch_tasks[task_id]['status'] = 'cancelled'
        batch_tasks[task_id]['message'] = '任务已取消'
        batch_tasks[task_id]['completed_at'] = datetime.now().isoformat()
        return jsonify({'task_id': task_id, 'status': 'cancelled'})
    else:
        return jsonify({'error': '任务不存在或已过期'}), 404
