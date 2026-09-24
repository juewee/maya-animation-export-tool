# 动画资产一键导出工具 (Animation Export Tool)

Maya 动画制作流程中的批量资产导出工具。支持 **FBX 骨骼动画**、**Alembic 几何体缓存**、**相机动画** 三种资产的批量导出，提供可视化界面、配置持久化与外部批处理能力。

## 功能概览

### 三条导出链路

| 类型 | 导出方式 | 文件名格式 |
|---|---|---|
| FBX 骨骼动画 | 复制骨骼 → 约束烘焙 → 全帧 TRS 补帧 → 导出干净 Joint 层级 | `RootName_Anim_101-251.fbx` |
| ABC 几何体缓存 | 三角化 >4 边面（可选，非破坏性）→ AbcExport (ogawa) | `Prefix_Name.abc` |
| 相机动画 | 新建干净相机 → parentConstraint + bakeResults（失败退回世界矩阵采样）→ 逐帧采样相机属性 → 导出 | `Prefix_Camera_101-138.fbx` |

### 核心特性

- **骨骼根智能识别**：从选中的组/物体中自动检测骨骼根，支持 DeformationSystem / Jnt_Grp / Bone_Grp 等容器识别，SkinCluster 反查变形骨架，FitSkeleton 自动避让
- **多骨骼根弹窗选择**：检测到多个骨骼根时弹窗让用户勾选，标注 `[推荐：影响骨骼 N]` / `[DeformationSystem]` / `[慎用：FitSkeleton]`
- **UEAnimCamExporter 对齐**：相机导出与骨骼导出流程照搬 UEAnimCamExporter v4.1.10 的约束烘焙方案，选项默认值也与其一致
- **导出进度条**：一键导出时显示 Maya 进度条（逐条目 + 逐帧推进），可中途取消；批处理下自动降级为日志
- **打开即自检**：窗口打开时做一次轻量自检（插件 / 导出目录 / 场景是否保存 / 条目是否还在场景中），窗口顶部显示摘要，详情打到脚本编辑器
- **设置面板**：齿轮按钮打开，可自定义命名后缀与全部导出选项（采样步长、相机轴向转换、Bake 方式、感光器检查等），并随配置持久化
- **配置持久化**：配置自动写入场景内 network 节点，随 `.ma/.mb` 保存；也支持导出/导入外部 JSON
- **前缀自动识别**：从场景文件名提取前缀（如 `S02_xx.mb` → `S02`），导出命名自动拼接
- **无 PySide2 依赖**：纯 Maya `cmds` 构建，兼容 Maya 2018+

## 安装与使用

### 方式一：拖拽安装（推荐）

将 `install_export_tool.mel` 拖入 Maya 视口，工具立即打开，并在当前工具架生成「动画导出」按钮。

### 方式二：Python 执行

在 Maya 脚本编辑器（Python 标签）执行：

```python
import sys; sys.path.insert(0, r"C:/path/to/maya_export")
from animation_exporter import ui; ui.launch()
```

### 导出流程

1. 设置导出目录
2. 在场景中选中物体，点击对应分类的「+」按钮添加条目
   - 条目行：[勾选] [物体名（点击可在场景中选中）] [备注/导出名] [更新] [删除]
   - **「更新」按钮**：物体被复制 / 改名 / 进了命名空间后，选中真正要导出的物体点「更新」，
     条目会重新指向它，而**备注（导出名）保持原样**，不用删了重加
3. 设置动画范围（当前时间滑块 / 自定义）；在 Maya 里改了时间轴范围后，点右侧「刷新」即可把开始/结束同步成当前时间轴范围（选“当前时间滑块”时导出本来就是实时读取时间轴，刷新只是让显示的数字与实际一致）
4. 点击「一键导出」

## 项目结构

