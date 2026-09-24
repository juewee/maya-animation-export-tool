# -*- coding: utf-8 -*-
"""
动画资产一键导出工具 - 导出执行层

FBX 骨骼动画 / ABC 几何体缓存（含导出前清理）/ 相机动画 三条导出链路。
每个函数只接收具体参数，不依赖 UI 与全局状态，可被 UI、core 或批处理脚本复用。
"""
import os
import shutil
import tempfile
from collections import deque

import maya.cmds as cmds

from . import config
from . import utils


def _build_filename(name, suffix_template, start, end):
    """根据命名模板构建文件名（不含扩展名）"""
    suffix = suffix_template.format(name=name, start=int(start), end=int(end))
    return "{0}{1}".format(name, suffix)


# ---------------------------------------------------------------------------
# ABC 几何体缓存导出
# ---------------------------------------------------------------------------
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
            if cmds.objectType(shp, isAType="mesh") and shp not in targets:
                targets.append(shp)
    return targets


def _run_abc_cleanup(objects):
    """ABC 导出前清理：只把 **多于 4 条边** 的面三角化。

    展开多边形组选择 -> polyCleanupArgList（>4 边面，保留构造历史）。

    注意：这里**不删除构造历史**、也**不冻结变换**。
    ABC 一般用于导出动画，而
      - 删除构造历史会断开变形器 / 约束 / 驱动关键帧等对几何的驱动；
      - 冻结变换会把当前帧的位移旋转烘进顶点、清掉动画通道。
    参数含义见 config.ABC_CLEANUP_MEL 的注释。
    """
    if not objects:
        return
    saved = cmds.ls(sl=True) or []
    try:
        cmds.select(_abc_cleanup_targets(objects), replace=True)
        utils.mel_eval(config.ABC_CLEANUP_MEL)
    finally:
        utils.restore_selection(saved)


def export_abc_item(item, export_dir, start, end, do_cleanup=False):
    """导出单个 ABC 几何体缓存组，返回输出文件路径；失败抛 RuntimeError

    item       条目 dict（object 可为单个名字或名字列表）
    export_dir 已存在的导出目录
    start/end  帧范围
    do_cleanup 是否先执行导出前清理
    """
    name = utils.sanitize_filename(item.get("export_name", ""))
    raw = item.get("object") or []
    if not isinstance(raw, (list, tuple)):
        raw = [raw]
    objs = []
    for obj_name in raw:
        path = utils.resolve_unique(obj_name, u"ABC 物体")
        if path is None:
            continue
        path = utils.to_transform(path)
        if path not in objs:
            objs.append(path)
    if not objs:
        raise RuntimeError(u"导出组 '{0}' 中没有可导出的物体".format(name))

    if do_cleanup:
        try:
            _run_abc_cleanup(objs)
        except Exception as exc:
            cmds.warning(u"ABC 清理失败（继续导出）: {0}".format(exc))

    file_name = name
    if config.NAMING_PRESETS.get("abc_add_range"):
        file_name = _build_filename(name, "_{start}-{end}", start, end)
    file_path = "{0}/{1}.abc".format(export_dir, file_name)
    # 默认**保留**命名空间（不加 -stripNamespaces）；
    # 在设置里取消勾选“保留命名空间”时才按旧行为去掉命名空间
    strip_namespaces = not bool(config.option("abc_keep_namespaces", True))

    job = "-frameRange {0} {1}".format(int(start), int(end))
    if strip_namespaces:
        job += " -stripNamespaces"
    job += " -uvWrite -writeColorSets -writeFaceSets"
    job += " -wholeFrameGeo -worldSpace -writeVisibility -writeUVSets"
    job += " -dataFormat ogawa"
    for root_path in objs:
        job += " -root \"{0}\"".format(root_path)
    job += " -file \"{0}\"".format(file_path)
    _log(u"AbcExport: {0}".format(job))
    _progress_step(0.2, u"写入 Alembic 缓存…")

    # 手动去掉了命名空间、又撞上重名时的补救提示（默认是保留命名空间的）
    def _namespace_hint():
        if not strip_namespaces:
            return u""
        return (u"；如果错误信息里出现重名/命名空间（namespace）相关字样，"
                u"请在设置面板里重新勾选“ABC 几何体缓存 → 保留命名空间”后重试")

    try:
        cmds.AbcExport(jobArg=job)
    except Exception as exc:
        raise RuntimeError(u"AbcExport 失败: {0}{1}".format(exc, _namespace_hint()))
    _progress_step(1.0, u"ABC 写入完成")
    if not os.path.exists(file_path):
        raise RuntimeError(u"ABC 文件未生成: {0}{1}".format(file_path, _namespace_hint()))
    return file_path


# ---------------------------------------------------------------------------
# FBX 通用导出
# ---------------------------------------------------------------------------
def _set_fbx_options(start, end, sample_by=1, cameras=False, animation_only=True,
                     export_skins=False, export_shapes=False, z_up=True):
    """设置 FBX 导出选项（参考 UEAnimCamExporter 的 FBX 配置）。

    animation_only=True  适合骨骼动画：只导出动画数据；
    相机导出传 False：相机 shape（感光元件/焦距等静态数据）也需要写进文件，
    否则导回时没有相机。
    sample_by 与 bakeResults 的采样步长保持一致（参考工具同样把它传给
    FBXExportBakeComplexStep）。
    """
    utils.mel_eval("FBXResetExport;")
    utils.mel_eval('FBXExportFileVersion -v "FBX202000";')

    # 动画烘焙设置
    utils.mel_eval("FBXExportBakeComplexAnimation -v true;")
    utils.mel_eval("FBXExportBakeComplexStart -v {0};".format(int(start)))
    utils.mel_eval("FBXExportBakeComplexEnd -v {0};".format(int(end)))
    utils.mel_eval("FBXExportBakeComplexStep -v {0};".format(max(1, int(sample_by))))
    utils.mel_eval("FBXExportBakeResampleAnimation -v true;")
    utils.mel_eval("FBXExportApplyConstantKeyReducer -v false;")
    utils.mel_eval('FBXExportQuaternion -v "euler";')

    utils.mel_eval("FBXExportAnimationOnly -v {0};".format(
        "true" if animation_only else "false"))
    utils.mel_eval("FBXExportSmoothingGroups -v true;")
    utils.mel_eval("FBXExportSmoothMesh -v false;")
    utils.mel_eval("FBXExportHardEdges -v false;")
    utils.mel_eval("FBXExportTangents -v false;")
    utils.mel_eval("FBXExportTriangulate -v false;")

    utils.mel_eval("FBXExportSkins -v {0};".format(
        "true" if export_skins else "false"))
    utils.mel_eval("FBXExportShapes -v {0};".format(
        "true" if export_shapes else "false"))
    utils.mel_eval("FBXExportCameras -v {0};".format(
        "true" if cameras else "false"))
    utils.mel_eval("FBXExportLights -v false;")
    utils.mel_eval("FBXExportConstraints -v false;")
    utils.mel_eval("FBXExportInputConnections -v false;")
    utils.mel_eval("FBXExportInAscii -v false;")

    if z_up:
        utils.mel_eval("FBXExportUpAxis z;")
        utils.mel_eval('FBXExportAxisConversionMethod "convertAnimation";')


