# -*- coding: utf-8 -*-
"""
Maya 一键导出插件 - 符号化 UI 版（无 PySide2）
v7：已实现 FBX 骨骼动画 / ABC 几何体缓存（含导出前清理）/ 相机动画实际导出。
"""

import json
import os
import re
import maya.cmds as cmds
from maya import mel

WINDOW_NAME = "exportManagerCmdsUI_v6"

TYPE_FBX = "fbx"
TYPE_ABC = "abc"
TYPE_CAMERA = "camera"

CATEGORY_NAMES = {
    TYPE_FBX: "FBX 骨骼动画",
    TYPE_ABC: "ABC 几何体缓存",
    TYPE_CAMERA: "相机动画"
}

# 数据存储
data_store = {
    TYPE_FBX: [],
    TYPE_ABC: [],
    TYPE_CAMERA: []
}

# UI 控件引用
ui_controls = {}

# 全局选项
use_prefix = False
prefix_text = ""
abc_cleanup = False

ABC_DISPLAY_LIMIT = 2  # ABC组显示前几个对象名

# 导出所用插件名
FBX_PLUGIN = "fbxmaya"
ABC_PLUGIN = "AbcExport"

# 参考命令：导出前对多边形做清理（展开 Poly 组选择 + polyCleanupArgList）
ABC_CLEANUP_MEL = (
    "expandPolyGroupSelection; "
    'polyCleanupArgList 4 { "0","1","1","0","1","0","0","0","0","1e-05","0","1e-05","0","1e-05","0","-1","0","0" };'
)

# -----------------------------------------------------------------------------
# 工具函数
# -----------------------------------------------------------------------------
def get_short_name(long_name):
    return long_name.split('|')[-1]

def is_type_matching(obj, type_key):
    if type_key == TYPE_FBX:
        if cmds.objectType(obj, isAType='joint'):
            return True
        children = cmds.listRelatives(obj, children=True, fullPath=True) or []
        for child in children:
            if cmds.objectType(child, isAType='joint'):
                return True
        return False
    elif type_key == TYPE_CAMERA:
        if cmds.objectType(obj, isAType='camera'):
            return True
        shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or []
        for shape in shapes:
            if cmds.objectType(shape, isAType='camera'):
                return True
        return False
    return False

def find_skeleton_roots(obj):
    roots = []
    if cmds.objectType(obj, isAType='joint'):
        roots.append(get_short_name(obj))
        return roots
    all_joints = cmds.listRelatives(obj, allDescendents=True, type='joint', fullPath=True) or []
    if not all_joints:
        return roots
    for jnt in all_joints:
        parent = cmds.listRelatives(jnt, parent=True, fullPath=True)
        if not parent:
            roots.append(get_short_name(jnt))
            continue
        if not cmds.objectType(parent[0], isAType='joint'):
            roots.append(get_short_name(jnt))
    # 去重
    seen = set()
    unique_roots = []
    for r in roots:
        if r not in seen:
            seen.add(r)
            unique_roots.append(r)
    return unique_roots

def get_abc_display_text(object_list):
    if len(object_list) <= ABC_DISPLAY_LIMIT:
        return ", ".join(object_list)
    shown = ", ".join(object_list[:ABC_DISPLAY_LIMIT])
    return f"{shown} ... ({len(object_list)}个物体)"

def get_full_abc_text(object_list):
    return ", ".join(object_list)

