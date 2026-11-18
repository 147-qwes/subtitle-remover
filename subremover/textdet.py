"""逐帧文字检测（PP-OCRv4 DB 文本检测）。

为什么需要它：框选只能处理「字幕位置固定」的视频。滚动字幕、逐句换位、
弹幕式跳动的文字每帧位置都在变，固定区域必然漏字；反过来把区域放很大，
又会把画面主体一起抹掉。

这里的做法是每帧先在缩小图上跑一次文本检测，拿到该帧的文字框，再把这些框
交给 LaMa 重绘——"字走到哪就修到哪"，而且只修文字本身，画面里的人和场景不动。

模型（约 4.5 MB，PaddleOCR PP-OCRv4 DB 检测的 ONNX 导出）：
    python tools\\download_model.py --det   下载到 models\\ppocr_v4_det.onnx
    或设置环境变量 SUBTITLE_REMOVER_DET_MODEL 指向 .onnx 文件 / 所在目录
"""

from __future__ import annotations

import os
import sys
import time
from typing import List, Optional, Sequence, Tuple

from . import ffmpeg_tools as ft

DET_ENV = "SUBTITLE_REMOVER_DET_MODEL"
DET_FILENAME = "ppocr_v4_det.onnx"

# 官方模型的体积与校验值
DET_SIZE = 4745517
DET_SHA256 = "d2a7720d45a54257208b1e13e36a8479894cb74155a5efe29462512d42f49da9"

# 检测在缩小图上做：长边 960、宽高都取 32 的倍数（DB 网络的卷积下采样要求）
DET_LIMIT_SIDE = 960
DET_MEAN = (0.485, 0.456, 0.406)
DET_STD = (0.229, 0.224, 0.225)

# 概率图二值化阈值 / 每个连通域的平均置信度阈值
DET_THRESH = 0.30
BOX_THRESH = 0.55
MIN_AREA = 40          # 太小的连通域（噪点）直接丢
MIN_BOX_SIDE = 8       # 单边小于 8px 的框多半是噪点或一个笔画
MAX_BOXES = 16         # 一帧最多修这么多块，避免满屏文字把片子拖成几小时

# 检测框外扩：字幕的描边、发光、投影往往比字形本身大一圈。
# DB 检测出的框只包住字形"核心"（44px 的字，框高常常只有 21px），所以纵向要
# 给得比横向大得多，否则黑描边和字的上下缘会留在掩码外面，修完还剩一圈字影。
EXPAND_RATIO_X = 0.10
EXPAND_RATIO_Y = 0.70
EXPAND_MIN_X = 12
EXPAND_MIN_Y = 14


class DetUnavailable(RuntimeError):
    """缺少文字检测所需的依赖或模型。"""


# --------------------------------------------------------------------------- #
# 依赖
# --------------------------------------------------------------------------- #
def _load_numpy():
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover
        raise DetUnavailable("文字检测需要 numpy，请先执行：pip install -r requirements-inpaint.txt") from exc
    return np


def _load_ort():
    try:
        import onnxruntime as ort
    except Exception as exc:  # pragma: no cover
        raise DetUnavailable(
            "文字检测需要 onnxruntime，请先执行：pip install -r requirements-inpaint.txt"
        ) from exc
    return ort


def det_search_paths() -> List[str]:
    """按优先级列出可能的检测模型位置。"""
    candidates: List[str] = []
    env = os.environ.get(DET_ENV)
    if env:
        candidates.append(os.path.expanduser(env))

    here = os.path.dirname(os.path.abspath(__file__))
    project_root = os.path.dirname(here)
    candidates.append(os.path.join(project_root, "models", DET_FILENAME))

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(os.path.join(meipass, "models", DET_FILENAME))

    exe_dir = os.path.dirname(os.path.abspath(sys.executable))
    candidates.append(os.path.join(exe_dir, "models", DET_FILENAME))
    candidates.append(os.path.join(os.path.dirname(exe_dir), "models", DET_FILENAME))

    seen: set = set()
    unique: List[str] = []
    for path in candidates:
        key = os.path.normcase(os.path.abspath(path))
        if key in seen:
            continue
        seen.add(key)
        unique.append(os.path.abspath(path))
    return unique


def find_det_model() -> Optional[str]:
    for path in det_search_paths():
        candidate = path
        if os.path.isdir(candidate):
            candidate = os.path.join(candidate, DET_FILENAME)
        if candidate.lower().endswith(".onnx") and os.path.isfile(candidate):
            # 下载中断的模型会让检测结果乱跳，体积明显偏小就不认
            return candidate if os.path.getsize(candidate) >= DET_SIZE * 0.9 else None
    return None


