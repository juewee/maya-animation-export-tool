# -*- coding: utf-8 -*-
"""
动画资产一键导出工具 - 启动时的轻量自检

设计原则（不要占用太多资源）：
- 不遍历整个场景、不查蒙皮/网格/UV、不修改场景，只做少量只读查询；
- 只检查与本次导出直接相关的信息：环境 / 插件 / 导出目录 / 场景是否保存 / 配置条目；
- 条目检查有条数上限（MAX_ITEM_CHECK）与时间预算（TIME_BUDGET），
  超大配置也不会拖慢打开工具；
- 任何异常都吞掉并跳过该项，绝不影响窗口打开。

用法：
    issues = checks.light_check(store, export_dir, (start, end))
    print(checks.format_report(issues))
"""
import os
import time

import maya.cmds as cmds

from . import config

# 最多逐条检查多少条配置条目（超出的只计数，不再查询场景）
MAX_ITEM_CHECK = 200
# 条目检查的时间预算（秒）：超时提前结束，保证打开窗口不卡
TIME_BUDGET = 0.3

LEVEL_ERROR = "error"
LEVEL_WARN = "warn"
LEVEL_INFO = "info"

_LEVEL_ORDER = {LEVEL_ERROR: 0, LEVEL_WARN: 1, LEVEL_INFO: 2}
_LEVEL_LABEL = {LEVEL_ERROR: u"错误", LEVEL_WARN: u"提示", LEVEL_INFO: u"信息"}


def _add(issues, level, text):
    issues.append({"level": level, "text": text})


def _maya_version():
    try:
        return float(str(cmds.about(version=True)).split()[0])
    except Exception:
        return 0.0


def _check_environment(issues):
    version = _maya_version()
    if version and version < 2018.0:
        _add(issues, LEVEL_WARN,
             u"Maya 版本 {0} 低于 2018：部分 cmds 参数会自动降级".format(version))
    try:
        if cmds.about(batch=True):
            _add(issues, LEVEL_INFO,
                 u"当前是批处理（-batch）模式：不显示进度条，日志输出到 stdout")
    except Exception:
        pass


def _check_plugins(issues):
    """只查询插件是否已加载/是否找得到，不主动加载（避免打开窗口就有副作用）"""
    for plugin in config.PLUGIN_LIST:
        try:
            if cmds.pluginInfo(plugin, query=True, loaded=True):
                continue
        except Exception:
            pass
        try:
            exists = bool(cmds.pluginInfo(plugin, query=True, path=True))
        except Exception:
            exists = False
        if exists:
            _add(issues, LEVEL_INFO,
                 u"插件 {0} 未加载（导出时会自动加载）".format(plugin))
        else:
            _add(issues, LEVEL_ERROR,
                 u"找不到插件 {0}，FBX/ABC 导出会失败".format(plugin))


def _check_output_dir(issues, export_dir):
    export_dir = (export_dir or "").strip()
    if not export_dir:
        _add(issues, LEVEL_INFO, u"还没有设置导出目录")
        return
    if not os.path.isdir(export_dir):
        _add(issues, LEVEL_WARN,
             u"导出目录不存在（导出时会自动创建）：{0}".format(export_dir))
        return
    if not os.access(export_dir, os.W_OK):
        _add(issues, LEVEL_ERROR, u"导出目录不可写：{0}".format(export_dir))


def _check_scene(issues):
    try:
        scene = cmds.file(query=True, sceneName=True)
    except Exception:
        scene = ""
    if not scene:
        _add(issues, LEVEL_WARN,
             u"场景尚未保存：命名前缀无法自动识别，配置也不会随场景一起保存")
    return scene or ""


def _check_range(issues, frame_range):
    if not frame_range or len(frame_range) < 2:
        return
    start, end = frame_range[0], frame_range[1]
    try:
        if int(start) > int(end):
            _add(issues, LEVEL_WARN,
                 u"动画范围起点大于终点（{0} > {1}），导出时会自动交换".format(start, end))
    except (TypeError, ValueError):
        _add(issues, LEVEL_WARN, u"动画范围无效：{0}".format(frame_range))