# -----------------------------------------------------------------------------
# UI 构建辅助
# -----------------------------------------------------------------------------
def rebuild_category_ui(type_key):
    """清空并重建指定分类的行显示"""
    scroll = ui_controls.get(f"scroll_{type_key}")
    if not scroll or not cmds.scrollLayout(scroll, exists=True):
        return

    # 删除旧的子布局
    children = cmds.scrollLayout(scroll, query=True, childArray=True) or []
    for child in children:
        if cmds.layout(child, exists=True):
            cmds.deleteUI(child, layout=True)

    # 创建新的 columnLayout 作为行容器
    rows_layout = cmds.columnLayout(adjustableColumn=True, rowSpacing=2, parent=scroll)
    ui_controls[f"rows_{type_key}"] = rows_layout

    items = data_store[type_key]
    for idx, item in enumerate(items):
        # 每行使用 rowLayout
        row = cmds.rowLayout(
            numberOfColumns=4,
            columnWidth4=(30, 200, 150, 30),
            columnAttach=[(1, 'both', 2), (2, 'both', 2), (3, 'both', 2), (4, 'both', 2)],
            parent=rows_layout
        )

        # 1. 复选框
        cb = cmds.checkBox(
            label="",
            value=item["enabled"],
            changeCommand=lambda checked, tk=type_key, i=idx: on_checkbox_changed(tk, i, checked),
            annotation="勾选是否导出"
        )

        # 2. 物体名按钮（点击选中场景物体）
        obj_display = item["object"]
        if type_key == TYPE_ABC and isinstance(item["object"], list):
            obj_display = get_abc_display_text(item["object"])
            full_text = get_full_abc_text(item["object"])
            ann = f"点击选中组内所有物体\n完整列表: {full_text}"
        else:
            ann = f"点击选中 {obj_display}"
        obj_btn = cmds.button(
            label=obj_display,
            command=lambda *args, tk=type_key, i=idx: on_object_btn_clicked(tk, i),
            annotation=ann,
            backgroundColor=(0.25, 0.25, 0.25),
            height=20
        )

        # 3. 导出命名输入框
        name_field = cmds.textField(
            text=item["export_name"],
            changeCommand=lambda text, tk=type_key, i=idx: on_name_field_changed(tk, i, text),
            annotation="导出文件名（可编辑）"
        )

        # 4. 删除按钮（×符号）
        del_btn = cmds.symbolButton(
            image="deleteSmall.png",  # 使用Maya内置删除图标，如果没有可换字符
            command=lambda *args, tk=type_key, i=idx: on_delete_btn_clicked(tk, i),
            annotation="删除此条目"
        )
        # 若symbolButton图标可能不存在，回退为字符按钮
        if not cmds.symbolButton(del_btn, exists=True):
            cmds.deleteUI(del_btn)
            del_btn = cmds.button(
                label="×",
                command=lambda *args, tk=type_key, i=idx: on_delete_btn_clicked(tk, i),
                annotation="删除此条目",
                width=30,
                height=20
            )

        cmds.setParent('..')  # 结束rowLayout回到rows_layout

def on_checkbox_changed(type_key, idx, checked):
    """复选框状态变化"""
    if 0 <= idx < len(data_store[type_key]):
        data_store[type_key][idx]["enabled"] = checked

def on_name_field_changed(type_key, idx, text):
    """导出命名输入框变化"""
    if 0 <= idx < len(data_store[type_key]):
        data_store[type_key][idx]["export_name"] = text

def on_delete_btn_clicked(type_key, idx):
    """删除行按钮点击"""
    if 0 <= idx < len(data_store[type_key]):
        del data_store[type_key][idx]
        rebuild_category_ui(type_key)

def on_object_btn_clicked(type_key, idx):
    """点击物体名按钮，在场景中选中对应物体"""
    if idx < 0 or idx >= len(data_store[type_key]):
        return
    item = data_store[type_key][idx]
    cmds.select(clear=True)

    if type_key == TYPE_FBX:
        root = item["object"]
        if cmds.objExists(root):
            cmds.select(root, hierarchy=True)
        else:
            cmds.warning(f"骨骼根 {root} 不存在于场景中")
    elif type_key == TYPE_ABC:
        objs = item["object"] if isinstance(item["object"], list) else [item["object"]]
        valid = [o for o in objs if cmds.objExists(o)]
        if valid:
            cmds.select(valid)
        for o in objs:
            if not cmds.objExists(o):
                cmds.warning(f"物体 {o} 不存在于场景中")
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
            cmds.warning(f"相机 {cam} 不存在于场景中")

