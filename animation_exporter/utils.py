# -*- coding: utf-8 -*-
"""
动画资产一键导出工具 - Maya 通用工具函数
（只包含无 UI、无全局状态的纯函数）
"""
import os
import re
import maya.cmds as cmds
from maya import mel as _mel_module

from . import config


def get_short_name(long_name):
    """取节点长路径的叶子短名"""
    return str(long_name).split("|")[-1]


def is_type_matching(obj, type_key):
    """判断节点是否属于某分类（fbx：自身或子孙含骨骼；camera：相机或含相机子节点）"""
    if type_key == config.TYPE_FBX:
        if cmds.objectType(obj, isAType="joint"):
            return True
        # 检查所有子孙层级是否含 joint（不止直接子级）
        descendants = cmds.listRelatives(
            obj, allDescendents=True, fullPath=True, type="joint") or []
        return bool(descendants)
    if type_key == config.TYPE_CAMERA:
        if cmds.objectType(obj, isAType="camera"):
            return True
        shapes = cmds.listRelatives(obj, shapes=True, fullPath=True) or []
        for shape in shapes:
            if cmds.objectType(shape, isAType="camera"):
                return True
        return False
    return False


# ---------------------------------------------------------------------------
# 骨骼根查找（照搬 UEAnimCamExporter 的容器检测 + SkinCluster 反查方案）
# ---------------------------------------------------------------------------
def _strip_ns(name):
    """去命名空间"""
    return str(name).split(":")[-1]


def _is_skeleton_container_name(name_or_path):
    """判断 transform 名字是否像骨骼容器（DeformationSystem / Jnt_Grp 等）"""
    low = str(name_or_path or "").lower()
    leaf = _strip_ns(_strip_ns(low).split("|")[-1]).lower()
    norm = re.sub(r"[^a-z0-9]+", "", leaf)
    if not norm:
        return False
    if any(k in norm for k in (
            "deformationsystem", "deform", "bind", "skeleton", "skel", "bip", "biped")):
        return True
    negative = ("control", "ctrl", "ctl", "constraint", "helper", "dummy", "locator", "loc", "controller")
    if any(k in norm for k in negative):
        return False
    positive = ("jntgrp", "jointgrp", "jointsgrp", "bonegrp", "bonesgrp",
                "skeletongrp", "skelgrp", "bindgrp", "skingrp")
    return any(k in norm for k in positive)


def _is_fit_skeleton_root(root):
    """判断是否为 FitSkeleton 参考骨架（应避开）"""
    low = str(root or "").lower()
    return "fitskeleton" in low or "fit_skeleton" in low or "fitskel" in low


def _is_deformation_system_root(root):
    """判断是否更像 UE 应该使用的变形骨架 Root"""
    path = str(root or "")
    short = _strip_ns(get_short_name(path)).lower()
    low = path.lower()
    if "deformationsystem" in low:
        return True
    if short in ("root_m", "root", "deformationroot", "bindroot") and any(
            k in low for k in ("deform", "bind", "skin")):
        return True
    return False


def _topmost_joints(joints):
    """从 joint 列表中找父级不在列表内的根关节"""
    joint_set = set(joints)
    roots = []
    for jnt in joints:
        parent = cmds.listRelatives(jnt, parent=True, fullPath=True)
        if not parent or parent[0] not in joint_set:
            if jnt not in roots:
                roots.append(jnt)
    return roots


def _joint_chain_root(joint):
    """从任意 joint 向上找到最顶层 joint"""
    if not joint or not cmds.objExists(joint):
        return joint
    current = joint
    guard = 0
    while current and cmds.objExists(current) and guard < 512:
        parent = cmds.listRelatives(current, parent=True, fullPath=True) or []
        if not parent or not cmds.objExists(parent[0]) or cmds.nodeType(parent[0]) != "joint":
            return current
        current = parent[0]
        guard += 1
    return current


def _mesh_shapes_from_node(node):
    """从 transform 下收集 mesh shape（排除 intermediate）"""
    if not node or not cmds.objExists(node):
        return []
    shapes = []
    try:
        if cmds.nodeType(node) == "mesh":
            shapes.append(node)
        else:
            shapes.extend(cmds.listRelatives(node, shapes=True, fullPath=True, type="mesh") or [])
            shapes.extend(cmds.listRelatives(node, allDescendents=True, fullPath=True, type="mesh") or [])
    except Exception:
        pass
    result = []
    for s in shapes:
        try:
            if cmds.getAttr(s + ".intermediateObject"):
                continue
        except Exception:
            pass
        if s not in result:
            result.append(s)
    return result