def _export_fbx(file_path):
    """把当前选择导出为 FBX（先删除旧文件避免覆盖询问）。

    兼容中文路径/中文文件名：若直接导出失败，先写 ASCII 临时路径再复制。
    """
    if os.path.exists(file_path):
        try:
            os.remove(file_path)
        except OSError:
            pass
    fp = file_path.replace("\\", "/")
    folder = os.path.dirname(fp)
    if folder and not os.path.isdir(folder):
        try:
            os.makedirs(folder)
        except OSError:
            pass
    try:
        cmds.FBXExport(f=fp, s=True)
    except Exception:
        utils.mel_eval('FBXExport -f "{0}" -s;'.format(fp))
    if os.path.exists(file_path):
        return

    # 兼容部分 Maya/FBX 插件在中文路径下写入失败：先导出到 ASCII 临时路径再复制
    has_non_ascii = any(ord(c) > 127 for c in file_path)
    if has_non_ascii:
        temp_dir = tempfile.mkdtemp(prefix="animExp_FBX_")
        temp_path = os.path.join(temp_dir, "animexp_temp.fbx").replace("\\", "/")
        try:
            try:
                cmds.FBXExport(f=temp_path, s=True)
            except Exception:
                utils.mel_eval('FBXExport -f "{0}" -s;'.format(temp_path))
            if os.path.exists(temp_path):
                try:
                    shutil.copy2(temp_path, file_path)
                except Exception:
                    pass
        finally:
            try:
                shutil.rmtree(temp_dir)
            except Exception:
                pass

    if not os.path.exists(file_path):
        cmds.warning(u"FBX 命令已执行但未检测到文件: {0}".format(file_path))


# ---------------------------------------------------------------------------
# 共享辅助（骨骼/相机导出共用）
# ---------------------------------------------------------------------------
_TRS_ATTRS = (
    "translateX", "translateY", "translateZ",
    "rotateX", "rotateY", "rotateZ",
    "scaleX", "scaleY", "scaleZ",
)


def _unlock_trs(node):
    """解锁节点的 T/R/S 属性并设为可设关键帧"""
    for attr in _TRS_ATTRS:
        plug = "{0}.{1}".format(node, attr)
        if not cmds.objExists(plug):
            continue
        try:
            cmds.setAttr(plug, lock=False)
        except Exception:
            pass
        try:
            cmds.setAttr(plug, keyable=True)
        except Exception:
            pass


def _sample_frames(start, end, sample_by=1):
    """生成采样帧列表"""
    start = int(start)
    end = int(end)
    sample_by = max(1, int(sample_by))
    frames = list(range(start, end + 1, sample_by))
    if not frames or frames[-1] != end:
        frames.append(end)
    return frames


# ---------------------------------------------------------------------------
# FBX 骨骼动画导出辅助（照搬 UEAnimCamExporter 的复制+约束烘焙方案）
# ---------------------------------------------------------------------------
_TEMP_GRP = "animExp_TEMP_GRP"


def _make_temp_group():
    """创建/重建临时空组，用于挂载复制的骨骼"""
    if cmds.objExists(_TEMP_GRP):
        try:
            cmds.delete(_TEMP_GRP)
        except Exception:
            pass
    return cmds.group(empty=True, name=_TEMP_GRP)


def _joint_children(node):
    """返回节点的直接子关节（full path）"""
    return cmds.listRelatives(node, children=True, fullPath=True, type="joint") or []


def _pair_joint_tree(original_root, duplicate_root):
    """递归配对原始/复制骨骼树，返回 [(src, dst), ...]"""
    pairs = [(original_root, duplicate_root)]
    orig_children = _joint_children(original_root)
    dup_children = _joint_children(duplicate_root)
    for orig_child, dup_child in zip(orig_children, dup_children):
        pairs.extend(_pair_joint_tree(orig_child, dup_child))
    return pairs


def _delete_non_joint_descendants(root):
    """清理复制骨架：删掉 mesh/控制器曲线/locator/constraint 等非 joint 节点。

    从深到浅删除，若非 joint 节点下还挂着 joint 则跳过（避免连带删除骨骼）。
    """
    descendants = cmds.listRelatives(root, allDescendents=True, fullPath=True) or []
    descendants = sorted(descendants, key=lambda p: p.count("|"), reverse=True)
    for node in descendants:
        if not cmds.objExists(node):
            continue
        if cmds.nodeType(node) == "joint":
            continue
        joint_children = cmds.listRelatives(
            node, allDescendents=True, fullPath=True, type="joint") or []
        if joint_children:
            continue
        try:
            cmds.delete(node)
        except Exception:
            pass


def _copy_basic_transform_settings(src, dst):
    """复制 rotateOrder 等基础 transform 属性"""
    for attr in ["rotateOrder"]:
        src_plug = "{0}.{1}".format(src, attr)
        dst_plug = "{0}.{1}".format(dst, attr)
        if cmds.objExists(src_plug) and cmds.objExists(dst_plug):
            try:
                cmds.setAttr(dst_plug, cmds.getAttr(src_plug))
            except Exception:
                pass


def _delete_animation_on_nodes(nodes):
    """清除临时骨骼上的动画曲线"""
    for node in nodes or []:
        if not node or not cmds.objExists(node):
            continue
        try:
            cmds.cutKey(node, clear=True)
        except Exception:
            pass


def _force_full_trs_keys(nodes, start, end, sample_by=1, progress_range=None):
    """保证每根 Joint 在每个采样帧都有完整 T/R/S 关键帧。

    防止 FBX/UE 把某些静态子骨骼曲线认为可省略而丢弃。
    progress_range 给定时，在这段进度区间内按帧推进进度条。
    """
    frames = _sample_frames(start, end, sample_by)
    old_time = cmds.currentTime(q=True)
    try:
        for index, frame in enumerate(frames):
            if index % 8 == 0:
                _check_cancelled()
            if progress_range:
                low, high = progress_range
                _progress_step(low + (high - low) * (float(index) / max(1, len(frames))),
                               u"补全关键帧 {0}".format(frame))
            cmds.currentTime(frame, edit=True)
            for node in nodes or []:
                if not cmds.objExists(node):
                    continue
                for attr in _TRS_ATTRS:
                    plug = "{0}.{1}".format(node, attr)
                    if not cmds.objExists(plug):
                        continue
                    try:
                        cmds.setKeyframe(node, attribute=attr, time=frame)
                    except Exception:
                        pass
    finally:
        cmds.currentTime(old_time, edit=True)


