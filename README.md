# 动画资产一键导出工具 (Animation Export Tool)

Maya 动画制作流程中的批量资产导出工具。支持 **FBX 骨骼动画**、**Alembic 几何体缓存**、**相机动画** 三种资产的批量导出，提供可视化界面、配置持久化与外部批处理能力。

## 功能概览

### 三条导出链路

| 类型 | 导出方式 | 文件名格式 |
|---|---|---|
| FBX 骨骼动画 | 复制骨骼 → 约束烘焙 → 全帧 TRS 补帧 → 导出干净 Joint 层级 | `RootName_Anim_101-251.fbx` |
| ABC 几何体缓存 | 清理历史/冻结变换（可选）→ AbcExport (ogawa) | `Prefix_Name.abc` |
| 相机动画 | 新建干净相机 → parentConstraint + bakeResults → 逐帧采样相机属性 → 导出 | `Prefix_Camera_101-138.fbx` |

### 核心特性

- **骨骼根智能识别**：从选中的组/物体中自动检测骨骼根，支持 DeformationSystem / Jnt_Grp / Bone_Grp 等容器识别，SkinCluster 反查变形骨架，FitSkeleton 自动避让
- **多骨骼根弹窗选择**：检测到多个骨骼根时弹窗让用户勾选，标注 `[推荐：影响骨骼 N]` / `[DeformationSystem]` / `[慎用：FitSkeleton]`
- **UEAnimCamExporter 对齐**：相机导出和骨骼导出流程照搬 UEAnimCamExporter v4.1.10 的约束烘焙方案
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
3. 设置动画范围（当前时间滑块 / 自定义）
4. 点击「一键导出」

## 项目结构

```
maya_export/
├── install_export_tool.mel       # 拖拽安装器（自动发现路径，无需编辑）
├── launch_export_tool.py         # Python 启动入口
├── animation_exporter/           # 工具包
│   ├── __init__.py               # 包入口，launch()
│   ├── config.py                 # 常量、分类、插件名
│   ├── utils.py                  # Maya 通用工具（骨骼根查找、SkinCluster 反查等）
│   ├── persistence.py            # 数据持久化（场景 network 节点 + 外部 JSON）
│   ├── exporter.py               # 导出执行层（FBX / ABC / 相机）
│   ├── core.py                   # 核心逻辑（条目数据、批量导出入口）
│   └── ui.py                     # UI 层（窗口、交互、弹窗）
└── legacy/                       # 旧版单文件备份
    └── 导出函数脚本_旧版单文件v7.py
```

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

1. 新建干净相机并直接挂世界根（FBX 无多余父级组）
2. 复制旋转顺序 + 静态相机参数（焦距 / 光圈 / 裁剪面等 13 个属性）
3. parentConstraint + scaleConstraint → `bakeResults(shape=True, minimizeRotation=True)`
4. 逐帧拷贝 `_CAMERA_ATTRS` 并补齐 TRS 关键帧
5. `filterCurve` 平滑
6. 只选 Camera Transform + Shape 导出

### 骨骼根检测策略

1. 直接选中 joint → 返回该 joint
2. 扫描骨骼容器（DeformationSystem / Jnt_Grp / Bone_Grp 等）下的 joint
3. 全量 descendant joint 兜底
4. 以上均无 joint → SkinCluster influence 反查变形骨架 Root
5. `_topmost_joints` 取顶层 + `_root_preference_score` 排序（DeformationSystem 优先，FitSkeleton 靠后）

## 技术约束

- Maya 2018+，使用内置 `cmds` 模块，不依赖 PySide2
- 配置文件 JSON 格式，UTF-8 编码
- 导出依赖 Maya 自带 FBX 和 Alembic 插件
- 兼容中文路径（FBX 导出失败时自动回退 ASCII 临时路径）

## License

MIT
