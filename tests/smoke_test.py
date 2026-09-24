# -*- coding: utf-8 -*-
"""无 Maya 环境的冒烟测试：用假的 maya.cmds 跑通相机导出 / 进度条 / 启动自检 / 设置面板。

在项目根目录运行（不需要装 Maya）：
    python tests/smoke_test.py

覆盖：
  1-3  相机导出（默认不 Z-Up / 打开 Z-Up / 世界矩阵采样 + 采样步长）
  4-5  批量导出进度条 + 条目边界取消
  6-7  启动自检 + 打开窗口（状态行 / 场景节点写入）
  8-9  设置面板应用与恢复默认（含持久化往返）
  10   关闭进度条开关
  11   相机烘焙中途取消（临时节点必须被清理、不产出半成品）
  12   动画范围刷新按钮（改时间轴后同步显示）
  13   相机感光器检查里点“打开设置”：中止导出并保持选中相机本体
  14   ABC 去除命名空间选项（-stripNamespaces 开关）
  15   “更新”按钮：换物体但保留备注
  16   命名空间容错解析（短名在命名空间里的兜底查找）
"""
import fnmatch
import os
import shutil
import sys
import types

PROJECT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, PROJECT)

maya = types.ModuleType("maya")
cmds_mod = types.ModuleType("maya.cmds")
mel_mod = types.ModuleType("maya.mel")
maya.cmds = cmds_mod
maya.mel = mel_mod
sys.modules["maya"] = maya
sys.modules["maya.cmds"] = cmds_mod
sys.modules["maya.mel"] = mel_mod

UI_COMMANDS = (
    "window", "columnLayout", "scrollLayout", "frameLayout", "rowLayout", "text",
    "textField", "checkBox", "intField", "floatField", "button", "separator",
    "showWindow", "deleteUI", "control", "symbolButton", "textFieldButtonGrp",
    "radioCollection", "radioButton", "setParent", "layout",
)