def _bake_joint_pairs_by_constraints(pairs, start, end, sample_by=1):
    """UE 兼容的本地层级骨骼 Bake：约束临时骨骼并完整 Bake 所有 Joint 本地 T/R/S。

    为什么不用世界矩阵模式：
    - UE 动画 FBX 最终套到已有 Skeleton，最稳定的是与 RIG 骨架一致的本地 Joint 通道；
    - 世界矩阵 Bake 容易在 Y-Up/Z-Up 转换时造成根骨或子骨轴向偏移；
    - 约束 Bake 让 Maya 按 jointOrient/rotateOrder/父子层级求解本地通道，更接近 UE 需要的曲线。
    """
    ordered = sorted(pairs, key=lambda pair: pair[1].count("|"))
    dup_joints = [dst for _src, dst in ordered]
    constraints = []

    for src, dst in ordered:
        if not (cmds.objExists(src) and cmds.objExists(dst)):
            continue
        _copy_basic_transform_settings(src, dst)
        _unlock_trs(dst)
        try:
            constraints.extend(
                cmds.parentConstraint(src, dst, maintainOffset=False) or [])
        except Exception:
            # 降级：parentConstraint 失败时拆成 point + orient
            try:
                constraints.extend(
                    cmds.pointConstraint(src, dst, maintainOffset=False) or [])
            except Exception:
                pass
            try:
                constraints.extend(
                    cmds.orientConstraint(src, dst, maintainOffset=False) or [])
            except Exception:
                pass
        try:
            constraints.extend(
                cmds.scaleConstraint(src, dst, maintainOffset=False) or [])
        except Exception:
            pass

    _progress_step(0.05, u"约束 + 烘焙骨骼动画…")
    try:
        try:
            cmds.bakeResults(
                dup_joints,
                time=(start, end),
                sampleBy=sample_by,
                simulation=True,
                preserveOutsideKeys=False,
                sparseAnimCurveBake=False,
                removeBakedAttributeFromLayer=False,
                bakeOnOverrideLayer=False,
                minimizeRotation=False,
                controlPoints=False,
                shape=False,
                disableImplicitControl=False,
            )
        except TypeError:
            # 老版本 Maya 无部分参数，降级
            cmds.bakeResults(
                dup_joints,
                time=(start, end),
                sampleBy=sample_by,
                simulation=True,
                preserveOutsideKeys=False,
                sparseAnimCurveBake=False,
                minimizeRotation=False,
                controlPoints=False,
                shape=False,
            )
    finally:
        if constraints:
            try:
                cmds.delete(constraints)
            except Exception:
                pass

    # 再补一遍每帧 T/R/S key，防止静态子骨骼曲线被 FBX/UE 优化掉
    _progress_step(0.6, u"补全每帧 T/R/S 关键帧…")
    _force_full_trs_keys(dup_joints, start, end, sample_by=sample_by,
                         progress_range=(0.6, 1.0))
    return dup_joints


def _rename_joint_tree(original_root, duplicate_root):
    """递归重命名复制骨骼的短名与原始骨骼一致（保证 UE 导入时骨骼名匹配）"""
    orig_children = _joint_children(original_root)
    dup_children = _joint_children(duplicate_root)
    for orig_child, dup_child in zip(orig_children, dup_children):
        _rename_joint_tree(orig_child, dup_child)
    orig_short = utils.get_short_name(original_root)
    try:
        cmds.rename(duplicate_root, orig_short)
    except Exception:
        pass


def _key_count_on_nodes(nodes):
    """统计节点上的关键帧总数"""
    count = 0
    for node in nodes or []:
        if not node or not cmds.objExists(node):
            continue
        try:
            count += cmds.keyframe(node, query=True, keyframeCount=True) or 0
        except Exception:
            pass
    return count


def _create_baked_duplicate_skeleton(root, temp_group, start, end, sample_by=1):
    """复制骨骼层级，清理非骨骼节点，重命名，约束+烘焙动画。

    返回 (dup_root_full_path, dup_joints)。
    """
    # 1) 复制骨骼根（不带输入连接，避免把 IK/约束/表达式也带过来）
    try:
        dup_root = cmds.duplicate(
            root, renameChildren=True, inputConnections=False)[0]
    except TypeError:
        dup_root = cmds.duplicate(root, renameChildren=True)[0]
    dup_root = cmds.parent(dup_root, temp_group)[0]

    # 2) 删除复制骨架中所有非 joint 节点（mesh/曲线/locator/约束等）
    _delete_non_joint_descendants(dup_root)

    # 3) 递归重命名复制骨骼，使其短名与原始骨骼一致
    _rename_joint_tree(root, dup_root)

    # 4) 重新解析 dup_root 的完整路径（重命名后路径可能变化）
    orig_short = utils.get_short_name(root)
    children = cmds.listRelatives(
        temp_group, children=True, fullPath=True) or []
    dup_root = None
    for c in children:
        if utils.get_short_name(c) == orig_short:
            dup_root = c
            break
    if dup_root is None and children:
        dup_root = children[0]
    if dup_root is None:
        raise RuntimeError(u"复制骨骼后无法定位骨骼根")

    # 5) 配对原始/复制骨骼，清除副本上的动画，约束+烘焙
    pairs = _pair_joint_tree(root, dup_root)
    dup_joints = [dst for _src, dst in pairs]
    _delete_animation_on_nodes(dup_joints)
    _bake_joint_pairs_by_constraints(pairs, start, end, sample_by=sample_by)

    return dup_root, dup_joints


def export_fbx_item(item, export_dir, start, end):
    """导出单个 FBX 骨骼动画条目（照搬 UEAnimCamExporter 的复制+约束烘焙方案）。

    流程：复制骨骼 → 清理非骨骼节点 → 重命名 → 约束+烘焙 → 全帧 TRS 补帧 →
    检查关键帧 → 选中副本骨骼 → FBX 导出 → 删除临时组。
    FBX 里只有干净的 Joint 层级 + 动画曲线，不带 IK/约束/控制器/Mesh。
    """
    name = utils.sanitize_filename(item.get("export_name", ""))
    root = utils.resolve_unique(item.get("object", ""), u"FBX 骨骼根")
    if root is None:
        raise RuntimeError(u"骨骼根不存在或命名不唯一，无法导出 '{0}'".format(name))

    # 采样步长与 Z-Up 转换都由 config.EXPORT_OPTIONS 控制（设置面板可改）
    sample_by = max(1, int(config.option("sample_by", 1)))

    saved = cmds.ls(sl=True) or []
    temp_group = None
    try:
        # 1) 创建临时组，复制+烘焙骨骼
        temp_group = _make_temp_group()
        dup_root, dup_joints = _create_baked_duplicate_skeleton(
            root, temp_group, start, end, sample_by=sample_by)

        # 2) 检查烘焙结果
        key_count = _key_count_on_nodes(dup_joints)
        if key_count <= 0:
            raise RuntimeError(
                u"骨骼烘焙后没有任何动画关键帧，请确认选择的是实际变形/有动画驱动的"
                u"骨骼 Root，而不是 FitSkeleton/参考骨架/空 Root: {0}".format(root))

        # 3) 选中烘焙后的副本骨骼层级（不含 mesh/控制器/约束）
        cmds.select(dup_root, hierarchy=True, replace=True)

        # 4) 设置 FBX 选项并导出（骨骼/动画默认 Z-Up，与参考工具 RIG/Anim 一致）
        _set_fbx_options(start, end, sample_by=sample_by, cameras=False,
                         animation_only=False, export_skins=False,
                         export_shapes=False,
                         z_up=bool(config.option("fbx_z_up", True)))
        file_name = _build_filename(name, config.NAMING_PRESETS["fbx_anim_suffix"], start, end)
        file_path = "{0}/{1}.fbx".format(export_dir, file_name)
        _progress_step(0.98, u"写入 FBX…")
        _export_fbx(file_path)
        _progress_step(1.0, u"FBX 写入完成")
        return file_path
    finally:
        if temp_group and cmds.objExists(temp_group):
            try:
                cmds.delete(temp_group)
            except Exception:
                pass
        utils.restore_selection(saved)


