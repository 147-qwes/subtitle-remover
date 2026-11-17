"""视频去字幕工具。

- 软字幕轨：无损移除视频内的字幕流（mkv 内封、mp4 的 mov_text 等）；
- 硬字幕：裁切 / 模糊遮罩 / delogo 修复画面里的烧录字幕。
"""

__version__ = "1.0.0"

from . import ffmpeg_tools, remover  # noqa: F401  （包级便捷导入）

__all__ = ["ffmpeg_tools", "remover", "__version__"]
