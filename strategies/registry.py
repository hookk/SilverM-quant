"""
策略注册表模块

提供策略元数据管理和策略类加载功能。
支持从 STRATEGY_CONFIG 加载已有配置以保持向后兼容。

使用示例:
    from strategies.registry import Registry, StrategyMetadata
    
    # 获取注册表实例
    registry = Registry()
    
    # 列出所有策略
    print(registry.list())
    
    # 获取策略类
    strategy_class = registry.get('天宫B2策略v2')
    
    # 过滤策略
    threshold_strategies = registry.filter(threshold_required=True)
"""

import importlib
import importlib.util
import sys
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, List

from database.db_manager import DatabaseManager

# 项目根目录
PROJECT_ROOT = Path(__file__).parent.parent
STRATEGIES_DIR = PROJECT_ROOT / 'strategies'

# 数据库路径 —— 统一使用此常量，避免单例路径不一致
DB_PATH = str(PROJECT_ROOT / 'data' / 'Astock3.duckdb')


@dataclass
class StrategyMetadata:
    """
    策略元数据
    
    属性:
        name: 策略名称 (与文件名对应,不含.py)
        threshold_required: 是否需要threshold参数
        min_data_days: 最小数据天数
        description: 策略描述
        author: 作者
        version: 版本号
    """
    name: str
    threshold_required: bool = True
    min_data_days: int = 60
    description: str = ""
    author: str = ""
    version: str = "1.0.0"


def register(
    name: str,
    threshold_required: bool = True,
    min_data_days: int = 60,
    description: str = ""
):
    """
    策略注册装饰器
    
    Args:
        name: 策略名称
        threshold_required: 是否需要threshold参数
        min_data_days: 最小数据天数
        description: 策略描述
    
    Returns:
        装饰器函数
    
    使用示例:
        @register(name='我的策略', threshold_required=False)
        class MyStrategy(BaseStrategy):
            pass
    """
    def decorator(cls):
        metadata = StrategyMetadata(
            name=name,
            threshold_required=threshold_required,
            min_data_days=min_data_days,
            description=description
        )
        registry = Registry()
        registry.register(name, metadata)
        # 存储类引用
        registry._classes[name] = cls

        # 保存到数据库 —— 使用统一 DB_PATH，避免单例路径冲突
        db = DatabaseManager(DB_PATH)
        strategy_data = {
            'name': name,
            'class_path': f'strategies.{cls.__name__}',
            'description': description,
            'threshold_required': threshold_required,
            'min_data_days': min_data_days,
            'status': 'active',
        }
        db.save_strategy(strategy_data)

        return cls
    return decorator


