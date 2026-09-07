# -*- coding: utf-8 -*-
"""
动画资产一键导出工具（animation_exporter）
==========================================
Maya 插件包：FBX 骨骼动画 / ABC 几何体缓存（含导出前清理）/ 相机动画一键导出。

模块划分：
    config.py        常量与全局配置
    utils.py         Maya 通用工具函数
    persistence.py   数据持久化（场景 network 节点 + 外部 JSON）
    exporter.py      导出执行层（FBX / ABC / 相机）
    core.py          核心逻辑层（条目数据、批量导出入口，无 UI 依赖）
    ui.py            UI 层（构建窗口、回调、场景节点实时同步）
"""
from . import config          # noqa: F401
from . import core            # noqa: F401
from . import persistence      # noqa: F401
from . import exporter         # noqa: F401

__version__ = config.VERSION


def launch():
    """打开工具窗口（等价于执行 ui.launch()）"""
    from . import ui
    ui.launch()
