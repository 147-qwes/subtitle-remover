"""高层处理流程：单文件任务 = 输出路径 + 命令构建 + 执行 + 结果。"""

from __future__ import annotations

import os
import threading
from typing import Callable, Iterable, Optional

from . import ffmpeg_tools as ft

SOFT_MODE = "soft"
HARD_MODE = "hard"

DEFAULT_SUFFIX = "_nosub"


def ensure_dir(path: str) -> str:
    if path and not os.path.isdir(path):
        os.makedirs(path, exist_ok=True)
    return path


def output_path(src: str, outdir: Optional[str] = None, suffix: str = DEFAULT_SUFFIX) -> str:
    """按「原目录(或指定目录)/原名+suffix+原扩展名」生成不覆盖的输出路径。"""
    base_dir, filename = os.path.split(os.path.abspath(src))
    stem, ext = os.path.splitext(filename)
    target_dir = ensure_dir(os.path.abspath(outdir)) if outdir else base_dir
    suffix = (suffix or "").strip() or DEFAULT_SUFFIX

    candidate = os.path.join(target_dir, f"{stem}{suffix}{ext}")
    if os.path.normcase(candidate) == os.path.normcase(os.path.abspath(src)):
        candidate = os.path.join(target_dir, f"{stem}{DEFAULT_SUFFIX}{ext}")

    index = 1
    while os.path.exists(candidate):
        candidate = os.path.join(target_dir, f"{stem}{suffix}_{index}{ext}")
        index += 1
    return candidate


def remove_soft_subtitles(
    src: str,
    dst: str,
    keep_sub_indices: Optional[Iterable[int]] = None,
    on_progress: Optional[Callable[[float, float], None]] = None,
    on_log: Optional[Callable[[str], None]] = None,
    cancel: Optional[threading.Event] = None,
    duration: float = 0.0,
    copy_metadata: bool = True,
) -> ft.RunResult:
    """无损移除内嵌字幕轨。"""
    cmd = ft.build_soft_remove_command(
        src, dst, keep_sub_indices=keep_sub_indices, copy_metadata=copy_metadata
    )
    if on_log:
        on_log("命令: " + ft.join_command(cmd))
    return ft.run_command(
        cmd,
        duration=duration,
        on_progress=on_progress,
        on_log=on_log,
        cancel=cancel,
    )


def remove_hard_subtitles(
    src: str,
    dst: str,
    info: ft.MediaInfo,
    style: str,
    region: Optional[ft.Region] = None,
    height_ratio: Optional[float] = None,
    crf: int = 23,
    preset: str = "medium",
    encoder: str = "libx264",
    blur_radius: Optional[int] = None,
    on_progress: Optional[Callable[[float, float], None]] = None,
    on_log: Optional[Callable[[str], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> ft.RunResult:
    """去除画面内烧录字幕（裁切 / 模糊 / delogo），需要重新编码视频。"""
    cmd = ft.build_hard_remove_command(
        src,
        dst,
        info,
        style,
        region=region,
        height_ratio=height_ratio,
        crf=crf,
        preset=preset,
        encoder=encoder,
        blur_radius=blur_radius,
    )
    if on_log:
        on_log("命令: " + ft.join_command(cmd))
    return ft.run_command(
        cmd,
        duration=info.duration,
        on_progress=on_progress,
        on_log=on_log,
        cancel=cancel,
    )
