"""笔画掩码与修复块规划：动态字幕只重绘文字笔画本身。

从 inpaint.py 拆出来的独立模块，避免单文件过大；对外仍通过
``subremover.inpaint`` 暴露同样的名字，旧代码和测试不用改。
"""

from __future__ import annotations

import os
from typing import Callable, List, Sequence, Tuple

from . import ffmpeg_tools as ft

FEATHER_PX = 3

# ---- 像素级笔画修复（动态字幕默认走的路线）-------------------------------- #
# 只重绘「和周围背景不一样」的像素：文字笔画、描边、字下面的影子；背景的折线、
# 纹理一律不动。但只修笔画时洞里没有背景可参考，模型会瞎补，所以把修复块上下
# 各扩一块画面当上下文，让它照着旁边的结构把洞补出来。
STROKE_BLUR_DIV = 4       # 背景估计的模糊半径 = 字高的四分之一（窗口太大会把字本身当背景）
STROKE_MAX_BLUR = 22      # 模糊半径上限：检测框套得比字大时，别把整块字都糊进背景
STROKE_HIGH = 60          # 强响应：笔画本体
STROKE_LOW = 22           # 弱响应：描边和字的影子（只在强响应附近才保留）
STROKE_GROW = 9           # 弱响应允许的离笔画距离
STROKE_DILATE = 4         # 最终掩码再外扩，吃掉抗锯齿边缘
STROKE_ADAPT_MIN_HIGH = 14  # 低反差画面：强响应阈值下限（白卡片上的灰字也能成掩码）
STROKE_ADAPT_MIN_LOW = 6    # 低反差画面：弱响应阈值下限
STROKE_FLAT_STD = 14      # 背景亮度标准差低于这个才算「平坦背景」，才允许压低阈值
STROKE_EDGE_STRIP = 6     # 贴画面边界的字：边缘这条补进掩码，否则会留一小截残字
STROKE_EDGE_BAND = 12     # 贴边时按「和笔画同色」往外接的最大距离
STROKE_EDGE_TRIGGER = 40  # 检测框离边界小于这个距离，就把掩码往边界接
STROKE_COLOR_TOL = 48     # 同色判定：RGB 欧氏距离小于这个（0-255）
STROKE_CTX_Y = 1.2        # 修复块上下各扩 1.2 倍字高当上下文
STROKE_CTX_X = 56         # 左右各扩 56 像素
STROKE_TILE_W = 440       # 横向切块宽度：块 + 两侧上下文 ≈ 512，像素 1:1 进模型


def _load_numpy():
    try:
        import numpy as np  # noqa: WPS433 - 延迟导入，缺少时给出友好提示
    except Exception as exc:  # pragma: no cover - 取决于运行环境
        raise RuntimeError(
            "笔画掩码需要 numpy，请先执行：pip install -r requirements-inpaint.txt"
        ) from exc
    return np


def box_blur(array, radius: int):
    """积分图实现的盒式模糊（边缘按边缘像素延拓），用来估计「周围背景」。"""
    np = _load_numpy()
    radius = int(radius)
    padded = np.pad(array.astype(np.float64), ((radius + 1, radius), (radius + 1, radius)), mode="edge")
    cum = padded.cumsum(0).cumsum(1)
    cum = np.pad(cum, ((1, 0), (1, 0)))
    k = 2 * radius + 1
    h, w = array.shape
    total = cum[k : k + h, k : k + w] - cum[0:h, k : k + w] - cum[k : k + h, 0:w] + cum[0:h, 0:w]
    return (total / float(k * k)).astype(np.float32)


