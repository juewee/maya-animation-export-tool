# -*- coding: utf-8 -*-
"""
动画资产一键导出工具 - UI 层

基于 Maya 内置 cmds（无 PySide2）。职责：
- 构建窗口与控件（“一键导出”固定在窗口顶部，始终可见）
- 收集用户操作并更新 core.data_store
- 每次数据变化后把配置实时同步到场景 network 节点（初步持久化）
- 窗口打开时若场景内已有配置节点则自动载入
- 命名规则：添加条目时自动加“场景前缀_物体名”；相机默认名为“Camera”，
  导出相机时文件名追加“_起帧-止帧”（如 S02_Camera_101-138）
- 调用 core.run_export_batch 执行导出并汇总结果
"""
import os
import time

import maya.cmds as cmds

from . import checks
from . import config as cfg
from . import core
from . import persistence
from . import utils

WINDOW_NAME = cfg.WINDOW_NAME
TYPE_FBX = cfg.TYPE_FBX
TYPE_ABC = cfg.TYPE_ABC
TYPE_CAMERA = cfg.TYPE_CAMERA

# UI 控件引用
ui_controls = {}

# 全局选项（镜像当前 UI 状态）
prefix_text = ""
abc_cleanup = False

# 防止“加载配置 / 场景节点”回写控件时触发同步的开关
_suppress_sync = False


# ---------------------------------------------------------------------------
# 配置收集与场景节点同步
# ---------------------------------------------------------------------------
def _read_dir_field():
    if ui_controls.get("dir_field") and cmds.textFieldButtonGrp(ui_controls["dir_field"], exists=True):
        return cmds.textFieldButtonGrp(ui_controls["dir_field"], query=True, text=True) or ""
    return ""


def _current_prefix():
    """读取当前前缀（以输入框为准，并回写全局）"""
    global prefix_text
    if ui_controls.get("prefix_field") and cmds.textField(ui_controls["prefix_field"], exists=True):
        prefix_text = cmds.textField(ui_controls["prefix_field"], query=True, text=True) or ""
    return prefix_text


def _read_abc_cleanup():
    global abc_cleanup
    if ui_controls.get("abc_cleanup_checkbox") and cmds.checkBox(ui_controls["abc_cleanup_checkbox"], exists=True):
        abc_cleanup = cmds.checkBox(ui_controls["abc_cleanup_checkbox"], query=True, value=True)
    return abc_cleanup


def build_config_dict():
    """从当前 UI 控件 + core.data_store 收集一份完整配置字典"""
    _read_abc_cleanup()
    prefix = _current_prefix()
    use_custom = False
    if ui_controls.get("range_custom_radio") and cmds.radioButton(ui_controls["range_custom_radio"], exists=True):
        use_custom = cmds.radioButton(ui_controls["range_custom_radio"], query=True, select=True)
    start, end = get_animation_range(use_custom=use_custom)
    return {
        "export_dir": _read_dir_field(),
        "use_prefix": True,  # 前缀现在始终自动应用，字段仅为兼容旧配置保留
        "prefix_text": prefix,
        "abc_cleanup": abc_cleanup,
        "use_custom_range": use_custom,
        "animation_range": [start, end],
        "items": core.data_store,
        # 命名模板与导出高级选项一起持久化（随场景节点 / JSON 文件保存）
        "naming": dict(cfg.NAMING_PRESETS),
        "options": dict(cfg.EXPORT_OPTIONS),
    }


def _sync_scene():
    """把当前配置写入场景 network 节点（实时持久化）"""
    if _suppress_sync:
        return
    try:
        persistence.write_config_to_node(build_config_dict())
    except Exception as exc:
        cmds.warning(u"同步配置到场景节点失败: {0}".format(exc))


def _notify_changed():
    """供各回调在修改数据后调用：同步场景节点"""
    _sync_scene()


# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
def _unique_entry_name(items, desired):
    """若列表中已存在同名条目，则返回 名字_2 / 名字_3 ... 避免导出文件互相覆盖"""
    if not any(it.get("export_name") == desired for it in items):
        return desired
    i = 2
    while any(it.get("export_name") == u"{0}_{1}".format(desired, i) for it in items):
        i += 1
    return u"{0}_{1}".format(desired, i)


# ===========================================================================
# 界面布局参数（全部集中在这里，手动微调数值后重新运行即可生效）
# 单位均为像素；元组里的多个数字按“从左到右”对应各列。
# 例如 ROW_COLS=(22,100,86,32,22) 表示条目行五列宽分别是
#   勾选框22 / 物体名按钮100 / 备注输入框86 / 更新按钮32 / 删除X按钮22。
#
# 重要公式（避免底部内容被吞）：
#   BODY_SCROLL_H 不要超过  WIN_H - TOP_FIXED_H
#   其中 TOP_FIXED_H ≈ 顶部一键导出按钮 + 提示文字 + 分隔条 + 上下边距，
#   下面默认按 90 估算，若你改了 EXPORT_BTN_H 等请同步调整。
#
# 宽度预算（决定窗口能否“瘦”下来）：
#   窗口内可用宽度 ≈ WIN_W - 30。
#   下面每个“列宽元组”的各列之和都不要超过它，否则该行会把整窗撑宽：
#     DIR_COLUMNS 440→288、PREFIX_COLS 560→290、ROW_COLS 396→262、
#     RADIO_COLS 300→248、FIELD_COLS 317→241、CFG_COLS 380→280
# ===========================================================================
LAYOUT = {
    # ---------- 窗口（窄而高 = 长条长方体）----------
    "WIN_W": 320,          # 窗口宽度（长条，越窄越“细条”）
    "WIN_H": 700,          # 窗口高度（长条越长越高）
    "TOP_FIXED_H": 90,     # 顶部固定区高（按钮+提示+分隔+边距估算值，用于算滚动区）

    # ---------- 顶部“一键导出”区 ----------
    "EXPORT_BTN_H": 44,    # 顶部一键导出按钮高度
    "TOP_SEP_H": 6,        # 顶部提示文字与分隔条之间距/分隔条高度

    # ---------- 中部内容滚动区 ----------
    "BODY_SCROLL_H": 590,  # 中部滚动区高度 = WIN_H - TOP_FIXED_H 左右，别超过
    "BODY_SPACING": 5,     # 中部区块（目录/分类/范围/按钮）之间的间距
    "BOTTOM_PAD": 50,      # 滚动内容底部留空，防止最后一行被滚动区底边吃掉

    # ---------- 通用分隔条 ----------
    "SEP_H": 8,            # 分隔条高度

    # ---------- 导出目录行（textFieldButtonGrp 两列）----------
    "DIR_COLUMNS": [(1, 236), (2, 52)],   # (1)路径输入框宽 (2)浏览按钮宽，合计288

    # ---------- 命名前缀行（columnWidth3，三列从左到右）----------
    "PREFIX_COLS": (70, 150, 70),       # (1)标签“命名前缀:” (2)输入框 (3)重新识别按钮，合计290

    # ---------- 资产分类（FBX/ABC/相机 各自 frame 内）----------
    "FRAME_PAD": 4,        # 分类框内左右留白
    "START_COLLAPSED": True,  # True=三个分类默认折叠（点标题展开），窗口更短
    "LIST_H": 70,          # 条目列表滚动区高度（只控制列表，不控制展开栏总高）
    "FRAME_EXTRA_H": 30,   # 展开栏内容区额外高度：列表+加号的共同上级(col)底部留白
    "ADD_ROW_COLS": (40, 8, 1),   # 加号行三列：(1)＋按钮列 (2)空隙 (3)自适应提示文字列
    "ADD_BTN_W": 36,       # “＋”按钮宽度
    "ADD_BTN_H": 26,       # “＋”按钮高度

    # ---------- 条目行（分类列表内每行 rowLayout，columnWidth5）----------
    # (1)勾选框 (2)物体名按钮 (3)备注/导出名输入框 (4)更新按钮 (5)删除X，合计262
    "ROW_COLS": (22, 100, 86, 32, 22),
    "ROW_H": 20,           # 行内按钮/控件高度

    # ---------- 动画范围 ----------
    "RADIO_COLS": (128, 64, 56),    # 单选行三列：(1)当前时间滑块 (2)自定义 (3)刷新按钮，合计248
    "FIELD_COLS": (36, 84, 36, 84, 1),  # 帧段行五列：(1)开始标签 (2)开始输入框 (3)结束标签 (4)结束输入框 (5)自适应空隙

    # ---------- 底部配置按钮行 ----------
    "CFG_COLS": (140, 140),          # 保存配置/加载配置 两按钮列宽，合计280

    # ---------- 相机多选弹窗 ----------
    "PICK_W": 480,         # 相机选择弹窗宽度
    "PICK_H": 340,         # 相机选择弹窗高度
    "PICK_LIST_H": 220,    # 弹窗内相机列表滚动区高度
    "PICK_BTN_COLS": (120, 120, 120),  # 弹窗按钮行三列：全选 / 取消 / 确定添加
}