```
maya_export/
├── install_export_tool.mel       # 拖拽安装器（自动发现路径，无需编辑）
├── launch_export_tool.py         # Python 启动入口
├── batch_export.py               # 命令行导出器（mayapy 下运行，无 GUI）
├── animation_exporter/           # 工具包
│   ├── __init__.py               # 包入口，launch()
│   ├── config.py                 # 常量、分类、插件名、导出选项默认值
│   ├── checks.py                 # 打开窗口时的轻量自检
│   ├── utils.py                  # Maya 通用工具（骨骼根查找、SkinCluster 反查、进度条）
│   ├── persistence.py            # 数据持久化（场景 network 节点 + 外部 JSON）
│   ├── exporter.py               # 导出执行层（FBX / ABC / 相机）
│   ├── core.py                   # 核心逻辑（条目数据、批量导出入口）
│   ├── batch.py                  # 无 GUI 批处理 API（供外部脚本/AI 调用）
│   └── ui.py                     # UI 层（窗口、交互、弹窗、设置）
└── legacy/                       # 旧版单文件备份
    └── 导出函数脚本_旧版单文件v7.py
```

## 无 GUI 批处理 API

### 方式一：命令行工具（推荐）

在 `mayapy` 下运行 `batch_export.py`，适合批处理/渲染农场：

```bash
# 打开场景并一键导出
mayapy batch_export.py --scene "D:/scenes/S02.mb" --output "D:/exports" --start 101 --end 251

# 只导出 FBX 和相机
mayapy batch_export.py --scene "D:/scenes/S02.mb" --output "D:/exports" --types fbx,camera

# 预检查（列出配置和缺失物体，不导出）
mayapy batch_export.py --scene "D:/scenes/S02.mb" --check

# 不指定帧范围则用场景里保存的配置
mayapy batch_export.py --scene "D:/scenes/S02.mb" --output "D:/exports"
```

### 方式二：Python API

`batch.py` 提供无界面接口，可在 Maya Python 或 mayapy 中调用：

```python
import sys; sys.path.insert(0, r"C:/path/to/maya_export")
from animation_exporter import batch

# 查看场景里的导出配置
info = batch.describe_scene()

# 预检查（缺失物体列表，不导出）
report = batch.precheck_scene()

# 用场景配置一键导出
results = batch.export_from_scene(export_dir="D:/output", start=101, end=251)

# 只导出 FBX 和相机
results = batch.export_from_scene(export_dir="D:/output", start=101, end=251,
                                  types=["fbx", "camera"])

# 直接传配置导出（不读场景节点）
results = batch.export_with_config(
    {"fbx": [{"object": "Root_M", "export_name": "CharA", "enabled": True}]},
    export_dir="D:/output", start=101, end=251)

# 临时覆盖导出选项（键名见 config.EXPORT_OPTIONS）
results = batch.export_with_config(
    {"camera": [{"object": "Camera", "export_name": "S02_Camera", "enabled": True}]},
    export_dir="D:/output", start=101, end=138,
    options={"camera_z_up": True, "sample_by": 1, "show_progress": False})
```

> `export_from_scene` 会自动读取场景节点里保存的命名模板与导出选项，因此 GUI 里调好的设置对命令行导出同样生效。

## 技术细节

### FBX 骨骼动画导出

1. 复制骨骼根（`duplicate -inputConnections=False`）
2. 删除副本中所有非 joint 节点（mesh / 曲线 / locator / 约束）
3. 递归重命名副本与原始骨骼一致
4. parentConstraint + scaleConstraint 约束原始 → 副本
5. `bakeResults` 烘焙 + 逐帧补 T/R/S 关键帧
6. 检查关键帧数量，为 0 时报错（防止导出空骨架）
7. 选中副本骨骼层级导出 FBX（Z-Up、Euler、ResampleAnimation）

### 相机动画导出

完全照搬 UEAnimCamExporter v4.1.10 的相机链路（`exporter.py`）：

