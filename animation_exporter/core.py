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

# 最近一次 run_export_batch 是否被用户中止（进度条取消 / 相机检查里选了打开设置）
# 供 UI 汇总时区分“导出完成”与“导出已中止”
last_run_cancelled = False


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
        export_dir    导出目录（可不存在，会创建）
        start, end    帧范围
        abc_cleanup   是否先清理 ABC
        prefix        命名前缀（可选）：导出前自动确保文件名带“前缀_”，
                      已带前缀的条目不会被重复拼接。
        show_progress 是否显示 Maya 进度条（可选，默认取 config.EXPORT_OPTIONS）
    log      可选回调 log(text)，用于输出每条进度；默认打印。

    进度与取消：
        - 进度条由本函数 begin/end，每个条目分到 1/总数 的区间，
          条目内部由 exporter 按帧推进（utils.progress）；
        - 用户在进度条上点取消后，不再开始后续条目；正在烘焙的条目会抛
          “用户取消导出”，其临时节点由 exporter 的 finally 清理，不产出半成品；
        - mayapy / -batch 下没有进度条 UI，自动降级为一行日志。

    返回 (successes, failures)
        successes: [(type_key, export_name, output_path), ...]
        failures:  [(type_key, export_name, error_message), ...]
    """
    global last_run_cancelled
    last_run_cancelled = False
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
    enabled = enabled_items(store)
    total = len(enabled)

    # 导出进度：整体进度条由 core 统一 begin/end，每个条目分到 1/total 的区间，
    # 条目内部再由 exporter 按帧 step() 推进（mayapy 下自动降级为打印）
    show_progress = bool(options.get("show_progress",
                                     config.option("show_progress", True)))
    if show_progress and total:
        utils.progress.begin(u"导出中…（共 {0} 项）".format(total), u"准备中")

    cancelled = False
    try:
        for idx, (type_key, item) in enumerate(enabled, 1):
            entry_name = item.get("export_name") or u"?"
            cat_name = config.CATEGORY_NAMES.get(type_key, type_key)
            status = u"[{0}/{1}] {2}：{3}".format(idx, total, cat_name, entry_name)

            # 进度条上点了取消：不再开始后续条目（当前条目的临时节点由 exporter 清理）
            if show_progress and utils.progress.is_cancelled():
                cancelled = True
                log(u"用户取消导出，剩余 {0} 项未执行".format(total - idx + 1))
                break

            if show_progress:
                span = 1.0 / max(1, total)
                utils.progress.set_span((idx - 1) * span, span, status)

            log(u"[{0}/{1}] 正在导出 {2}：{3} ...".format(idx, total, cat_name, entry_name))
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
                log(u"[{0}/{1}] 导出成功：{2} -> {3}".format(idx, total, entry_name, out))
            except exporter.ExportCancelled as exc:
                # 用户主动中止（进度条取消 / 相机感光器检查里选了“打开设置”或“取消”）：
                # 不计入失败，直接停止后续条目
                cancelled = True
                log(u"[{0}/{1}] 已中止：{2}".format(idx, total, exc))
                break
            except Exception as exc:
                failures.append((type_key, entry_name, str(exc)))
                log(u"[{0}/{1}] 导出失败：{2}（{3}）: {4}".format(
                    idx, total, cat_name, entry_name, exc))
            finally:
                if show_progress:
                    utils.progress.step(1.0, status)
    finally:
        if show_progress:
            utils.progress.end()

    last_run_cancelled = cancelled
    if cancelled:
        log(u"导出已中止：成功 {0} 项，失败 {1} 项，其余条目未执行".format(
            len(successes), len(failures)))
    return successes, failures