def rebuild_category_ui(type_key):
    """清空并重建指定分类的行显示（所有控件显式指定 parent，避免上下文漂移）"""
    scroll = ui_controls.get("scroll_{0}".format(type_key))
    if not scroll or not cmds.scrollLayout(scroll, exists=True):
        return
    children = cmds.scrollLayout(scroll, query=True, childArray=True) or []
    for child in children:
        if cmds.layout(child, exists=True):
            cmds.deleteUI(child, layout=True)

    rows_layout = cmds.columnLayout(adjustableColumn=True, rowSpacing=2, parent=scroll)
    ui_controls["rows_{0}".format(type_key)] = rows_layout

    items = core.data_store.get(type_key, [])
    for idx, item in enumerate(items):
        # 行布局：5 列，宽度来自 LAYOUT["ROW_COLS"]
        #   (1)勾选框 (2)物体名按钮 (3)备注/导出名输入框 (4)更新按钮 (5)删除X按钮
        row = cmds.rowLayout(
            numberOfColumns=5,
            columnWidth5=LAYOUT["ROW_COLS"],
            columnAttach=[(1, 'both', 2), (2, 'both', 2), (3, 'both', 2),
                          (4, 'both', 2), (5, 'both', 2)],
            parent=rows_layout
        )

        cmds.checkBox(
            parent=row,
            label="",
            value=item.get("enabled", True),
            changeCommand=lambda checked, tk=type_key, i=idx: on_checkbox_changed(tk, i, checked),
            annotation=u"勾选是否导出"
        )

        obj_display = item["object"]
        if type_key == TYPE_ABC and isinstance(item["object"], (list, tuple)):
            obj_display = utils.abc_display_text(item["object"])
            full_text = utils.abc_full_text(item["object"])
            ann = u"点击选中组内所有物体\n完整列表: {0}".format(full_text)
        else:
            ann = u"点击选中 {0}".format(obj_display)
        cmds.button(
            parent=row,
            label=obj_display,
            command=lambda *args, tk=type_key, i=idx: on_object_btn_clicked(tk, i),
            annotation=ann,
            backgroundColor=(0.25, 0.25, 0.25),
            height=LAYOUT["ROW_H"]          # 行高（物体名按钮）
        )

        cmds.textField(
            parent=row,
            text=item.get("export_name", ""),
            changeCommand=lambda text, tk=type_key, i=idx: on_name_field_changed(tk, i, text),
            annotation=u"备注 / 导出文件名（可编辑；用“更新”换物体时不会被改写）"
        )

        # 更新按钮：把条目重新指向当前选择的物体，但保留上面填的备注
        cmds.button(
            parent=row,
            label=u"更新",
            width=LAYOUT["ROW_COLS"][3] - 4,
            height=LAYOUT["ROW_H"],
            command=lambda *args, tk=type_key, i=idx: on_update_btn_clicked(tk, i),
            annotation=u"用当前选择更新这个条目绑定的物体；备注（导出名）保持不变",
            backgroundColor=(0.22, 0.32, 0.45)
        )

        # 删除按钮用文字按钮，避免 symbolButton 图标缺失时显示空白
        cmds.button(
            parent=row,
            label="X",
            width=LAYOUT["ROW_COLS"][4] - 2,   # 删除按钮宽度≈第5列宽-2
            height=LAYOUT["ROW_H"],            # 行高（删除按钮）
            command=lambda *args, tk=type_key, i=idx: on_delete_btn_clicked(tk, i),
            annotation=u"删除此条目",
            backgroundColor=(0.45, 0.2, 0.2)
        )


def on_checkbox_changed(type_key, idx, checked):
    if 0 <= idx < len(core.data_store[type_key]):
        core.data_store[type_key][idx]["enabled"] = checked
    _notify_changed()


def on_name_field_changed(type_key, idx, text):
    if 0 <= idx < len(core.data_store[type_key]):
        core.data_store[type_key][idx]["export_name"] = text
    _notify_changed()


def on_delete_btn_clicked(type_key, idx):
    if 0 <= idx < len(core.data_store[type_key]):
        del core.data_store[type_key][idx]
        rebuild_category_ui(type_key)
    _notify_changed()


def on_update_btn_clicked(type_key, idx):
    """用当前选择更新该条目绑定的物体，**保留备注/导出名**。

    典型场景：物体被复制 / 改名 / 进了命名空间之后，原条目可能指向不存在或
    不唯一的名字。选中真正要导出的那个物体，点这一行上的“更新”即可重新绑定，
    不用删了重加、也不用重填备注。
    """
    if idx < 0 or idx >= len(core.data_store.get(type_key, [])):
        return
    sel = cmds.ls(sl=True, long=True)
    if not sel:
        cmds.warning(u"请先在场景中选择要更新为的物体（{0}）".format(
            cfg.CATEGORY_NAMES.get(type_key, type_key)))
        return

    if type_key == TYPE_ABC:
        names = []
        for obj in sel:
            short = utils.get_short_name(obj)
            if short not in names:
                names.append(short)
        _apply_entry_object(type_key, idx, names)
        return

    if type_key == TYPE_FBX:
        roots = []
        for obj in sel:
            if not utils.is_type_matching(obj, type_key):
                cmds.warning(u"物体 {0} 下未找到骨骼，已忽略".format(
                    utils.get_short_name(obj)))
                continue
            for root in utils.find_skeleton_roots(obj):
                if root not in roots:
                    roots.append(root)
        if not roots:
            cmds.warning(u"当前选择里没有找到骨骼根，条目未更新")
            return
        if len(roots) == 1:
            _apply_entry_object(type_key, idx, roots[0])
            return
        _open_root_chooser(
            _build_root_tags(sel[0], roots), utils.get_short_name(sel[0]),
            lambda picked: _apply_entry_object(
                type_key, idx, picked[0] if picked else None))
        return

    if type_key == TYPE_CAMERA:
        cameras = []
        for obj in sel:
            for cam in utils.find_camera_transforms(obj):
                if cam not in cameras:
                    cameras.append(cam)
        if not cameras:
            cmds.warning(u"当前选择里没有找到相机，条目未更新")
            return
        if len(cameras) == 1:
            _apply_entry_object(type_key, idx, utils.get_short_name(cameras[0]))
            return
        _open_camera_chooser(
            cameras,
            on_confirm=lambda picked: _apply_entry_object(
                type_key, idx, utils.get_short_name(picked[0])))
        return


def _apply_entry_object(type_key, idx, object_value):
    """把条目重新指向新物体；export_name（备注）一律保持原样"""
    items = core.data_store.get(type_key, [])
    if idx < 0 or idx >= len(items) or not object_value:
        return
    entry = items[idx]
    old = entry.get("object")
    entry["object"] = object_value
    rebuild_category_ui(type_key)
    _notify_changed()
    cmds.warning(u"已更新条目物体：{0}  →  {1}\n备注保持不变：{2}".format(
        old, object_value, entry.get("export_name") or u""))