class FrameStrokeMask:
    """从整帧亮度算「文字像素」：和周围背景差异大的那些点。

    亮度先做大半径模糊当背景估计，再和原亮度相减：白色字、黑描边、字下的影子
    都会冒出很大的差值，而背景本身是一条条平滑渐变，差值接近 0。
    """

    def __init__(self, frame) -> None:
        np = _load_numpy()
        self._np = np
        self.rgb = frame
        self.lum = frame.astype(np.float32) @ np.array([0.299, 0.587, 0.114], dtype=np.float32)
        self._blurred = {}

    def _background(self, radius: int):
        if radius not in self._blurred:
            self._blurred[radius] = box_blur(self.lum, radius)
        return self._blurred[radius]

    def strokes(self, region: ft.Region):
        """返回 region 大小的 0/1 笔画掩码（含描边和影子）。"""
        np = self._np
        region = region.clamped(self.lum.shape[1], self.lum.shape[0])
        radius = min(STROKE_MAX_BLUR, max(8, int(region.h / STROKE_BLUR_DIV)))
        background = self._background(radius)
        diff = np.abs(
            self.lum[region.y : region.y + region.h, region.x : region.x + region.w]
            - background[region.y : region.y + region.h, region.x : region.x + region.w]
        )
        peak = float(diff.max()) if diff.size else 0.0
        patch = self.lum[region.y : region.y + region.h, region.x : region.x + region.w]
        std = float(np.sqrt(max(0.0, (patch * patch).mean() - patch.mean() ** 2)))
        if peak < STROKE_HIGH and std < STROKE_FLAT_STD:
            # 低反差 + 平坦背景（纯白卡片上的浅灰字、浅色尿布上的白字）：固定的 60/22
            # 会把整个字漏掉。只在背景本身平坦（std 小）时才压阈值，有纹理的画面
            # 一律走固定阈值，免得把纹理当字重绘、越修越糊。
            high = max(STROKE_ADAPT_MIN_HIGH, peak * 0.45)
            low = max(STROKE_ADAPT_MIN_LOW, peak * 0.20)
        else:
            high, low = STROKE_HIGH, STROKE_LOW
        strong = (diff > high).astype(np.uint8)
        weak = (diff > low).astype(np.uint8)
        # 强响应直接算字；弱响应只在笔画附近才要，顺手把描边和字的影子带进来。
        # 注意强响应必须并进来：笔画粗的地方「内部」和周围背景差异反而不大，
        # 只留弱响应会在字中间留出一个个字形空洞，修完就是一串残字。
        near = dilate_mask(strong, STROKE_GROW)
        mask = (strong | (weak & near)).astype(np.uint8)
        if int(mask.sum()) == 0:
            return mask
        mask = dilate_mask(mask, STROKE_DILATE)
        return self._extend_edge(region, mask, strong)

    def _extend_edge(self, region: ft.Region, mask, strong):
        """字幕被画面边界切掉时，把边缘那一小截同色的字形补进掩码。

        贴边处盒式模糊只能按边缘像素延拓，背景估计被字自己的颜色「反射」，diff
        变小，笔画掩码会漏掉最边上那几列，修完剩一小截黄色字形。这里不盲目重绘
        整条边缘（那会把背景纹理一起抹掉），只把「和笔画主色接近、又贴着已有掩码」
        的像素补进来，既不漏字，也不动无关背景。
        """
        np = self._np
        w = self.lum.shape[1]
        h = self.lum.shape[0]
        band = np.zeros_like(mask)
        band_w = min(STROKE_EDGE_BAND, region.w)
        band_h = min(STROKE_EDGE_BAND, region.h)
        if region.x <= 1:
            band[:, :band_w] = 1
        if region.x + region.w >= w - 1:
            band[:, region.w - band_w :] = 1
        if region.y <= 1:
            band[:band_h, :] = 1
        if region.y + region.h >= h - 1:
            band[region.h - band_h :, :] = 1
        if int(band.sum()) == 0:
            return mask
        core = self.rgb[region.y : region.y + region.h, region.x : region.x + region.w][strong > 0]
        if core.size == 0:
            # 贴边这截字找不到笔画本体：只能保守地把边缘一条补进去
            return dilate_mask(mask | (band & 1), 0)
        color = np.median(core.astype(np.float32), axis=0)
        rgb = self.rgb[region.y : region.y + region.h, region.x : region.x + region.w].astype(np.float32)
        dist = np.sqrt(((rgb - color) ** 2).sum(axis=2))
        near = dilate_mask(mask, STROKE_GROW + 4)
        extra = (dist < STROKE_COLOR_TOL) & (near > 0) & (band > 0)
        if int(extra.sum()) == 0:
            return mask
        return dilate_mask((mask | extra.astype(np.uint8)), 2)


    def extend_to_edge(self, region: ft.Region, strokes, width: int, height: int):
        """检测框离画面边界很近时，把掩码同色延伸到边界（字幕滚出去的尾巴）。

        滚动字幕进出画面时，检测框只盖住框内那截，框外贴着边界还有十几像素的同色
        字形，掩码够不到，修完就剩一小截残字。这里把掩码扩到边界，但只让「和笔画
        主色接近、又贴着已有掩码」的像素进来，不会把边缘的背景纹理一起重画。
        """
        np = self._np
        if strokes is None:
            return region, strokes
        strokes = np.asarray(strokes, dtype=np.uint8)
        if strokes.shape != (region.h, region.w):
            return region, strokes
        x0, y0, x1, y1 = region.x, region.y, region.x + region.w, region.y + region.h
        nx0, ny0, nx1, ny1 = x0, y0, x1, y1
        grow = STROKE_EDGE_TRIGGER
        if x0 <= grow:
            nx0 = 0
        if x1 >= width - grow:
            nx1 = width
        if y0 <= grow:
            ny0 = 0
        if y1 >= height - grow:
            ny1 = height
        if (nx0, ny0, nx1, ny1) == (x0, y0, x1, y1) or int(strokes.sum()) == 0:
            return region, strokes
        new = ft.Region(nx0, ny0, nx1 - nx0, ny1 - ny0)
        mask = np.zeros((new.h, new.w), dtype=np.uint8)
        ox, oy = x0 - nx0, y0 - ny0
        mask[oy : oy + region.h, ox : ox + region.w] = strokes
        core = self.rgb[y0:y1, x0:x1][strokes > 0]
        if core.size == 0:
            return region, strokes
        color = np.median(core.astype(np.float32), axis=0)
        rgb = self.rgb[ny0:ny1, nx0:nx1].astype(np.float32)
        dist = np.sqrt(((rgb - color) ** 2).sum(axis=2))
        near = dilate_mask(mask, STROKE_GROW + 4)
        allow = np.ones((new.h, new.w), dtype=np.uint8)
        allow[oy : oy + region.h, ox : ox + region.w] = 0
        extra = (dist < STROKE_COLOR_TOL) & (near > 0) & (allow > 0)
        mask = mask | extra.astype(np.uint8)
        # 检测框和边界之间那一小段直接补满：字被画面切掉时，边缘几列的亮度和颜色
        # 都被背景估计「吃掉」，靠同色判定也未必认得出，留着就是一截残字。
        y0m, y1m = oy, oy + region.h
        x0m, x1m = ox, ox + region.w
        fill = STROKE_EDGE_BAND
        if nx1 > x1 and nx1 - x1 <= fill:
            mask[y0m:y1m, x1m:] = 1
        if nx0 < x0 and x0 - nx0 <= fill:
            mask[y0m:y1m, :x0m] = 1
        if ny1 > y1 and ny1 - y1 <= fill:
            mask[y1m:, x0m:x1m] = 1
        if ny0 < y0 and y0 - ny0 <= fill:
            mask[:y0m, x0m:x1m] = 1
        if int(mask.sum()) == int(strokes.sum()):
            return region, strokes
        return new, dilate_mask(mask, STROKE_DILATE)