class FakeMaya(object):
    def __init__(self):
        self.nodes = {}
        self.keys = {}
        self.widgets = {}
        self.selection = []
        self.warnings = []
        self.mel_calls = []
        self.progress_calls = []
        self.abc_jobs = []
        self.progress_open = False
        self.progress_percent = 0
        self.progress_status = ""
        self.cancel_after = None
        self.dialog_choice = u"否：继续导出"
        self.counter = 0
        self.current = 1
        self.playback_min = 101
        self.playback_max = 138

    # ================= DAG 场景 =================
    def add_transform(self, name, parent=None, attrs=None):
        path = (parent + "|" + name) if parent else "|" + name
        self.nodes[path] = {"type": "transform", "parent": parent,
                            "attrs": dict(attrs or {}), "shapes": []}
        return path

    def add_camera(self, name, parent=None, attrs=None):
        path = self.add_transform(name, parent, attrs)
        shape = name + "Shape"
        self.nodes[path + "|" + shape] = {"type": "camera", "parent": path,
                                          "attrs": dict(attrs or {}), "shapes": []}
        self.nodes[path]["shapes"] = [path + "|" + shape]
        return path

    def add_joint(self, name, parent=None):
        path = self.add_transform(name, parent, {"rotateOrder": "xyz"})
        self.nodes[path]["type"] = "joint"
        return path

    def _full(self, node):
        if node in self.nodes:
            return node
        matches = [p for p in self.nodes if p.split("|")[-1] == node]
        return matches[0] if len(matches) == 1 else None

    def objExists(self, item):
        if item is None:
            return False
        if "." in item:
            node, attr = item.split(".", 1)
            full = self._full(node)
            return bool(full and attr in self.nodes[full]["attrs"])
        return self._full(item) is not None

    def nodeType(self, node):
        full = self._full(node)
        return self.nodes[full]["type"] if full else "transform"

    def objectType(self, node, isAType=None):
        ntype = self.nodeType(node)
        return (ntype == isAType) if isAType else ntype

    def listRelatives(self, node, shapes=False, type=None, parent=False,
                      fullPath=False, children=False, allDescendents=False):
        full = self._full(node)
        if full is None:
            return None
        out = []
        if parent:
            p = self.nodes[full]["parent"]
            if p:
                out.append(p)
        if shapes:
            out.extend(self.nodes[full]["shapes"])
        if children:
            out.extend([p for p, d in self.nodes.items() if d["parent"] == full])
        if allDescendents:
            stack = [full]
            while stack:
                cur = stack.pop()
                for p, d in self.nodes.items():
                    if d["parent"] == cur:
                        out.append(p)
                        stack.append(p)
        if type:
            out = [n for n in out if self.nodes[n]["type"] == type]
        if not out:
            return None
        return out if fullPath else [n.split("|")[-1] for n in out]

    def ls(self, *args, **kwargs):
        if kwargs.get("sl"):
            return list(self.selection)
        names = []
        for a in args:
            names.extend(a if isinstance(a, (list, tuple)) else [a])
        out = []
        for name in names:
            if "|" in name:
                if name in self.nodes:
                    out.append(name)
            elif "*" in name:
                # 支持 "*:Mesh" 这类命名空间通配（供 _ls_by_name 用例）
                out.extend([p for p in self.nodes
                            if fnmatch.fnmatch(p.split("|")[-1], name)])
            else:
                out.extend([p for p in self.nodes if p.split("|")[-1] == name])
        return out

    def getAttr(self, item):
        node, attr = item.split(".", 1)
        full = self._full(node)
        if full is None or attr not in self.nodes[full]["attrs"]:
            raise RuntimeError("no attr " + item)
        return self.nodes[full]["attrs"][attr]

    def setAttr(self, item, *args, **kwargs):
        node, attr = item.split(".", 1)
        full = self._full(node)
        if full is None:
            raise RuntimeError("no node " + node)
        if args:
            self.nodes[full]["attrs"][attr] = args[0]
        return True

    def setKeyframe(self, node, attribute=None, time=None, **kwargs):
        self.keys.setdefault((self._full(node), attribute), {})[time] = 1
        return 1

    def keyframe(self, node, query=False, q=None, keyframeCount=False,
                 timeChange=False, time=None, **kwargs):
        query = query or q
        if "." in node:
            full, attr = node.split(".", 1)
            data = self.keys.get((self._full(full), attr), {})
        else:
            full = self._full(node)
            data = {}
            for (n, _a), times in self.keys.items():
                if n == full:
                    data.update(times)
        return len(data) if keyframeCount else sorted(data.keys())

    def AbcExport(self, jobArg=None, **kwargs):
        # 记下 job 字符串并按 -file 生成占位文件，供 ABC 用例断言
        self.abc_jobs.append(jobArg)
        import re as _re
        match = _re.search(r'-file "([^"]+)"', jobArg or "")
        if match:
            with open(match.group(1), "w") as fh:
                fh.write("fake abc")
        return "job"

    def cutKey(self, node, clear=False, **kwargs):
        full = self._full(node)
        for key in list(self.keys):
            if key[0] == full:
                self.keys.pop(key, None)
        return 1

    def camera(self, name="camera"):
        self.counter += 1
        path = self.add_camera(name, None, {"rotateOrder": "xyz", "focalLength": 35.0})
        return [path, self.nodes[path]["shapes"][0]]

    def group(self, empty=True, name=None, **kwargs):
        return self.add_transform(name or "group%d" % self.counter)

    def duplicate(self, node, renameChildren=False, inputConnections=False, **kwargs):
        full = self._full(node)
        new_root = self.add_transform(full.split("|")[-1] + "1", None,
                                      dict(self.nodes[full]["attrs"]))
        self.nodes[new_root]["type"] = self.nodes[full]["type"]

        def copy_children(src, dst):
            for p, d in list(self.nodes.items()):
                if d["parent"] == src:
                    child = self.add_transform(p.split("|")[-1] + "1", dst, dict(d["attrs"]))
                    self.nodes[child]["type"] = d["type"]
                    copy_children(p, child)
        copy_children(full, new_root)
        return [new_root]

    def parent(self, node, *args, **kwargs):
        # 兼容 cmds.parent(child, parent) 与 cmds.parent(child, world=True)
        world = bool(kwargs.get("world", False))
        target = kwargs.get("target")
        for a in args:
            if isinstance(a, bool):
                world = a
            else:
                target = a
        full = self._full(node)
        info = self.nodes.pop(full)
        short = full.split("|")[-1]
        if world:
            newpath, info["parent"] = "|" + short, None
        else:
            tfull = self._full(target)
            newpath, info["parent"] = tfull + "|" + short, tfull
        self.nodes[newpath] = info
        for p in list(self.nodes):
            if self.nodes[p]["parent"] == full:
                self.nodes[p]["parent"] = newpath
        self.nodes[newpath]["shapes"] = [p for p, d in self.nodes.items()
                                         if d["parent"] == newpath and d["type"] == "camera"]
        return [newpath]

    def rename(self, node, new_name):
        # 真实 Maya 重命名会连子节点（含 shape）的 DAG 路径一起改
        full = self._full(node)
        parent_path = self.nodes[full]["parent"]
        candidate = (parent_path + "|" + new_name) if parent_path else "|" + new_name
        i = 1
        while candidate in self.nodes:
            candidate = ((parent_path + "|" + new_name + str(i)) if parent_path
                         else "|" + new_name + str(i))
            i += 1
        subtree, stack = [], [full]
        while stack:
            cur = stack.pop()
            subtree.append(cur)
            for p, d in list(self.nodes.items()):
                if d["parent"] == cur:
                    stack.append(p)
        mapping = {old: candidate + old[len(full):] for old in subtree}
        for old in subtree:
            info = self.nodes.pop(old)
            if info["parent"] in mapping:
                info["parent"] = mapping[info["parent"]]
            self.nodes[mapping[old]] = info
        for p, d in self.nodes.items():
            if d["type"] == "transform":
                d["shapes"] = [q for q, qd in self.nodes.items()
                               if qd["parent"] == p and qd["type"] == "camera"]
        return candidate

    def delete(self, *args):
        targets = []
        for a in args:
            targets.extend(a if isinstance(a, (list, tuple)) else [a])
        for t in targets:
            full = self._full(t)
            if full is None:
                continue
            for p in list(self.nodes):
                if p == full or p.startswith(full + "|"):
                    self.nodes.pop(p, None)
            for key in list(self.keys):
                if key[0] and (key[0] == full or key[0].startswith(full + "|")):
                    self.keys.pop(key, None)
        return 1

    def select(self, *args, **kwargs):
        items = []
        for a in args:
            items.extend(a if isinstance(a, (list, tuple)) else [a])
        if kwargs.get("replace", True):
            self.selection = items
        else:
            self.selection.extend(items)
        return items

    def parentConstraint(self, src, dst, **kwargs):
        return ["parentConstraint%d" % self.counter]

    def scaleConstraint(self, src, dst, **kwargs):
        return ["scaleConstraint%d" % self.counter]

    def bakeResults(self, nodes, time=None, **kwargs):
        for node in nodes:
            for attr in ("translateX", "translateY", "translateZ", "rotateX",
                         "rotateY", "rotateZ", "scaleX", "scaleY", "scaleZ"):
                for frame in range(int(time[0]), int(time[1]) + 1):
                    self.setKeyframe(node, attribute=attr, time=frame)
        return 1

    def currentTime(self, value=None, query=False, q=None, edit=False, e=None, **kwargs):
        query = query or q
        edit = edit or e
        if query:
            return self.current
        if edit:
            self.current = value
        return value

    def autoKeyframe(self, query=False, q=None, state=None, **kwargs):
        return False if (query or q) else state

    def filterCurve(self, *a, **k):
        return 1

    def dgdirty(self, *a, **k):
        return 1

    def refresh(self, *a, **k):
        return 1

    def xform(self, node, query=False, q=None, ws=False, matrix=None, **kwargs):
        query = query or q
        if query:
            return [1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0, 0, 0, 0, 0, 1.0]
        return 1

    def listConnections(self, node, **kwargs):
        return []

    def warning(self, msg):
        self.warnings.append(msg)
        return msg

    def FBXExport(self, f=None, s=None, **kwargs):
        with open(f, "w") as fh:
            fh.write("fake fbx")
        return f

    def mel_eval(self, expr):
        self.mel_calls.append(expr)
        return None

    # ================= 进度条 =================
    def progressWindow(self, *args, **kwargs):
        if kwargs.get("query"):
            if kwargs.get("exists"):
                return self.progress_open
            if kwargs.get("isCancelled"):
                return bool(self.cancel_after is not None
                            and self.progress_percent >= self.cancel_after)
            return None
        if kwargs.get("endProgress"):
            self.progress_open = False
            self.progress_calls.append(("end",))
            return None
        if kwargs.get("edit"):
            self.progress_percent = kwargs.get("progress", self.progress_percent)
            if "status" in kwargs:
                self.progress_status = kwargs["status"]
            self.progress_calls.append(("edit", self.progress_percent, self.progress_status))
            return None
        self.progress_open = True
        self.progress_percent = kwargs.get("progress", 0)
        self.progress_status = kwargs.get("status", "")
        self.progress_calls.append(("begin", self.progress_status))
        return None

    # ================= 环境 / 场景节点 =================
    def about(self, **kwargs):
        if kwargs.get("version"):
            return "2022"
        if kwargs.get("batch"):
            return False
        return ""

    def file(self, **kwargs):
        if (kwargs.get("query") or kwargs.get("q")) and kwargs.get("sceneName"):
            return "D:/scenes/S02_shot.ma"
        return ""

    def pluginInfo(self, plugin, **kwargs):
        if kwargs.get("query"):
            if kwargs.get("loaded"):
                return True
            if kwargs.get("path"):
                return "C:/maya/plug-ins/%s.mll" % plugin
        return True

    def playbackOptions(self, query=False, q=None, minTime=False, maxTime=False, **kwargs):
        if query or q:
            if minTime:
                return self.playback_min
            if maxTime:
                return self.playback_max
        return None

    def createNode(self, ntype, name=None, **kwargs):
        self.counter += 1
        nm = name or "%s%d" % (ntype, self.counter)
        self.nodes[nm] = {"type": ntype, "parent": None, "attrs": {}, "shapes": []}
        return nm

    def addAttr(self, node, longName=None, **kwargs):
        full = self._full(node)
        if full:
            self.nodes[full]["attrs"][longName] = ""
        return longName

    def deleteAttr(self, node, attribute=None, **kwargs):
        full = self._full(node)
        if full:
            self.nodes[full]["attrs"].pop(attribute, None)
        return 1

    def attributeQuery(self, attr, node=None, exists=False, **kwargs):
        full = self._full(node)
        return bool(full and attr in self.nodes[full]["attrs"])

    def listAttr(self, node, userDefined=False, **kwargs):
        full = self._full(node)
        return list(self.nodes[full]["attrs"].keys()) if full else []

    def confirmDialog(self, **kwargs):
        return self.dialog_choice

    # ================= 通用 UI 控件 =================
    def _generic_ui(self, cmd, *args, **kwargs):
        if cmd == "deleteUI":
            for a in args:
                self.widgets.pop(a, None)
            return None
        if cmd == "showWindow":
            return args[0] if args else None
        if cmd == "control":
            name = args[0] if args else kwargs.get("name")
            return name in self.widgets
        if cmd == "setParent":
            return None
        if kwargs.get("query") or kwargs.get("q") or kwargs.get("exists"):
            name = args[0] if args else None
            if kwargs.get("exists"):
                return name in self.widgets
            if kwargs.get("childArray"):
                return []
            widget = self.widgets.get(name, {})
            for key in ("text", "value", "value1", "label", "select"):
                if kwargs.get(key):
                    return widget.get(key)
            return widget
        if kwargs.get("edit") or kwargs.get("e"):
            name = args[0] if args else None
            widget = self.widgets.setdefault(name, {"cmd": cmd})
            for key, value in kwargs.items():
                if key in ("edit", "e"):
                    continue
                widget[key] = value
            return name
        # 真实 Maya 里 cmds.window(name) / cmds.textField(name) 会以该名字创建控件
        name = None
        if args and isinstance(args[0], str) and not args[0].startswith("|"):
            name = args[0]
        if not name:
            name = kwargs.get("name") or "%s#%d" % (cmd, len(self.widgets))
        widget = dict(kwargs)
        widget["cmd"] = cmd
        self.widgets[name] = widget
        return name

    def __getattr__(self, name):
        if name in UI_COMMANDS:
            return lambda *a, **kw: self._generic_ui(name, *a, **kw)
        raise AttributeError(name)

    def widget_by_label(self, label):
        for name, widget in self.widgets.items():
            if widget.get("label") == label:
                return name, widget
        return None, None

    def invoke(self, label):
        name, widget = self.widget_by_label(label)
        if widget and callable(widget.get("command")):
            return widget["command"]()
        raise AssertionError("找不到按钮: %s" % label)