def on_object_btn_clicked(type_key, idx):
    """点击物体名按钮，在场景中选中对应物体"""
    if idx < 0 or idx >= len(core.data_store[type_key]):
        return
    item = core.data_store[type_key][idx]
    cmds.select(clear=True)

    if type_key == TYPE_FBX:
        root = item["object"]
        if cmds.objExists(root):
            cmds.select(root, hierarchy=True)
        else:
            cmds.warning(u"骨骼根 {0} 不存在于场景中".format(root))
    elif type_key == TYPE_ABC:
        objs = item["object"] if isinstance(item["object"], (list, tuple)) else [item["object"]]
        valid = [o for o in objs if cmds.objExists(o)]
        if valid:
            cmds.select(valid)
        for o in objs:
            if not cmds.objExists(o):
                cmds.warning(u"物体 {0} 不存在于场景中".format(o))
    elif type_key == TYPE_CAMERA:
        cam = item["object"]
        if cmds.objExists(cam):
            if cmds.objectType(cam, isAType='camera'):
                parent = cmds.listRelatives(cam, parent=True, fullPath=True)
                if parent:
                    cmds.select(parent[0])
                else:
                    cmds.select(cam)
            else:
                cmds.select(cam)
        else:
            cmds.warning(u"相机 {0} 不存在于场景中".format(cam))


def _add_cameras(cameras):
    """把相机（完整路径列表）加入导出列表；不改动场景当前选择"""
    added = 0
    for cam in cameras:
        cam_short = utils.get_short_name(cam)
        if any(item["object"] == cam_short for item in core.data_store[TYPE_CAMERA]):
            cmds.warning(u"相机 {0} 已在列表中，跳过".format(cam_short))
            continue
        export_name = _unique_entry_name(core.data_store[TYPE_CAMERA], "Camera")
        core.data_store[TYPE_CAMERA].append({
            "object": cam_short,
            "export_name": export_name,
            "enabled": True,
        })
        added += 1
    if added > 0:
        rebuild_category_ui(TYPE_CAMERA)
        cmds.warning(u"已添加 {0} 个相机".format(added))
    _notify_changed()


def _build_root_tags(obj, roots):
    """为每个骨骼 Root 生成推荐/慎用标签。

    照搬 UEAnimCamExporter 的标签策略：
    - SkinCluster 影响骨骼 > 0 → [推荐：影响骨骼 N]
    - DeformationSystem → [DeformationSystem]
    - FitSkeleton → [慎用：FitSkeleton 参考骨架]
    - 其他 → 空标签
    """
    # 从源物体取 mesh skinCluster influence
    skin_infs = utils.get_skin_influences(obj)
    tags = []
    for root in roots:
        tag = utils.get_root_tag(root, obj, skin_infs)
        tags.append((root, tag))
    return tags


def _add_picked_roots(picked_roots, source_obj, type_key):
    """弹窗回调：把用户勾选的 Root 加入导出列表"""
    added = 0
    for root in picked_roots:
        if any(item["object"] == root for item in core.data_store[type_key]):
            cmds.warning(u"骨骼根 {0} 已在列表中，跳过".format(root))
            continue
        core.data_store[type_key].append({
            "object": root,
            "export_name": root,
            "enabled": True,
        })
        added += 1
    if added > 0:
        rebuild_category_ui(type_key)
        cmds.warning(u"已添加 {0} 个骨骼根".format(added))
    _notify_changed()


def _open_root_chooser(roots_with_tags, source_obj, on_confirm):
    """非阻塞的骨骼根选择弹窗：列出所有检测到的 Root，标注推荐/慎用。

    roots_with_tags: [(root_short, tag_text), ...]
    source_obj: 选中物体的短名（显示在标题）
    on_confirm: 回调 list[str] -> None，传入用户勾选的 root 列表
    """
    win_name = "animExportPickRootWin"
    if cmds.window(win_name, exists=True):
        cmds.deleteUI(win_name)
    if not roots_with_tags:
        return

    win = cmds.window(win_name,
                      title=u"选择骨骼根 - {0}".format(source_obj),
                      widthHeight=(500, 350),
                      sizeable=True, minimizeButton=False, maximizeButton=False)
    col = cmds.columnLayout(adjustableColumn=True, rowSpacing=4, columnAttach=('both', 8))
    cmds.text(parent=col,
              label=u"发现 {0} 个骨骼根，勾选要添加的（推荐项已标注）：".format(
                  len(roots_with_tags)),
              align="left")
    scroll = cmds.scrollLayout(parent=col, height=200, childResizable=True)
    list_col = cmds.columnLayout(parent=scroll, adjustableColumn=True, rowSpacing=2)
    cbs = {}
    for root, tag in roots_with_tags:
        label = root
        if tag:
            label = u"{0}    [{1}]".format(root, tag)
        # 推荐项默认勾选，慎用项默认不勾选
        default = "慎用" not in tag and "跳过" not in tag
        cbs[root] = cmds.checkBox(parent=list_col, label=label, value=default)
    btn_row = cmds.rowLayout(parent=col, numberOfColumns=3,
                            columnWidth3=(120, 120, 120),
                            columnAttach=[(1, 'both', 5), (2, 'both', 5), (3, 'both', 5)])

    def _finish():
        picked = [root[0] for root in roots_with_tags
                  if cmds.checkBox(cbs[root[0]], query=True, value=True)]
        cmds.deleteUI(win_name)
        if picked:
            on_confirm(picked)

    cmds.button(parent=btn_row, label=u"全选",
                command=lambda *a: [cmds.checkBox(cb, edit=True, value=True)
                                   for cb in cbs.values()])
    cmds.button(parent=btn_row, label=u"取消",
                command=lambda *a: cmds.deleteUI(win_name))
    cmds.button(parent=btn_row, label=u"确定", command=lambda *a: _finish())
    cmds.showWindow(win)


def _open_camera_chooser(cameras, on_confirm=None):
    """非阻塞的多相机选择弹窗：立即返回，不锁 Maya。

    内部单独记录要选择的相机（完整路径）；点“确定”后回调 on_confirm(选中路径列表)，
    没传回调时按“新增条目”处理（_add_cameras）。全程不改动用户在场景里的选择。
    """
    win_name = "animExportPickCamWin"
    if cmds.window(win_name, exists=True):
        cmds.deleteUI(win_name)

    entries = []
    seen = set()
    for cam in cameras:
        short = utils.get_short_name(cam)
        if short in seen:
            continue
        seen.add(short)
        entries.append((short, cam))
    if not entries:
        return

    win = cmds.window(win_name, title=u"选择要添加的相机",
                      widthHeight=(LAYOUT["PICK_W"], LAYOUT["PICK_H"]),
                      sizeable=True, minimizeButton=False, maximizeButton=False)
    col = cmds.columnLayout(adjustableColumn=True, rowSpacing=4, columnAttach=('both', 8))
    cmds.text(parent=col, label=u"所选物体下发现多个相机，勾选要添加的：", align="left")
    scroll = cmds.scrollLayout(parent=col, height=LAYOUT["PICK_LIST_H"], childResizable=True)
    list_col = cmds.columnLayout(parent=scroll, adjustableColumn=True, rowSpacing=2)
    cbs = {}
    for short, _path in entries:
        cbs[short] = cmds.checkBox(parent=list_col, label=short, value=True,
                                   annotation=u"勾选后添加到导出列表")
    # 按钮行：三列 = 全选 / 取消 / 确定添加
    btn_row = cmds.rowLayout(parent=col, numberOfColumns=3,
                             columnWidth3=LAYOUT["PICK_BTN_COLS"],
                             columnAttach=[(1, 'both', 5), (2, 'both', 5), (3, 'both', 5)])

    def _finish():
        picked = [path for short, path in entries
                  if cmds.checkBox(cbs[short], query=True, value=True)]
        cmds.deleteUI(win_name)
        if not picked:
            return
        if on_confirm is not None:
            on_confirm(picked)
        else:
            _add_cameras(picked)

    cmds.button(parent=btn_row, label=u"全选",
                command=lambda *a: [cmds.checkBox(cb, edit=True, value=True)
                                    for cb in cbs.values()])
    cmds.button(parent=btn_row, label=u"取消",
                command=lambda *a: cmds.deleteUI(win_name))
    cmds.button(parent=btn_row, label=u"确定", command=lambda *a: _finish())
    cmds.showWindow(win)
    # 注意：这里不阻塞、不轮询，窗口由用户自己关闭，Maya 全程可响应


