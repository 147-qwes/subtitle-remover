"""AI 修复（LaMa）：把水印 / 字幕所在区域的画面真正「补」出来。

与裁切、模糊、delogo 不同，这里不是遮盖，而是让模型参考区域四周的画面内容
重新生成被文字压住的像素，因此不留遮挡带，人物和场景细节尽量保留。

依赖（可选安装）：
    pip install onnxruntime numpy
模型（约 208 MB，二选一）：
    python tools\\download_model.py 下载到 models\\lama_fp32.onnx
    或设置环境变量 SUBTITLE_REMOVER_MODEL 指向已有的 lama_fp32.onnx
"""

from __future__ import annotations

import os
import subprocess
import sys
import threading
import time
from collections import deque
from typing import Callable, List, Optional, Sequence, Tuple

from . import ffmpeg_tools as ft

MODEL_ENV = "SUBTITLE_REMOVER_MODEL"
MODEL_FILENAME = "lama_fp32.onnx"

# 官方镜像的校验值（208,044,816 字节）
MODEL_SIZE = 208044816
MODEL_SHA256 = "1faef5301d78db7dda502fe59966957ec4b79dd64e16f03ed96913c7a4eb68d6"

# 这个 ONNX 导出固定 512×512 输入，推理前后做缩放
NET_SIZE = 512

ALIGN = 8
FEATHER_PX = 3


class InpaintUnavailable(RuntimeError):
    """缺少 AI 修复所需的依赖或模型。"""


# --------------------------------------------------------------------------- #
# 依赖探测
# --------------------------------------------------------------------------- #
def _load_numpy():
    try:
        import numpy as np  # noqa: WPS433 - 延迟导入，缺少时给出友好提示
    except Exception as exc:  # pragma: no cover - 取决于运行环境
        raise InpaintUnavailable(
            "AI 修复需要 numpy，请先执行：pip install -r requirements-inpaint.txt"
        ) from exc
    return np


def _load_ort():
    try:
        import onnxruntime as ort  # noqa: WPS433
    except Exception as exc:  # pragma: no cover
        raise InpaintUnavailable(
            "AI 修复需要 onnxruntime，请先执行：pip install -r requirements-inpaint.txt"
        ) from exc
    return ort


_CUDA_DLL_DIRS_ADDED = False


def _prepare_cuda_dlls() -> None:
    """把 pip 装进来的 CUDA 运行库目录挂到 DLL 搜索路径（Windows）。"""
    global _CUDA_DLL_DIRS_ADDED
    if _CUDA_DLL_DIRS_ADDED or os.name != "nt":
        return
    _CUDA_DLL_DIRS_ADDED = True
    try:
        import glob

        import onnxruntime  # noqa: WPS433

        site = os.path.dirname(os.path.dirname(os.path.abspath(onnxruntime.__file__)))
        for folder in glob.glob(os.path.join(site, "nvidia", "*", "bin")):
            try:
                os.add_dll_directory(folder)
            except Exception:
                pass
            os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
    except Exception:
        pass


def model_search_paths() -> List[str]:
    """按优先级列出可能的模型位置。"""
    candidates: List[str] = []
    env = os.environ.get(MODEL_ENV)
    if env:
        candidates.append(os.path.expanduser(env))

    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(here)
    candidates.append(os.path.join(project_root, "models", MODEL_FILENAME))

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "models", MODEL_FILENAME))

    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    candidates.append(os.path.join(exe_dir, "models", MODEL_FILENAME))
    candidates.append(os.path.join(os.path.dirname(exe_dir), "models", MODEL_FILENAME))

    seen: set = set()
    unique: List[str] = []
    for path in candidates:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        unique.append(os.path.abspath(path))
    return unique


def _is_usable_model(path: str) -> bool:
    """文件存在且没有被截断（下载中断的模型会让推理结果一片糊）。"""
    try:
        return os.path.getsize(path) >= MODEL_SIZE * 0.95
    except OSError:
        return False