# ---------------------------------------------------------------------------
# 相机动画导出（照搬 UEAnimCamExporter v4.1.10 的相机烘焙 + 导出流程）
#
# 参考工具已验证可用的相机链路：
#   1. 新建干净相机并直接挂世界根（FBX 里没有额外父级，避免 UE 导入时父级偏移）
#   2. 复制 rotateOrder + 静态相机参数（焦距/光圈/裁剪面等 13 个属性）
#   3. parentConstraint + scaleConstraint 跟随源相机 →
#      bakeResults(shape=True, minimizeRotation=True)
#      约束创建失败时退回“世界矩阵逐帧采样”（dgdirty + refresh 强制求解）
#   4. 逐帧拷贝相机 shape 属性并补齐 TRS 关键帧
#   5. filterCurve 平滑
#   6. 只选 Camera Transform + Shape 导出
# 另外按参考工具补齐：相机 Rig 检测（仅日志）、相机实际动画段检测、
# 导出前感光器/分辨率检查、相机轴向转换独立开关（默认不做 Z-Up，
# 见 config.EXPORT_OPTIONS 里的 camera_z_up）。
# ---------------------------------------------------------------------------
# 需要从源相机 shape 复制到导出相机的参数（与 UEAnimCamExporter 一致）
_CAMERA_ATTRS = (
    "focalLength",
    "cameraScale",
    "horizontalFilmAperture",
    "verticalFilmAperture",
    "filmFit",
    "nearClipPlane",
    "farClipPlane",
    "lensSqueezeRatio",
    "horizontalFilmOffset",
    "verticalFilmOffset",
    "filmTranslateH",
    "filmTranslateV",
    "overscan",
)

# 相机 Rig 追踪深度上限（与参考工具 CAMERA_RIG_MAX_DEPTH 一致）
_CAMERA_RIG_MAX_DEPTH = 4

# 短属性名（参考工具 _key_times_on_node 用它兜底查关键帧）
_SHORT_TRS_ATTRS = ("tx", "ty", "tz", "rx", "ry", "rz", "sx", "sy", "sz")


def _log(message):
    """导出过程日志（输出到脚本编辑器）"""
    print(u"[动画导出] {0}".format(message))


def _progress_step(fraction, status=None):
    """推进当前条目的导出进度（进度条由 core.run_export_batch 统一管理）"""
    utils.progress.step(fraction, status)


class ExportCancelled(Exception):
    """用户主动中止导出（进度条取消 / 相机感光器检查里选了“打开设置”或“取消”）。

    core.run_export_batch 会捕获它并停止后续条目，且**不计入失败**——
    因为这是用户的选择，不是导出错误。
    """


def _check_cancelled():
    """用户在进度条上点了取消 -> 抛 ExportCancelled，由上层 finally 清理临时节点"""
    if utils.progress.is_cancelled():
        raise ExportCancelled(u"用户取消导出")


def _safe_mel(expr):
    """执行 MEL，失败忽略（参考工具的 _safe_mel）"""
    try:
        utils.mel_eval(expr)
    except Exception:
        pass


def _dedupe(nodes):
    """按长名去重保序（参考工具的 utils.dedupe）"""
    raw = [n for n in (nodes or []) if n]
    try:
        existing = cmds.ls(raw, long=True) or []
    except Exception:
        existing = raw
    result = []
    seen = set()
    for node in existing:
        if node and node not in seen:
            seen.add(node)
            result.append(node)
    return result


def _camera_shape(transform):
    """取变换下的 camera shape 节点"""
    shapes = cmds.listRelatives(transform, shapes=True, fullPath=True, type="camera") or []
    return shapes[0] if shapes else None


def _camera_parent_chain(cam_transform):
    """返回 Camera transform 向上的父级链（相机 Rig/Zero 组的动画通常在这些节点上）"""
    chain = []
    current = cam_transform
    guard = 0
    while current and cmds.objExists(current) and guard < 200:
        guard += 1
        parents = cmds.listRelatives(current, parent=True, fullPath=True) or []
        if not parents:
            break
        chain.append(parents[0])
        current = parents[0]
    return _dedupe(chain)


def _node_has_control_shape(transform):
    """判断节点下是否挂着曲线/locator 等控制器形状"""
    shapes = cmds.listRelatives(transform, shapes=True, fullPath=True) or []
    for shape in shapes:
        try:
            if cmds.nodeType(shape) in ("nurbsCurve", "locator"):
                return True
        except Exception:
            pass
    return False


def _interesting_camera_driver_node(node):
    """判断节点是否可能驱动相机最终结果（动画曲线/约束/工具节点等）"""
    try:
        ntype = cmds.nodeType(node)
    except Exception:
        return False
    if ntype.startswith("animCurve"):
        return True
    if ntype.endswith("Constraint"):
        return True
    if ntype in (
        "pairBlend", "blendWeighted", "unitConversion", "multDoubleLinear",
        "addDoubleLinear", "multiplyDivide", "plusMinusAverage", "condition",
        "clamp", "remapValue", "expression", "choice", "reverse", "animLayer",
        "transform", "joint", "locator", "nurbsCurve"
    ):
        return True
    return False


def _upstream_camera_rig_nodes(cam_transform, max_depth=_CAMERA_RIG_MAX_DEPTH):
    """粗略检测驱动相机最终结果的父级、控制器、约束和动画节点（仅用于日志与提示）"""
    seeds = [cam_transform]
    shape = _camera_shape(cam_transform)
    if shape:
        seeds.append(shape)
    seeds.extend(_camera_parent_chain(cam_transform))

    result = []
    queue = deque(_dedupe(seeds))
    visited = set()
    depth = {node: 0 for node in list(queue)}

    while queue:
        node = queue.popleft()
        if not node or not cmds.objExists(node):
            continue
        full = node
        try:
            matches = cmds.ls(node, long=True) or []
            full = matches[0] if matches else node
        except Exception:
            full = node
        if full in visited:
            continue
        visited.add(full)

        level = depth.get(node, 0)
        if node != cam_transform:
            try:
                if (node in seeds or _interesting_camera_driver_node(node)
                        or _node_has_control_shape(node)):
                    result.append(full)
            except Exception:
                pass

        if level >= max_depth:
            continue
        try:
            connections = cmds.listConnections(
                node, source=True, destination=False, plugs=False) or []
        except Exception:
            connections = []
        for conn in connections:
            if not conn or not cmds.objExists(conn):
                continue
            try:
                if (_interesting_camera_driver_node(conn)
                        or cmds.nodeType(conn) in ("transform", "joint")):
                    queue.append(conn)
                    depth[conn] = level + 1
            except Exception:
                pass

    return _dedupe(result)