# -----------------------------------------------------------------------------
# 添加选中物体
# -----------------------------------------------------------------------------
def add_selected_to_category(type_key):
    sel = cmds.ls(sl=True, long=True)
    if not sel:
        cmds.warning("请先在场景中选择要添加的物体")
        return

    added_count = 0

    if type_key == TYPE_FBX:
        for obj in sel:
            short_name = get_short_name(obj)
            if not is_type_matching(obj, type_key):
                cmds.warning(f"物体 {short_name} 下未找到骨骼，已跳过")
                continue
            roots = find_skeleton_roots(obj)
            if not roots:
                cmds.warning(f"物体 {short_name} 下未找到骨骼根关节，已跳过")
                continue
            for root in roots:
                if any(item["object"] == root for item in data_store[type_key]):
                    cmds.warning(f"骨骼根 {root} 已在列表中，跳过")
                    continue
                export_name = f"{prefix_text}_{root}" if use_prefix and prefix_text else root
                data_store[type_key].append({
                    "object": root,
                    "export_name": export_name,
                    "enabled": True
                })
                added_count += 1
        if added_count > 0:
            rebuild_category_ui(type_key)
            cmds.inViewMessage(amg=f"已添加 {added_count} 个骨骼根", pos='midCenter')
        return

    elif type_key == TYPE_ABC:
        # 直接添加所有选中物体（不做类型过滤）
        valid_objects = [get_short_name(obj) for obj in sel]
        if not valid_objects:
            cmds.warning("请选择至少一个物体")
            return

        first_obj = valid_objects[0]
        export_name = f"{prefix_text}_{first_obj}" if use_prefix and prefix_text else first_obj

        # 检查是否重复
        existing = any(
            isinstance(item["object"], list) and set(item["object"]) == set(valid_objects)
            for item in data_store[type_key]
        )
        if existing:
            cmds.warning("该物体组合已在列表中，跳过")
            return

        data_store[type_key].append({
            "object": valid_objects,
            "export_name": export_name,
            "enabled": True
        })
        rebuild_category_ui(type_key)
        cmds.inViewMessage(amg=f"已添加 ABC 导出组（{len(valid_objects)} 个物体）", pos='midCenter')
        return

    elif type_key == TYPE_CAMERA:
        for obj in sel:
            short_name = get_short_name(obj)
            if not is_type_matching(obj, type_key):
                cmds.warning(f"物体 {short_name} 不是相机，已跳过")
                continue
            if cmds.objectType(obj, isAType='camera'):
                parent = cmds.listRelatives(obj, parent=True, fullPath=True)
                if parent:
                    short_name = get_short_name(parent[0])
            if any(item["object"] == short_name for item in data_store[type_key]):
                cmds.warning(f"相机 {short_name} 已在列表中，跳过")
                continue
            export_name = f"{prefix_text}_{short_name}" if use_prefix and prefix_text else short_name
            data_store[type_key].append({
                "object": short_name,
                "export_name": export_name,
                "enabled": True
            })
            added_count += 1
        if added_count > 0:
            rebuild_category_ui(type_key)
            cmds.inViewMessage(amg=f"已添加 {added_count} 个相机", pos='midCenter')
        return

# -----------------------------------------------------------------------------
# 其他UI回调（目录、前缀、范围、配置等保持不变）
# -----------------------------------------------------------------------------
def on_browse_dir():
    current_dir = cmds.textFieldButtonGrp(ui_controls["dir_field"], query=True, text=True)
    if not current_dir:
        current_dir = cmds.workspace(q=True, rd=True)
    result = cmds.fileDialog2(dialogStyle=2, fileMode=3, caption="选择导出目录", startingDirectory=current_dir)
    if result:
        cmds.textFieldButtonGrp(ui_controls["dir_field"], edit=True, text=result[0])

def update_prefix_from_scene():
    scene_name = cmds.file(q=True, sceneName=True, shortName=True)
    if not scene_name:
        cmds.warning("场景尚未保存，无法自动识别前缀")
        return
    base = os.path.splitext(scene_name)[0]
    prefix = base.split('_')[0] if '_' in base else base
    global prefix_text
    prefix_text = prefix
    cmds.textField(ui_controls["prefix_field"], edit=True, text=prefix)

def on_prefix_checkbox_changed(checked):
    global use_prefix
    use_prefix = checked
    cmds.textField(ui_controls["prefix_field"], edit=True, enable=checked)
    cmds.button(ui_controls["prefix_btn"], edit=True, enable=checked)

def on_prefix_field_changed(text):
    global prefix_text
    prefix_text = text

def on_abc_cleanup_changed(checked):
    global abc_cleanup
    abc_cleanup = checked

def get_animation_range():
    if cmds.radioButton(ui_controls["range_current_radio"], query=True, select=True):
        start = cmds.playbackOptions(q=True, minTime=True)
        end = cmds.playbackOptions(q=True, maxTime=True)
        return int(start), int(end)
    else:
        start = cmds.intField(ui_controls["start_field"], query=True, value=True)
        end = cmds.intField(ui_controls["end_field"], query=True, value=True)
        return start, end