def find_model_path() -> Optional[str]:
    for path in model_search_paths():
        if path.lower().endswith(".onnx"):
            if _is_usable_model(path):
                return path
        elif os.path.isdir(path):
            candidate = os.path.join(path, MODEL_FILENAME)
            if _is_usable_model(candidate):
                return candidate
    return None


def require_model() -> str:
    path = find_model_path()
    if not path:
        raise InpaintUnavailable(
            "未找到 AI 修复模型（lama_fp32.onnx，约 208 MB）。\n"
            "解决办法：运行 python tools\\download_model.py 自动下载；\n"
            f"或设置环境变量 {MODEL_ENV} 指向模型文件 / 所在目录。"
        )
    return path


# --------------------------------------------------------------------------- #
# 纯计算（可单测，不需要模型）
# --------------------------------------------------------------------------- #
def expand_region(
    region: ft.Region,
    width: int,
    height: int,
    margin_ratio: float = 0.25,
    min_margin: int = 12,
) -> ft.Region:
    """给修复区域加一圈参考上下文，并夹到画面内部。"""
    region = region.clamped(width, height)
    margin_x = max(min_margin, int(region.w * margin_ratio))
    margin_y = max(min_margin, int(region.h * margin_ratio))

    x0 = max(0, region.x - margin_x)
    y0 = max(0, region.y - margin_y)
    x1 = min(width, region.x + region.w + margin_x)
    y1 = min(height, region.y + region.h + margin_y)
    return ft.Region(x0, y0, x1 - x0, y1 - y0)


def inner_box(region: ft.Region, patch_x: int, patch_y: int, width: int, height: int) -> Tuple[int, int, int, int]:
    """被遮区域在参考块内的坐标（x0, y0, x1, y1），夹在块内部。"""
    x0 = max(0, region.x - patch_x)
    y0 = max(0, region.y - patch_y)
    x1 = min(width, region.x + region.w - patch_x)
    y1 = min(height, region.y + region.h - patch_y)
    return x0, y0, max(x0, x1), max(y0, y1)


def context_box(
    region: ft.Region,
    width: int,
    height: int,
    margin_ratio: float = 0.25,
    min_margin: int = 12,
) -> Tuple[ft.Region, Tuple[int, int, int, int]]:
    """取修复块：返回(画面内的裁剪框, 四边需要镜像补齐的像素 (上,左,下,右))。

    字幕和水印常常贴着画面边缘，裁剪框被画面边界夹住时，那一侧就没有上下文。
    这里把缺口记录下来，后续用镜像补边，保证模型总能看到真实内容。
    """
    region = region.clamped(width, height)
    margin_x = max(min_margin, int(region.w * margin_ratio))
    margin_y = max(min_margin, int(region.h * margin_ratio))

    want_x0, want_y0 = region.x - margin_x, region.y - margin_y
    want_x1, want_y1 = region.x + region.w + margin_x, region.y + region.h + margin_y

    x0, y0 = max(0, want_x0), max(0, want_y0)
    x1, y1 = min(width, want_x1), min(height, want_y1)
    pad = (max(0, y0 - want_y0), max(0, x0 - want_x0), max(0, want_y1 - y1), max(0, want_x1 - x1))
    return ft.Region(x0, y0, x1 - x0, y1 - y0), pad


def rects_overlap(a: ft.Region, b: ft.Region) -> bool:
    return not (
        a.x + a.w <= b.x or b.x + b.w <= a.x or a.y + a.h <= b.y or b.y + b.h <= a.y
    )


def merge_into_list(regions: Sequence[ft.Region], new_region: ft.Region) -> List[ft.Region]:
    """把新区域并入列表；与已有区域重叠时合并包围盒。"""
    result = list(regions)
    current = new_region
    index = 0
    while index < len(result):
        if rects_overlap(current, result[index]):
            other = result[index]
            x0 = min(current.x, other.x)
            y0 = min(current.y, other.y)
            x1 = max(current.x + current.w, other.x + other.w)
            y1 = max(current.y + current.h, other.y + other.h)
            current = ft.Region(x0, y0, x1 - x0, y1 - y0)
            result.pop(index)
            index = 0
            continue
        index += 1
    result.append(current)
    return result