def _log_camera_rig_detection(cam_transform):
    """打印相机父级链与驱动节点（不导出这些控制器，只把最终结果烘焙到单 Camera）"""
    drivers = _upstream_camera_rig_nodes(cam_transform, max_depth=_CAMERA_RIG_MAX_DEPTH)
    parent_chain = _camera_parent_chain(cam_transform)
    if parent_chain:
        _log(u"相机父级/Zero 组链检测：{0} 层".format(len(parent_chain)))
        for parent in parent_chain[:12]:
            _log(u"  Parent: {0}".format(parent))
        if len(parent_chain) > 12:
            _log(u"  ... 其余 {0} 个父级省略".format(len(parent_chain) - 12))
    if drivers:
        _log(u"检测到相机可能由控制器/约束/动画节点驱动：{0} 个。"
             u"这些控制器不会被导出，只把最终结果烘焙到单 Camera。".format(len(drivers)))
        for driver in drivers[:20]:
            try:
                _log(u"  Driver[{0}]: {1}".format(cmds.nodeType(driver), driver))
            except Exception:
                _log(u"  Driver: {0}".format(driver))
        if len(drivers) > 20:
            _log(u"  ... 其余 {0} 个驱动节点省略".format(len(drivers) - 20))
    else:
        _log(u"未检测到明显相机控制器/约束；仍会按最终世界空间结果逐帧 Bake 到单 Camera。")
    return drivers


def _key_times_on_node(node):
    """返回节点上所有关键帧时间（用于相机实际动画帧段检测）"""
    if not node or not cmds.objExists(node):
        return []
    times = []
    try:
        times.extend(cmds.keyframe(node, query=True, timeChange=True) or [])
    except Exception:
        pass
    # 某些 Maya 版本对 node 直接查询不完整，再按常用属性兜底查一次
    for attr in _SHORT_TRS_ATTRS + _TRS_ATTRS + _CAMERA_ATTRS:
        plug = "{0}.{1}".format(node, attr)
        if not cmds.objExists(plug):
            continue
        try:
            times.extend(cmds.keyframe(plug, query=True, timeChange=True) or [])
        except Exception:
            pass
    return times


def _camera_related_animation_nodes(cam_transform, include_drivers=True):
    """收集与相机最终求解相关的节点：相机、shape、父级 Zero 组、上游约束/控制器"""
    nodes = []
    if cam_transform and cmds.objExists(cam_transform):
        nodes.append(cam_transform)
    shape = _camera_shape(cam_transform)
    if shape:
        nodes.append(shape)
    nodes.extend(_camera_parent_chain(cam_transform))
    if include_drivers:
        nodes.extend(_upstream_camera_rig_nodes(cam_transform, max_depth=5))
    return _dedupe([n for n in nodes if n and cmds.objExists(n)])


def _detect_camera_animation_range(cam_transform, ui_start, ui_end,
                                   include_drivers=True, clamp_to_ui=True):
    """检测 Camera 真实动画帧段（相机、Shape、父级 Zero 组、约束、控制器、动画曲线）"""
    ui_start = int(ui_start)
    ui_end = int(ui_end)
    all_times = []
    nodes = _camera_related_animation_nodes(cam_transform, include_drivers=include_drivers)
    for node in nodes:
        all_times.extend(_key_times_on_node(node))

    if not all_times:
        _log(u"Camera 未检测到关键帧，使用 UI 帧段：{0}-{1}".format(ui_start, ui_end))
        return ui_start, ui_end

    detected_start = int(round(min(all_times)))
    detected_end = int(round(max(all_times)))
    _log(u"Camera 检测到实际动画帧段：{0}-{1}，扫描节点数：{2}".format(
        detected_start, detected_end, len(nodes)))

    if clamp_to_ui:
        bake_start = max(ui_start, detected_start)
        bake_end = min(ui_end, detected_end)
        if bake_end < bake_start:
            cmds.warning(u"Camera 检测到的动画帧段不在 UI 帧段内，将退回使用 UI 帧段：{0}-{1}".format(
                ui_start, ui_end))
            return ui_start, ui_end
        if bake_start != detected_start or bake_end != detected_end:
            _log(u"Camera 动画帧段已限制在 UI Start/End 内：{0}-{1}".format(bake_start, bake_end))
        return bake_start, bake_end

    if detected_start < ui_start or detected_end > ui_end:
        cmds.warning(u"Camera 关键帧超出 UI 帧段：UI {0}-{1}，动画 {2}-{3}。"
                     u"当前设置允许使用完整动画帧段。".format(
                         ui_start, ui_end, detected_start, detected_end))
    return detected_start, detected_end


def _safe_get_attr(node_attr, default=None):
    try:
        if cmds.objExists(node_attr):
            return cmds.getAttr(node_attr)
    except Exception:
        pass
    return default


def _get_render_resolution_info():
    """读取 Maya 渲染设置里的分辨率与像素宽高比"""
    width = _safe_get_attr("defaultResolution.width", 1920)
    height = _safe_get_attr("defaultResolution.height", 1080)
    pixel_aspect = _safe_get_attr("defaultResolution.pixelAspect", 1.0)
    try:
        width = float(width)
        height = float(height)
        pixel_aspect = float(pixel_aspect or 1.0)
    except Exception:
        width, height, pixel_aspect = 1920.0, 1080.0, 1.0
    if width <= 0:
        width = 1920.0
    if height <= 0:
        height = 1080.0
    return {
        "width": width,
        "height": height,
        "pixel_aspect": pixel_aspect,
        "render_aspect": (width * pixel_aspect) / height,
    }


def _get_camera_aperture_info(cam_transform):
    """读取相机 Film Aperture 信息（Maya 相机光圈单位通常为 inch）"""
    shape = _camera_shape(cam_transform)
    if not shape:
        return None
    hfa = _safe_get_attr(shape + ".horizontalFilmAperture", None)
    vfa = _safe_get_attr(shape + ".verticalFilmAperture", None)
    lens_squeeze = _safe_get_attr(shape + ".lensSqueezeRatio", 1.0)
    try:
        hfa = float(hfa)
        vfa = float(vfa)
        lens_squeeze = float(lens_squeeze or 1.0)
    except Exception:
        return None
    if hfa <= 0 or vfa <= 0:
        return None
    return {
        "shape": shape,
        "horizontal_aperture": hfa,
        "vertical_aperture": vfa,
        "camera_aspect": hfa / vfa,
        "lens_squeeze": lens_squeeze,
    }