def _dump_jobs(frame, jobs, index: int) -> None:
    """调试：把这一帧的修复块与掩码存成 png（临时用，环境变量开启）。"""
    try:
        np = _load_numpy()
        out_dir = os.environ.get("SUBTITLE_REMOVER_DUMP_DIR", os.getcwd())
        overlay = frame.copy()
        for box, pad, mask, _alpha in jobs:
            x0, y0 = box.x - pad[1], box.y - pad[0]
            h, w = mask.shape
            x0c, y0c = max(0, x0), max(0, y0)
            hh = min(overlay.shape[0] - y0c, h - (y0c - y0))
            ww = min(overlay.shape[1] - x0c, w - (x0c - x0))
            if hh <= 0 or ww <= 0:
                continue
            m = mask[y0c - y0 : y0c - y0 + hh, x0c - x0 : x0c - x0 + ww]
            sub = overlay[y0c : y0c + hh, x0c : x0c + ww]
            sub[m > 0] = (255, 0, 0)
            overlay[y0c : y0c + hh, x0c : x0c + ww] = sub
        np.save(os.path.join(out_dir, f"jobs_{index}.npy"),
                np.array([[b.x, b.y, b.w, b.h, int(m.sum())] for b, _p, m, _a in jobs]))
        _write_png(overlay, os.path.join(out_dir, f"jobs_{index}.png"))
    except Exception as exc:  # pragma: no cover - 调试用
        print("dump 失败", exc)


