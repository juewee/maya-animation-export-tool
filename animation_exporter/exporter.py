# -*- coding: utf-8 -*-
"""
动画资产一键导出工具 - 导出执行层

FBX 骨骼动画 / ABC 几何体缓存（含导出前清理）/ 相机动画 三条导出链路。
每个函数只接收具体参数，不依赖 UI 与全局状态，可被 UI、core 或批处理脚本复用。
"""
import os
import shutil
import tempfile
import maya.cmds as cmds

from . import config
from . import utils


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
    """按参考命令做导出前清理：
    展开多边形组选择 -> polyCleanupArgList -> 删除历史 -> 冻结变换
    """
    if not objects:
        return
    saved = cmds.ls(sl=True) or []
    try:
        cmds.select(_abc_cleanup_targets(objects), replace=True)
        utils.mel_eval(config.ABC_CLEANUP_MEL)   # expandPolyGroupSelection; polyCleanupArgList 4 {...};
        cmds.delete(constructionHistory=True)    # 清理历史
        cmds.select(objects, replace=True)
        cmds.makeIdentity(apply=True, translate=True, rotate=True, scale=True)  # 冻结变换
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
        raise RuntimeError(u"AbcExport 失败: {0}".format(exc))
    if not os.path.exists(file_path):
        raise RuntimeError(u"ABC 文件未生成: {0}".format(file_path))
    return file_path


# ---------------------------------------------------------------------------
# FBX 通用导出
# ---------------------------------------------------------------------------
def _set_fbx_options(start, end, cameras=False, animation_only=True,
                     export_skins=False, export_shapes=False, z_up=True):
    """设置 FBX 导出选项（参考 UEAnimCamExporter 的 FBX 配置）。

    animation_only=True  适合骨骼动画：只导出动画数据；
    相机导出传 False：相机 shape（感光元件/焦距等静态数据）也需要写进文件，
    否则导回时没有相机。
    """
    utils.mel_eval("FBXResetExport;")
    utils.mel_eval('FBXExportFileVersion -v "FBX202000";')

    # 动画烘焙设置
    utils.mel_eval("FBXExportBakeComplexAnimation -v true;")
    utils.mel_eval("FBXExportBakeComplexStart -v {0};".format(int(start)))
    utils.mel_eval("FBXExportBakeComplexEnd -v {0};".format(int(end)))
    utils.mel_eval("FBXExportBakeComplexStep -v 1;")
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


def _force_full_trs_keys(nodes, start, end, sample_by=1):
    """保证每根 Joint 在每个采样帧都有完整 T/R/S 关键帧。

    防止 FBX/UE 把某些静态子骨骼曲线认为可省略而丢弃。
    """
    frames = _sample_frames(start, end, sample_by)
    old_time = cmds.currentTime(q=True)
    try:
        for frame in frames:
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
    _force_full_trs_keys(dup_joints, start, end, sample_by=sample_by)
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

    saved = cmds.ls(sl=True) or []
    temp_group = None
    try:
        # 1) 创建临时组，复制+烘焙骨骼
        temp_group = _make_temp_group()
        dup_root, dup_joints = _create_baked_duplicate_skeleton(
            root, temp_group, start, end)

        # 2) 检查烘焙结果
        key_count = _key_count_on_nodes(dup_joints)
        if key_count <= 0:
            raise RuntimeError(
                u"骨骼烘焙后没有任何动画关键帧，请确认选择的是实际变形/有动画驱动的"
                u"骨骼 Root，而不是 FitSkeleton/参考骨架/空 Root: {0}".format(root))

        # 3) 选中烘焙后的副本骨骼层级（不含 mesh/控制器/约束）
        cmds.select(dup_root, hierarchy=True, replace=True)

        # 4) 设置 FBX 选项并导出
        _set_fbx_options(start, end, cameras=False, animation_only=False,
                         export_skins=False, export_shapes=False, z_up=True)
        file_name = "{0}_Anim_{1}-{2}".format(name, int(start), int(end))
        file_path = "{0}/{1}.fbx".format(export_dir, file_name)
        _export_fbx(file_path)
        return file_path
    finally:
        if temp_group and cmds.objExists(temp_group):
            try:
                cmds.delete(temp_group)
            except Exception:
                pass
        utils.restore_selection(saved)


# ---------------------------------------------------------------------------
# 相机动画导出（照搬 UEAnimCamExporter v4.1.10 的相机烘焙+导出流程）
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