# ---------------------------------------------------------------------------
# 添加选中物体到分类
# ---------------------------------------------------------------------------
def add_selected_to_category(type_key):
    sel = cmds.ls(sl=True, long=True)
    if not sel:
        cmds.warning(u"请先在场景中选择要添加的物体")
        return

    added_count = 0

    if type_key == TYPE_FBX:
        for obj in sel:
            short_name = utils.get_short_name(obj)
            if not utils.is_type_matching(obj, type_key):
                cmds.warning(u"物体 {0} 下未找到骨骼，已跳过".format(short_name))
                continue
            roots = utils.find_skeleton_roots(obj)
            if not roots:
                cmds.warning(u"物体 {0} 下未找到骨骼根关节，已跳过".format(short_name))
                continue

            # 多个 Root 时弹窗让用户选择，标注推荐/慎用
            if len(roots) > 1:
                roots_with_tags = _build_root_tags(obj, roots)
                _open_root_chooser(
                    roots_with_tags, short_name,
                    lambda picked, _obj=obj, _type=type_key: _add_picked_roots(
                        picked, _obj, _type))
                continue  # 弹窗是非阻塞的，跳过自动添加

            # 单个 Root 直接添加
            for root in roots:
                if any(item["object"] == root for item in core.data_store[type_key]):
                    cmds.warning(u"骨骼根 {0} 已在列表中，跳过".format(root))
                    continue
                export_name = root
                core.data_store[type_key].append({
                    "object": root,
                    "export_name": export_name,
                    "enabled": True,
                })
                added_count += 1
        if added_count > 0:
            rebuild_category_ui(type_key)
            cmds.warning(u"已添加 {0} 个骨骼根".format(added_count))
        _notify_changed()
        return

    if type_key == TYPE_ABC:
        valid_objects = [utils.get_short_name(obj) for obj in sel]
        if not valid_objects:
            cmds.warning(u"请选择至少一个物体")
            return
        first_obj = valid_objects[0]
        export_name = first_obj  # 前缀在导出时自动加
        existing = any(
            isinstance(item["object"], (list, tuple)) and set(item["object"]) == set(valid_objects)
            for item in core.data_store[type_key]
        )
        if existing:
            cmds.warning(u"该物体组合已在列表中，跳过")
            return
        core.data_store[type_key].append({
            "object": valid_objects,
            "export_name": export_name,
            "enabled": True,
        })
        rebuild_category_ui(type_key)
        cmds.warning(u"已添加 ABC 导出组（{0} 个物体）".format(len(valid_objects)))
        _notify_changed()
        return

    if type_key == TYPE_CAMERA:
        # 先汇总所有选中物体（或其绑定组）里的相机，记录为内部导出目标，
        # 全程不改动用户的场景选择
        found = []
        for obj in sel:
            cameras = utils.find_camera_transforms(obj)
            if not cameras:
                cmds.warning(u"物体 {0} 下未找到相机，已跳过（绑定相机请直接选其所在组）"
                             .format(utils.get_short_name(obj)))
                continue
            for cam in cameras:
                if cam not in found:
                    found.append(cam)
        if not found:
            cmds.warning(u"请选择相机或其所在绑定组")
            return
        if len(found) == 1:
            _add_cameras(found)
        else:
            # 组里/多选里有多个相机：非阻塞弹窗勾选（内部记录，不影响原选择）
            _open_camera_chooser(found)
        return


# ---------------------------------------------------------------------------
# 目录 / 前缀 / 清理选项回调
# ---------------------------------------------------------------------------
def on_browse_dir():
    current_dir = _read_dir_field() or cmds.workspace(q=True, rd=True)
    result = cmds.fileDialog2(dialogStyle=2, fileMode=3, caption=u"选择导出目录", startingDirectory=current_dir)
    if result:
        cmds.textFieldButtonGrp(ui_controls["dir_field"], edit=True, text=result[0])
        _notify_changed()


def update_prefix_from_scene(silent=False):
    """从场景文件名自动识别前缀（如 S02_xxx.mb -> S02），成功返回 True"""
    scene_name = cmds.file(q=True, sceneName=True, shortName=True)
    if not scene_name:
        if not silent:
            cmds.warning(u"场景尚未保存，无法自动识别前缀")
        return False
    base = os.path.splitext(scene_name)[0]
    prefix = base.split('_')[0] if '_' in base else base
    global prefix_text
    prefix_text = prefix
    if ui_controls.get("prefix_field") and cmds.textField(ui_controls["prefix_field"], exists=True):
        cmds.textField(ui_controls["prefix_field"], edit=True, text=prefix)
    _notify_changed()
    return True


def on_prefix_field_changed(text):
    global prefix_text
    prefix_text = text
    _notify_changed()


def on_abc_cleanup_changed(checked):
    global abc_cleanup
    abc_cleanup = checked
    _notify_changed()


# ---------------------------------------------------------------------------
# 动画范围
# ---------------------------------------------------------------------------
def get_animation_range(use_custom=None):
    """返回 (start, end)；use_custom=None 时按当前单选状态决定"""
    if use_custom is None:
        use_custom = False
        if ui_controls.get("range_custom_radio") and cmds.radioButton(ui_controls["range_custom_radio"], exists=True):
            use_custom = cmds.radioButton(ui_controls["range_custom_radio"], query=True, select=True)
    if use_custom:
        start = cmds.intField(ui_controls["start_field"], query=True, value=True)
        end = cmds.intField(ui_controls["end_field"], query=True, value=True)
        return start, end
    start = cmds.playbackOptions(q=True, minTime=True)
    end = cmds.playbackOptions(q=True, maxTime=True)
    return int(start), int(end)


def refresh_range_from_timeline(silent=False):
    """把“当前时间滑块”的范围（playbackOptions min/max）读进开始/结束输入框。

    导出时选“当前时间滑块”本来就是实时读取时间轴范围，这个刷新只让界面显示的
    数字和实际一致：在 Maya 里改了时间轴范围后点一下即可。
    """
    try:
        start = int(cmds.playbackOptions(q=True, minTime=True))
        end = int(cmds.playbackOptions(q=True, maxTime=True))
    except Exception as exc:
        if not silent:
            cmds.warning(u"读取当前时间滑块范围失败：{0}".format(exc))
        return None

    for key, value in (("start_field", start), ("end_field", end)):
        ctrl = ui_controls.get(key)
        if ctrl and cmds.intField(ctrl, exists=True):
            try:
                cmds.intField(ctrl, edit=True, value=value)
            except Exception:
                pass
    _notify_changed()
    if not silent:
        cmds.warning(u"已同步当前时间滑块范围：{0} - {1}".format(start, end))
    return start, end


def toggle_range_fields(enable_custom):
    start_field = ui_controls.get("start_field")
    end_field = ui_controls.get("end_field")
    if start_field and cmds.intField(start_field, exists=True):
        cmds.intField(start_field, edit=True, enable=enable_custom)
    if end_field and cmds.intField(end_field, exists=True):
        cmds.intField(end_field, edit=True, enable=enable_custom)


def on_range_radio_current(*_args):
    toggle_range_fields(False)
    # 切回“当前时间滑块”时顺手同步一次显示（导出本来就是实时读取时间轴）
    refresh_range_from_timeline(silent=True)


def on_range_radio_custom(*_args):
    toggle_range_fields(True)
    _notify_changed()


