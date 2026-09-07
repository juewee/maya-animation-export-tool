# -*- coding: utf-8 -*-
"""
Animation Export Tool - launcher (keep this file pure ASCII so it can be
exec()'d with any default encoding, e.g. GBK on Chinese Windows).

Run inside Maya:
    exec(open(r"C:/Users/TheFl/Desktop/losheep/code/maya_export/launch_export_tool.py").read())

or simply drag install_export_tool.mel into the Maya viewport.
"""
import os
import sys

_ROOT = os.path.dirname(os.path.abspath(__file__))
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

# Drop any previously loaded copy so re-running always uses the latest code.
for _name in [m for m in list(sys.modules)
              if m == "animation_exporter" or m.startswith("animation_exporter.")]:
    del sys.modules[_name]

from animation_exporter import ui  # noqa: E402

ui.launch()