def _camera_shape(transform):
    """取变换下的 camera shape 节点"""
    shapes = cmds.listRelatives(transform, shapes=True, fullPath=True, type="camera") or []
    return shapes[0] if shapes else None


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
                                      start, end, sample_by=1):
    """相机约束烘焙（照搬 UEAnimCamExporter 的 parentConstraint + bakeResults 方案）：

    复制单 Camera 到世界根节点，用 parentConstraint/scaleConstraint 跟随源相机，
    再 bakeResults(shape=True, minimizeRotation=True) 烘焙，并逐帧拷贝 Camera Shape
    属性和补齐 TRS 关键帧。
    """
    start = int(start)
    end = int(end)
    sample_by = max(1, int(sample_by))

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
            cmds.warning(u"Camera parentConstraint 创建失败: {0}".format(exc))
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
        for frame in _sample_frames(start, end, sample_by):
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


def export_camera_item(item, export_dir, start, end):
    """导出单个相机动画（FBX），照搬 UEAnimCamExporter v4.1.10 的相机链路：

    新建干净相机(挂世界根) -> 复制旋转顺序+静态相机参数 -> 约束+烘焙(含 shape
    属性逐帧采样) -> 只选单 Camera 导出 -> 删除临时相机。
    FBX 里只有一个 Camera Transform + Camera Shape，不带任何控制器/约束/父级组。
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
    src_shape = _camera_shape(cam)
    if src_shape is None:
        raise RuntimeError(u"{0} 下没有 camera shape，无法导出".format(cam))

    saved = cmds.ls(sl=True) or []
    new_cam = None
    try:
        # 1) 新建干净相机并直接挂到世界根（FBX 里没有额外父级，避免 UE 导入时父级偏移）
        clean_name = utils.sanitize_filename(utils.get_short_name(cam))
        dup_transform, dup_shape = cmds.camera(name="animExp_TMP_CAM")
        try:
            dup_transform = cmds.parent(dup_transform, world=True)[0]
        except Exception:
            pass
        # 重命名为源相机名 + _exp
        i = 1
        new_name = clean_name + "_exp"
        while cmds.objExists(new_name):
            new_name = "{0}_exp{1}".format(clean_name, i)
            i += 1
        try:
            dup_transform = cmds.rename(dup_transform, new_name)
        except Exception:
            pass
        dup_shape = _camera_shape(dup_transform) or dup_shape
        new_cam = dup_transform

        _unlock_trs(dup_transform)

        # 2) 复制旋转顺序 + 静态相机参数（焦距/光圈/裁剪面等）
        try:
            cmds.setAttr("{0}.rotateOrder".format(dup_transform),
                         cmds.getAttr("{0}.rotateOrder".format(cam)))
        except Exception:
            pass
        for attr in _CAMERA_ATTRS:
            _safe_copy_camera_attr(src_shape, dup_shape, attr, set_key=False)

        # 3) 约束 + 烘焙（含逐帧 shape 属性采样 + TRS 补帧）
        _bake_camera_by_parent_constraint(cam, dup_transform, dup_shape, start, end)

        # 4) 检查烘焙结果
        try:
            key_count = sum(
                len(cmds.keyframe("{0}.{1}".format(dup_transform, attr),
                                  q=True, time=(start, end)) or [])
                for attr in _TRS_ATTRS
            )
        except Exception:
            key_count = 0
        if key_count <= 0:
            raise RuntimeError(
                u"相机烘焙后没有任何关键帧，请确认选择的是 Camera Transform "
                u"而非空组/控制器: {0}".format(cam))

        # 5) 只选中烘焙后的单 Camera（transform + shape），不选任何控制器/约束/原相机
        if dup_shape:
            cmds.select([dup_transform, dup_shape], replace=True)
        else:
            cmds.select(dup_transform, replace=True)

        # 6) 设置 FBX 导出选项并导出
        _set_fbx_options(start, end, cameras=True, animation_only=False, z_up=True)
        file_name = "{0}_{1}-{2}".format(name, int(start), int(end))
        file_path = "{0}/{1}.fbx".format(export_dir, file_name)
        _export_fbx(file_path)
        return file_path
    finally:
        if new_cam and cmds.objExists(new_cam):
            try:
                cmds.delete(new_cam)
            except Exception:
                pass
        utils.restore_selection(saved)