def toggle_range_fields(enable_custom):
    cmds.intField(ui_controls["start_field"], edit=True, enable=enable_custom)
    cmds.intField(ui_controls["end_field"], edit=True, enable=enable_custom)

def on_save_config():
    config = {
        "export_dir": cmds.textFieldButtonGrp(ui_controls["dir_field"], query=True, text=True),
        "animation_range": get_animation_range(),
        "use_prefix": use_prefix,
        "prefix_text": prefix_text,
        "abc_cleanup": abc_cleanup,
        "items": data_store
    }
    file_path = cmds.fileDialog2(dialogStyle=2, fileMode=0, caption="保存配置", fileFilter="JSON Files (*.json)")
    if not file_path:
        return
    file_path = file_path[0]
    try:
        with open(file_path, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=4)
        cmds.confirmDialog(title="成功", message="配置已保存。", button=["确定"])
    except Exception as e:
        cmds.confirmDialog(title="错误", message=f"保存配置失败：{str(e)}", button=["确定"])

def on_load_config():
    file_path = cmds.fileDialog2(dialogStyle=2, fileMode=1, caption="加载配置", fileFilter="JSON Files (*.json)")
    if not file_path:
        return
    file_path = file_path[0]
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            config = json.load(f)
    except Exception as e:
        cmds.confirmDialog(title="错误", message=f"加载配置失败：{str(e)}", button=["确定"])
        return

    global use_prefix, prefix_text, abc_cleanup
    use_prefix = config.get("use_prefix", False)
    prefix_text = config.get("prefix_text", "")
    abc_cleanup = config.get("abc_cleanup", False)

    cmds.checkBox(ui_controls["prefix_checkbox"], edit=True, value=use_prefix)
    cmds.textField(ui_controls["prefix_field"], edit=True, text=prefix_text, enable=use_prefix)
    cmds.button(ui_controls["prefix_btn"], edit=True, enable=use_prefix)
    cmds.checkBox(ui_controls["abc_cleanup_checkbox"], edit=True, value=abc_cleanup)

    cmds.textFieldButtonGrp(ui_controls["dir_field"], edit=True, text=config.get("export_dir", ""))

    anim_range = config.get("animation_range", None)
    if anim_range and len(anim_range) == 2:
        cmds.radioButton(ui_controls["range_custom_radio"], edit=True, select=True)
        cmds.intField(ui_controls["start_field"], edit=True, value=int(anim_range[0]))
        cmds.intField(ui_controls["end_field"], edit=True, value=int(anim_range[1]))
        toggle_range_fields(True)
    else:
        cmds.radioButton(ui_controls["range_current_radio"], edit=True, select=True)
        toggle_range_fields(False)

    global data_store
    data_store = config.get("items", {TYPE_FBX: [], TYPE_ABC: [], TYPE_CAMERA: []})
    for type_key in [TYPE_FBX, TYPE_ABC, TYPE_CAMERA]:
        rebuild_category_ui(type_key)

# -----------------------------------------------------------------------------
# 导出核心实现（FBX 骨骼动画 / ABC 几何体缓存 / 相机动画）
# -----------------------------------------------------------------------------
def _ensure_plugin_loaded(plugin):
    """加载 Maya 导出插件（fbxmaya / AbcExport）"""
    try:
        if not cmds.pluginInfo(plugin, query=True, loaded=True):
            cmds.loadPlugin(plugin, quiet=True)
        return True
    except Exception as exc:
        cmds.warning("加载插件失败: {0} ({1})".format(plugin, exc))
        return False

def ensure_export_plugins():
    """确保导出所需插件已加载，缺失时抛出 RuntimeError"""
    missing = []
    for plugin in (FBX_PLUGIN, ABC_PLUGIN):
        if not _ensure_plugin_loaded(plugin):
            missing.append(plugin)
    if missing:
        raise RuntimeError("缺少必需插件，无法导出: " + ", ".join(missing))

def sanitize_filename(name):
    """把导出命名处理成安全的文件基本名（去非法字符与空白）"""
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(name))
    text = re.sub(r"\s+", "_", text).strip("_. ")
    return text or "export"