fake = FakeMaya()

from animation_exporter import checks, config, core, exporter, persistence, ui, utils  # noqa

exporter.cmds = fake
utils.cmds = fake
ui.cmds = fake
checks.cmds = fake
core.cmds = fake
persistence.cmds = fake
utils._mel_module = types.SimpleNamespace(eval=fake.mel_eval)

OUT = os.path.join(PROJECT, "_smoke_out")


def reset_scene():
    fake.nodes.clear()
    fake.keys.clear()
    fake.widgets.clear()
    fake.warnings = []
    fake.mel_calls = []
    fake.progress_calls = []
    fake.progress_open = False
    fake.abc_jobs = []
    fake.progress_percent = 0
    fake.progress_status = ""
    fake.cancel_after = None
    fake.selection = []
    fake.counter = 0
    fake.playback_min = 101
    fake.playback_max = 138
    config.reset_options()


def build_scene():
    grp = fake.add_transform("Camera_Grp", None, {"rotateOrder": "xyz"})
    cam = fake.add_camera("Camera", grp, {
        "rotateOrder": "xyz", "focalLength": 35.0, "cameraScale": 1.0,
        "horizontalFilmAperture": 1.41732, "verticalFilmAperture": 0.94488,
        "filmFit": 1, "nearClipPlane": 0.1, "farClipPlane": 10000.0,
        "lensSqueezeRatio": 1.0, "horizontalFilmOffset": 0.0,
        "verticalFilmOffset": 0.0, "filmTranslateH": 0.0,
        "filmTranslateV": 0.0, "overscan": 1.0,
        "translateX": 0.0, "translateY": 0.0, "translateZ": 0.0,
        "rotateX": 0.0, "rotateY": 0.0, "rotateZ": 0.0,
        "scaleX": 1.0, "scaleY": 1.0, "scaleZ": 1.0})
    fake.add_transform("defaultResolution", None,
                       {"width": 1920.0, "height": 1080.0, "pixelAspect": 1.0})
    root = fake.add_joint("Root_M", None)
    fake.add_joint("Jnt1", root)
    return cam, grp, root