def on_range_int_changed(*_args):
    _notify_changed()


# ---------------------------------------------------------------------------
# 配置应用（场景节点 / JSON 文件 -> UI）
# ---------------------------------------------------------------------------
def apply_config(raw):
    """把一份配置应用到 UI 与 core.data_store"""
    global prefix_text, abc_cleanup, _suppress_sync
    data = persistence.normalize_config(raw)

    _suppress_sync = True
    try:
        # 命名模板 / 导出选项写回 config，并刷新可能开着的设置面板
        persistence.apply_runtime_config(data)
        _refresh_settings_from_config()

        cmds.textFieldButtonGrp(ui_controls["dir_field"], edit=True, text=data["export_dir"] or "")

        prefix_text = data["prefix_text"]
        cmds.textField(ui_controls["prefix_field"], edit=True, text=prefix_text)

        abc_cleanup = data["abc_cleanup"]
        cmds.checkBox(ui_controls["abc_cleanup_checkbox"], edit=True, value=abc_cleanup)

        if data["use_custom_range"]:
            cmds.radioButton(ui_controls["range_custom_radio"], edit=True, select=True)
            start, end = data["animation_range"] or (None, None)
            if start is not None:
                cmds.intField(ui_controls["start_field"], edit=True, value=int(start))
            if end is not None:
                cmds.intField(ui_controls["end_field"], edit=True, value=int(end))
            toggle_range_fields(True)
        else:
            cmds.radioButton(ui_controls["range_current_radio"], edit=True, select=True)
            toggle_range_fields(False)
            # “当前时间滑块”模式以场景时间轴为准，载入后同步显示
            refresh_range_from_timeline(silent=True)

        core.replace_store(data["items"])
        for type_key in [TYPE_FBX, TYPE_ABC, TYPE_CAMERA]:
            rebuild_category_ui(type_key)
    finally:
        _suppress_sync = False

    # 前缀为空且场景已保存时自动识别，保证新增条目自动带上前缀
    if not _current_prefix():
        update_prefix_from_scene(silent=True)
    _sync_scene()


# ---------------------------------------------------------------------------
# 配置保存 / 加载（外部 JSON 文件）
# ---------------------------------------------------------------------------
def on_save_config():
    file_path = cmds.fileDialog2(dialogStyle=2, fileMode=0, caption=u"保存配置到文件",
                                 fileFilter="JSON Files (*.json)")
    if not file_path:
        return
    file_path = file_path[0]
    if not file_path.lower().endswith(".json"):
        file_path += ".json"
    try:
        persistence.write_config_file(build_config_dict(), file_path)
        cmds.warning(u"配置已保存：{0}".format(file_path))
    except Exception as exc:
        cmds.warning(u"保存配置失败：{0}".format(exc))


def on_load_config():
    file_path = cmds.fileDialog2(dialogStyle=2, fileMode=1, caption=u"从文件加载配置",
                                 fileFilter="JSON Files (*.json)")
    if not file_path:
        return
    try:
        data = persistence.read_config_file(file_path[0])
        apply_config(data)
        cmds.warning(u"配置已从文件加载。")
    except Exception as exc:
        cmds.warning(u"加载配置失败：{0}".format(exc))


# ---------------------------------------------------------------------------
# 设置面板
# ---------------------------------------------------------------------------
SETTINGS_WIN = "animExportSettingsWin"
SETTINGS_LAYOUT = {
    "WIN_W": 430,
    "WIN_H": 600,
    "SCROLL_H": 470,
    "LABEL_W": 210,
}

# 设置面板控件表：{配置键: (控件名, 控件类型)}；类型用于读写值
_settings_controls = {}


def _set_control_value(ctrl, kind, value):
    if not ctrl or not cmds.control(ctrl, exists=True):
        return
    try:
        if kind == "text":
            cmds.textField(ctrl, edit=True, text=value or "")
        elif kind == "check":
            cmds.checkBox(ctrl, edit=True, value=bool(value))
        elif kind == "int":
            cmds.intField(ctrl, edit=True, value=int(value))
        elif kind == "float":
            cmds.floatField(ctrl, edit=True, value=float(value))
    except Exception:
        pass


def _get_control_value(ctrl, kind):
    try:
        if kind == "text":
            return cmds.textField(ctrl, query=True, text=True)
        if kind == "check":
            return cmds.checkBox(ctrl, query=True, value=True)
        if kind == "int":
            return cmds.intField(ctrl, query=True, value=True)
        return cmds.floatField(ctrl, query=True, value=True)
    except Exception:
        return None


def _refresh_settings_from_config():
    """把当前 config 值刷回设置面板（加载配置 / 恢复默认时用）"""
    if not cmds.window(SETTINGS_WIN, exists=True):
        return
    for key, (ctrl, kind) in _settings_controls.items():
        if key in cfg.NAMING_PRESETS:
            _set_control_value(ctrl, kind, cfg.NAMING_PRESETS[key])
        elif key in cfg.EXPORT_OPTIONS:
            _set_control_value(ctrl, kind, cfg.EXPORT_OPTIONS[key])


def _settings_row(parent, label, key, kind, annotation="", width=None):
    """在设置面板里加一行“标签 + 输入控件”，并登记到 _settings_controls"""
    row = cmds.rowLayout(parent=parent, numberOfColumns=2,
                         columnWidth2=(SETTINGS_LAYOUT["LABEL_W"], width or 180),
                         columnAttach=[(1, 'both', 2), (2, 'both', 2)],
                         adjustableColumn=2)
    cmds.text(parent=row, label=label, align="right", annotation=annotation or label)
    if kind == "text":
        ctrl = cmds.textField(parent=row, text="", annotation=annotation or label)
    elif kind == "check":
        ctrl = cmds.checkBox(parent=row, label="", value=False,
                             annotation=annotation or label)
    elif kind == "int":
        ctrl = cmds.intField(parent=row, value=1, minValue=1, maxValue=1000,
                             annotation=annotation or label)
    else:
        ctrl = cmds.floatField(parent=row, value=0.005, precision=4,
                               minValue=0.0, maxValue=1.0,
                               annotation=annotation or label)
    _settings_controls[key] = (ctrl, kind)
    if key in cfg.NAMING_PRESETS:
        _set_control_value(ctrl, kind, cfg.NAMING_PRESETS[key])
    elif key in cfg.EXPORT_OPTIONS:
        _set_control_value(ctrl, kind, cfg.EXPORT_OPTIONS[key])
    return ctrl