1. 可选：检测相机的父级 Zero 组 / 控制器 / 约束 / 动画曲线，只写日志，不导出这些控制器
2. 可选：只 Bake 相机实际动画段（扫描相机、Shape、父级、约束、控制器的关键帧；可限制在 Start/End 内）
3. 可选：导出前比较 Render Settings 分辨率比例与 Camera Film Aperture 比例，不一致时弹窗提示
   （选“是：打开设置”会**先选中这台相机本体（transform + shape）**再打开 Render Settings 与属性编辑器，
   并中止本次导出、保持相机被选中，方便直接改 Film Aperture；选“否：继续导出”照常导出）
4. 新建干净相机并直接挂世界根（FBX 无多余父级组）
5. 复制旋转顺序 + 静态相机参数（焦距 / 光圈 / 裁剪面等 13 个属性）
6. parentConstraint + scaleConstraint → `bakeResults(shape=True, minimizeRotation=True)`；
   约束创建失败时**自动退回世界矩阵逐帧采样**（`dgdirty` + `refresh` 强制求解，兜住 Aim 约束 / 表达式 / 动画层）
7. 逐帧拷贝 `_CAMERA_ATTRS` 并补齐 TRS 关键帧，`filterCurve` 平滑
8. 只选 Camera Transform + Shape 导出；失败或被取消时临时相机一定被删除

> **相机默认不做 Z-Up / ConvertAnimation**（`camera_z_up = False`）：
> 参考工具把相机轴向转换做成独立开关且默认关闭，
> 否则相机位置/方向会被 Maya 导出与 UE 导入各转换一次（二次转换），
> 导入 Sequencer 后视角与 Maya 对不上。骨骼 FBX 仍保持 Z-Up 不变。

### ABC 导出前清理（可选项）

勾选「导出前三角化多边面（>4 边）」后，导出前只执行
`expandPolyGroupSelection` + `polyCleanupArgList`，把 **多于 4 条边** 的面三角化：

- `selectOnly=1`（对当前选择执行清理）+ `nsided=1`、其余检查全为 0 → 只动 n 边面
- `historyOn=1` → 保留构造历史，三角化以 `polyTriangulate` 节点接在历史链上（非破坏性）

**不删除构造历史、不冻结变换**。ABC 通常用于导出动画：删历史会断开变形器 / 约束 /
驱动关键帧；冻结变换在动画通道上会报“未应用冻结变换，因为 xxx 具有引入连接”，
而且旧实现在报错前已经先把历史删掉了。勾选后若模型没有多边面，Maya 会提示
“找不到要清理的项目”，属正常，不影响导出。

### ABC 命名空间（导出失败时看这里）

命名空间相关有两处坑，症状都是“导不出来”，要分开处理：

**（1）物体名字解析不到（最常见）**

条目里存的是短名，而 Maya 的 `ls` **不会用短名匹配命名空间里的物体**：
`cmds.ls("Mesh")` 在场景只有 `ns1:Mesh` 时返回空，于是条目被判定“不存在”而跳过，
最后报 `导出组 'xxx' 中没有可导出的物体`。

现在 `utils.resolve_unique()` 做了命名空间容错：精确匹配不到时会再按
`*:名字` / `*:叶子名` 找一次（`*:` 不跨多级命名空间，嵌套命名空间用 `*:子层:名` 也能命中）。
只有一个候选时直接用它；有多个候选（真的重名）时会明确报出候选列表，并提示
**用条目行上的「更新」按钮**把它重新指到当前选择——这样也**不用重填备注**。

**（2）AbcExport 的 `-stripNamespaces` 与重名冲突**

AbcExport 默认带 `-stripNamespaces`（可在设置面板关闭）。当命名空间里确实存在**同名**物体时，
去掉命名空间后两个物体重名，AbcExport 会直接失败：

```
AbcExport 失败: std::exception encountered: Conflicting root node names specified:
|ns2:Mesh |ns1:Mesh with -stripNamespace specified.
```