def show_progress_tail():
    edits = [c for c in fake.progress_calls if c[0] == "edit"]
    return edits[:2], edits[-1] if edits else None


print("### 1. 相机导出（默认：不做 Z-Up / ParentConstraint Bake）")
reset_scene()
build_scene()
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
config.EXPORT_OPTIONS["camera_check_sensor"] = False
path = exporter.export_camera_item({"object": "Camera", "export_name": "S02_Camera"},
                                   OUT, 101, 138)
print("   输出文件存在:", os.path.exists(path), "|", os.path.basename(path))
print("   轴向调用   :", [c for c in fake.mel_calls if "UpAxis" in c or "AxisConversion" in c])
print("   残留临时节点:", [n for n in fake.nodes if "animExp" in n])
print("   进度调用   :", len(fake.progress_calls), "条（直接调用 exporter 时无进度条，符合预期）")

print("### 2. 相机导出（camera_z_up=True 对比）")
reset_scene()
build_scene()
config.EXPORT_OPTIONS["camera_check_sensor"] = False
config.EXPORT_OPTIONS["camera_z_up"] = True
exporter.export_camera_item({"object": "Camera", "export_name": "S02_Camera"}, OUT, 101, 138)
print("   轴向调用   :", [c for c in fake.mel_calls if "UpAxis" in c or "AxisConversion" in c])