def normalize_dir(path):
    """规范化导出目录为 / 分隔路径，不存在则创建"""
    path = (path or "").strip().replace("\\", "/")
    if not path:
        raise RuntimeError("导出目录为空，请先在界面中选择导出目录")
    if not os.path.isdir(path):
        try:
            os.makedirs(path)
        except OSError as exc:
            raise RuntimeError("无法创建导出目录: {0}（{1}）".format(path, exc))
    return path

def resolve_unique(node, what):
    """把记录中的名字解析为唯一完整路径；缺失/重名返回 None"""
    if not node:
        cmds.warning("{0} 名称为空，已跳过".format(what))
        return None
    try:
        found = cmds.ls(node, long=True) or []
    except RuntimeError:
        found = []
    if not found:
        cmds.warning("{0} 在场景中不存在，已跳过: {1}".format(what, node))
        return None
    if len(found) > 1:
        cmds.warning("{0} 存在多个同名节点，无法唯一确定，已跳过: {1}".format(what, node))
        return None
    return found[0]

def _to_transform(path):
    """若传入的是 shape 节点则取其父变换，否则原样返回"""
    try:
        if cmds.nodeType(path) not in ("transform", "joint"):
            parent = cmds.listRelatives(path, parent=True, fullPath=True)
            if parent:
                return parent[0]
    except Exception:
        pass
    return path

def _restore_selection(saved):
    if saved:
        try:
            cmds.select(saved, replace=True)
        except Exception:
            cmds.select(clear=True)
    else:
        cmds.select(clear=True)

def _mel(expr):
    try:
        mel.eval(expr)
    except Exception as exc:
        raise RuntimeError("MEL 命令执行失败: {0} -> {1}".format(expr, exc))

# ---------------------------- ABC 清理与导出 ----------------------------
def _abc_cleanup_targets(objects):
    """收集清理目标：物体自身 + 其下所有 mesh shape"""
    targets = []
    for obj in objects:
        targets.append(obj)
        try:
            shapes = cmds.listRelatives(obj, shapes=True, allDescendents=True, fullPath=True) or []
        except Exception:
            shapes = []
        for shp in shapes:
            if cmds.objectType(shp, isAType='mesh') and shp not in targets:
                targets.append(shp)
    return targets

def _run_abc_cleanup(objects):
    """按参考命令做导出前清理：
    展开多边形组选择 -> polyCleanupArgList -> 删除历史 -> 冻结变换
    """
    if not objects:
        return
    saved = cmds.ls(sl=True) or []
    try:
        cmds.select(_abc_cleanup_targets(objects), replace=True)
        _mel(ABC_CLEANUP_MEL)                      # expandPolyGroupSelection; polyCleanupArgList 4 {...};
        cmds.delete(constructionHistory=True)      # 清理历史
        cmds.select(objects, replace=True)
        cmds.makeIdentity(apply=True, translate=True, rotate=True, scale=True)  # 冻结变换
    finally:
        _restore_selection(saved)

def export_abc_item(item, export_dir, start, end):
    """导出单个 ABC 几何体缓存组，返回输出文件路径"""
    name = sanitize_filename(item.get("export_name", ""))
    raw = item.get("object") or []
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    objs = []
    for o in raw:
        path = resolve_unique(o, "ABC 物体")
        if path is None:
            continue
        path = _to_transform(path)
        if path not in objs:
            objs.append(path)
    if not objs:
        raise RuntimeError("导出组 '{0}' 中没有可导出的物体".format(name))

    if abc_cleanup:  # “导出前清理历史/冻结变换”已勾选
        try:
            _run_abc_cleanup(objs)
        except Exception as exc:
            cmds.warning("ABC 清理失败（继续导出）: {0}".format(exc))

    file_path = "{0}/{1}.abc".format(export_dir, name)
    job = "-frameRange {0} {1}".format(int(start), int(end))
    job += " -stripNamespaces -uvWrite -writeColorSets -writeFaceSets"
    job += " -wholeFrameGeo -worldSpace -writeVisibility -writeUVSets"
    job += " -dataFormat ogawa"
    for root_path in objs:
        job += " -root \"{0}\"".format(root_path)
    job += " -file \"{0}\"".format(file_path)
    try:
        cmds.AbcExport(jobArg=job)
    except Exception as exc:
        raise RuntimeError("AbcExport 失败: {0}".format(exc))
    if not os.path.exists(file_path):
        raise RuntimeError("ABC 文件未生成: {0}".format(file_path))
    return file_path