def _skin_influences_from_shape(shape):
    """从 mesh shape 的 history 中取 skinCluster influence joints"""
    try:
        hist = cmds.listHistory(shape, pruneDagObjects=True) or []
    except Exception:
        return []
    influences = []
    for h in hist:
        try:
            if cmds.nodeType(h) == "skinCluster":
                influences.extend(cmds.skinCluster(h, q=True, influence=True) or [])
        except Exception:
            pass
    # 去重保序
    seen = set()
    result = []
    for j in influences:
        if j and j not in seen:
            seen.add(j)
            result.append(j)
    return result


def _root_preference_score(root):
    """Root 排序评分：DeformationSystem 优先，FitSkeleton 靠后"""
    path = str(root or "")
    short = _strip_ns(get_short_name(path)).lower()
    score = 0
    if _is_deformation_system_root(path):
        score -= 1000
    if short in ("root_m", "deformationroot", "bindroot"):
        score -= 100
    if short == "root":
        score -= 20
    if _is_fit_skeleton_root(path):
        score += 1000
    return (score, path.lower())


def find_skeleton_roots(obj):
    """从组/关节中提取顶层根关节（去重，按推荐度排序）。

    照搬 UEAnimCamExporter 的检测策略：
    1. 直接选 joint → 返回该 joint
    2. 扫描 DeformationSystem / Jnt_Grp 等骨骼容器下的 joint
    3. 全量 descendant joint 兜底
    4. 若以上均无 joint，用 SkinCluster influence 反查变形骨架 Root
    5. _topmost_joints 去掉子级，_root_preference_score 排序（FitSkeleton 靠后）
    """
    # 1) 直接选 joint
    if cmds.objectType(obj, isAType="joint"):
        return [get_short_name(obj)]

    joints = []

    # 2) 扫描骨骼容器（DeformationSystem / Jnt_Grp / Bone_Grp 等）
    try:
        children = cmds.listRelatives(obj, children=True, fullPath=True) or []
    except Exception:
        children = []
    containers = []
    for c in children:
        if _is_skeleton_container_name(c):
            containers.append(c)
    # 直接子级没找到容器时，做一次轻量 descendant 扫描
    if not containers:
        try:
            desc = cmds.listRelatives(obj, allDescendents=True, fullPath=True, type="transform") or []
            for d in desc:
                if _is_skeleton_container_name(d):
                    containers.append(d)
        except Exception:
            pass
    for container in containers:
        try:
            joints.extend(cmds.listRelatives(
                container, allDescendents=True, fullPath=True, type="joint") or [])
            if cmds.nodeType(container) == "joint":
                joints.append(container)
        except Exception:
            pass

    # 3) 全量 descendant joint 兜底（容器没找到时）
    if not joints:
        try:
            joints.extend(cmds.listRelatives(
                obj, allDescendents=True, fullPath=True, type="joint") or [])
        except Exception:
            pass

    # 4) SkinCluster 反查：以上均无 joint 时，从 mesh skinCluster 反查
    if not joints:
        skin_infs = []
        for shape in _mesh_shapes_from_node(obj):
            skin_infs.extend(_skin_influences_from_shape(shape))
        for inf in skin_infs:
            root = _joint_chain_root(inf)
            if root:
                joints.append(root)

    # 5) 找顶层 + 排序 + 转短名
    roots = _topmost_joints(joints)
    roots = sorted(roots, key=_root_preference_score)
    return [get_short_name(r) for r in roots]


def get_skin_influences(obj):
    """从物体下的 mesh skinCluster 获取所有 influence joints（完整路径去重）"""
    joints = []
    for shape in _mesh_shapes_from_node(obj):
        joints.extend(_skin_influences_from_shape(shape))
    # 去重保序
    seen = set()
    result = []
    for j in joints:
        if j and j not in seen:
            seen.add(j)
            result.append(j)
    return result