def _open_render_settings_and_camera(cam_transform):
    """选中相机并打开 Render Settings 与属性编辑器（感光器检查里的“打开设置”）。

    顺序很关键：**先选中相机本体**（transform + camera shape），再开窗口，
    这样属性编辑器直接显示这台相机的 Film Aperture / 分辨率相关属性。
    同时用 utils.request_focus_node 记一笔：导出收尾恢复原选择时，UI 会保持
    选中这台相机，避免刚打开的属性编辑器又被顶掉。
    """
    shape = _camera_shape(cam_transform)
    targets = [cam_transform] + ([shape] if shape else [])
    try:
        cmds.select(targets, replace=True)
    except Exception:
        pass
    utils.request_focus_node(cam_transform)

    try:
        _safe_mel("unifiedRenderGlobalsWindow;")
        _safe_mel("RenderGlobalsWindow;")
    except Exception as exc:
        cmds.warning(u"无法自动打开渲染设置窗口，请手动打开 Render Settings：{0}".format(exc))
    try:
        _safe_mel("AttributeEditor;")
    except Exception as exc:
        cmds.warning(u"无法自动打开相机属性窗口，请手动选择 Camera 并打开 Attribute Editor：{0}".format(exc))


def _camera_sensor_mismatch_items(cams, tolerance=None):
    """检查 Camera Film Aperture 宽高比是否与 Render Settings 分辨率宽高比一致。

    UE 导入 FBX 时主要识别 Camera Filmback / Film Aperture；Maya 视口/渲染常由
    Render Settings 分辨率决定画幅。两者不一致时，导入 UE Sequencer 后相机构图
    可能和 Maya 渲染预览不同。
    """
    if tolerance is None:
        tolerance = float(config.option("camera_aperture_tolerance", 0.005))
    render = _get_render_resolution_info()
    items = []
    for cam in cams or []:
        cam_info = _get_camera_aperture_info(cam)
        if not cam_info:
            continue
        render_aspect = render["render_aspect"]
        cam_aspect = cam_info["camera_aspect"]
        relative = abs(cam_aspect - render_aspect) / max(render_aspect, 0.000001)
        if relative > tolerance:
            hfa = cam_info["horizontal_aperture"]
            vfa = cam_info["vertical_aperture"]
            items.append({
                "camera": cam,
                "shape": cam_info["shape"],
                "render": render,
                "camera_info": cam_info,
                "diff_percent": relative * 100.0,
                "recommended_vfa": hfa / render_aspect,
                "recommended_hfa": vfa * render_aspect,
            })
    return items


def _validate_camera_sensor_before_export(cams):
    """导出 Camera 前检查渲染分辨率与 Camera Film Aperture 比例是否匹配。

    返回 True 表示继续导出，False 表示暂停/取消导出（与参考工具行为一致）。
    """
    mismatches = _camera_sensor_mismatch_items(cams)
    if not mismatches:
        _log(u"Camera 感光器检查通过：Render Settings 分辨率比例与 Camera Film Aperture 比例基本一致。")
        return True

    lines = []
    first_cam = mismatches[0]["camera"]
    for item in mismatches[:4]:
        render = item["render"]
        cam_info = item["camera_info"]
        lines.append(
            u"Camera: {0}\n"
            u"  Render Settings: {1} x {2}, PixelAspect {3:.4f}, 比例 {4:.4f}\n"
            u"  Camera Aperture: H {5:.4f} in / V {6:.4f} in, 比例 {7:.4f}\n"
            u"  建议二选一：保持 H 则 V≈{8:.4f} in；保持 V 则 H≈{9:.4f} in".format(
                item["camera"],
                int(round(render["width"])),
                int(round(render["height"])),
                render["pixel_aspect"],
                render["render_aspect"],
                cam_info["horizontal_aperture"],
                cam_info["vertical_aperture"],
                cam_info["camera_aspect"],
                item["recommended_vfa"],
                item["recommended_hfa"],
            )
        )
    if len(mismatches) > 4:
        lines.append(u"另外还有 {0} 个 Camera 存在类似问题。".format(len(mismatches) - 4))

    msg = (
        u"检测到相机感光器比例与 Maya 渲染分辨率比例不一致。\n\n"
        u"原因：Maya 渲染/视口通常参考 Render Settings 的分辨率画幅；"
        u"但 FBX 导入 UE Sequencer 后，UE 主要读取 Camera 里的 Filmback/Camera Aperture。"
        u"如果两者比例不同，UE 里的相机构图、裁切或感光器比例可能和 Maya 不一致。\n\n"
        + u"\n\n".join(lines) +
        u"\n\n点击“是：打开设置”会打开 Render Settings 和相机属性窗口，并暂停本次导出；"
        u"点击“否：继续导出”会忽略该警告并正常导出。"
    )

    try:
        choice = cmds.confirmDialog(
            title=u"Camera 感光器/分辨率不匹配",
            message=msg,
            button=[u"是：打开设置", u"否：继续导出", u"取消导出"],
            defaultButton=u"是：打开设置",
            cancelButton=u"取消导出",
            dismissString=u"取消导出",
        )
    except Exception:
        # 批处理（mayapy）下弹窗不可用：只提示，继续导出
        cmds.warning(msg)
        return True
    if choice == u"否：继续导出":
        cmds.warning(u"已忽略 Camera 感光器/分辨率不匹配警告，继续导出。")
        return True
    if choice == u"是：打开设置":
        _open_render_settings_and_camera(first_cam)
        return False
    return False


def _safe_copy_camera_attr(src_shape, dst_shape, attr, set_key=False, frame=None):
    """安全复制相机 shape 属性，可选逐帧打关键帧（照搬 UEAnimCamExporter）"""
    src_attr = "{0}.{1}".format(src_shape, attr)
    dst_attr = "{0}.{1}".format(dst_shape, attr)
    if not (cmds.objExists(src_attr) and cmds.objExists(dst_attr)):
        return False
    try:
        value = cmds.getAttr(src_attr)
        # getAttr 有时会返回 [(x, y, z)] 这种复合值
        if isinstance(value, (list, tuple)) and len(value) == 1 and isinstance(value[0], (list, tuple)):
            value = value[0]
        try:
            if isinstance(value, (list, tuple)):
                cmds.setAttr(dst_attr, *value)
            else:
                cmds.setAttr(dst_attr, value)
        except Exception:
            return False
        if set_key:
            try:
                if frame is None:
                    cmds.setKeyframe(dst_shape, attribute=attr)
                else:
                    cmds.setKeyframe(dst_shape, attribute=attr, time=frame)
            except Exception:
                pass
        return True
    except Exception:
        return False


