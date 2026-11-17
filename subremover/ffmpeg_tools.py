"""ffmpeg / ffprobe 封装层。

职责：
1. 定位 ffmpeg、ffprobe 可执行文件（环境变量 -> 程序目录 bin -> PATH -> 常见安装位置）；
2. 解析媒体信息（时长、分辨率、字幕流列表）；
3. 构建两类处理命令：软字幕轨无损移除、硬字幕（画面内烧录字幕）去除；
4. 带进度回调地执行命令，支持取消。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from functools import lru_cache
from typing import Callable, Iterable, List, Optional, Sequence, Tuple

ENV_FFMPEG_DIR = "SUBTITLE_REMOVER_FFMPEG_DIR"

EXE_SUFFIX = ".exe" if os.name == "nt" else ""

# 硬字幕处理方式 -> ffmpeg 参数
HARD_STYLES = ("crop_bottom", "crop_top", "blur", "delogo")


class FFmpegNotFound(RuntimeError):
    """找不到 ffmpeg / ffprobe。"""


class ProbeError(RuntimeError):
    """ffprobe 无法解析该文件。"""


# --------------------------------------------------------------------------- #
# 可执行文件定位
# --------------------------------------------------------------------------- #
def _creation_flags() -> int:
    """Windows 下隐藏子进程控制台窗口。"""
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _project_root() -> str:
    if getattr(sys, "frozen", False):
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def candidate_dirs() -> List[str]:
    """ffmpeg 搜索目录，按优先级排列。"""
    dirs: List[str] = []
    env_dir = os.environ.get(ENV_FFMPEG_DIR, "").strip()
    if env_dir:
        dirs.append(env_dir)

    root = _project_root()
    dirs.extend([os.path.join(root, "bin"), root, os.path.join(root, "tools", "bin")])

    if os.name == "nt":
        dirs.extend(
            [
                r"C:\ffmpeg\bin",
                r"C:\Program Files\ffmpeg\bin",
                r"C:\Program Files (x86)\ffmpeg\bin",
            ]
        )
        local = os.environ.get("LOCALAPPDATA", "")
        if local:
            dirs.append(os.path.join(local, "Programs", "ffmpeg", "bin"))
            dirs.append(os.path.join(local, "Microsoft", "WinGet", "Links"))
    else:
        dirs.extend(["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin"])

    return [d for d in dirs if d]


@lru_cache(maxsize=None)
def find_binary(name: str) -> Optional[str]:
    """返回 name（ffmpeg / ffprobe）的绝对路径，找不到返回 None。"""
    exe = name + EXE_SUFFIX
    for directory in candidate_dirs():
        path = os.path.join(directory, exe)
        if os.path.isfile(path):
            return path
    return shutil.which(name) or shutil.which(exe)


def clear_binary_cache() -> None:
    find_binary.cache_clear()


def require_binaries() -> Tuple[str, str]:
    ffmpeg = find_binary("ffmpeg")
    ffprobe = find_binary("ffprobe")
    if not ffmpeg or not ffprobe:
        raise FFmpegNotFound(
            "未找到 ffmpeg / ffprobe。\n"
            "解决办法二选一：\n"
            "1) 运行项目内 tools/setup_ffmpeg.ps1 自动下载到 bin 目录；\n"
            "2) 手动安装 ffmpeg 并加入 PATH，或设置环境变量 "
            f"{ENV_FFMPEG_DIR} 指向其 bin 目录。"
        )
    return ffmpeg, ffprobe


def _ffmpeg_path() -> str:
    return find_binary("ffmpeg") or "ffmpeg"


# --------------------------------------------------------------------------- #
# 媒体信息
# --------------------------------------------------------------------------- #
@dataclass
class StreamInfo:
    index: int
    codec_type: str
    codec_name: str = "unknown"
    language: str = ""
    title: str = ""
    width: Optional[int] = None
    height: Optional[int] = None
    duration: float = 0.0
    default: bool = False
    forced: bool = False

    @property
    def label(self) -> str:
        parts = [f"#{self.index}", self.codec_name]
        if self.language:
            parts.append(self.language)
        if self.title:
            parts.append(self.title)
        if self.forced:
            parts.append("forced")
        return " ".join(parts)


@dataclass
class MediaInfo:
    path: str
    duration: float = 0.0
    format_name: str = ""
    streams: List[StreamInfo] = field(default_factory=list)

    @property
    def video_streams(self) -> List[StreamInfo]:
        return [s for s in self.streams if s.codec_type == "video"]

    @property
    def audio_streams(self) -> List[StreamInfo]:
        return [s for s in self.streams if s.codec_type == "audio"]

    @property
    def subtitle_streams(self) -> List[StreamInfo]:
        return [s for s in self.streams if s.codec_type == "subtitle"]

    @property
    def first_video(self) -> Optional[StreamInfo]:
        videos = self.video_streams
        return videos[0] if videos else None

    @property
    def width(self) -> int:
        stream = self.first_video
        return int(stream.width or 0) if stream else 0

    @property
    def height(self) -> int:
        stream = self.first_video
        return int(stream.height or 0) if stream else 0

    @property
    def has_video_dimensions(self) -> bool:
        return bool(self.width and self.height)

    @property
    def subtitle_count(self) -> int:
        return len(self.subtitle_streams)

    @property
    def subtitle_label(self) -> str:
        streams = self.subtitle_streams
        if not streams:
            return "无"
        return f"{len(streams)} 条：" + "、".join(s.label for s in streams[:3]) + (
            "…" if len(streams) > 3 else ""
        )


def _to_float(value, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def parse_probe_json(data: dict, path: str) -> MediaInfo:
    """把 ffprobe 的 JSON 结果转成 MediaInfo（纯函数，便于测试）。"""
    fmt = data.get("format") or {}
    duration = _to_float(fmt.get("duration"))
    streams: List[StreamInfo] = []
    max_duration = 0.0

    for raw in data.get("streams") or []:
        tags = raw.get("tags") or {}
        disposition = raw.get("disposition") or {}
        stream = StreamInfo(
            index=int(raw.get("index", len(streams))),
            codec_type=str(raw.get("codec_type") or "unknown"),
            codec_name=str(raw.get("codec_name") or "unknown"),
            language=str(tags.get("language") or ""),
            title=str(tags.get("title") or ""),
            width=raw.get("width"),
            height=raw.get("height"),
            duration=_to_float(raw.get("duration")),
            default=bool(disposition.get("default", 0)),
            forced=bool(disposition.get("forced", 0)),
        )
        streams.append(stream)
        max_duration = max(max_duration, stream.duration)

    return MediaInfo(
        path=path,
        duration=duration or max_duration,
        format_name=str(fmt.get("format_name") or ""),
        streams=streams,
    )


def probe(path: str) -> MediaInfo:
    _, ffprobe = require_binaries()
    cmd = [
        ffprobe,
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        path,
    ]
    completed = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_creation_flags(),
    )
    if completed.returncode != 0:
        raise ProbeError(
            f"无法解析文件：{os.path.basename(path)}\n{completed.stderr.strip()[:500]}"
        )
    try:
        data = json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise ProbeError(f"ffprobe 输出无法解析：{exc}") from exc
    return parse_probe_json(data, path)


# --------------------------------------------------------------------------- #
# 区域
# --------------------------------------------------------------------------- #
@dataclass
class Region:
    """像素坐标的矩形区域。"""

    x: int
    y: int
    w: int
    h: int

    def clamped(self, width: int, height: int) -> "Region":
        width = max(2, int(width))
        height = max(2, int(height))
        w = max(2, min(int(self.w), width))
        h = max(2, min(int(self.h), height))
        x = max(0, min(int(self.x), width - w))
        y = max(0, min(int(self.y), height - h))
        return Region(x, y, w, h)

    def even(self) -> "Region":
        """宽高取偶数，避免 yuv420p 编码报错。"""
        w = max(2, self.w - self.w % 2)
        h = max(2, self.h - self.h % 2)
        return Region(self.x, self.y, w, h)

    def as_text(self) -> str:
        return f"{self.x},{self.y},{self.w},{self.h}"


def parse_region(text: str) -> Region:
    """解析 "x,y,w,h" 形式的区域。"""
    numbers = [int(float(part)) for part in re.split(r"[,，xX ]+", text.strip()) if part]
    if len(numbers) != 4:
        raise ValueError("区域格式应为 x,y,w,h 四个数字")
    return Region(*numbers)


@dataclass
class RelativeRegion:
    """相对画面尺寸的区域（0~1），便于批量处理不同分辨率文件。"""

    x: float
    y: float
    w: float
    h: float

    def to_pixels(self, width: int, height: int) -> Region:
        return Region(
            int(round(width * self.x)),
            int(round(height * self.y)),
            int(round(width * self.w)),
            int(round(height * self.h)),
        ).clamped(width, height)

    @classmethod
    def from_pixels(cls, region: Region, width: int, height: int) -> "RelativeRegion":
        width = max(1, width)
        height = max(1, height)
        return cls(
            x=region.x / width,
            y=region.y / height,
            w=region.w / width,
            h=region.h / height,
        )

    @classmethod
    def bottom_band(cls, ratio: float) -> "RelativeRegion":
        ratio = min(0.9, max(0.01, ratio))
        return cls(x=0.0, y=1.0 - ratio, w=1.0, h=ratio)

    @classmethod
    def top_band(cls, ratio: float) -> "RelativeRegion":
        ratio = min(0.9, max(0.01, ratio))
        return cls(x=0.0, y=0.0, w=1.0, h=ratio)


# --------------------------------------------------------------------------- #
# 命令构建
# --------------------------------------------------------------------------- #
def _is_mp4_family(path: str) -> bool:
    return os.path.splitext(path)[1].lower() in {".mp4", ".m4v", ".mov"}


def build_soft_remove_command(
    src: str,
    dst: str,
    keep_sub_indices: Optional[Iterable[int]] = None,
    copy_metadata: bool = True,
    ffmpeg: Optional[str] = None,
) -> List[str]:
    """构建「移除软字幕轨」命令。

    视频、音频、章节均为 -c copy 直接复制，不重新编码；默认移除全部字幕流，
    keep_sub_indices 可指定要保留的字幕流绝对序号（ffprobe 中的 index）。
    """
    cmd: List[str] = [
        ffmpeg or _ffmpeg_path(),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-i",
        src,
    ]

    keep = [int(i) for i in (keep_sub_indices or [])]
    if keep:
        # 显式映射：真实视频流 + 音频流 + 需保留的字幕流
        cmd += ["-map", "0:V?", "-map", "0:a?"]
        for index in keep:
            cmd += ["-map", f"0:{index}"]
    else:
        # 映射全部，再排除字幕流
        cmd += ["-map", "0", "-map", "-0:s"]

    cmd += ["-c", "copy"]
    if copy_metadata:
        cmd += ["-map_metadata", "0", "-map_chapters", "0"]
    if _is_mp4_family(dst):
        cmd += ["-movflags", "+faststart"]
    cmd.append(dst)
    return cmd


def _video_encode_args(
    encoder: str,
    crf: int,
    preset: str,
    width: int = 0,
    height: int = 0,
    dst: str = "",
) -> List[str]:
    args = ["-c:v", encoder, "-crf", str(int(crf))]
    if preset:
        args += ["-preset", preset]
    if width and height and width % 2 == 0 and height % 2 == 0:
        args += ["-pix_fmt", "yuv420p"]
    if encoder == "libx265":
        args += ["-x265-params", "log-level=error"]
        if _is_mp4_family(dst):
            args += ["-tag:v", "hvc1"]
    return args


def build_hard_remove_command(
    src: str,
    dst: str,
    info: MediaInfo,
    style: str,
    region: Optional[Region] = None,
    height_ratio: Optional[float] = None,
    crf: int = 23,
    preset: str = "medium",
    encoder: str = "libx264",
    blur_radius: Optional[int] = None,
    ffmpeg: Optional[str] = None,
) -> List[str]:
    """构建「硬字幕去除」命令（需要重新编码视频）。

    style:
      crop_bottom  裁掉画面底部一条（height_ratio 为占比，默认 0.18）
      crop_top     裁掉画面顶部一条
      blur         在字幕区域打模糊遮罩
      delogo       用 delogo 滤镜按周围像素插值抹掉字幕
    """
    if style not in HARD_STYLES:
        raise ValueError(f"未知的硬字幕处理方式：{style}")
    if not info.has_video_dimensions:
        raise ValueError("该文件没有可识别的视频画面，无法做硬字幕处理")

    width, height = info.width, info.height
    cmd: List[str] = [ffmpeg or _ffmpeg_path(), "-hide_banner", "-nostdin", "-y", "-i", src]

    if style in ("crop_bottom", "crop_top"):
        band = int(round(height * (height_ratio if height_ratio is not None else 0.18)))
        band = max(2, min(band, height - 2))
        out_h = height - band
        out_w = width
        if out_h % 2:
            out_h -= 1
        if out_w % 2:
            out_w -= 1
        offset = "0" if style == "crop_bottom" else str(height - out_h)
        cmd += ["-vf", f"crop={out_w}:{out_h}:0:{offset}"]
        cmd += ["-map", "0:V:0", "-map", "0:a?"]
        cmd += _video_encode_args(encoder, crf, preset, out_w, out_h, dst)
    else:
        if region is None:
            raise ValueError("模糊 / delogo 模式必须先指定字幕区域")
        box = region.clamped(width, height)
        if style == "blur":
            box = box.even()
            radius = int(blur_radius or max(4, round(min(box.w, box.h) / 10)))
            filters = (
                f"[0:V]split=2[base][part];"
                f"[part]crop={box.w}:{box.h}:{box.x}:{box.y},"
                f"boxblur=luma_radius={radius}:luma_power=2[blurred];"
                f"[base][blurred]overlay={box.x}:{box.y}[v]"
            )
            cmd += ["-filter_complex", filters, "-map", "[v]", "-map", "0:a?"]
        else:
            # delogo 要求区域完全在画面内部，四周留 1 像素
            x = max(1, min(box.x, width - 2))
            y = max(1, min(box.y, height - 2))
            w = max(2, min(box.w, width - x - 1))
            h = max(2, min(box.h, height - y - 1))
            cmd += ["-vf", f"delogo=x={x}:y={y}:w={w}:h={h}:show=0"]
            cmd += ["-map", "0:V:0", "-map", "0:a?"]
        cmd += _video_encode_args(encoder, crf, preset, 0, 0, dst)

    cmd += ["-c:a", "copy", "-map_metadata", "0", "-map_chapters", "0"]
    if _is_mp4_family(dst):
        cmd += ["-movflags", "+faststart"]
    cmd.append(dst)
    return cmd


# --------------------------------------------------------------------------- #
# 执行
# --------------------------------------------------------------------------- #
_OUT_TIME_RE = re.compile(r"out_time=(\d+):(\d+):(\d+(?:\.\d+)?)")


def parse_out_time(text: str) -> float:
    """解析 ffmpeg -progress 输出中的时间戳（HH:MM:SS.micro）。"""
    match = _OUT_TIME_RE.search(text)
    if not match:
        return 0.0
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)


@dataclass
class RunResult:
    returncode: int = 0
    cancelled: bool = False
    seconds: float = 0.0
    log_tail: str = ""

    @property
    def ok(self) -> bool:
        return self.returncode == 0 and not self.cancelled


def run_command(
    cmd: Sequence[str],
    duration: float = 0.0,
    on_progress: Optional[Callable[[float, float], None]] = None,
    on_log: Optional[Callable[[str], None]] = None,
    cancel: Optional[threading.Event] = None,
) -> RunResult:
    """执行 ffmpeg 命令，解析 -progress 输出并回调进度。"""
    full_cmd = [cmd[0], "-progress", "pipe:1", "-nostats"] + list(cmd[1:])
    started = time.time()
    tail: deque = deque(maxlen=120)
    cancelled = False

    proc = subprocess.Popen(
        full_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        creationflags=_creation_flags(),
    )

    def drain_stderr() -> None:
        assert proc.stderr is not None
        for line in proc.stderr:
            line = line.rstrip()
            if not line:
                continue
            tail.append(line)
            if on_log:
                on_log(line)

    stderr_thread = threading.Thread(target=drain_stderr, daemon=True)
    stderr_thread.start()

    last_emit = 0.0
    assert proc.stdout is not None
    for raw in proc.stdout:
        line = raw.strip()
        if line.startswith("out_time="):
            seconds = parse_out_time(line)
            # 进度节流：每 0.4 秒最多回调一次
            now = time.time()
            if on_progress and (now - last_emit > 0.4 or seconds <= 0.05):
                last_emit = now
                fraction = min(0.999, seconds / duration) if duration > 0 else 0.0
                on_progress(fraction, seconds)
        elif line == "progress=end" and on_progress:
            on_progress(1.0, duration)
        if cancel is not None and cancel.is_set() and proc.poll() is None:
            cancelled = True
            proc.terminate()

    returncode = proc.wait()
    stderr_thread.join(timeout=2.0)
    if cancelled:
        returncode = -1

    elapsed = time.time() - started
    return RunResult(
        returncode=returncode,
        cancelled=cancelled,
        seconds=elapsed,
        log_tail="\n".join(list(tail)[-12:]),
    )


def run_quiet(cmd: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(
        list(cmd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=_creation_flags(),
    )


def extract_frame(src: str, dst_png: str, time_sec: float, out_width: int) -> bool:
    """抽一帧到 PNG，宽度缩放到 out_width（高度自动，保持比例）。"""
    out_width = max(2, int(out_width))
    cmd = [
        _ffmpeg_path(),
        "-hide_banner",
        "-nostdin",
        "-y",
        "-ss",
        f"{max(0.0, time_sec):.3f}",
        "-i",
        src,
        "-frames:v",
        "1",
        "-vf",
        f"scale={out_width}:-2",
        "-an",
        dst_png,
    ]
    completed = run_quiet(cmd)
    return completed.returncode == 0 and os.path.isfile(dst_png)


# --------------------------------------------------------------------------- #
# 展示用格式化
# --------------------------------------------------------------------------- #
def format_duration(seconds: float) -> str:
    seconds = max(0, int(round(seconds)))
    hours, remainder = divmod(seconds, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_size(num_bytes: float) -> str:
    units = ["B", "KB", "MB", "GB", "TB"]
    value = float(num_bytes)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.1f} {unit}"
        value /= 1024
    return f"{value:.1f} TB"


def join_command(cmd: Sequence[str]) -> str:
    """把命令拼成便于阅读 / 复制到终端的字符串。"""
    return " ".join(f'"{part}"' if (" " in part) else part for part in cmd)
