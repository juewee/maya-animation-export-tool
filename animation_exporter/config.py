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

# ---------------------------------------------------------------------------
# 命名规范模板（可在设置面板自定义）
# {name} = 导出名, {start} = 起始帧, {end} = 结束帧
# fbx_anim_suffix: FBX 骨骼动画文件名后缀模板
# camera_suffix:  相机动画文件名后缀模板
# abc_add_range:  ABC 文件名是否追加帧范围
# ---------------------------------------------------------------------------
NAMING_PRESETS = {
    "fbx_anim_suffix": "_Anim_{start}-{end}",
    "camera_suffix": "_{start}-{end}",
    "abc_add_range": False,
}

# ---------------------------------------------------------------------------
# 导出高级选项（设置面板可改，随配置一起持久化）
#
# 默认值全部对齐参考工具 UEAnimCamExporter（已验证可用的那版）：
#   - 骨骼/动画 FBX：Z-Up + ConvertAnimation 打开（参考工具 RIG/Anim 默认开）
#   - 相机 FBX：Z-Up + ConvertAnimation **关闭**（参考工具独立开关
#     “Camera Z-Up Convert” 默认关，提示语“相机位置/方向不对时单独切换测试”）
#     原因：Maya 导出转一次、UE 导入相机 FBX 再解释一次 = 二次转换，
#     相机位置/朝向就会和 Maya 对不上。
#   - 相机 ParentConstraint Bake / 挂世界根 / 检测相机 Rig：开
#   - 只 Bake 相机实际动画段：关；限制在 Start/End 内：开
#   - 导出前检查感光器/分辨率：开
# ---------------------------------------------------------------------------
EXPORT_OPTIONS = {
    # 采样步长：FBXExportBakeComplexStep 与 bakeResults 的 sampleBy 都用它
    "sample_by": 1,

    # FBX 骨骼动画是否 Z-Up / ConvertAnimation
    "fbx_z_up": True,

    # 相机是否 Z-Up / ConvertAnimation（默认关，见上方说明）
    "camera_z_up": False,

    # 相机临时 Bake 节点挂世界根（FBX 里没有额外父级，避免 UE 导入时父级偏移）
    "camera_world_root": True,

    # 相机用 parentConstraint + bakeResults 烘焙；关闭则用世界矩阵逐帧采样
    "camera_parent_bake": True,

    # 导出前检测相机的父级/控制器/约束/动画节点（只影响日志与提示）
    "camera_detect_rig": True,

    # 只 Bake 相机实际动画段（扫描相机/Shape/父级/约束/控制器的关键帧）
    "camera_use_anim_range": False,

    # 相机实际动画段限制在 UI 的 Start/End 内
    "camera_clamp_anim_range": True,

    # 导出前检查 Render Settings 分辨率比例与 Camera Film Aperture 比例
    "camera_check_sensor": True,

    # 感光器比例相对容差（超过才提示）
    "camera_aperture_tolerance": 0.005,

    # ABC 导出是否**保留**命名空间（勾选 = 不加 -stripNamespaces）。
    # 默认不保留（= 加 -stripNamespaces，把命名空间从 ABC 层级里去掉）。
    # 需要勾选的场景：命名空间里存在**同名**物体（复制/引用很容易造成），
    # 去掉命名空间后两个物体重名，AbcExport 会直接报错导不出来。
    "abc_keep_namespaces": False,

    # 导出时显示 Maya 进度条（批处理/无界面时自动跳过）
    "show_progress": True,

    # 打开工具时做一次轻量自检（不遍历场景，见 checks.py）
    "startup_check": True,
}

# 出厂默认值快照（设置面板“恢复默认”用）
DEFAULT_EXPORT_OPTIONS = dict(EXPORT_OPTIONS)
DEFAULT_NAMING_PRESETS = dict(NAMING_PRESETS)

# 选项类型表（供持久化 / 设置面板做类型校验）
OPTION_TYPES = {
    "sample_by": int,
    "fbx_z_up": bool,
    "camera_z_up": bool,
    "camera_world_root": bool,
    "camera_parent_bake": bool,
    "camera_detect_rig": bool,
    "camera_use_anim_range": bool,
    "camera_clamp_anim_range": bool,
    "camera_check_sensor": bool,
    "camera_aperture_tolerance": float,
    "abc_keep_namespaces": bool,
    "show_progress": bool,
    "startup_check": bool,
}

NAMING_TYPES = {
    "fbx_anim_suffix": str,
    "camera_suffix": str,
    "abc_add_range": bool,
}


def option(name, default=None):
    """读取导出选项（带默认值兜底，避免旧配置缺键时报错）"""
    if name in EXPORT_OPTIONS:
        return EXPORT_OPTIONS[name]
    return default


def reset_options():
    """恢复命名模板与导出选项的出厂默认值"""
    NAMING_PRESETS.clear()
    NAMING_PRESETS.update(DEFAULT_NAMING_PRESETS)
    EXPORT_OPTIONS.clear()
    EXPORT_OPTIONS.update(DEFAULT_EXPORT_OPTIONS)

# ABC 导出前的多边形清理（展开 Poly 组选择 + polyCleanupArgList）。
#
# 第一个参数 4 是参数版本号，后面 18 个字符串按 polyCleanupArgList 的约定排列：
#   [0] allMeshes=0           只处理当前选择
#   [1] selectOnly=1          对当前选择执行清理（2 才是“只选中不修改”）
#   [2] historyOn=1           保留构造历史（非破坏性，不改原 mesh 数据）
#   [3] quads=0               不处理四边面
#   [4] nsided=1              处理 >4 边面（n 边面三角化）<- 本项目只做这一项
#   [5] concave=0  [6] holed=0  [7] nonplanar=0
#   [8..13] 零面积面/边/UV 检查与容差，全部为 0（不删任何几何）
#   [14] sharedUVs=0  [15] nonmanifold=-1（不检查）  [16] lamina=0  [17] invalidComponents=0
#
# 不用 delete(constructionHistory) / makeIdentity 的原因见 exporter._run_abc_cleanup：
# ABC 多用于导出动画，删历史会断开变形器/驱动，冻结变换会毁掉动画通道。
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