def _bake_camera_by_parent_constraint(cam_transform, dup_transform, dup_shape,
                                      start, end, sample_by=1, detect_rig=True):
    """相机约束烘焙（照搬 UEAnimCamExporter 的 parentConstraint + bakeResults 方案）：

    复制单 Camera 到世界根节点，用 parentConstraint/scaleConstraint 跟随源相机，
    再 bakeResults(shape=True, minimizeRotation=True) 烘焙，并逐帧拷贝 Camera Shape
    属性和补齐 TRS 关键帧。约束创建失败时退回世界矩阵逐帧采样。
    """
    start = int(start)
    end = int(end)
    sample_by = max(1, int(sample_by))

    if detect_rig:
        _log_camera_rig_detection(cam_transform)

    _log(u"相机 ParentConstraint Bake：{0}  帧段 {1}-{2}  SampleBy {3}  输出单 Camera: {4}".format(
        cam_transform, start, end, sample_by, dup_transform))

    _progress_step(0.05, u"约束并烘焙相机…")
    _unlock_trs(dup_transform)
    constraints = []
    old_time = cmds.currentTime(q=True)
    old_auto_key = None
    try:
        try:
            old_auto_key = cmds.autoKeyframe(q=True, state=True)
            cmds.autoKeyframe(state=False)
        except Exception:
            old_auto_key = None

        # parentConstraint 跟随源相机运动
        try:
            constraints.append(
                cmds.parentConstraint(cam_transform, dup_transform,
                                      maintainOffset=False, weight=1)[0])
        except Exception as exc:
            cmds.warning(u"Camera parentConstraint 创建失败，退回世界矩阵逐帧采样：{0}".format(exc))
            _sample_world_camera_to_duplicate(
                cam_transform, dup_transform, dup_shape, start, end,
                sample_by=sample_by, detect_rig=False, force_scene_eval=True)
            return

        # scaleConstraint 保持大小一致
        try:
            constraints.append(
                cmds.scaleConstraint(cam_transform, dup_transform,
                                     maintainOffset=False, weight=1)[0])
        except Exception:
            pass

        # 烘焙 Transform（含 shape=True, minimizeRotation=True）
        try:
            cmds.bakeResults(
                [dup_transform],
                time=(start, end),
                simulation=True,
                sampleBy=sample_by,
                oversamplingRate=1,
                disableImplicitControl=True,
                preserveOutsideKeys=True,
                sparseAnimCurveBake=False,
                removeBakedAttributeFromLayer=False,
                removeBakedAnimFromLayer=False,
                bakeOnOverrideLayer=False,
                minimizeRotation=True,
                controlPoints=False,
                shape=True,
            )
        except TypeError:
            # 老版本 Maya 无部分参数，降级
            cmds.bakeResults(
                [dup_transform],
                time=(start, end),
                simulation=True,
                sampleBy=sample_by,
                disableImplicitControl=True,
                preserveOutsideKeys=True,
                sparseAnimCurveBake=False,
                minimizeRotation=True,
                controlPoints=False,
                shape=True,
            )

        # 删除约束
        for c in constraints:
            try:
                if c and cmds.objExists(c):
                    cmds.delete(c)
            except Exception:
                pass
        constraints = []

        # 逐帧拷贝 Camera Shape 属性（焦距/FilmOffset 等）并补齐 TRS 关键帧，
        # 避免 UE 导入时某些静态通道被优化导致相机偏移/方向不同。
        src_shape = _camera_shape(cam_transform)
        frames = _sample_frames(start, end, sample_by)
        _progress_step(0.5, u"逐帧采样相机参数…")
        for index, frame in enumerate(frames):
            if index % 8 == 0:
                _check_cancelled()
            _progress_step(0.5 + 0.5 * (float(index) / max(1, len(frames))),
                           u"相机采样 {0}".format(frame))
            try:
                cmds.currentTime(frame, edit=True, update=True)
            except TypeError:
                cmds.currentTime(frame, edit=True)
            if src_shape:
                for attr in _CAMERA_ATTRS:
                    _safe_copy_camera_attr(src_shape, dup_shape, attr,
                                           set_key=True, frame=frame)
            for attr in _TRS_ATTRS:
                try:
                    cmds.setKeyframe(dup_transform, attribute=attr, time=frame)
                except Exception:
                    pass
    finally:
        for c in constraints:
            try:
                if c and cmds.objExists(c):
                    cmds.delete(c)
            except Exception:
                pass
        try:
            cmds.currentTime(old_time, edit=True)
        except Exception:
            pass
        if old_auto_key is not None:
            try:
                cmds.autoKeyframe(state=old_auto_key)
            except Exception:
                pass

    try:
        cmds.filterCurve(dup_transform)
    except Exception:
        pass


def _sample_world_camera_to_duplicate(cam_transform, dup_transform, dup_shape,
                                      start, end, sample_by=1, detect_rig=True,
                                      force_scene_eval=True):
    """直接采样源相机最终世界矩阵到一个“单独 Camera”（参考工具的备用 Bake 模式）：

    - 自动检测父级 Zero 组/控制器/约束/动画节点；
    - 不导出这些控制器，只把最终求解结果逐帧写到单 Camera 的 T/R/S；
    - 每个采样帧强制刷新 DG/视口，减少复杂 Rig、Aim 约束、表达式、动画层
      没有及时求解的问题。
    """
    start = int(start)
    end = int(end)
    sample_by = max(1, int(sample_by))
    frames = _sample_frames(start, end, sample_by)

    if detect_rig:
        _log_camera_rig_detection(cam_transform)

    _log(u"相机最终结果 Bake（世界矩阵采样）：{0}  帧段 {1}-{2}  SampleBy {3}  输出单 Camera: {4}".format(
        cam_transform, start, end, sample_by, dup_transform))

    old_time = cmds.currentTime(q=True)
    old_auto_key = None
    try:
        old_auto_key = cmds.autoKeyframe(q=True, state=True)
        cmds.autoKeyframe(state=False)
    except Exception:
        old_auto_key = None

    try:
        for index, frame in enumerate(frames):
            if index % 8 == 0:
                _check_cancelled()
            _progress_step(float(index) / max(1, len(frames)),
                           u"相机世界矩阵采样 {0}".format(frame))
            try:
                cmds.currentTime(frame, edit=True, update=True)
            except TypeError:
                cmds.currentTime(frame, edit=True)

            if force_scene_eval:
                # 复杂相机 Rig 常依赖约束/表达式/动画层/父级控制器，强制刷新
                # 才能让查询到的是最终视口结果
                try:
                    cmds.dgdirty(a=True)
                except Exception:
                    pass
                try:
                    cmds.refresh(currentView=True, force=True)
                except Exception:
                    try:
                        cmds.refresh(force=True)
                    except Exception:
                        pass

            try:
                matrix = cmds.xform(cam_transform, q=True, ws=True, matrix=True)
                cmds.xform(dup_transform, ws=True, matrix=matrix)
            except Exception as exc:
                cmds.warning(u"相机最终世界矩阵采样失败 Frame {0}: {1}".format(frame, exc))

            # 明确给 transform 打完整 T/R/S key
            for attr in _TRS_ATTRS:
                try:
                    cmds.setKeyframe(dup_transform, attribute=attr, time=frame)
                except Exception:
                    pass

            # 焦距/胶片门/Film Offset 等镜头参数逐帧采样
            src_shape = _camera_shape(cam_transform)
            if src_shape:
                for attr in _CAMERA_ATTRS:
                    _safe_copy_camera_attr(src_shape, dup_shape, attr,
                                           set_key=True, frame=frame)
    finally:
        try:
            cmds.currentTime(old_time, edit=True)
        except Exception:
            pass
        if old_auto_key is not None:
            try:
                cmds.autoKeyframe(state=old_auto_key)
            except Exception:
                pass

    try:
        cmds.filterCurve(dup_transform)
    except Exception:
        pass