def _write_png(array, path: str) -> None:
    np = _load_numpy()
    import struct
    import zlib

    height, width = array.shape[:2]
    raw = b"".join(b"\x00" + array[y].tobytes() for y in range(height))

    def chunk(tag: bytes, data: bytes) -> bytes:
        return (
            struct.pack(">I", len(data))
            + tag
            + data
            + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
        )

    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b"")
    )
    with open(path, "wb") as handle:
        handle.write(png)


def plan_stroke_tiles(region: ft.Region, strokes, width: int, height: int) -> List[Tuple]:
    """把一个检测框拆成「带上下文的修复块」，掩码只盖住笔画像素。

    和 plan_tiles 的区别：块比检测框大得多（上下各扩 STROKE_CTX_Y 倍字高），
    这样模型能看到字幕带上下的真实结构；掩码仍然只标笔画，其余像素原样保留。
    """
    np = _load_numpy()
    region = region.clamped(width, height)
    ctx_y = max(12, int(round(region.h * STROKE_CTX_Y)))
    ctx_x = STROKE_CTX_X
    if strokes is None:
        strokes = None
    else:
        strokes = np.asarray(strokes, dtype=np.uint8)
        if int(strokes.sum()) == 0:
            # 背景太平、找不到笔画：退回整框重绘，至少保证字去掉
            strokes = np.ones((region.h, region.w), dtype=np.uint8)
        elif strokes.shape != (region.h, region.w):
            return []
    if strokes is None:
        strokes = np.ones((region.h, region.w), dtype=np.uint8)

    want = ft.Region(region.x - ctx_x, region.y - ctx_y, region.w + 2 * ctx_x, region.h + 2 * ctx_y)
    x0, y0 = max(0, want.x), max(0, want.y)
    x1 = min(width, want.x + want.w)
    y1 = min(height, want.y + want.h)
    box = ft.Region(x0, y0, x1 - x0, y1 - y0)
    if box.w <= 0 or box.h <= 0:
        return []
    pad = (
        max(0, y0 - want.y),
        max(0, x0 - want.x),
        max(0, want.y + want.h - y1),
        max(0, want.x + want.w - x1),
    )
    mask = np.zeros((box.h + pad[0] + pad[2], box.w + pad[1] + pad[3]), dtype=np.uint8)
    dst_y = pad[0] + (region.y - box.y)
    dst_x = pad[1] + (region.x - box.x)
    mask[dst_y : dst_y + region.h, dst_x : dst_x + region.w] = strokes
    # 再整体外扩一圈：检测框偶尔差一两个像素，字的上下缘会漏在框外，
    # 这一圈让掩码能吃进上下文里，把漏出去的字缘一起修掉
    mask = dilate_mask(mask, STROKE_DILATE)
    # 贴边的那两三列/行直接补齐：字形被画面切掉时，检测框和笔画掩码常常差最后一
    # 两个像素，羽化又按边缘延拓，修完会在边上留一道同色残影
    edge = 3
    if region.x <= 0:
        mask[dst_y : dst_y + region.h, :edge] = 1
    if region.x + region.w >= width:
        mask[dst_y : dst_y + region.h, mask.shape[1] - edge :] = 1
    if region.y <= 0:
        mask[:edge, dst_x : dst_x + region.w] = 1
    if region.y + region.h >= height:
        mask[mask.shape[0] - edge :, dst_x : dst_x + region.w] = 1
    if int(mask.sum()) == 0:
        return []
    return [(box, pad, mask, feather_alpha(mask))]


