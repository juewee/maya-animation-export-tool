# -*- coding: utf-8 -*-
"""
动画资产一键导出工具 - 常量与全局配置定义
（纯常量模块，不依赖 Maya，可被任意模块导入）
"""

# 窗口与版本
WINDOW_NAME = "animExportManagerCmdsUI_v8"
WINDOW_TITLE = u"动画资产一键导出工具 v0.8"
VERSION = "0.8.0"

# 资产分类
TYPE_FBX = "fbx"
TYPE_ABC = "abc"
TYPE_CAMERA = "camera"
TYPE_ORDER = [TYPE_FBX, TYPE_ABC, TYPE_CAMERA]

CATEGORY_NAMES = {
    TYPE_FBX: u"FBX 骨骼动画",
    TYPE_ABC: u"ABC 几何体缓存",
    TYPE_CAMERA: u"相机动画",
}

# 导出文件扩展名
EXT_FOR_TYPE = {
    TYPE_FBX: ".fbx",
    TYPE_ABC: ".abc",
    TYPE_CAMERA: ".fbx",
}

# 导出依赖的 Maya 插件
PLUGIN_FBX = "fbxmaya"
PLUGIN_ABC = "AbcExport"
PLUGIN_LIST = [PLUGIN_FBX, PLUGIN_ABC]

# ABC 组在列表中最多直接显示的对象名数量
ABC_DISPLAY_LIMIT = 2

# 参考命令：导出前对多边形做清理（展开 Poly 组选择 + polyCleanupArgList）
ABC_CLEANUP_MEL = (
    "expandPolyGroupSelection; "
    'polyCleanupArgList 4 { "0","1","1","0","1","0","0","0","0","1e-05","0","1e-05","0","1e-05","0","-1","0","0" };'
)

# ---------------------------------------------------------------------------
# 数据持久化相关
# ---------------------------------------------------------------------------
# 场景内用于保存配置的 network 节点名
SCENE_NODE_NAME = "exportConfigData"
# 字符串属性名前缀与单段长度（Maya 字符串属性有长度限制，需分块）
ATTR_PREFIX = "configChunk"
CHUNK_SIZE = 400
