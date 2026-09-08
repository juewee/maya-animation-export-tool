# -*- coding: utf-8 -*-
"""
无 GUI 批处理 API — 供外部脚本 / AI 调用

使用方式（在 Maya Python 或 mayapy 中）：

    import sys; sys.path.insert(0, r"C:/path/to/maya_export")
    from animation_exporter import batch

    # 1) 查看当前场景里的导出配置
    info = batch.describe_scene()
    print(info)
    # -> {"export_dir": "...", "items": {"fbx": [...], "abc": [...], "camera": [...]}, ...}

    # 2) 读取配置 + 一键导出（用场景里已保存的配置）
    results = batch.export_from_scene(export_dir="D:/output", start=101, end=251)
    # -> {"success": [...], "failure": [...]}

    # 3) 只导出指定分类
    results = batch.export_from_scene(export_dir="D:/output", start=101, end=251,
                                      types=["fbx", "camera"])

    # 4) 不读场景节点，直接传配置
    results = batch.export_with_config(config_dict, export_dir="D:/output",
                                       start=101, end=251)

    # 5) 导出前预检查（不实际导出）
    report = batch.precheck_scene()
    print(report)
    # -> {"total": 5, "enabled": 3, "missing": ["Root_M", "Camera_01"], ...}
"""
import maya.cmds as cmds

from . import config
from . import core
from . import persistence
from . import utils


def describe_scene():
    """读取当前场景里的导出配置，返回可读的字典（不打开 UI）。

    返回结构：
        {
            "export_dir": str,
            "prefix": str,
            "abc_cleanup": bool,
            "animation_range": [start, end],
            "use_custom_range": bool,
            "items": {
                "fbx": [{"object": str, "export_name": str, "enabled": bool}, ...],
                "abc": [...],
                "camera": [...],
            },
            "total": int,
            "enabled_count": int,
        }
    """
    raw = persistence.read_config_from_node()
    if not raw:
        raw = persistence.default_config()
    data = persistence.normalize_config(raw)

    # 恢复 data_store 以便 core 函数使用
    core.replace_store(data["items"])

    start, end = data.get("animation_range", [None, None])
    if start is None:
        start = int(cmds.playbackOptions(q=True, minTime=True))
    if end is None:
        end = int(cmds.playbackOptions(q=True, maxTime=True))

    return {
        "export_dir": data.get("export_dir", ""),
        "prefix": data.get("prefix_text", ""),
        "abc_cleanup": data.get("abc_cleanup", False),
        "animation_range": [start, end],
        "use_custom_range": data.get("use_custom_range", False),
        "items": {
            type_key: [
                {"object": item.get("object"), "export_name": item.get("export_name", ""),
                 "enabled": item.get("enabled", True)}
                for item in data["items"].get(type_key, [])
            ]
            for type_key in config.TYPE_ORDER
        },
        "total": core.count_total(),
        "enabled_count": core.count_enabled(),
    }


def precheck_scene():
    """预检查场景配置，返回缺失物体列表和统计信息（不导出）"""
    raw = persistence.read_config_from_node()
    if not raw:
        return {"total": 0, "enabled": 0, "missing": [], "has_config": False}

    data = persistence.normalize_config(raw)
    core.replace_store(data["items"])

    missing = []
    for type_key in config.TYPE_ORDER:
        for item in data["items"].get(type_key, []):
            obj = item.get("object", "")
            if not obj:
                continue
            if isinstance(obj, (list, tuple)):
                for o in obj:
                    if not cmds.objExists(o):
                        missing.append(o)
            else:
                if not cmds.objExists(obj):
                    missing.append(obj)

    return {
        "total": core.count_total(),
        "enabled": core.count_enabled(),
        "missing": missing,
        "has_config": True,
        "export_dir": data.get("export_dir", ""),
    }


def export_from_scene(export_dir=None, start=None, end=None, types=None, prefix=None):
    """从场景节点读取配置并执行无 GUI 批量导出。

    参数：
        export_dir: 导出目录（None 则用配置中保存的目录）
        start, end: 帧范围（None 则用配置或播放范围）
        types: 只导出这些分类（如 ["fbx"]；None = 全部）
        prefix: 命名前缀（None 则用配置中的前缀）

    返回：
        {"success": [(type, name, path), ...], "failure": [(type, name, error), ...]}
    """
    raw = persistence.read_config_from_node()
    if not raw:
        raw = persistence.default_config()
    data = persistence.normalize_config(raw)
    core.replace_store(data["items"])

    # 如果只导出指定分类，把其他分类的 enabled 关掉
    if types:
        type_set = set(types)
        for type_key in config.TYPE_ORDER:
            if type_key not in type_set:
                for item in core.data_store.get(type_key, []):
                    item["enabled"] = False

    if export_dir is None:
        export_dir = data.get("export_dir", "")
    if start is None or end is None:
        if data.get("use_custom_range") and data.get("animation_range"):
            s, e = data["animation_range"]
            if start is None and s is not None:
                start = int(s)
            if end is None and e is not None:
                end = int(e)
        if start is None:
            start = int(cmds.playbackOptions(q=True, minTime=True))
        if end is None:
            end = int(cmds.playbackOptions(q=True, maxTime=True))
    if prefix is None:
        prefix = data.get("prefix_text", "")

    options = {
        "export_dir": export_dir,
        "start": start,
        "end": end,
        "abc_cleanup": data.get("abc_cleanup", False),
        "prefix": prefix,
    }
    successes, failures = core.run_export_batch(core.data_store, options)
    return {"success": successes, "failure": failures}


def export_with_config(config_dict, export_dir, start, end, prefix="", types=None):
    """直接传入配置字典执行导出（不读场景节点）。

    config_dict 结构同 describe_scene 返回的 items，例如：
        {"fbx": [{"object": "Root_M", "export_name": "CharA", "enabled": True}],
         "abc": [], "camera": []}
    """
    store = {key: [] for key in config.TYPE_ORDER}
    for type_key in config.TYPE_ORDER:
        for item in config_dict.get(type_key, []):
            store[type_key].append({
                "object": item.get("object"),
                "export_name": item.get("export_name", ""),
                "enabled": item.get("enabled", True),
            })
    core.replace_store(store)

    if types:
        type_set = set(types)
        for type_key in config.TYPE_ORDER:
            if type_key not in type_set:
                for item in core.data_store.get(type_key, []):
                    item["enabled"] = False

    options = {
        "export_dir": export_dir,
        "start": start,
        "end": end,
        "abc_cleanup": False,
        "prefix": prefix,
    }
    successes, failures = core.run_export_batch(core.data_store, options)
    return {"success": successes, "failure": failures}