# ---------------------------- FBX 通用导出 ----------------------------
def _set_fbx_options(start, end, cameras=False):
    """设置 FBX 导出选项（参考 UEAnimCamExporter 逻辑：采样烘焙 + 帧段）"""
    _mel("FBXResetExport;")
    exprs = [
        "FBXExportAnimationOnly -v true;",
        "FBXExportBakeComplexAnimation -v true;",
        "FBXExportBakeComplexStart -f {0};".format(int(start)),
        "FBXExportBakeComplexEnd -f {0};".format(int(end)),
        "FBXExportBakeComplexStep -s 1;",
        "FBXExportSkins -v false;",
        "FBXExportShapes -v false;",
        "FBXExportCameras -v {0};".format("true" if cameras else "false"),
        "FBXExportLights -v false;",
        "FBXExportConstraints -v false;",
        "FBXExportSmoothingGroups -v false;",
        "FBXExportTangents -v false;",
        "FBXExportInAscii -v false;",
        "FBXExportInputConnections -v false;",
    ]
    for expr in exprs:
        _mel(expr)

def _export_fbx(file_path):
    """把当前选择导出为 FBX 文件（先删除旧文件避免覆盖询问）"""
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError:
            pass
    fp = file_path.replace("\\", "/")
    try:
        cmds.FBXExport(f=fp, s=True)
    except Exception:
        _mel('FBXExport -f "{0}" -s;'.format(fp))
    if not os.path.exists(file_path):
        cmds.warning("FBX 命令已执行但未检测到文件（部分版本仅日志显示路径不同）: {0}".format(file_path))

def export_fbx_item(item, export_dir, start, end):
    """导出单个 FBX 骨骼动画条目，返回输出文件路径"""
    name = sanitize_filename(item.get("export_name", ""))
    root = resolve_unique(item.get("object", ""), "FBX 骨骼根")
    if root is None:
        raise RuntimeError("骨骼根不存在或命名不唯一，无法导出 '{0}'".format(name))
    saved = cmds.ls(sl=True) or []
    try:
        cmds.select(root, hierarchy=True)
        dag_nodes = cmds.ls(sl=True, long=True) or []
        joints = [n for n in dag_nodes if cmds.objectType(n, isAType='joint')]
        if not joints:
            raise RuntimeError("在 {0} 的层级下未找到关节".format(root))
        cmds.select(joints, replace=True)
        _set_fbx_options(start, end, cameras=False)
        file_path = "{0}/{1}.fbx".format(export_dir, name)
        _export_fbx(file_path)
        return file_path
    finally:
        _restore_selection(saved)

# ---------------------------- 相机动画导出 ----------------------------
def export_camera_item(item, export_dir, start, end):
    """导出单个相机动画（FBX）：
    复制相机 -> 约束源相机 -> bakeResults 烘焙最终求解 -> 导出 -> 删除临时节点
    """
    name = sanitize_filename(item.get("export_name", ""))
    cam = resolve_unique(item.get("object", ""), "相机")
    if cam is None:
        raise RuntimeError("相机不存在或命名不唯一，无法导出 '{0}'".format(name))
    if cmds.objectType(cam, isAType='camera'):  # 选中了 shape 时转父变换
        parent = cmds.listRelatives(cam, parent=True, fullPath=True)
        if parent:
            cam = parent[0]
    saved = cmds.ls(sl=True) or []
    tmp_grp = None
    try:
        # 1) 临时组 + 复制相机
        tmp_grp = cmds.group(empty=True, name="expTmpCamGrp")
        base = sanitize_filename(get_short_name(cam)) + "_exp"
        dup_name = base
        i = 1
        while cmds.objExists(dup_name):
            dup_name = "{0}{1}".format(base, i)
            i += 1
        dup = cmds.duplicate(cam, parentOnly=False)[0]
        try:
            dup = cmds.rename(dup, dup_name)
        except Exception:
            pass
        cmds.parent(dup, tmp_grp)
        # 2) 复制相机先贴合源相机世界位姿，再约束求解
        matrix = cmds.xform(cam, query=True, matrix=True, worldSpace=True)
        cmds.xform(dup, worldSpace=True, matrix=matrix)
        pc_node = cmds.parentConstraint(cam, dup, maintainOffset=False)[0]
        try:
            cmds.bakeResults(dup, time=(start, end), sampleBy=1,
                             simulation=True, disableImplicitControl=True)
        except TypeError:  # 老版本 Maya 无该参数
            cmds.bakeResults(dup, time=(start, end), sampleBy=1, simulation=True)
        cmds.delete(pc_node)
        # 3) 导出复制相机
        cmds.select(dup, replace=True)
        _set_fbx_options(start, end, cameras=True)
        file_path = "{0}/{1}.fbx".format(export_dir, name)
        _export_fbx(file_path)
        return file_path
    finally:
        if tmp_grp and cmds.objExists(tmp_grp):
            try:
                cmds.delete(tmp_grp)
            except Exception:
                pass
        _restore_selection(saved)