def get_root_tag(root_short, source_obj, skin_infs=None):
    """为骨骼 Root 生成推荐/慎用标签文字（照搬 UEAnimCamExporter 的标签策略）。

    返回字符串（可能为空），用于在弹窗中显示。
    """
    if skin_infs is None:
        skin_infs = get_skin_influences(source_obj)

    # 统计属于该 Root 的 skinCluster influence 数量
    root_full = None
    try:
        matches = cmds.ls(root_short, long=True) or []
        if matches:
            root_full = matches[0]
    except Exception:
        pass

    skin_count = 0
    if root_full and skin_infs:
        for inf in skin_infs:
            # 判断 inf 是否在 root 层级下
            try:
                inf_full = cmds.ls(inf, long=True) or [inf]
                inf_full = inf_full[0]
                if inf_full == root_full or inf_full.startswith(root_full + "|"):
                    skin_count += 1
            except Exception:
                pass

    if skin_count > 0:
        return u"推荐：影响骨骼 {0}".format(skin_count)
    if _is_deformation_system_root(root_short):
        return u"DeformationSystem"
    if _is_fit_skeleton_root(root_short):
        return u"慎用：FitSkeleton 参考骨架"
    return u""


def find_camera_transforms(obj):
    """返回 obj 自身或其子孙层级中所有带 camera shape 的变换节点（完整路径，去重）。

    适用于“绑定相机”场景：相机被包在 Camera_Global / 控制器 等组下面，
    直接选组也能把里面的相机找出来。
    """
    found = []
    shapes = []
    try:
        if cmds.objectType(obj, isAType="camera"):
            shapes.append(obj)
        shapes += cmds.listRelatives(obj, allDescendents=True,
                                     type="camera", fullPath=True) or []
    except Exception:
        shapes = []
    for shp in shapes:
        parent = cmds.listRelatives(shp, parent=True, fullPath=True)
        trans = parent[0] if parent else shp
        if trans not in found:
            found.append(trans)
    return found


def abc_display_text(object_list, limit=None):
    """ABC 组列表展示文本（超过 limit 个自动省略并显示总数）"""
    if limit is None:
        limit = config.ABC_DISPLAY_LIMIT
    object_list = list(object_list)
    if len(object_list) <= limit:
        return ", ".join(object_list)
    shown = ", ".join(object_list[:limit])
    return u"{0} ... ({1}个物体)".format(shown, len(object_list))


def abc_full_text(object_list):
    """ABC 组完整对象列表文本"""
    return ", ".join(object_list)


def sanitize_filename(name):
    """把导出命名处理成安全的文件基本名（去非法字符与空白）"""
    text = re.sub(r'[\\/:*?"<>|\r\n\t]', "_", str(name))
    text = re.sub(r"\s+", "_", text).strip("_. ")
    return text or "export"


def normalize_dir(path):
    """规范化导出目录为 / 分隔路径，不存在则创建；异常抛出 RuntimeError"""
    path = (path or "").strip().replace("\\", "/")
    if not path:
        raise RuntimeError(u"导出目录为空，请先在界面中选择导出目录")
    if not os.path.isdir(path):
        try:
            os.makedirs(path)
        except OSError as exc:
            raise RuntimeError(u"无法创建导出目录: {0}（{1}）".format(path, exc))
    return path


def resolve_unique(node, what):
    """把记录中的名字解析为唯一完整路径；缺失/重名返回 None 并给出 warning"""
    if not node:
        cmds.warning(u"{0} 名称为空，已跳过".format(what))
        return None
    try:
        found = cmds.ls(node, long=True) or []
    except RuntimeError:
        found = []
    if not found:
        cmds.warning(u"{0} 在场景中不存在，已跳过: {1}".format(what, node))
        return None
    if len(found) > 1:
        cmds.warning(u"{0} 存在多个同名节点，无法唯一确定，已跳过: {1}".format(what, node))
        return None
    return found[0]


def to_transform(path):
    """若传入的是 shape 节点则取其父变换，否则原样返回"""
    try:
        if cmds.nodeType(path) not in ("transform", "joint"):
            parent = cmds.listRelatives(path, parent=True, fullPath=True)
            if parent:
                return parent[0]
    except Exception:
        pass
    return path


def restore_selection(saved):
    """恢复之前保存的选择集"""
    if saved:
        try:
            cmds.select(saved, replace=True)
        except Exception:
            cmds.select(clear=True)
    else:
        cmds.select(clear=True)


def mel_eval(expr):
    """安全执行 MEL 命令；失败抛出 RuntimeError"""
    try:
        _mel_module.eval(expr)
    except Exception as exc:
        raise RuntimeError(u"MEL 命令执行失败: {0} -> {1}".format(expr, exc))


def ensure_plugin_loaded(plugin):
    """加载单个 Maya 插件，返回是否成功"""
    try:
        if not cmds.pluginInfo(plugin, query=True, loaded=True):
            cmds.loadPlugin(plugin, quiet=True)
        return True
    except Exception as exc:
        cmds.warning(u"加载插件失败: {0} ({1})".format(plugin, exc))
        return False