def require_det_model() -> str:
    path = find_det_model()
    if not path:
        raise DetUnavailable(
            "未找到文字检测模型（ppocr_v4_det.onnx，约 4.5 MB）。\n"
            "解决办法：运行 python tools\\download_model.py --det 自动下载；\n"
            f"或设置环境变量 {DET_ENV} 指向模型文件 / 所在目录。"
        )
    return path


# --------------------------------------------------------------------------- #
# 纯计算（可单测，不需要模型）
# --------------------------------------------------------------------------- #
def resize_to_limit(height: int, width: int, limit: int = DET_LIMIT_SIDE, align: int = 32) -> Tuple[int, int]:
    """按长边限制缩放，并把宽高取成 align 的倍数。"""
    longest = max(1, max(height, width))
    scale = min(1.0, float(limit) / longest)
    out_h = int(round(height * scale / align)) * align
    out_w = int(round(width * scale / align)) * align
    return max(align, out_h), max(align, out_w)


def components(binary, min_area: int = MIN_AREA) -> List[Tuple[int, int, int, int]]:
    """返回二值图上每个连通域的包围盒 (x, y, w, h)。"""
    np = _load_numpy()
    height, width = binary.shape
    visited = np.zeros((height, width), dtype=bool)
    boxes: List[Tuple[int, int, int, int]] = []
    ys, xs = np.nonzero(binary)
    for start_y, start_x in zip(ys, xs):
        y0, x0 = int(start_y), int(start_x)
        if visited[y0, x0]:
            continue
        stack = [(y0, x0)]
        visited[y0, x0] = True
        min_x = max_x = x0
        min_y = max_y = y0
        count = 0
        while stack:
            cy, cx = stack.pop()
            count += 1
            if cx < min_x:
                min_x = cx
            elif cx > max_x:
                max_x = cx
            if cy < min_y:
                min_y = cy
            elif cy > max_y:
                max_y = cy
            for ny, nx in ((cy - 1, cx), (cy + 1, cx), (cy, cx - 1), (cy, cx + 1)):
                if 0 <= ny < height and 0 <= nx < width and binary[ny, nx] and not visited[ny, nx]:
                    visited[ny, nx] = True
                    stack.append((ny, nx))
        if count >= min_area:
            boxes.append((min_x, min_y, max_x - min_x + 1, max_y - min_y + 1))
    return boxes


def boxes_from_prob(prob, thresh: float = DET_THRESH, box_thresh: float = BOX_THRESH) -> List[Tuple[int, int, int, int]]:
    """把 DB 的概率图变成文字框：二值化 → 连通域 → 置信度过滤。"""
    np = _load_numpy()
    binary = prob > thresh
    result = []
    for x, y, w, h in components(binary):
        if w < MIN_BOX_SIDE and h < MIN_BOX_SIDE:
            continue
        score = float(prob[y : y + h, x : x + w][binary[y : y + h, x : x + w]].mean())
        if score < box_thresh:
            continue
        result.append((int(x), int(y), int(w), int(h)))
    return result


def expand_box(box: Tuple[int, int, int, int], width: int, height: int) -> ft.Region:
    """按比例外扩，把描边 / 发光 / 投影一起包住，并夹在画面内。"""
    x, y, w, h = box
    pad_x = max(EXPAND_MIN_X, int(w * EXPAND_RATIO_X))
    pad_y = max(EXPAND_MIN_Y, int(h * EXPAND_RATIO_Y))
    return ft.Region(x - pad_x, y - pad_y, w + 2 * pad_x, h + 2 * pad_y).clamped(width, height)


def rect_overlap_ratio(a: ft.Region, b: ft.Region) -> float:
    x0 = max(a.x, b.x)
    y0 = max(a.y, b.y)
    x1 = min(a.x + a.w, b.x + b.w)
    y1 = min(a.y + a.h, b.y + b.h)
    if x1 <= x0 or y1 <= y0:
        return 0.0
    inter = (x1 - x0) * (y1 - y0)
    return inter / float(min(a.w * a.h, b.w * b.h) or 1)


def merge_boxes(regions: Sequence[ft.Region], gap: int = 6) -> List[ft.Region]:
    """同一行被切开的两段文字合并成一个框，避免一块字修两次。"""
    result: List[ft.Region] = []
    for region in regions:
        current = region
        merged = True
        while merged:
            merged = False
            for index, other in enumerate(result):
                grown = ft.Region(other.x - gap, other.y - gap, other.w + 2 * gap, other.h + 2 * gap)
                if rect_overlap_ratio(current, grown) > 0.0:
                    x0 = min(current.x, other.x)
                    y0 = min(current.y, other.y)
                    x1 = max(current.x + current.w, other.x + other.w)
                    y1 = max(current.y + current.h, other.y + other.h)
                    current = ft.Region(x0, y0, x1 - x0, y1 - y0)
                    result.pop(index)
                    merged = True
                    break
        result.append(current)
    return result