def plan_tiles(
    region: ft.Region,
    width: int,
    height: int,
    tile: int = 440,
    margin: int = 36,
) -> List[Tuple[ft.Region, Tuple[int, int, int, int], "np.ndarray", "np.ndarray"]]:
    """把修复区域拆成若干块，每块连同上下文都不超过模型 512 的输入。

    返回 [(画面内裁剪框, 镜像补边量, 掩码, 羽化 alpha)]。拆块是为了让原始像素
    1:1 参与推理——整条字幕带缩到 512 会把细节抹平。
    """
    np = _load_numpy()
    region = region.clamped(width, height)
    jobs = []
    for core_y in range(region.y, region.y + region.h, tile):
        core_h = min(tile, region.y + region.h - core_y)
        for core_x in range(region.x, region.x + region.w, tile):
            core_w = min(tile, region.x + region.w - core_x)
            core = ft.Region(core_x, core_y, core_w, core_h)

            want = ft.Region(core.x - margin, core.y - margin, core.w + 2 * margin, core.h + 2 * margin)
            x0 = max(0, want.x)
            y0 = max(0, want.y)
            x1 = min(width, want.x + want.w)
            y1 = min(height, want.y + want.h)
            box = ft.Region(x0, y0, x1 - x0, y1 - y0)
            pad = (
                max(0, y0 - want.y),
                max(0, x0 - want.x),
                max(0, want.y + want.h - y1),
                max(0, want.x + want.w - x1),
            )

            pad_h, pad_w = box.h + pad[0] + pad[2], box.w + pad[1] + pad[3]
            mask = np.zeros((pad_h, pad_w), dtype=np.uint8)
            top = pad[0] + (core.y - box.y)
            left = pad[1] + (core.x - box.x)
            mask[top : top + core.h, left : left + core.w] = 1
            if int(mask.sum()) == 0:
                continue
            jobs.append((box, pad, mask, feather_alpha(mask)))
    return jobs


def pad_to_multiple(value: int, align: int = ALIGN) -> int:
    if align <= 1:
        return value
    remainder = value % align
    return value if remainder == 0 else value + (align - remainder)


def resize_bilinear(image, out_h: int, out_w: int):
    """双线性缩放（HxWx3 float32 或 HxW），不依赖 OpenCV。"""
    np = _load_numpy()
    array = np.asarray(image, dtype=np.float32)
    if array.ndim == 3:
        channels = [resize_bilinear(array[..., c], out_h, out_w) for c in range(array.shape[2])]
        return np.stack(channels, axis=-1)
    if array.ndim == 2:
        in_h, in_w = array.shape
        if in_h == out_h and in_w == out_w:
            return array.copy()
        src_y = np.linspace(0, in_h - 1, out_h, dtype=np.float32)
        src_x = np.linspace(0, in_w - 1, out_w, dtype=np.float32)
        y0 = np.floor(src_y).astype(np.int32)
        x0 = np.floor(src_x).astype(np.int32)
        y1 = np.minimum(y0 + 1, in_h - 1)
        x1 = np.minimum(x0 + 1, in_w - 1)
        wy = (src_y - y0)[:, None, None]
        wx = (src_x - x0)[None, :, None]
        if array.ndim == 2:
            wy = wy[..., 0]
            wx = wx[..., 0]
        top = array[y0][:, x0] * (1 - wx) + array[y0][:, x1] * wx
        bottom = array[y1][:, x0] * (1 - wx) + array[y1][:, x1] * wx
        return top * (1 - wy) + bottom * wy
    return array