这时在设置面板取消勾选「ABC 几何体缓存 → 去除命名空间」，让 ABC 保留命名空间层级即可正常导出；
导出失败的报错里也会附上这句建议。（注意：如果是上面 (1) 的解析问题，关掉这个开关是没用的，
因为失败发生在 AbcExport 之前。）

### 设置面板（齿轮按钮）

窗口底部「设置」打开，所有选项立即生效并随配置持久化（场景节点 / JSON）：

| 分组 | 选项 | 默认 |
|---|---|---|
| 命名规范 | FBX 骨骼动画后缀 / 相机动画后缀 / ABC 文件名追加帧范围 | `_Anim_{start}-{end}` / `_{start}-{end}` / 关 |
| ABC 几何体缓存 | 去除命名空间（AbcExport `-stripNamespaces`） | 开 |
| 通用 | 采样步长（`bakeResults sampleBy` + `FBXExportBakeComplexStep`） | 1（逐帧） |
| 通用 | 显示导出进度条 / 打开工具时自检 | 开 / 开 |
| FBX 骨骼动画 | Z-Up / ConvertAnimation | 开 |
| 相机动画 | Z-Up / ConvertAnimation | **关**（与参考工具一致） |
| 相机动画 | 临时相机挂世界根 / ParentConstraint Bake / 检测相机控制器 / 只 Bake 实际动画段 / 限制在 Start-End 内 / 感光器检查 / 感光器容差 | 开 / 开 / 开 / 关 / 开 / 开 / 0.005 |

「恢复默认」一键回到参考工具的默认值。

### 导出进度

- `core.run_export_batch` 打开一个 Maya 进度条，按条目划分区间，条目内部由 `exporter` 按帧推进（相机采样、补 TRS 帧、写盘）
- 进度条上的「取消」会在当前条目结束后停止后续条目；条目内部的按帧循环也会每 8 帧检查一次并安全退出（临时节点由 `finally` 清理）
- `mayapy` / `-batch` 下没有进度条 UI，自动降级为一行日志；设置面板可整体关闭

### 打开时的自检

窗口打开时跑一次 `checks.light_check()`，只做少量只读查询（**不遍历场景、不查蒙皮/网格**）：

- Maya 版本、必需插件是否找得到、导出目录是否可写、场景是否已保存、帧范围是否合法
- 配置条目是否还在场景中、相机条目下是否有 camera shape、FBX 条目下是否有 joint
- 条目检查有 200 条上限与 0.3 秒时间预算，超大配置也不会拖慢打开
- 结果：窗口顶部状态行显示摘要（绿/黄/红），完整报告输出到脚本编辑器

### 骨骼根检测策略

1. 直接选中 joint → 返回该 joint
2. 扫描骨骼容器（DeformationSystem / Jnt_Grp / Bone_Grp 等）下的 joint
3. 全量 descendant joint 兜底
4. 以上均无 joint → SkinCluster influence 反查变形骨架 Root
5. `_topmost_joints` 取顶层 + `_root_preference_score` 排序（DeformationSystem 优先，FitSkeleton 靠后）

## 无 Maya 冒烟测试

`tests/smoke_test.py` 用一份假的 `maya.cmds` 实现，在没有 Maya 的机器上跑通关键链路
（相机导出 / 进度条 / 取消清理 / 启动自检 / 设置面板 / 持久化往返）：

```bash
python tests/smoke_test.py
```

它不会连接或修改任何 Maya 场景，只用于改代码后快速回归。真实导出仍建议在 Maya 里验证。

## 技术约束

- Maya 2018+，使用内置 `cmds` 模块，不依赖 PySide2
- 配置文件 JSON 格式，UTF-8 编码
- 导出依赖 Maya 自带 FBX 和 Alembic 插件
- 兼容中文路径（FBX 导出失败时自动回退 ASCII 临时路径）

## License

MIT
