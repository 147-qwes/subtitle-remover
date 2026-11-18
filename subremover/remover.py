"""高层处理流程：单文件任务 = 输出路径 + 命令构建 + 执行 + 结果。"""

from __future__ import annotations

import os
import threading
from typing import Callable, Iterable, List, Optional

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
    regions: Optional[Iterable[ft.Region]] = None,
    height_ratio: Optional[float] = None,
    crf: int = 23,
    preset: str = "medium",
    encoder: str = "libx264",
    blur_radius: Optional[int] = None,
    on_progress: Optional[Callable[[float, float], None]] = None,
    on_log: Optional[Callable[[str], None]] = None,
    cancel: Optional[threading.Event] = None,
    model_path: Optional[str] = None,
    device: str = "auto",
    workers: int = 0,
    det_model_path: Optional[str] = None,
    det_device: Optional[str] = None,
    refine: int = -1,
) -> ft.RunResult:
    """去除画面内烧录字幕 / 水印。

    - crop_* / blur / delogo：拼 ffmpeg 命令一次跑完；
    - inpaint：交给 inpaint.py 逐帧重绘（需要 onnxruntime + LaMa 模型）；
    - auto：每帧先跑文字检测，字在哪里就在哪里重绘（动态字幕用它）。
    """
    if style in (ft.INPAINT_STYLE, ft.AUTO_STYLE):
        from . import inpaint as ip  # 延迟导入：不装 onnxruntime 也能用其他功能

        detect = style == ft.AUTO_STYLE

        # region 与 regions 可能同时传入（GUI / CLI 都会这样），同一块区域只修一次
        targets: List[ft.Region] = []
        for item in list(regions or []) + ([region] if region else []):
            if item is None:
                continue
            if any(
                abs(item.x - seen.x) < 2
                and abs(item.y - seen.y) < 2
                and abs(item.w - seen.w) < 2
                and abs(item.h - seen.h) < 2
                for seen in targets
            ):
                continue
            targets.append(item)
        if not targets and not detect:
            raise ValueError("AI 修复需要先框选文字区域")
        return ip.process_video(
            src,
            dst,
            info,
            targets,
            crf=crf,
            preset=preset,
            encoder=encoder,
            on_progress=on_progress,
            on_log=on_log,
            cancel=cancel,
            model_path=model_path,
            device=device,
            workers=workers,
            detect=detect,
            detect_scope=targets or None,
            det_model_path=det_model_path,
            det_device=det_device,
            refine=refine,
        )

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
