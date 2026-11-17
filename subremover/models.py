"""界面与处理流程共用的数据模型。"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from . import ffmpeg_tools as ft


@dataclass
class FileRow:
    """文件列表中的一行。"""

    iid: str
    path: str
    info: Optional[ft.MediaInfo] = None
    status: str = "待处理"
    note: str = ""