def _open_settings():
    """设置面板：命名后缀 + 导出高级选项。

    选项默认值全部对齐参考工具 UEAnimCamExporter（已验证可用的那版），
    尤其是相机轴向转换（默认关闭）与相机 Bake 方式。
    """
    if cmds.window(SETTINGS_WIN, exists=True):
        cmds.deleteUI(SETTINGS_WIN)
    _settings_controls.clear()

    win = cmds.window(SETTINGS_WIN, title=u"导出设置",
                      widthHeight=(SETTINGS_LAYOUT["WIN_W"], SETTINGS_LAYOUT["WIN_H"]),
                      sizeable=True, minimizeButton=False, maximizeButton=False)
    main = cmds.columnLayout(parent=win, adjustableColumn=True, rowSpacing=5,
                             columnAttach=('both', 8))
    scroll = cmds.scrollLayout(parent=main, height=SETTINGS_LAYOUT["SCROLL_H"],
                               childResizable=True)
    col = cmds.columnLayout(parent=scroll, adjustableColumn=True, rowSpacing=4,
                            columnAttach=('both', 6))

    # ---- 命名规范 ----
    frame = cmds.frameLayout(parent=col, label=u"命名规范", collapsable=True,
                             collapse=False, marginWidth=6, marginHeight=4)
    body = cmds.columnLayout(parent=frame, adjustableColumn=True, rowSpacing=3)
    cmds.text(parent=body, label=u"可用变量：{name} 导出名 / {start} 起始帧 / {end} 结束帧",
              align="left", font="smallPlainLabelFont", wordWrap=True)
    cmds.text(parent=body, label=u"留空则只用导出名，不加后缀", align="left",
              font="smallPlainLabelFont")
    _settings_row(body, u"FBX 骨骼动画后缀:", "fbx_anim_suffix", "text",
                  annotation=u"例如 _Anim_{start}-{end} → S02_Root_Anim_101-251.fbx")
    _settings_row(body, u"相机动画后缀:", "camera_suffix", "text",
                  annotation=u"例如 _{start}-{end} → S02_Camera_101-138.fbx")
    _settings_row(body, u"ABC 文件名追加帧范围:", "abc_add_range", "check",
                  annotation=u"勾选后 ABC 文件名也会带 _起帧-止帧")

    # ---- ABC 几何体缓存 ----
    frame = cmds.frameLayout(parent=col, label=u"ABC 几何体缓存", collapsable=True,
                             collapse=False, marginWidth=6, marginHeight=4)
    body = cmds.columnLayout(parent=frame, adjustableColumn=True, rowSpacing=3)
    _settings_row(body, u"去除命名空间:", "abc_strip_namespaces", "check",
                  annotation=u"对应 AbcExport 的 -stripNamespaces（默认开）。"
                             u"命名空间里存在同名物体时（复制/引用造成），去掉命名空间会重名，"
                             u"AbcExport 会直接报错导不出来——这种情况请取消勾选")

    # ---- 通用 ----
    frame = cmds.frameLayout(parent=col, label=u"通用", collapsable=True,
                             collapse=False, marginWidth=6, marginHeight=4)
    body = cmds.columnLayout(parent=frame, adjustableColumn=True, rowSpacing=3)
    _settings_row(body, u"采样步长:", "sample_by", "int",
                  annotation=u"bakeResults 的 sampleBy 与 FBXExportBakeComplexStep 都用它；1=逐帧")
    _settings_row(body, u"显示导出进度条:", "show_progress", "check",
                  annotation=u"导出时显示 Maya 进度条（可取消）；批处理模式自动跳过")
    _settings_row(body, u"打开工具时自检:", "startup_check", "check",
                  annotation=u"打开窗口时做一次轻量自检（只查插件/目录/条目是否存在，"
                             u"不遍历场景），结果输出到脚本编辑器")

    # ---- FBX 骨骼动画 ----
    frame = cmds.frameLayout(parent=col, label=u"FBX 骨骼动画", collapsable=True,
                             collapse=True, marginWidth=6, marginHeight=4)
    body = cmds.columnLayout(parent=frame, adjustableColumn=True, rowSpacing=3)
    _settings_row(body, u"Z-Up / ConvertAnimation:", "fbx_z_up", "check",
                  annotation=u"骨骼/动画 FBX 的轴向转换。参考工具 RIG/Anim 默认开启，"
                             u"当前 RIG 已正常时保持现状。")

    # ---- 相机动画 ----
    frame = cmds.frameLayout(parent=col, label=u"相机动画", collapsable=True,
                             collapse=False, marginWidth=6, marginHeight=4)
    body = cmds.columnLayout(parent=frame, adjustableColumn=True, rowSpacing=3)
    _settings_row(body, u"Z-Up / ConvertAnimation:", "camera_z_up", "check",
                  annotation=u"参考工具“Camera Z-Up Convert”默认关闭：相机位置/方向不对时"
                             u"再单独打开测试，避免 Maya 导出与 UE 导入各转换一次（二次转换）"
                             u"导致视角对不上。不要影响已经正常的 RIG/Anim。")
    _settings_row(body, u"临时相机挂世界根:", "camera_world_root", "check",
                  annotation=u"FBX 里没有额外父级，避免 UE Sequencer 导入时父级偏移")
    _settings_row(body, u"ParentConstraint Bake:", "camera_parent_bake", "check",
                  annotation=u"参考 export_camera_20.py：复制相机、解父级、ParentConstraint"
                             u"源相机，再用 bakeResults(shape=True) 烘焙。"
                             u"取消勾选则改用世界矩阵逐帧采样")
    _settings_row(body, u"检测相机控制器/约束:", "camera_detect_rig", "check",
                  annotation=u"扫描相机的父级 Zero 组、控制器、约束和动画节点，只写日志提示，"
                             u"这些控制器不会被导出")
    _settings_row(body, u"只 Bake 实际动画段:", "camera_use_anim_range", "check",
                  annotation=u"扫描相机/Shape/父级/约束/控制器的关键帧，只 Bake 有动画的帧段")
    _settings_row(body, u"实际动画段限制在 Start/End 内:", "camera_clamp_anim_range", "check",
                  annotation=u"避免 Bake 时间轴外或 UI 帧段外的动画")
    _settings_row(body, u"导出前检查感光器/分辨率:", "camera_check_sensor", "check",
                  annotation=u"比较 Render Settings 分辨率比例与相机 Film Aperture 比例；"
                             u"不一致时提示（UE 认 Filmback，不认 Maya 分辨率）")
    _settings_row(body, u"感光器比例容差:", "camera_aperture_tolerance", "float",
                  annotation=u"相对容差，默认 0.005（0.5%），超过才提示")

    cmds.separator(parent=main, height=6, style='in')

    def _apply_settings():
        for key, (ctrl, kind) in list(_settings_controls.items()):
            value = _get_control_value(ctrl, kind)
            if value is None:
                continue
            if key in cfg.NAMING_PRESETS:
                cfg.NAMING_PRESETS[key] = value
            elif key in cfg.EXPORT_OPTIONS:
                cfg.EXPORT_OPTIONS[key] = value
        # 采样步长 / 容差做范围保护
        try:
            cfg.EXPORT_OPTIONS["sample_by"] = max(1, int(cfg.EXPORT_OPTIONS.get("sample_by", 1)))
        except (TypeError, ValueError):
            cfg.EXPORT_OPTIONS["sample_by"] = 1
        try:
            tolerance = float(cfg.EXPORT_OPTIONS.get("camera_aperture_tolerance", 0.005))
        except (TypeError, ValueError):
            tolerance = 0.005
        cfg.EXPORT_OPTIONS["camera_aperture_tolerance"] = tolerance if tolerance > 0 else 0.005

        _notify_changed()
        cmds.deleteUI(SETTINGS_WIN)
        cmds.warning(u"设置已应用并保存到场景节点（相机 Z-Up 转换：{0}）".format(
            u"开" if cfg.EXPORT_OPTIONS.get("camera_z_up") else u"关"))

    def _restore_defaults():
        cfg.reset_options()
        _refresh_settings_from_config()
        _notify_changed()
        cmds.warning(u"已恢复默认设置（相机 Z-Up 转换默认关闭，与参考工具一致）")

    btn_row = cmds.rowLayout(parent=main, numberOfColumns=3,
                             columnWidth3=(110, 90, 90),
                             columnAttach=[(1, 'both', 5), (2, 'both', 5), (3, 'both', 5)],
                             adjustableColumn=1)
    cmds.button(parent=btn_row, label=u"恢复默认",
                command=lambda *a: _restore_defaults(),
                annotation=u"把命名后缀与所有导出选项恢复为参考工具默认值")
    cmds.button(parent=btn_row, label=u"取消",
                command=lambda *a: cmds.deleteUI(SETTINGS_WIN))
    cmds.button(parent=btn_row, label=u"应用", backgroundColor=(0.25, 0.5, 0.85),
                command=lambda *a: _apply_settings())
    cmds.showWindow(win)


# ---------------------------------------------------------------------------
# 一键导出
# ---------------------------------------------------------------------------
def _focus_node_for_settings(node):
    """选中节点（相机则连 camera shape 一起选）并让属性编辑器显示它。

    用于“相机感光器/分辨率不匹配”弹窗里点“打开设置”之后：导出收尾时保持这台
    相机被选中，用户打开属性编辑器就能直接改 Film Aperture。
    """
    if not node or not cmds.objExists(node):
        return
    targets = [node]
    try:
        shapes = cmds.listRelatives(node, shapes=True, fullPath=True) or []
        for shp in shapes:
            if shp not in targets:
                targets.append(shp)
    except Exception:
        pass
    try:
        cmds.select(targets, replace=True)
    except Exception:
        pass
    try:
        utils.mel_eval("AttributeEditor;")
    except Exception:
        pass