print("### 3. 世界矩阵采样 + 采样步长 2")
reset_scene()
build_scene()
config.EXPORT_OPTIONS["camera_check_sensor"] = False
config.EXPORT_OPTIONS["camera_parent_bake"] = False
config.EXPORT_OPTIONS["sample_by"] = 2
p = exporter.export_camera_item({"object": "Camera", "export_name": "S02_Camera"}, OUT, 101, 138)
print("   输出文件存在:", os.path.exists(p))
print("   step 调用   :", [c for c in fake.mel_calls if "BakeComplexStep" in c])

print("### 4. 批量导出 + 进度条")
reset_scene()
build_scene()
config.EXPORT_OPTIONS["camera_check_sensor"] = False
store = {k: [] for k in config.TYPE_ORDER}
store[config.TYPE_CAMERA] = [{"object": "Camera", "export_name": "S02_Camera", "enabled": True}]
store[config.TYPE_FBX] = [{"object": "Root_M", "export_name": "S02_Root", "enabled": True}]
logs = []
succ, fail = core.run_export_batch(store, {"export_dir": OUT, "start": 101, "end": 110,
                                           "show_progress": True}, log=logs.append)
head, tail = show_progress_tail()
print("   成功:", len(succ), "失败:", len(fail))
print("   失败详情   :", [(f[0], f[1], f[2]) for f in fail])
print("   进度首尾   :", head, "->", tail)
print("   进度条已关闭:", not fake.progress_open, "| 最终百分比:", fake.progress_percent)
print("   输出文件   :", [os.path.basename(s[2]) for s in succ])

