"""拖拽支持探测（可选依赖 tkinterdnd2）。"""

from __future__ import annotations

try:  # pragma: no cover - 取决于运行环境是否安装 tkinterdnd2
    from tkinterdnd2 import DND_FILES, TkinterDnD

    AVAILABLE = True
except Exception:  # pragma: no cover
    DND_FILES = None
    TkinterDnD = None
    AVAILABLE = False