def on_export():
    _sync_scene()  # 导出前先把当前状态写入场景节点
    if core.count_enabled() == 0:
        cmds.warning(u"没有勾选任何要导出的条目。")
        return

    options = {
        "export_dir": _read_dir_field(),
        "start": get_animation_range()[0],
        "end": get_animation_range()[1],
        "abc_cleanup": _read_abc_cleanup(),
        "prefix": _current_prefix(),
        "show_progress": bool(cfg.option("show_progress", True)),
    }
    # 记录导出前用户选择，导出结束后整体恢复（导出过程内部自选导出对象，
    # 相机/FBX/ABC 都不会改动用户原始的选中内容）
    saved_sel = cmds.ls(sl=True, long=True) or []
    try:
        successes, failures = core.run_export_batch(core.data_store, options)
    except RuntimeError as exc:  # 目录 / 插件等前置错误
        cmds.warning(str(exc))
        return
    finally:
        # 导出中途若用户点了“相机感光器不匹配 -> 打开设置”，说明他要去改这台相机：
        # 保持相机被选中（属性编辑器才不会又被恢复选择顶掉）；否则恢复导出前的选择。
        focus = utils.consume_focus_node()
        if focus:
            _focus_node_for_settings(focus)
        else:
            utils.restore_selection(saved_sel)

    if core.last_run_cancelled:
        msg = u"导出已中止：成功 {0} 项，失败 {1} 项，其余条目未执行。".format(
            len(successes), len(failures))
    else:
        msg = u"导出完成：成功 {0} 项，失败 {1} 项。".format(len(successes), len(failures))
    if successes:
        msg += u"\n成功输出：\n" + u"\n".join(
            u"  - {0}".format(out) for _tk, _nm, out in successes)
    if failures:
        msg += u"\n失败详情：\n" + u"\n".join(
            u"  - {0}（{1}）: {2}".format(cfg.CATEGORY_NAMES.get(tk, tk), nm, err)
            for tk, nm, err in failures)
    cmds.warning(msg)