# --------------------------------------------------------------------------- #
# 模型推理
# --------------------------------------------------------------------------- #
class LamaInpainter:
    """LaMa 局部重绘。只对区域小块推理，速度远快于整帧。"""

    def __init__(
        self,
        model_path: Optional[str] = None,
        threads: Optional[int] = None,
        device: str = "auto",
    ) -> None:
        self._np = _load_numpy()
        ort = _load_ort()
        self.model_path = model_path or require_model()

        options = ort.SessionOptions()
        options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        try:
            cpu_count = os.cpu_count() or 4
            options.intra_op_num_threads = threads or max(1, min(8, cpu_count))
        except Exception:
            pass

        if device != "cpu":
            _prepare_cuda_dlls()

        started = time.time()
        available = list(ort.get_available_providers())
        if device == "cpu":
            providers = ["CPUExecutionProvider"]
        elif device in ("cuda", "gpu"):
            providers = ["CUDAExecutionProvider"]
        else:  # auto：有 GPU 就用 GPU，失败再降级
            providers = [
                name
                for name in ("CUDAExecutionProvider", "DmlExecutionProvider")
                if name in available
            ]
        if "CPUExecutionProvider" not in providers:
            providers.append("CPUExecutionProvider")
        providers = [p for p in providers if p in available] or ["CPUExecutionProvider"]

        self.session = ort.InferenceSession(
            self.model_path, sess_options=options, providers=providers
        )
        self.providers = list(self.session.get_providers())
        inputs = self.session.get_inputs()
        self._image_input = inputs[0].name
        self._mask_input = inputs[1].name if len(inputs) > 1 else "mask"
        self._output = self.session.get_outputs()[0].name
        self.load_seconds = time.time() - started

    # ---------------------------------------------------------------- 推理
    def inpaint_patch(self, patch_rgb, mask_01):
        """patch_rgb: HxWx3 uint8；mask_01: HxW 0/1。返回同尺寸的修复结果。

        模型输入固定 512×512：块按比例铺进画布，推理后再缩回原尺寸。
        调用方（plan_tiles）会把区域拆成接近 512 的小块，避免大幅缩放丢细节。
        """
        np = self._np
        height, width = mask_01.shape[:2]

        if height != NET_SIZE or width != NET_SIZE:
            image_small = resize_bilinear(patch_rgb, NET_SIZE, NET_SIZE) / 255.0
            mask_small = (resize_bilinear(mask_01, NET_SIZE, NET_SIZE) >= 0.5).astype(np.float32)
        else:
            image_small = patch_rgb.astype(np.float32) / 255.0
            mask_small = mask_01.astype(np.float32)

        image = np.ascontiguousarray(np.transpose(image_small, (2, 0, 1))[None, ...], dtype=np.float32)
        mask_tensor = np.ascontiguousarray(mask_small[None, None, ...], dtype=np.float32)

        outputs = self.session.run(
            [self._output],
            {self._image_input: image, self._mask_input: mask_tensor},
        )
        filled_small = outputs[0][0]
        # 输入是 0~1，这个导出模型的输出已经是 0~255
        filled_small = np.transpose(np.clip(filled_small, 0.0, 255.0), (1, 2, 0))
        if height != NET_SIZE or width != NET_SIZE:
            filled = resize_bilinear(filled_small, height, width)
        else:
            filled = filled_small
        return np.clip(filled + 0.5, 0, 255).astype(np.uint8)


def feather_alpha(mask_01, radius: int = FEATHER_PX):
    """把 0/1 掩码做成边缘渐变的 alpha，避免拼接缝。"""
    np = _load_numpy()
    alpha = mask_01.astype(np.float32)
    if radius <= 0:
        return alpha

    # 可分离盒式模糊（累积和实现），边缘渐隐
    kernel = radius * 2 + 1
    for axis in (0, 1):
        pad_width = [(0, 0), (0, 0)]
        pad_width[axis] = (radius + 1, radius)
        padded = np.pad(alpha, pad_width, mode="edge")
        cum = np.cumsum(padded, axis=axis)
        if axis == 0:
            alpha = (cum[kernel:, :] - cum[:-kernel, :]) / kernel
        else:
            alpha = (cum[:, kernel:] - cum[:, :-kernel]) / kernel
    return np.clip(alpha, 0.0, 1.0)