# -----------------------------------------------------------------------------
# 一键导出
# -----------------------------------------------------------------------------
def on_export():
    """一键导出：按分类执行 FBX 骨骼动画 / ABC 几何体缓存 / 相机动画导出"""
    enabled_items = []
    for type_key in [TYPE_FBX, TYPE_ABC, TYPE_CAMERA]:
        for item in data_store[type_key]:
            if item.get("enabled"):
                enabled_items.append((type_key, item))
    if not enabled_items:
        cmds.confirmDialog(title="提示", message="没有勾选任何要导出的条目。", button=["确定"])
        return

    start, end = get_animation_range()
    if start > end:
        start, end = end, start

    try:
        export_dir = normalize_dir(cmds.textFieldButtonGrp(ui_controls["dir_field"], query=True, text=True))
        ensure_export_plugins()
    except RuntimeError as exc:
        cmds.confirmDialog(title="错误", message=str(exc), button=["确定"])
        return

    results = []  # [(成功文件路径 或 None, 错误消息或 None)]
    for type_key, item in enabled_items:
        entry_name = item.get("export_name") or "?"
        try:
            if type_key == TYPE_FBX:
                out = export_fbx_item(item, export_dir, start, end)
            elif type_key == TYPE_ABC:
                out = export_abc_item(item, export_dir, start, end)
            elif type_key == TYPE_CAMERA:
                out = export_camera_item(item, export_dir, start, end)
            else:
                out = None
            results.append((out, None))
            print("[导出成功] {0} -> {1}".format(entry_name, out))
        except Exception as exc:
            err = "{0}（{1}）: {2}".format(CATEGORY_NAMES[type_key], entry_name, exc)
            results.append((None, err))
            cmds.warning("[导出失败] {0}".format(err))

    ok_count = sum(1 for out, err in results if out)
    fail_msgs = [err for out, err in results if err]

    msg = "导出完成：成功 {0} 项，失败 {1} 项。".format(ok_count, len(fail_msgs))
    if ok_count:
        msg += "\n\n成功输出：\n" + "\n".join("  - {0}".format(out) for out, err in results if out)
    if fail_msgs:
        msg += "\n\n失败详情：\n" + "\n".join("  - {0}".format(m) for m in fail_msgs)
    cmds.confirmDialog(title="导出结果", message=msg, button=["确定"])