class StrokeMask:
    """检测得到的低分辨率笔画掩码 → 任意修复块上的像素级掩码。

    每个输出像素映射到检测尺度上最近的格子，并做 2×2 取或：即使缩放比小于 1，
    一两个像素宽的笔画也不会被采样漏掉。
    """

    def __init__(self, mask_low, net_w: int, net_h: int, width: int, height: int) -> None:
        np = _load_numpy()
        self._np = np
        self.mask = np.asarray(mask_low, dtype=np.uint8)
        self.net_w = max(1, int(net_w))
        self.net_h = max(1, int(net_h))
        self.scale_x = self.net_w / float(max(1, width))
        self.scale_y = self.net_h / float(max(1, height))

    def patch(self, box: ft.Region, pad: Tuple[int, int, int, int]):
        """取出裁剪框（含镜像补边区）对应的掩码，形状与补边后的块一致。"""
        np = self._np
        out_h = box.h + pad[0] + pad[2]
        out_w = box.w + pad[1] + pad[3]
        ys = (np.arange(out_h, dtype=np.float32) + (box.y - pad[0])) * self.scale_y
        xs = (np.arange(out_w, dtype=np.float32) + (box.x - pad[1])) * self.scale_x
        iy = np.clip(np.floor(ys).astype(np.int32), 0, self.net_h - 1)
        ix = np.clip(np.floor(xs).astype(np.int32), 0, self.net_w - 1)
        iy1 = np.minimum(iy + 1, self.net_h - 1)
        ix1 = np.minimum(ix + 1, self.net_w - 1)
        grown = self.mask[iy][:, ix] | self.mask[iy1][:, ix] | self.mask[iy][:, ix1] | self.mask[iy1][:, ix1]
        return grown.astype(np.uint8)


def scope_mask_fn(field: StrokeMask, boxes: Sequence[ft.Region]) -> Callable:
    """把笔画掩码限制在检测框内：框外零星的噪点像素不重绘。"""
    np = _load_numpy()

    def mask_fn(box: ft.Region, pad: Tuple[int, int, int, int]):
        stroke = field.patch(box, pad)
        if int(stroke.sum()) == 0:
            return stroke
        allow = np.zeros_like(stroke)
        x0, y0 = box.x - pad[1], box.y - pad[0]
        out_h, out_w = stroke.shape
        for region in boxes:
            ax0 = max(region.x, x0)
            ax1 = min(region.x + region.w, x0 + out_w)
            ay0 = max(region.y, y0)
            ay1 = min(region.y + region.h, y0 + out_h)
            if ax1 > ax0 and ay1 > ay0:
                allow[ay0 - y0 : ay1 - y0, ax0 - x0 : ax1 - x0] = 1
        return (stroke & allow).astype(np.uint8)

    return mask_fn


def dilate_mask(mask_01, radius: int):
    """把 0/1 掩码向外膨胀 radius 像素（可分离最大值滤波，不依赖 OpenCV）。"""
    np = _load_numpy()
    if radius <= 0:
        return np.asarray(mask_01, dtype=np.uint8)
    current = (np.asarray(mask_01) > 0).astype(np.int32)
    kernel = radius * 2 + 1
    for axis in (0, 1):
        pad_width = [(0, 0), (0, 0)]
        pad_width[axis] = (radius + 1, radius)
        padded = np.pad(current, pad_width, mode="constant")
        cum = np.cumsum(padded, axis=axis)
        if axis == 0:
            current = cum[kernel:, :] - cum[:-kernel, :]
        else:
            current = cum[:, kernel:] - cum[:, :-kernel]
    return (current > 0).astype(np.uint8)


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