# --------------------------------------------------------------------------- #
# 视频管道
# --------------------------------------------------------------------------- #
def _creation_flags() -> int:
    if os.name == "nt":
        return getattr(subprocess, "CREATE_NO_WINDOW", 0)
    return 0


def _read_exact(stream, size: int) -> Optional[bytes]:
    chunks = []
    remaining = size
    while remaining > 0:
        chunk = stream.read(remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    if remaining >= size:
        return None
    data = b"".join(chunks)
    return data if len(data) == size else None


def estimate_frames(info: ft.MediaInfo) -> int:
    if info.duration <= 0:
        return 0
    return max(1, int(round(info.duration * info.frame_rate)))


def process_video(
    src: str,
    dst: str,
    info: ft.MediaInfo,
    regions: Sequence[ft.Region],
    crf: int = 20,
    preset: str = "medium",
    encoder: str = "libx264",
    on_progress: Optional[Callable[[float, float], None]] = None,
    on_log: Optional[Callable[[str], None]] = None,
    cancel: Optional[threading.Event] = None,
    inpainter: Optional[LamaInpainter] = None,
    model_path: Optional[str] = None,
    device: str = "auto",
) -> ft.RunResult:
    """逐帧修复画面中的文字区域，再重新编码成视频。"""
    np = _load_numpy()
    width, height = info.width, info.height
    if not (width and height):
        raise ValueError("该文件没有可识别的视频画面")
    if not regions:
        raise ValueError("AI 修复需要至少一个文字区域")

    ffmpeg, _ffprobe = ft.require_binaries()
    actual_regions = [r.clamped(width, height).even() for r in regions]
    fps = info.frame_rate

    started = time.time()
    tail: deque = deque(maxlen=80)
    cancelled = False

    def log(text: str) -> None:
        if on_log:
            on_log(text)

    if inpainter is None:
        inpainter = LamaInpainter(model_path=model_path, device=device)
    log(f"AI 修复模型：{inpainter.model_path}（加载 {inpainter.load_seconds:.1f}s）")
    log(f"推理后端：{', '.join(inpainter.providers)}")

    # 预计算每个区域的修复块：区域大就拆块，保证每块原始像素 1:1 进模型
    jobs = []
    for region in actual_regions:
        tiles = plan_tiles(region, width, height)
        log(
            f"修复区域：x={region.x} y={region.y} w={region.w} h={region.h}"
            f"（拆成 {len(tiles)} 块）"
        )
        jobs.extend(tiles)
    if not jobs:
        raise ValueError("文字区域为空，请重新框选")

    decoder_cmd = [
        ffmpeg,
        "-v",
        "error",
        "-nostdin",
        "-i",
        src,
        "-map",
        "0:v:0",
        "-fps_mode",
        "passthrough",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-",
    ]
    encoder_cmd = [
        ffmpeg,
        "-v",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        f"{fps:.6f}",
        "-i",
        "-",
        "-i",
        src,
        "-map",
        "0:v:0",
        "-map",
        "1:a?",
        "-c:v",
        encoder,
        "-crf",
        str(int(crf)),
        "-preset",
        preset,
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-shortest",
    ]
    if os.path.splitext(dst)[1].lower() in (".mp4", ".m4v", ".mov"):
        encoder_cmd += ["-movflags", "+faststart"]
    encoder_cmd.append(dst)

    log("命令: " + ft.join_command(decoder_cmd))
    log("命令: " + ft.join_command(encoder_cmd))

    decoder = subprocess.Popen(
        decoder_cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        stdin=subprocess.DEVNULL,
        creationflags=_creation_flags(),
    )
    encoder = subprocess.Popen(
        encoder_cmd,
        stdin=subprocess.PIPE,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=_creation_flags(),
    )

    def drain(proc, name: str) -> None:
        if proc.stderr is None:
            return
        for raw in proc.stderr:
            line = raw.decode("utf-8", "replace").rstrip()
            if not line:
                continue
            tail.append(f"[{name}] {line}")
            if on_log:
                on_log(f"[{name}] {line}")

    for proc, name in ((decoder, "decode"), (encoder, "encode")):
        threading.Thread(target=drain, args=(proc, name), daemon=True).start()

    frame_size = width * height * 3
    total = estimate_frames(info)
    processed = 0
    last_emit = 0.0
    encode_ok = True

    try:
        assert decoder.stdout is not None and encoder.stdin is not None
        while True:
            if cancel is not None and cancel.is_set():
                cancelled = True
                break
            buffer = _read_exact(decoder.stdout, frame_size)
            if not buffer:
                break
            frame = np.frombuffer(buffer, dtype=np.uint8).reshape(height, width, 3).copy()

            for box, pad, mask, alpha in jobs:
                patch = frame[box.y : box.y + box.h, box.x : box.x + box.w]
                if any(pad):
                    patch = np.pad(
                        patch,
                        ((pad[0], pad[2]), (pad[1], pad[3]), (0, 0)),
                        mode="symmetric",
                    )
                filled = inpainter.inpaint_patch(patch, mask)
                blended = filled.astype(np.float32) * alpha[..., None] + patch.astype(
                    np.float32
                ) * (1.0 - alpha[..., None])
                blended = np.clip(blended + 0.5, 0, 255).astype(np.uint8)
                blended = blended[pad[0] : pad[0] + box.h, pad[1] : pad[1] + box.w]
                frame[box.y : box.y + box.h, box.x : box.x + box.w] = blended

            encoder.stdin.write(frame.tobytes())
            processed += 1

            now = time.time()
            if on_progress and (now - last_emit > 0.4 or processed <= 1):
                last_emit = now
                fraction = min(0.999, processed / total) if total else 0.0
                on_progress(fraction, processed / fps if fps else 0.0)
    except BrokenPipeError:
        encode_ok = False
        tail.append("编码进程提前退出（可能是磁盘空间或编码参数问题）")
    finally:
        try:
            if encoder.stdin:
                encoder.stdin.close()
        except Exception:
            pass
        try:
            if decoder.stdout:
                decoder.stdout.close()
        except Exception:
            pass
        if cancelled:
            for proc in (decoder, encoder):
                if proc.poll() is None:
                    proc.terminate()
        decoder_rc = decoder.wait()
        encoder_rc = encoder.wait()

    seconds = time.time() - started
    if cancelled:
        if os.path.exists(dst):
            try:
                os.remove(dst)
            except OSError:
                pass
        return ft.RunResult(
            returncode=-1, cancelled=True, seconds=seconds, log_tail="\n".join(list(tail)[-10:])
        )

    if processed == 0:
        return ft.RunResult(
            returncode=decoder_rc or 1,
            cancelled=False,
            seconds=seconds,
            log_tail="没有从源视频读到任何画面\n" + "\n".join(list(tail)[-10:]),
        )

    if encoder_rc != 0 or not encode_ok:
        if os.path.exists(dst):
            try:
                os.remove(dst)
            except OSError:
                pass
        return ft.RunResult(
            returncode=encoder_rc or 1,
            cancelled=False,
            seconds=seconds,
            log_tail="\n".join(list(tail)[-10:]),
        )

    log(f"完成：{processed} 帧，用时 {seconds:.1f}s（{processed / seconds:.2f} 帧/秒）")
    if on_progress:
        on_progress(1.0, info.duration)
    return ft.RunResult(returncode=0, cancelled=False, seconds=seconds, log_tail="")
