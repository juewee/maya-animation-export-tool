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
import maya.cmds as cmds

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
# 例如 ROW_COLS=(30,190,150,26) 表示条目行四列宽分别是
#   勾选框30 / 物体名按钮190 / 命名输入框150 / 删除X按钮26。
#
# 重要公式（避免底部内容被吞）：
#   BODY_SCROLL_H 不要超过  WIN_H - TOP_FIXED_H
#   其中 TOP_FIXED_H ≈ 顶部一键导出按钮 + 提示文字 + 分隔条 + 上下边距，
#   下面默认按 90 估算，若你改了 EXPORT_BTN_H 等请同步调整。
#
# 宽度预算（决定窗口能否“瘦”下来）：
#   窗口内可用宽度 ≈ WIN_W - 30。
#   下面每个“列宽元组”的各列之和都不要超过它，否则该行会把整窗撑宽：
#     DIR_COLUMNS 440→288、PREFIX_COLS 560→290、ROW_COLS 396→274、
#     RADIO_COLS 300→230、FIELD_COLS 317→241、CFG_COLS 380→280
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

    # ---------- 条目行（分类列表内每行 rowLayout，columnWidth4）----------
    "ROW_COLS": (24, 118, 110, 22),   # (1)勾选框 (2)物体名按钮 (3)命名输入框 (4)删除X，合计274
    "ROW_H": 20,           # 行内按钮/控件高度

    # ---------- 动画范围 ----------
    "RADIO_COLS": (140, 90),        # 单选行两列：(1)当前时间滑块 (2)自定义，合计230
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
        # 行布局：4 列，宽度来自 LAYOUT["ROW_COLS"]
        #   (1)勾选框 (2)物体名按钮 (3)导出命名输入框 (4)删除X按钮
        row = cmds.rowLayout(
            numberOfColumns=4,
            columnWidth4=LAYOUT["ROW_COLS"],
            columnAttach=[(1, 'both', 2), (2, 'both', 2), (3, 'both', 2), (4, 'both', 2)],
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
            annotation=u"导出文件名（可编辑）"
        )

        # 删除按钮用文字按钮，避免 symbolButton 图标缺失时显示空白
        cmds.button(
            parent=row,
            label="X",
            width=LAYOUT["ROW_COLS"][3] - 2,   # 删除按钮宽度≈第4列宽-2
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
    cmds.button(parent=btn_row, label=u"确定添加", command=lambda *a: _finish())
    cmds.showWindow(win)


def _open_camera_chooser(cameras):
    """非阻塞的多相机选择弹窗：立即返回，不锁 Maya。

    内部单独记录要添加的相机（完整路径），点“确定添加”后回调 _add_cameras，
    全程不改动用户在场景里的选择。
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
        if picked:
            _add_cameras(picked)

    cmds.button(parent=btn_row, label=u"全选",
                command=lambda *a: [cmds.checkBox(cb, edit=True, value=True)
                                    for cb in cbs.values()])
    cmds.button(parent=btn_row, label=u"取消",
                command=lambda *a: cmds.deleteUI(win_name))
    cmds.button(parent=btn_row, label=u"确定添加", command=lambda *a: _finish())
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


def toggle_range_fields(enable_custom):
    start_field = ui_controls.get("start_field")
    end_field = ui_controls.get("end_field")
    if start_field and cmds.intField(start_field, exists=True):
        cmds.intField(start_field, edit=True, enable=enable_custom)
    if end_field and cmds.intField(end_field, exists=True):
        cmds.intField(end_field, edit=True, enable=enable_custom)


def on_range_radio_current(*_args):
    toggle_range_fields(False)
    _notify_changed()


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
# 一键导出
# ---------------------------------------------------------------------------
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
        utils.restore_selection(saved_sel)

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
                    parent=col, label=u"导出前清理历史/冻结变换",
                    value=abc_cleanup,
                    changeCommand=on_abc_cleanup_changed
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
        # 单选行：2 列 = 当前时间滑块 / 自定义（宽见 LAYOUT["RADIO_COLS"]）
        range_radio_row = cmds.rowLayout(parent=body, numberOfColumns=2,
                                         columnWidth2=LAYOUT["RADIO_COLS"],
                                         columnAttach=[(1, 'both', 4), (2, 'both', 4)])
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

        # ---- 配置操作按钮行：2 列等宽（见 LAYOUT["CFG_COLS"]）----
        cfg_row = cmds.rowLayout(parent=body, numberOfColumns=2,
                                 columnWidth2=LAYOUT["CFG_COLS"],
                                 columnAttach=[(1, 'both', 5), (2, 'both', 5)])
        cmds.button(parent=cfg_row, label=u"保存配置到文件…",
                    command=lambda *args: on_save_config())
        cmds.button(parent=cfg_row, label=u"从文件加载配置…",
                    command=lambda *args: on_load_config())

        # 底部留空（见 LAYOUT["BOTTOM_PAD"]），保证滚到最底最后一行不被吞掉
        cmds.separator(parent=body, height=LAYOUT["BOTTOM_PAD"], style='none')

        cmds.showWindow(window)
    finally:
        _suppress_sync = False


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
    cmds.warning(u"动画资产一键导出工具已就绪（{0} 个条目）".format(core.count_total()))
