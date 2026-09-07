# -*- coding: utf-8 -*-
"""
动画资产一键导出工具 - 核心逻辑层（与 UI 解耦）

持有当前条目数据 data_store，并提供：
- 条目管理辅助（替换 store、统计勾选项）
- 一键批量导出入口 run_export_batch（供 UI / 批处理复用）

UI 与场景节点持久化之间的粘合放在 ui.py（UI 负责读控件、调 persistence）。
"""
from . import config
from . import exporter
from . import utils


# 当前条目数据（UI 直接读写此字典）
data_store = {key: [] for key in config.TYPE_ORDER}


def replace_store(items):
    """用一份新的 items 字典替换当前数据（保持引用对象，供 UI 重建）"""
    data_store.clear()
    for key in config.TYPE_ORDER:
        data_store[key] = list((items or {}).get(key, []))


def count_total(store=None):
    """统计所有条目数"""
    store = data_store if store is None else store
    return sum(len(store.get(key, [])) for key in config.TYPE_ORDER)


def enabled_items(store=None):
    """返回所有勾选条目 [(type_key, item), ...]（按分类顺序）"""
    store = data_store if store is None else store
    result = []
    for type_key in config.TYPE_ORDER:
        for item in store.get(type_key, []):
            if item.get("enabled"):
                result.append((type_key, item))
    return result


def count_enabled(store=None):
    """统计勾选条目数量"""
    return len(enabled_items(store))


def _default_log(text):
    print(text)


def ensure_prefixed_name(name, prefix):
    """保证导出名带 前缀_；已带则不重复添加。prefix 为空时原样返回"""
    prefix = (prefix or "").strip()
    name = (name or u"").strip()
    if not prefix:
        return name
    if name == prefix or name.startswith(prefix + u"_"):
        return name
    if name:
        return u"{0}_{1}".format(prefix, name)
    return prefix


def run_export_batch(store, options, log=None):
    """执行勾选条目的一键导出（无 UI 依赖）

    options 需包含：
        export_dir   导出目录（可不存在，会创建）
        start, end   帧范围
        abc_cleanup  是否先清理 ABC
        prefix       命名前缀（可选）：导出前自动确保文件名带“前缀_”，
                     已带前缀的条目不会被重复拼接。
    log      可选回调 log(text)，用于输出每条进度；默认打印。

    返回 (successes, failures)
        successes: [(type_key, export_name, output_path), ...]
        failures:  [(type_key, export_name, error_message), ...]
    """
    if log is None:
        log = _default_log
    export_dir = utils.normalize_dir(options.get("export_dir"))
    utils.ensure_export_plugins()
    start = int(options.get("start", 1))
    end = int(options.get("end", 1))
    if start > end:
        start, end = end, start
    cleanup = bool(options.get("abc_cleanup", False))
    prefix = options.get("prefix") or u""

    successes = []
    failures = []
    for type_key, item in enabled_items(store):
        entry_name = item.get("export_name") or u"?"
        try:
            # 导出名统一确保带前缀（不修改列表中已显示的文本）
            eff_item = item
            if prefix:
                eff_item = dict(item)
                eff_item["export_name"] = ensure_prefixed_name(entry_name, prefix)
            if type_key == config.TYPE_FBX:
                out = exporter.export_fbx_item(eff_item, export_dir, start, end)
            elif type_key == config.TYPE_ABC:
                out = exporter.export_abc_item(eff_item, export_dir, start, end,
                                               do_cleanup=cleanup)
            elif type_key == config.TYPE_CAMERA:
                out = exporter.export_camera_item(eff_item, export_dir, start, end)
            else:
                raise RuntimeError(u"未知条目类型: {0}".format(type_key))
            successes.append((type_key, entry_name, out))
            log(u"[导出成功] {0} -> {1}".format(entry_name, out))
        except Exception as exc:
            failures.append((type_key, entry_name, str(exc)))
            log(u"[导出失败] {0}（{1}）: {2}".format(
                config.CATEGORY_NAMES.get(type_key, type_key), entry_name, exc))
    return successes, failures