print("### 5. 进度条取消（50% 后点取消）")
reset_scene()
build_scene()
config.EXPORT_OPTIONS["camera_check_sensor"] = False
fake.cancel_after = 50
logs = []
succ, fail = core.run_export_batch(store, {"export_dir": OUT, "start": 101, "end": 138,
                                           "show_progress": True}, log=logs.append)
print("   成功:", len(succ), "失败:", len(fail))
print("   中止日志   :", [l for l in logs if u"中止" in l or u"取消" in l][:2])
print("   已中止标志 :", core.last_run_cancelled)
print("   残留临时节点:", [n for n in fake.nodes if "animExp" in n])
print("   进度条已关闭:", not fake.progress_open)

print("### 6. 启动自检")
reset_scene()
build_scene()
store2 = {k: [] for k in config.TYPE_ORDER}
store2[config.TYPE_CAMERA] = [{"object": "Camera", "export_name": "C1", "enabled": True},
                              {"object": "Gone_Camera", "export_name": "C2", "enabled": True}]
store2[config.TYPE_FBX] = [{"object": "Root_M", "export_name": "R1", "enabled": True},
                           {"object": "Camera_Grp", "export_name": "R2", "enabled": True}]
issues = checks.light_check(store2, OUT, (101, 138))
print(checks.format_report(issues, 0.01))
print("   摘要:", checks.summary_line(issues), "| has_errors:", checks.has_errors(issues))

print("### 7. 打开窗口（build_ui + launch + 自检状态行）")
reset_scene()
build_scene()
core.replace_store(store2)
ui.launch()
label = fake.widgets.get(ui.ui_controls.get("check_label"), {})
print("   状态行文本:", label.get("label"))
print("   窗口已创建:", ui.WINDOW_NAME in fake.widgets)
print("   配置节点已写:", any("exportConfigData" in n for n in fake.nodes))
print("   前缀:", repr(ui.prefix_text))
print("   场景节点:", [n for n in fake.nodes if not n.startswith("|")])

print("### 8. 设置面板：改后缀 + 开相机 Z-Up + 应用")
reset_scene()
build_scene()
core.replace_store(store2)
ui.build_ui()
ui._open_settings()
print("   面板控件数:", len(ui._settings_controls))
cam_suffix_ctrl, _ = ui._settings_controls["camera_suffix"]
fake.widgets[cam_suffix_ctrl]["text"] = "_MyCam_{start}-{end}"
zup_ctrl, _ = ui._settings_controls["camera_z_up"]
fake.widgets[zup_ctrl]["value"] = True
fake.invoke(u"应用")
print("   应用后 camera_suffix:", config.NAMING_PRESETS["camera_suffix"])
print("   应用后 camera_z_up  :", config.EXPORT_OPTIONS["camera_z_up"])
cfg_dict = ui.build_config_dict()
print("   配置字典含 naming/options:", "naming" in cfg_dict, "options" in cfg_dict)
print("   持久化往返 camera_z_up:", persistence.normalize_config(cfg_dict)["options"]["camera_z_up"])
print("   场景节点配置块:", [a for a in fake.nodes.get("exportConfigData", {}).get("attrs", {})
                            if a.startswith("configChunk")][:2], "...")