def ensure_export_plugins():
    """确保导出所需插件都已加载；缺失抛出 RuntimeError"""
    missing = []
    for plugin in config.PLUGIN_LIST:
        if not ensure_plugin_loaded(plugin):
            missing.append(plugin)
    if missing:
        raise RuntimeError(u"缺少必需插件，无法导出: " + ", ".join(missing))


# ---------------------------------------------------------------------------
# 导出进度
#
# 用法（core.run_export_batch 负责 begin/end 与整体区间）：
#     utils.progress.begin(u"导出中…")
#     utils.progress.set_span(0.0, 1.0 / 总条目数, u"正在导出 1/3")
#     utils.progress.step(0.5, u"相机采样 120 帧")     # 当前条目内 0~1
#     utils.progress.end()
#
# - mayapy / -batch 下没有进度条 UI，自动降级为只打印一行；
# - step() 只在整数百分比变化时刷新，避免逐帧刷 UI 拖慢导出；
# - is_cancelled() 在无进度条时直接返回 False，零开销。
# ---------------------------------------------------------------------------
class ProgressReporter(object):
    """Maya progressWindow 的轻量封装"""

    def __init__(self):
        self._active = False
        self._base = 0.0
        self._span = 1.0
        self._last_percent = -1

    def _has_ui(self):
        try:
            if cmds.about(batch=True):
                return False
        except Exception:
            return False
        try:
            return callable(cmds.progressWindow)
        except Exception:
            return False

    def begin(self, title=u"导出中…", status=u""):
        """打开进度条；无界面环境只打印一行"""
        self._base = 0.0
        self._span = 1.0
        self._last_percent = -1
        if not self._has_ui():
            self._active = False
            print(u"[导出进度] {0}".format(title))
            return
        try:
            if cmds.progressWindow(query=True, exists=True):
                cmds.progressWindow(endProgress=True)
        except Exception:
            pass
        try:
            cmds.progressWindow(title=title, progress=0, status=status,
                                isInterruptable=True)
            self._active = True
        except Exception:
            self._active = False

    def set_span(self, base, span, status=u""):
        """为当前条目分配整体进度的区间（base~base+span）"""
        try:
            self._base = max(0.0, min(1.0, float(base)))
            self._span = max(0.0, min(1.0, float(span)))
        except (TypeError, ValueError):
            self._base, self._span = 0.0, 1.0
        self._last_percent = -1
        self.step(0.0, status)

    def step(self, fraction, status=None):
        """推进当前条目内的进度（fraction 0~1）"""
        try:
            fraction = max(0.0, min(1.0, float(fraction)))
        except (TypeError, ValueError):
            return
        percent = int(round((self._base + self._span * fraction) * 100))
        if percent == self._last_percent and status is None:
            return
        self._last_percent = percent
        if not self._active:
            return
        try:
            kwargs = {"edit": True, "progress": percent}
            if status is not None:
                kwargs["status"] = status
            cmds.progressWindow(**kwargs)
        except Exception:
            self._active = False

    def is_cancelled(self):
        """用户在进度条上点了取消（无进度条时恒为 False）"""
        if not self._active:
            return False
        try:
            return bool(cmds.progressWindow(query=True, isCancelled=True))
        except Exception:
            return False

    def end(self):
        if self._active:
            try:
                cmds.progressWindow(endProgress=True)
            except Exception:
                pass
        self._active = False
        self._last_percent = -1


# 全局进度实例（exporter / core / batch 共用）
progress = ProgressReporter()


# ---------------------------------------------------------------------------
# “导出中途请求聚焦某个节点”
#
# 导出过程中会临时改选择，收尾时 ui.on_export 会恢复用户原来的选择。
# 如果用户中途明确要求去看某个节点的设置（例如相机感光器检查里点“打开设置”），
# 就在导出层记一笔，收尾时改为保持选中该节点，免得刚打开的属性编辑器被顶掉。
# ---------------------------------------------------------------------------
_pending_focus = []


def request_focus_node(node):
    """记录导出收尾时要聚焦的节点（只保留最后一次请求）"""
    if node:
        del _pending_focus[:]
        _pending_focus.append(node)


def consume_focus_node():
    """取出并清空待聚焦节点；没有请求时返回 None"""
    if not _pending_focus:
        return None
    node = _pending_focus[-1]
    del _pending_focus[:]
    return node