# -----------------------------------------------------------------------------
# 主 UI 构建
# -----------------------------------------------------------------------------
def build_ui():
    if cmds.window(WINDOW_NAME, exists=True):
        cmds.deleteUI(WINDOW_NAME)

    window = cmds.window(WINDOW_NAME, title="动画资产一键导出工具", widthHeight=(720, 800), sizeable=True)
    main_layout = cmds.columnLayout(adjustableColumn=True, rowSpacing=5, columnAttach=('both', 10))

    # ---- 导出目录 ----
    cmds.text(label="导出目录:", align="left")
    ui_controls["dir_field"] = cmds.textFieldButtonGrp(
        label="",
        buttonLabel="浏览...",
        buttonCommand=on_browse_dir,
        text="",
        columnWidth=[(1, 400), (2, 80)]
    )
    cmds.separator(height=10, style='in')

    # ---- 前缀识别 ----
    cmds.text(label="命名前缀:", align="left")
    cmds.rowLayout(numberOfColumns=3, columnWidth3=(200, 150, 100), columnAttach=[(1, 'both', 5), (2, 'both', 5), (3, 'both', 5)])
    ui_controls["prefix_checkbox"] = cmds.checkBox(label="使用场景名前缀", value=False, changeCommand=on_prefix_checkbox_changed)
    ui_controls["prefix_field"] = cmds.textField(text="", enable=False, changeCommand=on_prefix_field_changed)
    ui_controls["prefix_btn"] = cmds.button(label="重新识别", enable=False, command=lambda *args: update_prefix_from_scene())
    cmds.setParent('..')
    cmds.separator(height=10, style='in')

    # ---- 三个分类分组 ----
    for type_key in [TYPE_FBX, TYPE_ABC, TYPE_CAMERA]:
        frame = cmds.frameLayout(label=CATEGORY_NAMES[type_key], collapsable=True, collapse=False, borderStyle='etchedIn')
        cmds.columnLayout(adjustableColumn=True, rowSpacing=3)

        # 提示文字
        hint_text = ""
        if type_key == TYPE_FBX:
            hint_text = "选择包含骨骼的组或关节，点击下方 + 添加；点击物体名可选中骨骼层级。"
        elif type_key == TYPE_ABC:
            hint_text = "可选择任意多个物体作为一个导出组（不筛选类型）；点击物体名可选中组内所有物体。"
        else:
            hint_text = "选择相机（或其变换节点），点击下方 + 添加；点击物体名可选中相机。"
        cmds.text(label=hint_text, align="left", font="smallPlainLabelFont", wordWrap=True)

        # ABC 清理选项
        if type_key == TYPE_ABC:
            ui_controls["abc_cleanup_checkbox"] = cmds.checkBox(
                label="导出前清理历史/冻结变换",
                value=abc_cleanup,
                changeCommand=on_abc_cleanup_changed
            )

        # 可滚动区域
        ui_controls[f"scroll_{type_key}"] = cmds.scrollLayout(height=120, childResizable=True)
        # 初始构建空列表
        rebuild_category_ui(type_key)

        # 添加按钮（+符号）
        cmds.button(
            label="＋",
            command=lambda *args, tk=type_key: add_selected_to_category(tk),
            annotation="添加当前选择到该分类",
            width=30,
            height=25,
            backgroundColor=(0.2, 0.5, 0.2)
        )

        cmds.setParent('..')  # 结束分类的 columnLayout
        cmds.setParent('..')  # 结束 frameLayout

    cmds.separator(height=10, style='in')

    # ---- 动画范围 ----
    cmds.text(label="动画范围:", align="left")
    cmds.rowLayout(numberOfColumns=5, columnWidth5=(100, 20, 50, 20, 50), columnAttach=[(1, 'both', 5), (2, 'both', 5), (3, 'both', 5), (4, 'both', 5), (5, 'both', 5)])
    ui_controls["range_current_radio"] = cmds.radioButton(label="当前时间滑块", select=True, onCommand=lambda *args: toggle_range_fields(False))
    ui_controls["range_custom_radio"] = cmds.radioButton(label="自定义", onCommand=lambda *args: toggle_range_fields(True))
    cmds.text(label="开始:", align="left")
    ui_controls["start_field"] = cmds.intField(value=int(cmds.playbackOptions(q=True, minTime=True)), minValue=-10000, maxValue=10000, enable=False)
    ui_controls["end_field"] = cmds.intField(value=int(cmds.playbackOptions(q=True, maxTime=True)), minValue=-10000, maxValue=10000, enable=False)
    cmds.setParent('..')
    toggle_range_fields(False)

    cmds.separator(height=10, style='in')

    # ---- 配置按钮 ----
    cmds.rowLayout(numberOfColumns=2, columnWidth2=(120, 120), columnAttach=[(1, 'both', 5), (2, 'both', 5)])
    cmds.button(label="保存配置", command=lambda *args: on_save_config())
    cmds.button(label="加载配置", command=lambda *args: on_load_config())
    cmds.setParent('..')

    cmds.separator(height=10, style='in')

    # ---- 一键导出 ----
    cmds.button(label="一键导出", height=40, backgroundColor=(0.3, 0.5, 0.8), command=lambda *args: on_export())

    cmds.showWindow(window)

def main():
    build_ui()

if __name__ == "__main__":
    main()