print("### 9. 设置面板：恢复默认")
ui._open_settings()
fake.invoke(u"恢复默认")
print("   恢复后 camera_z_up:", config.EXPORT_OPTIONS["camera_z_up"],
      "| camera_suffix:", config.NAMING_PRESETS["camera_suffix"])

print("### 10. 进度条开关关闭时不创建进度条")
reset_scene()
build_scene()
config.EXPORT_OPTIONS["camera_check_sensor"] = False
config.EXPORT_OPTIONS["show_progress"] = False
logs = []
core.run_export_batch(store, {"export_dir": OUT, "start": 101, "end": 110,
                              "show_progress": False}, log=logs.append)
print("   进度条调用数:", len(fake.progress_calls), "| 成功:", len(logs))

print("### 11. 相机烘焙中途取消（验证临时节点被清理、不产出半成品文件）")
reset_scene()
build_scene()
config.EXPORT_OPTIONS["camera_check_sensor"] = False
fake.cancel_after = 15
cam_store = {k: [] for k in config.TYPE_ORDER}
cam_store[config.TYPE_CAMERA] = [{"object": "Camera", "export_name": "S02_Camera",
                                  "enabled": True}]
before = set(os.listdir(OUT))
logs = []
succ, fail = core.run_export_batch(cam_store, {"export_dir": OUT, "start": 101, "end": 138,
                                               "show_progress": True}, log=logs.append)
after = set(os.listdir(OUT))
print("   成功:", len(succ), "失败:", len(fail), "（用户取消不计入失败）")
print("   中止日志:", [l for l in logs if u"中止" in l][:1])
print("   已中止标志:", core.last_run_cancelled)
print("   新增文件:", sorted(after - before))
print("   残留临时节点:", [n for n in fake.nodes if "animExp" in n])
print("   进度条已关闭:", not fake.progress_open)

print("### 12. 动画范围：刷新按钮（在 Maya 里改时间轴后同步显示）")
reset_scene()
build_scene()
core.replace_store(store2)
ui.build_ui()
start_ctrl = ui.ui_controls["start_field"]
end_ctrl = ui.ui_controls["end_field"]
print("   初始显示:", fake.widgets[start_ctrl].get("value"), "-",
      fake.widgets[end_ctrl].get("value"))
fake.playback_min, fake.playback_max = 120, 200
fake.invoke(u"刷新")
print("   时间轴改为 120-200 后点刷新:", fake.widgets[start_ctrl].get("value"), "-",
      fake.widgets[end_ctrl].get("value"))
print("   导出读取的范围:", ui.get_animation_range())
fake.playback_min, fake.playback_max = 55, 88
ui.on_range_radio_current()   # 切回“当前时间滑块”
print("   时间轴改为 55-88 后切回时间滑块:", fake.widgets[start_ctrl].get("value"), "-",
      fake.widgets[end_ctrl].get("value"))
print("   刷新按钮存在:", fake.widget_by_label(u"刷新")[0] is not None)

print("### 14. ABC 去除命名空间选项（-stripNamespaces）")
reset_scene()
build_scene()
config.reset_options()
geo = fake.add_transform("ns1:Mesh", None, {})
fake.add_transform("ns2:Mesh", None, {})
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
abc_item = {"object": ["ns1:Mesh", "ns2:Mesh"], "export_name": "DupTest"}
p1 = exporter.export_abc_item(abc_item, OUT, 101, 110)
strip_on = fake.abc_jobs[-1]
print("   默认 job 含 -stripNamespaces:", "-stripNamespaces" in strip_on)
config.EXPORT_OPTIONS["abc_strip_namespaces"] = False
p2 = exporter.export_abc_item(abc_item, OUT, 101, 110)
strip_off = fake.abc_jobs[-1]
print("   关闭后 job 不含该参数:", "-stripNamespaces" not in strip_off)
print("   两个文件都生成:", os.path.exists(p1), os.path.exists(p2))
ui.build_ui()
ui._open_settings()
print("   设置面板含该选项:", "abc_strip_namespaces" in ui._settings_controls)
print("   面板控件数:", len(ui._settings_controls))

