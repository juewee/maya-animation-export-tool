# -*- coding: utf-8 -*-
"""
动画资产一键导出工具 - 数据持久化

提供两类持久化：
1. 场景内持久化：把配置 JSON 分块写入 network 节点（exportConfigData），随 .ma/.mb 一起保存；
   插件启动时自动读回。解决 Maya 字符串属性长度限制（单段 CHUNK_SIZE）。
2. 外部 JSON 文件：写入 / 读取导出配置文件（UTF-8）。
"""
import io
import json
import sys
import maya.cmds as cmds

from . import config


# ---------------------------------------------------------------------------
# 配置结构
# ---------------------------------------------------------------------------
def empty_items():
    return {key: [] for key in config.TYPE_ORDER}


def default_config():
    """返回一份结构完整的默认配置（字典，可 JSON 序列化）"""
    return {
        "export_dir": "",
        "use_prefix": False,
        "prefix_text": "",
        "abc_cleanup": False,
        "use_custom_range": False,
        "animation_range": [None, None],
        "items": empty_items(),
    }


def _as_text(value):
    if isinstance(value, bool):
        return u"true" if value else u"false"
    if value is None:
        return u""
    if isinstance(value, bytes):
        return value.decode("utf-8", "replace")
    if sys.version_info[0] < 3:
        return unicode(value)  # noqa: F821 (py2)
    return str(value)


def normalize_config(raw):
    """把任意来源（旧 JSON / 新 JSON / 场景节点）的配置归一化为标准结构"""
    if not isinstance(raw, dict):
        raw = {}
    cfg = default_config()

    cfg["export_dir"] = _as_text(raw.get("export_dir", ""))
    cfg["use_prefix"] = bool(raw.get("use_prefix", False))
    cfg["prefix_text"] = _as_text(raw.get("prefix_text", ""))
    cfg["abc_cleanup"] = bool(raw.get("abc_cleanup", False))

    # 动画范围：兼容 [start, end] 或 {"start":..,"end":..} 两种形态
    range_raw = raw.get("animation_range")
    start = end = None
    if isinstance(range_raw, (list, tuple)) and len(range_raw) >= 2:
        start, end = range_raw[0], range_raw[1]
    elif isinstance(range_raw, dict):
        start = range_raw.get("start")
        end = range_raw.get("end")
    try:
        start = None if start is None else int(start)
        end = None if end is None else int(end)
    except (TypeError, ValueError):
        start = end = None
    cfg["animation_range"] = [start, end]
    cfg["use_custom_range"] = bool(raw.get("use_custom_range", False))

    # 条目列表：兼容旧字段名 data_store / items
    items_raw = raw.get("items", raw.get("data_store", {}))
    if not isinstance(items_raw, dict):
        items_raw = {}
    clean_items = empty_items()
    for type_key in config.TYPE_ORDER:
        entries = items_raw.get(type_key)
        if not isinstance(entries, list):
            continue
        clean = []
        for entry in entries:
            if not isinstance(entry, dict):
                continue
            obj = entry.get("object")
            if obj is None:
                continue
            clean.append({
                "object": obj,
                "export_name": _as_text(entry.get("export_name", "")) or _as_text(obj),
                "enabled": bool(entry.get("enabled", True)),
            })
        clean_items[type_key] = clean
    cfg["items"] = clean_items
    return cfg


# ---------------------------------------------------------------------------
# 场景 network 节点持久化
# ---------------------------------------------------------------------------
def _ensure_scene_node():
    """获取/创建保存配置的 network 节点，返回节点名"""
    if cmds.objExists(config.SCENE_NODE_NAME):
        node = config.SCENE_NODE_NAME
        if cmds.nodeType(node) != "network":
            cmds.warning(u"已存在同名节点但不是 network 类型，配置节点改名后重建: {0}".format(node))
            node = None
    else:
        node = None
    if node is None:
        base = config.SCENE_NODE_NAME
        name = base
        i = 1
        while cmds.objExists(name):
            name = "{0}_{1}".format(base, i)
            i += 1
        node = cmds.createNode("network", name=name)
    return node


def _chunk_attrs(node):
    """返回节点上按序号排好的配置块属性名列表"""
    names = cmds.listAttr(node, userDefined=True) or []
    prefix = config.ATTR_PREFIX + "_"
    chunks = []
    for n in names:
        if n.startswith(prefix):
            try:
                idx = int(n[len(prefix):])
            except ValueError:
                continue
            chunks.append((idx, n))
    chunks.sort()
    return [n for _idx, n in chunks]


def _set_attr(node, attr, text):
    if not cmds.attributeQuery(attr, node=node, exists=True):
        cmds.addAttr(node, longName=attr, dataType="string", keyable=False)
    cmds.setAttr("{0}.{1}".format(node, attr), text, type="string")


def write_config_to_node(raw_config):
    """把配置分块写入场景 network 节点（用于实时持久化，随场景保存）"""
    node = _ensure_scene_node()
    cfg = normalize_config(raw_config)
    text = json.dumps(cfg, ensure_ascii=False)
    chunks = [text[i:i + config.CHUNK_SIZE] for i in range(0, len(text), config.CHUNK_SIZE)]
    if not chunks:
        chunks = [u""]

    # 先清掉旧块属性，避免残留
    for attr in _chunk_attrs(node):
        cmds.deleteAttr(node, attribute=attr)
    for idx, chunk in enumerate(chunks):
        _set_attr(node, "{0}_{1:02d}".format(config.ATTR_PREFIX, idx), chunk)
    return node


def read_config_from_node():
    """从场景 network 节点读回配置；节点不存在或无数据返回 None"""
    if not cmds.objExists(config.SCENE_NODE_NAME):
        return None
    node = config.SCENE_NODE_NAME
    try:
        attrs = _chunk_attrs(node)
        if not attrs:
            return None
        text = "".join(cmds.getAttr("{0}.{1}".format(node, a)) or "" for a in attrs)
        if not text.strip():
            return None
        return json.loads(text)
    except Exception as exc:
        cmds.warning(u"读取场景配置失败: {0}".format(exc))
        return None


# ---------------------------------------------------------------------------
# 外部 JSON 文件持久化
# ---------------------------------------------------------------------------
def write_config_file(raw_config, file_path):
    """把配置写入 UTF-8 JSON 文件"""
    cfg = normalize_config(raw_config)
    with io.open(file_path, "w", encoding="utf-8") as fh:
        json.dump(cfg, fh, ensure_ascii=False, indent=4)
    return file_path


def read_config_file(file_path):
    """从 UTF-8 JSON 文件读配置；返回归一化后的字典或抛异常"""
    with io.open(file_path, "r", encoding="utf-8") as fh:
        raw = json.load(fh)
    return normalize_config(raw)