def _create_baked_duplicate_camera(cam_transform, temp_group, start, end, sample_by=1,
                                   detect_rig=True, world_root=True,
                                   parent_constraint_bake=True):
    """复制相机并烘焙（照搬 UEAnimCamExporter 的 _create_baked_duplicate_camera）"""
    cam_shape = _camera_shape(cam_transform)
    if not cam_shape:
        raise RuntimeError(u"没有在对象下找到 camera shape: {0}".format(cam_transform))

    clean_name = utils.get_short_name(cam_transform)
    dup_transform, dup_shape_default = cmds.camera(name="animExp_TMP_CAM")

    if world_root:
        # 相机默认不挂临时父组：FBX 里没有额外父级，避免 UE Sequencer 导入时父级偏移
        try:
            dup_transform = cmds.parent(dup_transform, world=True)[0]
        except Exception:
            pass
    else:
        dup_transform = cmds.parent(dup_transform, temp_group)[0]

    try:
        dup_transform = cmds.rename(dup_transform, clean_name)
    except Exception:
        pass
    dup_shape = _camera_shape(dup_transform) or dup_shape_default

    _copy_basic_transform_settings(cam_transform, dup_transform)
    _unlock_trs(dup_transform)

    # 先复制一次静态相机参数，再逐帧采样动画参数
    for attr in _CAMERA_ATTRS:
        _safe_copy_camera_attr(cam_shape, dup_shape, attr, set_key=False)

    try:
        if parent_constraint_bake:
            _bake_camera_by_parent_constraint(
                cam_transform, dup_transform, dup_shape, start, end,
                sample_by=sample_by, detect_rig=detect_rig)
        else:
            _sample_world_camera_to_duplicate(
                cam_transform, dup_transform, dup_shape, start, end,
                sample_by=sample_by, detect_rig=detect_rig, force_scene_eval=True)

        key_count = _key_count_on_nodes([dup_transform, dup_shape])
        _log(u"相机 Bake 后关键帧数量：{0}".format(key_count))
        if key_count <= 0:
            raise RuntimeError(
                u"相机 Bake 后没有任何关键帧。请确认选择的是实际 Camera transform，"
                u"而不是相机组、控制器或空组：{0}".format(cam_transform))
    except Exception:
        # 烘焙失败 / 被取消：临时相机挂在世界根，必须在这里删掉，
        # 否则会残留在用户场景里（参考工具的同样位置也有这个隐患）。
        try:
            if dup_transform and cmds.objExists(dup_transform):
                cmds.delete(dup_transform)
        except Exception:
            pass
        raise

    return dup_transform


def export_camera_item(item, export_dir, start, end):
    """导出单个相机动画（FBX），照搬 UEAnimCamExporter v4.1.10 的相机链路：

    新建干净相机（默认挂世界根）-> 复制旋转顺序 + 静态相机参数 ->
    约束 + 烘焙（含 shape 属性逐帧采样）-> 只选单 Camera 导出 -> 删除临时相机。
    FBX 里只有一个 Camera Transform + Camera Shape，不带控制器/约束/父级组。

    行为全部由 config.EXPORT_OPTIONS 控制（设置面板可改，默认值与参考工具一致）：
        camera_z_up / camera_world_root / camera_parent_bake / camera_detect_rig /
        camera_use_anim_range / camera_clamp_anim_range / camera_check_sensor /
        sample_by
    """
    name = utils.sanitize_filename(item.get("export_name", ""))
    cam = utils.resolve_unique(item.get("object", ""), u"相机")
    if cam is None:
        raise RuntimeError(u"相机不存在或命名不唯一，无法导出 '{0}'".format(name))
    if int(start) > int(end):
        start, end = end, start

    # 如果选中了 camera shape，转父变换
    if cmds.objectType(cam, isAType="camera"):
        parent = cmds.listRelatives(cam, parent=True, fullPath=True)
        if parent:
            cam = parent[0]
    if _camera_shape(cam) is None:
        raise RuntimeError(u"{0} 下没有 camera shape，无法导出".format(cam))

    sample_by = max(1, int(config.option("sample_by", 1)))
    detect_rig = bool(config.option("camera_detect_rig", True))
    world_root = bool(config.option("camera_world_root", True))
    parent_bake = bool(config.option("camera_parent_bake", True))

    # 1) 相机实际动画段（参考工具“只 Bake 相机实际动画段”+“限制在 Start/End 内”）
    bake_start, bake_end = int(start), int(end)
    if config.option("camera_use_anim_range", False):
        bake_start, bake_end = _detect_camera_animation_range(
            cam, start, end, include_drivers=detect_rig,
            clamp_to_ui=bool(config.option("camera_clamp_anim_range", True)))

    # 2) 导出前感光器/分辨率检查（UE 认 Filmback，不认 Render Settings 分辨率）
    if config.option("camera_check_sensor", True):
        if not _validate_camera_sensor_before_export([cam]):
            raise ExportCancelled(u"已按用户选择暂停：相机感光器/分辨率检查未通过")

    saved = cmds.ls(sl=True) or []
    temp_group = None
    dup_cam = None
    try:
        temp_group = _make_temp_group()
        dup_cam = _create_baked_duplicate_camera(
            cam, temp_group, bake_start, bake_end, sample_by=sample_by,
            detect_rig=detect_rig, world_root=world_root,
            parent_constraint_bake=parent_bake)

        dup_cam_shape = _camera_shape(dup_cam)
        # 只选中 Bake 后的单 Camera，不选任何控制器、父级组、约束或原相机 Rig
        if dup_cam_shape:
            cmds.select([dup_cam, dup_cam_shape], replace=True)
        else:
            cmds.select(dup_cam, replace=True)

        # 3) FBX 导出选项：相机轴向转换默认关闭（见 config.EXPORT_OPTIONS 注释）
        _set_fbx_options(bake_start, bake_end, sample_by=sample_by, cameras=True,
                         animation_only=False, export_skins=False,
                         export_shapes=False,
                         z_up=bool(config.option("camera_z_up", False)))
        # 文件名沿用 UI 的帧段（与参考工具一致），不因“只 Bake 实际动画段”而变
        file_name = _build_filename(name, config.NAMING_PRESETS["camera_suffix"],
                                    start, end)
        file_path = "{0}/{1}.fbx".format(export_dir, file_name)
        _progress_step(0.98, u"写入相机 FBX…")
        _export_fbx(file_path)
        _progress_step(1.0, u"相机 FBX 写入完成")
        return file_path
    finally:
        # world_root 模式下 dup_cam 不在临时组下，必须单独删除，避免残留在场景里
        if dup_cam and cmds.objExists(dup_cam):
            try:
                cmds.delete(dup_cam)
            except Exception:
                pass
        if temp_group and cmds.objExists(temp_group):
            try:
                cmds.delete(temp_group)
            except Exception:
                pass
        utils.restore_selection(saved)