print("### 13. 相机感光器检查里点“打开设置”（应中止导出 + 保持选中相机本体）")
reset_scene()
build_scene()
core.replace_store(store2)
config.EXPORT_OPTIONS["camera_check_sensor"] = True
ui.build_ui()
fake.widgets[ui.ui_controls["dir_field"]]["text"] = OUT
fake.select(["|Root_M"], replace=True)      # 导出前用户选中的是骨骼
fake.dialog_choice = u"是：打开设置"
ui.on_export()
print("   最终选择:", fake.selection)
print("   选中相机 transform:", "|Camera_Grp|Camera" in fake.selection)
print("   选中相机 shape    :", "|Camera_Grp|Camera|CameraShape" in fake.selection)
print("   已中止标志        :", core.last_run_cancelled)
print("   打开属性编辑器    :", "AttributeEditor;" in fake.mel_calls)
print("   待聚焦请求已消费  :", utils.consume_focus_node() is None)
print("   摘要消息          :", [w for w in fake.warnings if u"中止" in w][:1])

print("### 15. “更新”按钮：用当前选择换物体，备注保持不变")
reset_scene()
build_scene()
config.reset_options()
shutil.rmtree(OUT, ignore_errors=True)
os.makedirs(OUT)
fake.add_transform("ns1:Mesh", None, {})
fake.add_transform("ns2:Mesh", None, {})
fake.add_camera("Camera2", "|Camera_Grp",
                {"rotateOrder": "xyz", "focalLength": 50.0})
core.replace_store({
    config.TYPE_FBX: [{"object": "Root_M", "export_name": u"我的骨骼备注", "enabled": True}],
    config.TYPE_ABC: [{"object": ["ns1:Mesh"], "export_name": u"我的ABC备注", "enabled": True}],
    config.TYPE_CAMERA: [{"object": "Camera", "export_name": u"我的相机备注", "enabled": True}],
})
ui.build_ui()
print("   行内的更新按钮存在:", fake.widget_by_label(u"更新")[0] is not None)

# FBX：选 Jnt1 -> 条目应指向 Jnt1，备注不变
fake.select(["|Root_M|Jnt1"], replace=True)
ui.on_update_btn_clicked(config.TYPE_FBX, 0)
fbx_entry = core.data_store[config.TYPE_FBX][0]
print("   FBX 物体/备注:", fbx_entry["object"], "/", fbx_entry["export_name"])

# ABC：选 ns2:Mesh -> 条目应指向 ns2:Mesh，备注不变
fake.select(["|ns2:Mesh"], replace=True)
ui.on_update_btn_clicked(config.TYPE_ABC, 0)
abc_entry = core.data_store[config.TYPE_ABC][0]
print("   ABC 物体/备注:", abc_entry["object"], "/", abc_entry["export_name"])

# 相机：选 Camera2 -> 条目应指向 Camera2，备注不变
fake.select(["|Camera_Grp|Camera2"], replace=True)
ui.on_update_btn_clicked(config.TYPE_CAMERA, 0)
cam_entry = core.data_store[config.TYPE_CAMERA][0]
print("   相机 物体/备注:", cam_entry["object"], "/", cam_entry["export_name"])

print("   备注全部保留:",
      fbx_entry["export_name"] == u"我的骨骼备注"
      and abc_entry["export_name"] == u"我的ABC备注"
      and cam_entry["export_name"] == u"我的相机备注")

print("### 16. 命名空间容错解析")
reset_scene()
build_scene()
fake.add_transform("ns1:Mesh", None, {})
print("   只有一个命名空间时解析短名:", utils.resolve_unique("Mesh", "ABC 物体"))
fake.add_transform("ns2:Mesh", None, {})
print("   两个命名空间时解析短名（应拒绝并提示）:", utils.resolve_unique("Mesh", "ABC 物体"))
print("   直接给带命名空间的名字:", utils.resolve_unique("ns2:Mesh", "ABC 物体"))
print("   名字确实不存在:", utils.resolve_unique("NoSuchMesh", "ABC 物体"))

shutil.rmtree(OUT, ignore_errors=True)
print("### 全部用例执行完毕")
