"""渲染器注册中心 —— 启动时自动发现 models/ 目录下所有 BaseRenderer 子类"""
import importlib
import logging
import os
import pkgutil

from .base import BaseRenderer

_logger = logging.getLogger("astrbot.rss")


def discover_renderers(builder) -> dict[str, BaseRenderer]:
    """扫描 models/ 目录，实例化所有 BaseRenderer 子类。

    Args:
        builder: MessageBuilder 实例，注入到每个渲染器

    Returns:
        {name: renderer_instance} 字典
    """
    registry: dict[str, BaseRenderer] = {}
    _dir = os.path.dirname(__file__)

    # 首先加载内置渲染器
    from . import default, compose  # noqa: F401 — 确保内置渲染器被导入

    # 递归扫描 BaseRenderer 的所有子类
    for sub_cls in _find_subclasses(BaseRenderer):
        if sub_cls.name == "base":
            continue
        try:
            registry[sub_cls.name] = sub_cls(builder)
            _logger.debug(f"📄 渲染器已注册: {sub_cls.name} ({sub_cls.__module__})")
        except Exception as e:
            _logger.warning(f"⚠️ 渲染器 {sub_cls.name} 初始化失败: {e}")

    return registry


def _find_subclasses(cls):
    """递归查找所有子类（支持用户自定义渲染器文件）"""
    found = set()

    # 遍历 models 包下的所有模块
    pkg_path = os.path.dirname(__file__)
    for _, name, _ in pkgutil.iter_modules([pkg_path]):
        if name.startswith("_"):
            continue
        try:
            module = importlib.import_module(f".{name}", package=__package__)
        except Exception as e:
            _logger.warning(f"⚠️ 加载渲染器模块 {name} 失败: {e}")
            continue

    # 收集 BaseRenderer 的所有子类
    def _recurse(c):
        for sub in c.__subclasses__():
            found.add(sub)
            _recurse(sub)

    _recurse(cls)
    return found


def get_renderer(registry: dict[str, BaseRenderer], name: str | None, platform: str,
                 is_compose: bool = False) -> BaseRenderer:
    """根据配置选择合适的渲染器。

    优先级：
    1. 订阅指定的渲染器名称
    2. aiocqhttp 平台 + compose 开启 → "compose"
    3. 回退到 "default"
    """
    # 1. 订阅指定了渲染器
    if name and name in registry:
        return registry[name]

    # 2. QQ 平台合并转发
    if platform == "aiocqhttp" and is_compose and "compose" in registry:
        return registry["compose"]

    # 3. 默认
    return registry.get("default", next(iter(registry.values())))