# ---------------------------------------------------------------------------
# 主窗口构建
# ---------------------------------------------------------------------------
def build_ui():
    global _suppress_sync
    if cmds.window(WINDOW_NAME, exists=True):
        cmds.deleteUI(WINDOW_NAME)

    _suppress_sync = True
    try:
        # 所有宽/高/列宽都来自上方 LAYOUT 字典，直接改那里再运行即可。
        # 每个控件都显式指定 parent，不依赖隐式父级/setParent。
        window = cmds.window(WINDOW_NAME, title=cfg.WINDOW_TITLE,
                             widthHeight=(LAYOUT["WIN_W"], LAYOUT["WIN_H"]),  # 窗口宽高
                             sizeable=True)
        # 第一个布局直接作为 window 的子级，之后的控件一律显式 parent=main
        main = cmds.columnLayout(adjustableColumn=True, rowSpacing=4,
                                 columnAttach=('both', 8))  # 主列左右留白 8

        # ---- 顶部：一键导出（始终可见）----
        cmds.button(parent=main, label=u"一键导出",
                    height=LAYOUT["EXPORT_BTN_H"],  # 按钮高
                    backgroundColor=(0.25, 0.5, 0.85),
                    command=lambda *args: on_export())
        cmds.text(parent=main,
                  label=u"配置会自动保存到场景节点（{0}），随场景保存/自动载入"
                         .format(cfg.SCENE_NODE_NAME),
                  align="center", font="smallPlainLabelFont")
        # 启动自检状态行（打开工具时填一次；详情见脚本编辑器）
        ui_controls["check_label"] = cmds.text(
            parent=main, label=u"自检：未运行", align="center",
            font="smallPlainLabelFont",
            annotation=u"打开工具时做一次轻量自检；详细结果输出到脚本编辑器")
        cmds.separator(parent=main, height=LAYOUT["TOP_SEP_H"], style='in')

        # ---- 中部内容放进滚动区（内容超高时出现滚动条）----
        body_scroll = cmds.scrollLayout(parent=main,
                                        height=LAYOUT["BODY_SCROLL_H"],  # 滚动区高
                                        childResizable=True)
        body = cmds.columnLayout(parent=body_scroll, adjustableColumn=True,
                                 rowSpacing=LAYOUT["BODY_SPACING"],  # 区块间垂直间距
                                 columnAttach=('both', 2))            # 区块左右留白 2

        # ---- 导出目录行 ----
        cmds.text(parent=body, label=u"导出目录:", align="left")
        ui_controls["dir_field"] = cmds.textFieldButtonGrp(
            parent=body, label="", buttonLabel=u"浏览...",
            buttonCommand=on_browse_dir, text="",
            # 两列宽：第1列=路径输入框，第2列=浏览按钮（见 LAYOUT["DIR_COLUMNS"]）
            columnWidth=LAYOUT["DIR_COLUMNS"]
        )
        cmds.separator(parent=body, height=LAYOUT["SEP_H"], style='in')

        # ---- 命名前缀行：3 列 = 标签 / 输入框 / 重新识别按钮（见 LAYOUT["PREFIX_COLS"]）----
        prefix_row = cmds.rowLayout(parent=body, numberOfColumns=3,
                                    columnWidth3=LAYOUT["PREFIX_COLS"],
                                    columnAttach=[(1, 'both', 4), (2, 'both', 4),
                                                  (3, 'both', 4)])
        cmds.text(parent=prefix_row, label=u"命名前缀:", align="left")
        ui_controls["prefix_field"] = cmds.textField(parent=prefix_row, text="",
                                                     changeCommand=on_prefix_field_changed,
                                                     annotation=u"自动取自场景文件名（S02_xxx.mb → S02），可手改")
        ui_controls["prefix_btn"] = cmds.button(parent=prefix_row, label=u"重新识别",
                                                command=lambda *args: update_prefix_from_scene())
        cmds.separator(parent=body, height=LAYOUT["SEP_H"], style='in')

        # ---- 三个资产分类（FBX / ABC / 相机；默认折叠，点标题展开）----
        for type_key in [TYPE_FBX, TYPE_ABC, TYPE_CAMERA]:
            frame = cmds.frameLayout(parent=body, label=cfg.CATEGORY_NAMES[type_key],
                                     collapsable=True,
                                     collapse=LAYOUT["START_COLLAPSED"])  # True=默认折叠
            col = cmds.columnLayout(parent=frame, adjustableColumn=True, rowSpacing=3,
                                    columnAttach=('both', LAYOUT["FRAME_PAD"]))  # 框内左右留白

            hint_text = u""
            if type_key == TYPE_FBX:
                hint_text = u"选择包含骨骼的组或关节，点击下方 ＋ 添加；导出文件名自动带前缀。"
            elif type_key == TYPE_ABC:
                hint_text = u"可选择任意多个物体作为一个导出组（不筛选类型）；导出文件名自动带前缀。"
            else:
                hint_text = u"选择相机或其所在绑定组添加；组内多相机时会弹窗勾选。列表默认名为 Camera，导出文件名自动加前缀和帧范围，如 S02_Camera_101-138。"
            cmds.text(parent=col, label=hint_text, align="left",
                      font="smallPlainLabelFont", wordWrap=True)

            if type_key == TYPE_ABC:
                ui_controls["abc_cleanup_checkbox"] = cmds.checkBox(
                    parent=col, label=u"导出前三角化多边面（>4 边）",
                    value=abc_cleanup,
                    changeCommand=on_abc_cleanup_changed,
                    annotation=u"只把多于 4 条边的面三角化（保留构造历史）；"
                               u"不删除构造历史、不冻结变换，避免破坏 ABC 动画。"
                               u"没有多边面时 Maya 会提示“找不到要清理的项目”，属正常"
                )

            # 条目列表滚动区（高度见 LAYOUT["LIST_H"]，列表内部自带滚动条）
            ui_controls["scroll_{0}".format(type_key)] = cmds.scrollLayout(
                parent=col, height=LAYOUT["LIST_H"], childResizable=True)
            rebuild_category_ui(type_key)

            # “＋ 添加”行：3 列 = ＋按钮 / 空隙 / 自适应提示文字（见 LAYOUT["ADD_ROW_COLS"]）
            add_row = cmds.rowLayout(parent=col, numberOfColumns=3,
                                     columnWidth3=LAYOUT["ADD_ROW_COLS"],
                                     columnAttach=[(1, 'both', 0), (2, 'both', 0),
                                                   (3, 'both', 4)],
                                     adjustableColumn=3)
            cmds.button(parent=add_row, label=u"＋",
                        width=LAYOUT["ADD_BTN_W"],   # ＋按钮宽
                        height=LAYOUT["ADD_BTN_H"],  # ＋按钮高
                        backgroundColor=(0.2, 0.5, 0.2),
                        command=lambda *args, tk=type_key: add_selected_to_category(tk),
                        annotation=u"添加当前选择到该分类")
            cmds.text(parent=add_row, label="", width=LAYOUT["ADD_ROW_COLS"][1])  # 中间空隙宽
            cmds.text(parent=add_row, label=u"添加当前选择到该列表", align="left",
                      font="smallPlainLabelFont")
            # 列表+加号的共同上级 = col；在这里给它底部加 FRAME_EXTRA_H 高度的留白，
            # 使“展开栏的内容区”整体更高（不改变列表/加号自身尺寸）
            cmds.separator(parent=col, height=LAYOUT["FRAME_EXTRA_H"], style='none')

        cmds.separator(parent=body, height=LAYOUT["SEP_H"], style='in')

        # ---- 动画范围 ----
        cmds.text(parent=body, label=u"动画范围:", align="left")
        # 单选行：3 列 = 当前时间滑块 / 自定义 / 刷新按钮（宽见 LAYOUT["RADIO_COLS"]）
        range_radio_row = cmds.rowLayout(parent=body, numberOfColumns=3,
                                         columnWidth3=LAYOUT["RADIO_COLS"],
                                         columnAttach=[(1, 'both', 4), (2, 'both', 4),
                                                       (3, 'both', 4)])
        # 两个单选钮必须显式归入同一个 radioCollection：
        # 若不带 collection 创建，Maya 会把它加进“最近创建”的集合，而重建窗口时旧集合
        # 已随旧窗口一起删除，于是报“找不到集合，或没有当前集合”。集合挂在 body 下
        # （不可见、不占布局空间），随窗口一起删除，重建时重新创建。
        range_collection = cmds.radioCollection(parent=body)
        ui_controls["range_collection"] = range_collection
        ui_controls["range_current_radio"] = cmds.radioButton(
            parent=range_radio_row, label=u"当前时间滑块", select=True,
            collection=range_collection, onCommand=on_range_radio_current)
        ui_controls["range_custom_radio"] = cmds.radioButton(
            parent=range_radio_row, label=u"自定义",
            collection=range_collection, onCommand=on_range_radio_custom)
        # 刷新按钮：把时间轴当前范围同步到下面的开始/结束输入框
        ui_controls["range_refresh_btn"] = cmds.button(
            parent=range_radio_row, label=u"刷新", height=LAYOUT["ROW_H"],
            command=lambda *args: refresh_range_from_timeline(),
            annotation=u"在 Maya 里改了时间轴范围后点这里，把开始/结束同步成"
                       u"当前时间滑块的范围（导出时本来就是实时读取，这里只是同步显示）")

        # 帧段行：5 列 = 开始标签/开始输入框/结束标签/结束输入框/自适应空隙
        # 列宽见 LAYOUT["FIELD_COLS"]
        range_field_row = cmds.rowLayout(parent=body, numberOfColumns=5,
                                         columnWidth5=LAYOUT["FIELD_COLS"],
                                         columnAttach=[(1, 'both', 4), (2, 'both', 4),
                                                       (3, 'both', 4), (4, 'both', 4),
                                                       (5, 'both', 0)],
                                         adjustableColumn=5)
        cmds.text(parent=range_field_row, label=u"开始:", align="right")
        ui_controls["start_field"] = cmds.intField(
            parent=range_field_row,
            value=int(cmds.playbackOptions(q=True, minTime=True)),
            minValue=-100000, maxValue=100000, enable=False,
            changeCommand=on_range_int_changed)
        cmds.text(parent=range_field_row, label=u"结束:", align="right")
        ui_controls["end_field"] = cmds.intField(
            parent=range_field_row,
            value=int(cmds.playbackOptions(q=True, maxTime=True)),
            minValue=-100000, maxValue=100000, enable=False,
            changeCommand=on_range_int_changed)
        cmds.text(parent=range_field_row, label="")  # 第5列：自适应占位
        toggle_range_fields(False)

        cmds.separator(parent=body, height=LAYOUT["SEP_H"], style='in')

        # ---- 配置操作按钮行：3 列 = 保存 / 加载 / 设置(齿轮) ----
        cfg_row = cmds.rowLayout(parent=body, numberOfColumns=3,
                                 columnWidth3=(130, 130, 28),
                                 columnAttach=[(1, 'both', 5), (2, 'both', 5),
                                               (3, 'both', 2)],
                                 adjustableColumn=3)
        cmds.button(parent=cfg_row, label=u"保存配置到文件…",
                    command=lambda *args: on_save_config())
        cmds.button(parent=cfg_row, label=u"从文件加载配置…",
                    command=lambda *args: on_load_config())
        cmds.symbolButton(parent=cfg_row, image="gear.png",
                          width=26, height=26,
                          command=lambda *args: _open_settings())

        # 底部留空（见 LAYOUT["BOTTOM_PAD"]），保证滚到最底最后一行不被吞掉
        cmds.separator(parent=body, height=LAYOUT["BOTTOM_PAD"], style='none')

        cmds.showWindow(window)
    finally:
        _suppress_sync = False


def _run_startup_check():
    """打开工具时做一次轻量自检（见 checks.py：不遍历场景、有条数与时间上限）"""
    if not cfg.option("startup_check", True):
        return []
    started = time.time()
    try:
        issues = checks.light_check(core.data_store, _read_dir_field(),
                                    get_animation_range())
    except Exception as exc:
        print(u"[自检] 已跳过：{0}".format(exc))
        return []
    elapsed = time.time() - started
    print(checks.format_report(issues, elapsed))

    summary = checks.summary_line(issues)
    label = ui_controls.get("check_label")
    if label and cmds.text(label, exists=True):
        try:
            if checks.has_errors(issues):
                color = (0.55, 0.25, 0.25)
            elif issues:
                color = (0.5, 0.42, 0.2)
            else:
                color = (0.25, 0.45, 0.25)
            cmds.text(label, edit=True, label=summary, backgroundColor=color)
        except Exception:
            pass
    if checks.has_errors(issues):
        cmds.warning(u"{0}（详情见脚本编辑器）".format(summary))
    return issues


def launch():
    """打开窗口；若场景内已有配置节点则自动载入（场景打开/插件启动自动恢复）"""
    build_ui()
    try:
        raw = persistence.read_config_from_node()
    except Exception:
        raw = None
    if raw:
        apply_config(raw)
    elif not _current_prefix():
        # 无持久化配置时也自动识别一次前缀
        update_prefix_from_scene(silent=True)
    # 打开工具时做一次轻量自检（结果同时输出到脚本编辑器与窗口状态行）
    _run_startup_check()
    cmds.warning(u"动画资产一键导出工具已就绪（{0} 个条目）".format(core.count_total()))