def _has_camera(obj):
    try:
        if cmds.objectType(obj, isAType="camera"):
            return True
        return bool(cmds.listRelatives(obj, shapes=True, type="camera", fullPath=True)
                    or cmds.listRelatives(obj, ad=True, type="camera", fullPath=True))
    except Exception:
        return False


def _has_joint(obj):
    try:
        if cmds.objectType(obj, isAType="joint"):
            return True
        return bool(cmds.listRelatives(obj, ad=True, type="joint", fullPath=True))
    except Exception:
        return False


def _check_items(issues, store):
    """检查配置条目是否还在场景中（有条数上限与时间预算）"""
    if not store:
        return
    checked = 0
    skipped = 0
    missing = []
    deadline = time.time() + TIME_BUDGET

    for type_key in config.TYPE_ORDER:
        for item in store.get(type_key, []):
            obj = item.get("object")
            if not obj:
                continue
            if checked >= MAX_ITEM_CHECK or time.time() > deadline:
                skipped += 1
                continue
            checked += 1
            names = obj if isinstance(obj, (list, tuple)) else [obj]
            for name in names:
                if not name:
                    continue
                try:
                    exists = bool(cmds.objExists(name))
                except Exception:
                    exists = False
                if not exists:
                    missing.append(u"{0}".format(name))
                    continue
                if type_key == config.TYPE_CAMERA and not _has_camera(name):
                    _add(issues, LEVEL_ERROR,
                         u"相机条目 {0} 下没有 camera shape".format(name))
                elif type_key == config.TYPE_FBX and not _has_joint(name):
                    _add(issues, LEVEL_WARN,
                         u"FBX 条目 {0} 下没有骨骼（joint）".format(name))

    if missing:
        preview = u", ".join(missing[:8]) + (u" ..." if len(missing) > 8 else u"")
        _add(issues, LEVEL_ERROR,
             u"配置里有 {0} 个物体已不在场景中：{1}".format(len(missing), preview))
    if skipped:
        _add(issues, LEVEL_INFO,
             u"条目较多，本次只检查了前 {0} 条（跳过 {1} 条）".format(checked, skipped))


def light_check(store=None, export_dir=None, frame_range=None):
    """执行一次轻量自检，返回问题列表 [{"level": ..., "text": ...}, ...]"""
    issues = []
    for func, args in ((_check_environment, ()),
                       (_check_plugins, ()),
                       (_check_output_dir, (export_dir,)),
                       (_check_scene, ()),
                       (_check_range, (frame_range,)),
                       (_check_items, (store,))):
        try:
            func(issues, *args)
        except Exception:
            # 单项检查失败不影响其它检查，也绝不让窗口打不开
            pass
    issues.sort(key=lambda item: _LEVEL_ORDER.get(item.get("level"), 9))
    return issues


def summary_line(issues):
    """一行摘要，用于窗口里的状态提示"""
    if not issues:
        return u"自检通过：未发现问题"
    counts = {}
    for issue in issues:
        level = issue.get("level")
        counts[level] = counts.get(level, 0) + 1
    parts = []
    for level in (LEVEL_ERROR, LEVEL_WARN, LEVEL_INFO):
        if counts.get(level):
            parts.append(u"{0} {1}".format(counts[level], _LEVEL_LABEL[level]))
    return u"自检：" + u" / ".join(parts)


def has_errors(issues):
    return any(issue.get("level") == LEVEL_ERROR for issue in issues or [])


def format_report(issues, elapsed=None):
    """多行报告，输出到脚本编辑器"""
    lines = [u"===== 动画资产导出工具 · 启动自检 ====="]
    if elapsed is not None:
        lines.append(u"耗时 {0:.0f} ms".format(float(elapsed) * 1000.0))
    if not issues:
        lines.append(u"未发现问题。")
    else:
        for issue in issues:
            lines.append(u"[{0}] {1}".format(
                _LEVEL_LABEL.get(issue.get("level"), issue.get("level")),
                issue.get("text")))
    lines.append(u"=====================================")
    return u"\n".join(lines)
