# -*- coding: utf-8 -*-
"""
Maya 动画资产命令行导出器

在 mayapy（无 GUI）下运行，打开场景、读取配置节点、一键导出。

用法：
    mayapy batch_export.py --scene "D:/scenes/S02.mb" --output "D:/exports" --start 101 --end 251
    mayapy batch_export.py --scene "D:/scenes/S02.mb" --types fbx,camera --output "D:/exports"
    mayapy batch_export.py --scene "D:/scenes/S02.mb" --check
    mayapy batch_export.py --scene "D:/scenes/S02.mb" --output "D:/exports" --prefix S02

参数：
    --scene PATH       场景文件路径（.ma / .mb），必填
    --output DIR       导出目录，不指定则用场景里保存的配置
    --start N          起始帧，不指定则用场景配置或播放范围
    --end N            结束帧
    --types a,b,c      只导出这些分类（fbx / abc / camera），逗号分隔
    --prefix TEXT      命名前缀，不指定则用场景配置
    --check            只预检查（列出配置和缺失物体），不导出

退出码：全成功=0，有失败=1，参数错误/无配置=2
"""
import argparse
import os
import sys


def _init_maya():
    """初始化 headless Maya（maya.standalone），仅在 mayapy 下需要"""
    try:
        import maya.standalone
        maya.standalone.initialize(name="batch_export")
    except Exception:
        pass  # 已在 Maya 内或 standalone 已初始化

    import maya.cmds as cmds
    # 加载导出依赖插件
    for plugin in ("fbxmaya", "AbcExport"):
        try:
            if not cmds.pluginInfo(plugin, q=True, loaded=True):
                cmds.loadPlugin(plugin)
        except Exception:
            pass
    return cmds


def main():
    parser = argparse.ArgumentParser(
        description="Maya animation export tool (headless)")
    parser.add_argument("--scene", required=True, help="Scene file path (.ma/.mb)")
    parser.add_argument("--output", default=None, help="Export directory")
    parser.add_argument("--start", type=int, default=None, help="Start frame")
    parser.add_argument("--end", type=int, default=None, help="End frame")
    parser.add_argument("--types", default=None,
                        help="Only export these types (comma-separated: fbx,abc,camera)")
    parser.add_argument("--prefix", default=None, help="Name prefix")
    parser.add_argument("--check", action="store_true",
                        help="Pre-check only (no export)")
    args = parser.parse_args()

    if not os.path.isfile(args.scene):
        print("[ERROR] Scene file not found: {0}".format(args.scene))
        sys.exit(2)

    # 初始化 Maya
    cmds = _init_maya()

    # 添加项目路径到 sys.path，以便 import animation_exporter
    script_dir = os.path.dirname(os.path.abspath(__file__))
    if script_dir not in sys.path:
        sys.path.insert(0, script_dir)

    from animation_exporter import batch

    # 打开场景
    print("[INFO] Opening scene: {0}".format(args.scene))
    cmds.file(args.scene, open=True, force=True)

    # 预检查
    if args.check:
        print("\n===== Pre-check =====")
        report = batch.precheck_scene()
        if not report.get("has_config"):
            print("No export config found in scene.")
            sys.exit(0)
        print("Total items: {0}".format(report["total"]))
        print("Enabled:     {0}".format(report["enabled"]))
        print("Export dir:  {0}".format(report.get("export_dir", "")))
        if report["missing"]:
            print("Missing objects ({0}):".format(len(report["missing"])))
            for m in report["missing"]:
                print("  - {0}".format(m))
        else:
            print("All objects present.")
        # 也列出详细配置
        info = batch.describe_scene()
        print("\n----- Items -----")
        for type_key in ("fbx", "abc", "camera"):
            items = info["items"].get(type_key, [])
            if items:
                print("\n[{0}]".format(type_key))
                for item in items:
                    flag = "[x]" if item["enabled"] else "[ ]"
                    print("  {0} {1} -> {2}".format(
                        flag, item["object"], item["export_name"]))
        print("\nAnimation range: {0} - {1}".format(*info["animation_range"]))
        sys.exit(0)

    # 导出
    types = None
    if args.types:
        types = [t.strip() for t in args.types.split(",") if t.strip()]

    print("\n===== Export =====")
    results = batch.export_from_scene(
        export_dir=args.output,
        start=args.start,
        end=args.end,
        types=types,
        prefix=args.prefix,
    )

    successes = results.get("success", [])
    failures = results.get("failure", [])

    print("\n===== Results =====")
    print("Success: {0}".format(len(successes)))
    for type_key, name, path in successes:
        print("  [OK] {0}: {1}".format(name, path))

    if failures:
        print("Failure: {0}".format(len(failures)))
        for type_key, name, error in failures:
            print("  [FAIL] {0}: {1}".format(name, error))

    if failures and not successes:
        sys.exit(1)
    elif failures:
        sys.exit(1)
    else:
        sys.exit(0)


if __name__ == "__main__":
    main()
