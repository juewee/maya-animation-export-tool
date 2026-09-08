# 命令行导出器实现计划

## Context

当前 `batch.py` 提供了无 GUI 的 Python API，但需要用户在 Maya Python 环境中手动 import 和调用。用户想要一个可以在 `mayapy` 下直接运行的命令行工具，适合批处理/渲染农场场景，能传参数打开场景、读取配置、一键导出。

## 实现方案

新建一个独立脚本 `batch_export.py`（放在项目根目录），不修改现有包代码（除修复 `batch.py` 中的 import 问题）。

### 新增文件

**`batch_export.py`** — 命令行入口脚本

用法示例：
```bash
# 用 mayapy 运行，打开场景并导出
mayapy batch_export.py --scene "D:/scenes/S02_anim.mb" --output "D:/exports" --start 101 --end 251

# 只导出 FBX 和相机
mayapy batch_export.py --scene "D:/scenes/S02_anim.mb" --output "D:/exports" --types fbx,camera

# 预检查（不导出，只列出配置和缺失物体）
mayapy batch_export.py --scene "D:/scenes/S02_anim.mb" --check

# 不指定帧范围则用场景里保存的配置
mayapy batch_export.py --scene "D:/scenes/S02_anim.mb" --output "D:/exports"
```

脚本流程：
1. `argparse` 解析参数：`--scene`, `--output`, `--start`, `--end`, `--types`, `--check`, `--prefix`
2. `maya.standalone` 初始化（headless 模式）
3. `cmds.file(scene, open=True)` 打开场景
4. 加载 FBX/AbcExport 插件
5. 如果 `--check`：调用 `batch.precheck_scene()` 打印结果后退出
6. 否则：调用 `batch.export_from_scene()` 执行导出
7. 打印结果汇总（成功/失败列表）
8. 退出码：全成功=0，有失败=1

### 修改文件

**`animation_exporter/batch.py`** — 修复 import

把 `__import__("maya.cmds", fromlist=["cmds"])` 改为正常的 `import maya.cmds as cmds`，放在文件顶部。当前写法在 `mayapy` 环境下可能因为 standalone 未初始化而失败，但修复 import 是正确做法。

## 验证

```bash
# 在 mayapy 下测试预检查
mayapy batch_export.py --scene "D:/test/scene.mb" --check

# 测试导出
mayapy batch_export.py --scene "D:/test/scene.mb" --output "D:/test/output" --start 101 --end 251
```