class Registry:
    """
    策略注册表
    
    管理策略元数据和策略类的注册、查询、过滤操作。
    支持单例模式,确保全局只有一个注册表实例。
    
    使用示例:
        registry = Registry()
        registry.register('我的策略', StrategyMetadata(name='我的策略'))
        strategy_class = registry.get('我的策略')
        print(registry.list())
    """
    
    _instance: Optional['Registry'] = None
    _initialized: bool = False
    
    @classmethod
    def clear(cls) -> None:
        """清除所有注册信息 (仅用于测试)"""
        if cls._instance is not None:
            cls._instance._metadata.clear()
            cls._instance._classes.clear()
            cls._instance._modules.clear()
        cls._initialized = False
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
        return cls._instance
    
    def __init__(self):
        if Registry._initialized:
            return
        
        # 内部注册表: name -> StrategyMetadata
        self._metadata: dict[str, StrategyMetadata] = {}
        
        # 策略类缓存: name -> class
        self._classes: dict[str, type] = {}
        
        # 已加载的模块缓存: name -> module
        self._modules: dict[str, object] = {}
        
        # 从数据库加载策略
        self._load_from_database()
        
        Registry._initialized = True
    
    def _load_from_database(self) -> None:
        """从数据库加载策略"""
        try:
            db = DatabaseManager(DB_PATH)
            strategies = db.list_strategies(status='active')
            
            for strategy_info in strategies:
                name = strategy_info.get('name')
                if not name:
                    continue
                
                # 创建元数据
                metadata = StrategyMetadata(
                    name=name,
                    threshold_required=strategy_info.get('threshold_required', True),
                    min_data_days=strategy_info.get('min_data_days', 60),
                    description=strategy_info.get('description', ''),
                    author=strategy_info.get('author', ''),
                    version=strategy_info.get('version', '1.0.0'),
                )
                self._metadata[name] = metadata
                
                # 尝试加载策略类并缓存（失败不阻断）
                strategy_class = self._load_strategy_class(name)
                if strategy_class is not None:
                    self._classes[name] = strategy_class
                    
        except Exception:
            # 如果加载失败,静默处理 (可能是数据库问题)
            pass
    
    def register(self, name: str, metadata: StrategyMetadata) -> None:
        """
        注册策略
        
        Args:
            name: 策略名称
            metadata: 策略元数据
        """
        if not isinstance(metadata, StrategyMetadata):
            raise TypeError("metadata must be a StrategyMetadata instance")
        
        metadata.name = name  # 确保name字段一致
        self._metadata[name] = metadata
    
    def get(self, name: str) -> Optional[type]:
        """
        获取策略类 —— 三路 fallback：
          1. 内存缓存 _classes
          2. 递归扫描 strategies/ 目录，按注册名匹配
          3. 数据库 class_path 字段动态 import
        
        Args:
            name: 策略名称（注册名或文件名均可）
            
        Returns:
            策略类，如果未找到返回 None
        """
        # 路径1：检查内存缓存
        if name in self._classes:
            return self._classes[name]
        
        # 路径2：按文件名或注册名递归扫描
        strategy_class = self._load_strategy_class(name)
        if strategy_class is not None:
            self._classes[name] = strategy_class
            return strategy_class

        # 路径3：从数据库 class_path 动态 import
        strategy_class = self._load_from_class_path(name)
        if strategy_class is not None:
            self._classes[name] = strategy_class
            return strategy_class

        return None
    
    def _load_strategy_class(self, name: str) -> Optional[type]:
        """
        动态加载策略类
        
        先查 strategies/<name>.py（精确匹配文件名），
        再递归扫描所有子目录中文件，匹配 @register(name=...) 注册名。
        
        Args:
            name: 策略注册名 or 文件名(不含.py)
            
        Returns:
            策略类，如果加载失败返回 None
        """
        # ---- 精确路径匹配（文件名 == name）----
        exact_file = STRATEGIES_DIR / f'{name}.py'
        if exact_file.exists():
            cls = self._import_strategy_from_file(exact_file, f'strategies.{name}')
            if cls is not None:
                return cls

        # ---- 递归扫描所有 .py 文件，按注册名匹配 ----
        for py_file in STRATEGIES_DIR.rglob('*.py'):
            if py_file.name.startswith('_') or py_file == exact_file:
                continue
            # 快速检查文件内容是否含目标注册名（避免全量 import）
            try:
                source = py_file.read_text(encoding='utf-8', errors='ignore')
            except Exception:
                continue
            if name not in source:
                continue

            # 检查是否有 @register(name='...') 匹配
            if not re.search(
                r'@register\s*\(.*?name\s*=\s*["\']' + re.escape(name) + r'["\']',
                source
            ):
                continue

            # 构造模块名（相对于项目根）
            rel = py_file.relative_to(PROJECT_ROOT)
            module_name = '.'.join(rel.with_suffix('').parts)
            cls = self._import_strategy_from_file(py_file, module_name)
            if cls is not None:
                return cls

        return None

    def _import_strategy_from_file(self, py_file: Path, module_name: str) -> Optional[type]:
        """从文件 import 并返回第一个 BaseStrategy/PortfolioStrategy 子类"""
        try:
            if module_name in sys.modules:
                module = sys.modules[module_name]
            else:
                spec = importlib.util.spec_from_file_location(module_name, py_file)
                if spec is None or spec.loader is None:
                    return None
                module = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = module
                spec.loader.exec_module(module)

            from strategies.base.framework_strategy import BaseStrategy
            from strategies.base.portfolio_strategy import PortfolioStrategy

            for attr_name in dir(module):
                attr = getattr(module, attr_name)
                if (
                    isinstance(attr, type)
                    and attr_name not in ('BaseStrategy', 'FrameworkStrategy', 'PortfolioStrategy')
                    and (issubclass(attr, BaseStrategy) or issubclass(attr, PortfolioStrategy))
                ):
                    return attr

        except Exception:
            pass
        return None

    def _load_from_class_path(self, name: str) -> Optional[type]:
        """通过数据库 class_path 字段动态 import 策略类"""
        try:
            db = DatabaseManager(DB_PATH)
            info = db.get_strategy(name)
            if info is None:
                return None
            class_path = info.get('class_path', '')
            if not class_path:
                return None

            # class_path 格式：  "strategies.module_name"  或  "strategies.sub.ClassName"
            # 先尝试直接 import
            parts = class_path.rsplit('.', 1)
            if len(parts) == 2:
                mod_path, cls_name = parts
                try:
                    module = importlib.import_module(mod_path)
                    cls = getattr(module, cls_name, None)
                    if cls is not None and isinstance(cls, type):
                        return cls
                except Exception:
                    pass

            # 再尝试把整个 class_path 当模块 import
            try:
                module = importlib.import_module(class_path)
                from strategies.base.framework_strategy import BaseStrategy
                from strategies.base.portfolio_strategy import PortfolioStrategy
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if (
                        isinstance(attr, type)
                        and attr_name not in ('BaseStrategy', 'FrameworkStrategy', 'PortfolioStrategy')
                        and (issubclass(attr, BaseStrategy) or issubclass(attr, PortfolioStrategy))
                    ):
                        return attr
            except Exception:
                pass

        except Exception:
            pass
        return None

    def list(self, status: str = 'active') -> List[str]:
        """
        列出已注册策略名称（默认只返回active状态）

        Args:
            status: 策略状态过滤，默认'active'，可选值包括'active'、'deprecated'等

        Returns:
            符合条件的策略名称列表
        """
        db_manager = DatabaseManager(DB_PATH)
        strategies = db_manager.list_strategies(status=status)
        return [s['name'] for s in strategies]

    def list_all(self) -> List[str]:
        """
        列出所有已注册策略名称（包括已废弃的策略）

        Returns:
            所有策略名称列表
        """
        db_manager = DatabaseManager(DB_PATH)
        strategies = db_manager.list_strategies(status=None)
        return [s['name'] for s in strategies]
    
    def filter(self, **kwargs) -> List[str]:
        """
        根据条件过滤策略
        
        Args:
            **kwargs: 过滤条件,支持 threshold_required, min_data_days 等
            
        Returns:
            符合条件的策略名称列表
        """
        results = []
        
        for name, metadata in self._metadata.items():
            match = True
            
            for key, value in kwargs.items():
                if not hasattr(metadata, key):
                    match = False
                    break
                
                if getattr(metadata, key) != value:
                    match = False
                    break
            
            if match:
                results.append(name)
        
        return results
    
    def is_registered(self, name: str) -> bool:
        """
        检查策略是否已注册
        
        Args:
            name: 策略名称
            
        Returns:
            是否已注册
        """
        # 内存缓存 OR 数据库
        if name in self._metadata:
            return True
        try:
            db = DatabaseManager(DB_PATH)
            return db.get_strategy(name) is not None
        except Exception:
            return False
    
    def get_metadata(self, name: str) -> Optional[StrategyMetadata]:
        """
        获取策略元数据
        
        Args:
            name: 策略名称
            
        Returns:
            策略元数据，如果未找到返回 None
        """
        if name in self._metadata:
            return self._metadata[name]
        # 从数据库补充
        try:
            db = DatabaseManager(DB_PATH)
            info = db.get_strategy(name)
            if info:
                metadata = StrategyMetadata(
                    name=name,
                    threshold_required=info.get('threshold_required', True),
                    min_data_days=info.get('min_data_days', 60),
                    description=info.get('description', ''),
                    author=info.get('author', ''),
                    version=info.get('version', '1.0.0'),
                )
                self._metadata[name] = metadata
                return metadata
        except Exception:
            pass
        return None
    
    def __contains__(self, name: str) -> bool:
        """支持 'in' 操作符"""
        return self.is_registered(name)
    
    def __len__(self) -> int:
        """返回已注册策略数量"""
        return len(self._metadata)
    
    def __iter__(self):
        """支持迭代"""
        return iter(self._metadata.keys())
    
    def soft_delete(self, name: str) -> bool:
        """
        软删除策略 (标记为archived)
        
        Args:
            name: 策略名称
            
        Returns:
            True if successful, False if not found
        """
        # 先确认策略存在（内存或数据库）
        if name not in self._metadata:
            # 尝试从数据库确认
            try:
                db = DatabaseManager(DB_PATH)
                if db.get_strategy(name) is None:
                    return False
            except Exception:
                return False

        db_manager = DatabaseManager(DB_PATH)
        success = db_manager.update_strategy_status(name, 'archived')
        
        if success:
            # 从内存缓存中移除
            self._classes.pop(name, None)
            self._metadata.pop(name, None)
            self._modules.pop(name, None)
            # 重置单例初始化标志，确保下次 Registry() 重新从数据库加载
            Registry._initialized = False

        return success