def limit_boxes(regions: Sequence[ft.Region], scope: Optional[Sequence[ft.Region]] = None) -> List[ft.Region]:
    """按面积保留最大的若干块；给了 scope 就只留下落在 scope 里的。"""
    items = list(regions)
    if scope:
        kept = []
        for region in items:
            center_x = region.x + region.w / 2.0
            center_y = region.y + region.h / 2.0
            if any(
                s.x <= center_x <= s.x + s.w and s.y <= center_y <= s.y + s.h for s in scope
            ):
                kept.append(region)
        items = kept
    items.sort(key=lambda r: r.w * r.h, reverse=True)
    return items[:MAX_BOXES]


# --------------------------------------------------------------------------- #
# 模型推理
# --------------------------------------------------------------------------- #
def _bilinear(image, out_h: int, out_w: int):
    """双线性缩放（HxWx3 uint8 / float32），不依赖 OpenCV。"""
    np = _load_numpy()
    array = np.asarray(image, dtype=np.float32)
    if array.ndim == 3:
        channels = [_bilinear(array[..., c], out_h, out_w) for c in range(array.shape[2])]
        return np.stack(channels, axis=-1)
    in_h, in_w = array.shape
    if in_h == out_h and in_w == out_w:
        return array.copy()
    src_y = np.linspace(0, in_h - 1, out_h, dtype=np.float32)
    src_x = np.linspace(0, in_w - 1, out_w, dtype=np.float32)
    y0 = np.floor(src_y).astype(np.int32)
    x0 = np.floor(src_x).astype(np.int32)
    y1 = np.minimum(y0 + 1, in_h - 1)
    x1 = np.minimum(x0 + 1, in_w - 1)
    wy = (src_y - y0)[:, None]
    wx = (src_x - x0)[None, :]
    top = array[y0][:, x0] * (1 - wx) + array[y0][:, x1] * wx
    bottom = array[y1][:, x0] * (1 - wx) + array[y1][:, x1] * wx
    return top * (1 - wy) + bottom * wy


class TextDetector:
    """PP-OCRv4 DB 检测器：输入一帧，输出该帧的文字框（原图像素坐标）。"""

    def __init__(self, model_path: Optional[str] = None, device: str = "auto", threads: Optional[int] = None) -> None:
        self._np = _load_numpy()
        ort = _load_ort()
        from .inpaint import _prepare_cuda_dlls, provider_candidates  # 复用后端准备逻辑

        self.model_path = model_path or require_det_model()
        self.requested_device = device
        if device != "cpu":
            _prepare_cuda_dlls()

        started = time.time()
        available = list(ort.get_available_providers())
        self.session = None
        self.providers: List[str] = []
        last_error: Optional[Exception] = None
        for providers in provider_candidates(device, available):
            options = ort.SessionOptions()
            options.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
            try:
                cpu_count = os.cpu_count() or 4
                options.intra_op_num_threads = threads or max(1, min(4, cpu_count))
            except Exception:
                pass
            try:
                self.session = ort.InferenceSession(
                    self.model_path, sess_options=options, providers=providers
                )
            except Exception as exc:
                last_error = exc
                continue
            self.providers = list(self.session.get_providers())
            break
        if self.session is None:
            raise DetUnavailable(f"文字检测会话创建失败：{last_error}")

        self._input = self.session.get_inputs()[0].name
        self._output = self.session.get_outputs()[0].name
        self.load_seconds = time.time() - started

    # ---------------------------------------------------------------- 推理
    def detect(
        self,
        frame_rgb,
        scope: Optional[Sequence[ft.Region]] = None,
    ) -> List[ft.Region]:
        """返回该帧要重绘的区域（已外扩、已合并、按面积截断）。"""
        np = self._np
        height, width = frame_rgb.shape[:2]
        net_h, net_w = resize_to_limit(height, width)

        small = _bilinear(frame_rgb, net_h, net_w) / 255.0
        mean = np.array(DET_MEAN, dtype=np.float32)
        std = np.array(DET_STD, dtype=np.float32)
        small = (small - mean) / std
        tensor = np.ascontiguousarray(np.transpose(small, (2, 0, 1))[None, ...], dtype=np.float32)

        outputs = self.session.run([self._output], {self._input: tensor})
        prob = np.asarray(outputs[0], dtype=np.float32)
        while prob.ndim > 2:
            prob = prob[0]
        if prob.shape != (net_h, net_w):
            prob = _bilinear(prob, net_h, net_w)

        boxes = boxes_from_prob(prob)
        scale_x = width / float(net_w)
        scale_y = height / float(net_h)
        regions = []
        for x, y, w, h in boxes:
            regions.append(
                expand_box(
                    (
                        int(x * scale_x),
                        int(y * scale_y),
                        max(1, int(round(w * scale_x))),
                        max(1, int(round(h * scale_y))),
                    ),
                    width,
                    height,
                )
            )
        return limit_boxes(merge_boxes(regions), scope)
