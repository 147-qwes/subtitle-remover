"""核心逻辑单元测试：不需要真的安装 ffmpeg。"""

from __future__ import annotations

import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from subremover import ffmpeg_tools as ft  # noqa: E402
from subremover import remover  # noqa: E402

SAMPLE_PROBE = {
    "format": {"format_name": "matroska,webm", "duration": "61.5"},
    "streams": [
        {
            "index": 0,
            "codec_type": "video",
            "codec_name": "h264",
            "width": 1920,
            "height": 1080,
            "duration": "61.5",
        },
        {
            "index": 1,
            "codec_type": "audio",
            "codec_name": "aac",
            "tags": {"language": "jpn"},
            "duration": "61.4",
        },
        {
            "index": 2,
            "codec_type": "subtitle",
            "codec_name": "ass",
            "tags": {"language": "chi", "title": "简体中文"},
            "disposition": {"default": 1},
        },
        {
            "index": 3,
            "codec_type": "subtitle",
            "codec_name": "subrip",
            "tags": {"language": "eng"},
        },
    ],
}


def make_info(width=1920, height=1080, duration=60.0, subs=2) -> ft.MediaInfo:
    streams = [
        ft.StreamInfo(index=0, codec_type="video", codec_name="h264", width=width, height=height),
        ft.StreamInfo(index=1, codec_type="audio", codec_name="aac"),
    ]
    for i in range(subs):
        streams.append(ft.StreamInfo(index=2 + i, codec_type="subtitle", codec_name="ass"))
    return ft.MediaInfo(path="in.mkv", duration=duration, streams=streams)


class ProbeParseTests(unittest.TestCase):
    def test_parse_probe_json(self) -> None:
        info = ft.parse_probe_json(SAMPLE_PROBE, "ep1.mkv")
        self.assertAlmostEqual(info.duration, 61.5)
        self.assertEqual(info.width, 1920)
        self.assertEqual(info.height, 1080)
        self.assertEqual(info.subtitle_count, 2)
        self.assertTrue(info.has_video_dimensions)
        self.assertEqual(info.subtitle_streams[0].title, "简体中文")
        self.assertTrue(info.subtitle_streams[0].default)

    def test_duration_falls_back_to_stream(self) -> None:
        data = {
            "format": {},
            "streams": [{"index": 0, "codec_type": "video", "duration": "12.25"}],
        }
        info = ft.parse_probe_json(data, "a.mp4")
        self.assertAlmostEqual(info.duration, 12.25)

    def test_missing_streams(self) -> None:
        info = ft.parse_probe_json({}, "broken.mp4")
        self.assertEqual(info.streams, [])
        self.assertEqual(info.subtitle_count, 0)
        self.assertFalse(info.has_video_dimensions)


class RegionTests(unittest.TestCase):
    def test_clamp_inside_frame(self) -> None:
        region = ft.Region(-10, 1200, 2200, 100).clamped(1920, 1080)
        self.assertEqual(region.x, 0)
        self.assertEqual(region.y, 980)
        self.assertEqual(region.w, 1920)
        self.assertEqual(region.h, 100)

    def test_even(self) -> None:
        region = ft.Region(3, 5, 101, 21).even()
        self.assertEqual(region.w, 100)
        self.assertEqual(region.h, 20)

    def test_parse_region(self) -> None:
        self.assertEqual(ft.parse_region("10,20,300,80"), ft.Region(10, 20, 300, 80))
        self.assertEqual(ft.parse_region("10 20 300 80"), ft.Region(10, 20, 300, 80))
        with self.assertRaises(ValueError):
            ft.parse_region("1,2,3")

    def test_relative_roundtrip(self) -> None:
        relative = ft.RelativeRegion.from_pixels(ft.Region(0, 900, 1920, 180), 1920, 1080)
        self.assertAlmostEqual(relative.y, 0.8333, places=3)
        pixels = relative.to_pixels(1920, 1080)
        self.assertEqual(pixels.x, 0)
        self.assertEqual(pixels.y, 900)
        self.assertEqual(pixels.w, 1920)
        self.assertEqual(pixels.h, 180)

    def test_bottom_band(self) -> None:
        band = ft.RelativeRegion.bottom_band(0.2)
        self.assertAlmostEqual(band.y, 0.8)
        self.assertAlmostEqual(band.h, 0.2)


class SoftCommandTests(unittest.TestCase):
    def test_drop_all_subtitles_lossless(self) -> None:
        cmd = ft.build_soft_remove_command("in.mkv", "out.mkv", ffmpeg="ffmpeg")
        joined = " ".join(cmd)
        self.assertIn("-map 0 -map -0:s", joined)
        self.assertIn("-c copy", joined)
        self.assertEqual(cmd[-1], "out.mkv")
        self.assertNotIn("-vf", cmd)

    def test_keep_selected_subtitles(self) -> None:
        cmd = ft.build_soft_remove_command(
            "in.mkv", "out.mkv", keep_sub_indices=[3], ffmpeg="ffmpeg"
        )
        joined = " ".join(cmd)
        self.assertIn("-map 0:V?", joined)
        self.assertIn("-map 0:a?", joined)
        self.assertIn("-map 0:3", joined)
        self.assertNotIn("-map -0:s", joined)

    def test_mp4_gets_faststart(self) -> None:
        cmd = ft.build_soft_remove_command("in.mp4", "out.mp4", ffmpeg="ffmpeg")
        self.assertIn("+faststart", " ".join(cmd))


class HardCommandTests(unittest.TestCase):
    def test_crop_bottom(self) -> None:
        info = make_info()
        cmd = ft.build_hard_remove_command(
            "in.mp4", "out.mp4", info, "crop_bottom", height_ratio=0.2, ffmpeg="ffmpeg"
        )
        joined = " ".join(cmd)
        # 1080 * 0.2 = 216 -> 输出 1920x864
        self.assertIn("crop=1920:864:0:0", joined)
        self.assertIn("-c:v libx264", joined)
        self.assertIn("-c:a copy", joined)

    def test_crop_top_offset(self) -> None:
        info = make_info()
        cmd = ft.build_hard_remove_command(
            "in.mp4", "out.mp4", info, "crop_top", height_ratio=0.1, ffmpeg="ffmpeg"
        )
        self.assertIn("crop=1920:972:0:108", " ".join(cmd))

    def test_crop_odd_height_is_evened(self) -> None:
        info = make_info(height=1081)
        cmd = ft.build_hard_remove_command(
            "in.mp4", "out.mp4", info, "crop_bottom", height_ratio=0.1, ffmpeg="ffmpeg"
        )
        joined = " ".join(cmd)
        self.assertIn("crop=1920:972:0:0", joined)
        self.assertIn("-pix_fmt yuv420p", joined)

    def test_blur_uses_filter_complex(self) -> None:
        info = make_info()
        cmd = ft.build_hard_remove_command(
            "in.mp4",
            "out.mp4",
            info,
            "blur",
            region=ft.Region(0, 900, 1920, 180),
            ffmpeg="ffmpeg",
        )
        joined = " ".join(cmd)
        self.assertIn("-filter_complex", cmd)
        self.assertIn("split=2[base][part]", joined)
        self.assertIn("crop=1920:180:0:900", joined)
        self.assertIn("boxblur=luma_radius=", joined)
        self.assertIn("overlay=0:900", joined)
        self.assertIn("-map [v]", joined)
        self.assertIn("-map 0:a?", joined)

    def test_blur_requires_region(self) -> None:
        with self.assertRaises(ValueError):
            ft.build_hard_remove_command("in.mp4", "out.mp4", make_info(), "blur", ffmpeg="ffmpeg")

    def test_delogo_stays_inside_frame(self) -> None:
        info = make_info()
        cmd = ft.build_hard_remove_command(
            "in.mp4",
            "out.mp4",
            info,
            "delogo",
            region=ft.Region(1900, 1070, 50, 50),
            ffmpeg="ffmpeg",
        )
        joined = " ".join(cmd)
        self.assertIn("delogo=x=1870:y=1030", joined)
        self.assertIn("w=49:h=49", joined)

    def test_unknown_style_raises(self) -> None:
        with self.assertRaises(ValueError):
            ft.build_hard_remove_command("in.mp4", "out.mp4", make_info(), "magic", ffmpeg="ffmpeg")

    def test_no_video_raises(self) -> None:
        info = ft.MediaInfo(path="a.mp3", streams=[ft.StreamInfo(index=0, codec_type="audio")])
        with self.assertRaises(ValueError):
            ft.build_hard_remove_command("a.mp3", "b.mp3", info, "crop_bottom", ffmpeg="ffmpeg")


class MiscTests(unittest.TestCase):
    def test_parse_out_time(self) -> None:
        self.assertAlmostEqual(ft.parse_out_time("out_time=00:00:05.500000"), 5.5)
        self.assertAlmostEqual(ft.parse_out_time("out_time=01:02:03.25"), 3723.25)
        self.assertEqual(ft.parse_out_time("frame=123"), 0.0)

    def test_format_duration(self) -> None:
        self.assertEqual(ft.format_duration(65), "01:05")
        self.assertEqual(ft.format_duration(3725), "1:02:05")

    def test_output_path_unique(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "ep1.mp4")
            open(src, "w").close()
            dst = remover.output_path(src)
            self.assertEqual(os.path.basename(dst), "ep1_nosub.mp4")
            open(dst, "w").close()
            dst2 = remover.output_path(src)
            self.assertEqual(os.path.basename(dst2), "ep1_nosub_1.mp4")

    def test_output_path_into_outdir(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "a.mkv")
            open(src, "w").close()
            outdir = os.path.join(tmp, "out")
            dst = remover.output_path(src, outdir, "_clean")
            self.assertEqual(os.path.dirname(dst), os.path.abspath(outdir))
            self.assertEqual(os.path.basename(dst), "a_clean.mkv")

    def test_empty_suffix_falls_back(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            src = os.path.join(tmp, "a.mkv")
            open(src, "w").close()
            self.assertTrue(remover.output_path(src, None, "  ").endswith("a_nosub.mkv"))


class InpaintLogicTests(unittest.TestCase):
    """AI 修复的几何逻辑（不需要模型和 onnxruntime）。"""

    def test_parse_fps(self) -> None:
        self.assertAlmostEqual(ft.parse_fps("25/1"), 25.0)
        self.assertAlmostEqual(ft.parse_fps("30000/1001"), 29.97, places=2)
        self.assertAlmostEqual(ft.parse_fps("24"), 24.0)
        self.assertEqual(ft.parse_fps("0/0"), 0.0)
        self.assertEqual(ft.parse_fps(None), 0.0)

    def test_media_frame_rate_fallback(self) -> None:
        info = make_info()
        self.assertEqual(info.fps, 0.0)
        self.assertEqual(info.frame_rate, 25.0)

    def test_expand_region_adds_context(self) -> None:
        from subremover import inpaint

        region = ft.Region(100, 800, 400, 100)
        box = inpaint.expand_region(region, 1920, 1080)
        self.assertLessEqual(box.x, 100)
        self.assertLessEqual(box.y, 800)
        self.assertGreaterEqual(box.x + box.w, 500)
        self.assertGreaterEqual(box.y + box.h, 900)
        self.assertGreaterEqual(box.w, region.w)
        self.assertGreaterEqual(box.h, region.h)

    def test_expand_region_stays_inside_frame(self) -> None:
        from subremover import inpaint

        box = inpaint.expand_region(ft.Region(0, 1000, 1920, 80), 1920, 1080)
        self.assertGreaterEqual(box.x, 0)
        self.assertGreaterEqual(box.y, 0)
        self.assertLessEqual(box.x + box.w, 1920)
        self.assertLessEqual(box.y + box.h, 1080)

    def test_inner_box_maps_into_patch(self) -> None:
        from subremover import inpaint

        region = ft.Region(100, 800, 400, 100)
        box = inpaint.expand_region(region, 1920, 1080)
        x0, y0, x1, y1 = inpaint.inner_box(region, box.x, box.y, box.w, box.h)
        self.assertEqual((x0, y0), (region.x - box.x, region.y - box.y))
        self.assertEqual(x1 - x0, region.w)
        self.assertEqual(y1 - y0, region.h)
        self.assertLessEqual(x1, box.w)
        self.assertLessEqual(y1, box.h)

    def test_pad_to_multiple(self) -> None:
        from subremover import inpaint

        self.assertEqual(inpaint.pad_to_multiple(100), 104)
        self.assertEqual(inpaint.pad_to_multiple(16), 16)
        self.assertEqual(inpaint.pad_to_multiple(1), 8)

    def test_merge_overlapping_regions(self) -> None:
        from subremover import inpaint

        merged = inpaint.merge_into_list(
            [ft.Region(0, 900, 1920, 180)], ft.Region(0, 880, 100, 60)
        )
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].y, 880)
        self.assertGreaterEqual(merged[0].h, 200)

    def test_merge_keeps_separate_regions(self) -> None:
        from subremover import inpaint

        merged = inpaint.merge_into_list(
            [ft.Region(0, 0, 200, 60)], ft.Region(0, 900, 1920, 180)
        )
        self.assertEqual(len(merged), 2)

    def test_model_path_can_be_pointed_by_env(self) -> None:
        from subremover import inpaint

        paths = inpaint.model_search_paths()
        self.assertTrue(any("models" in p for p in paths))
        # 下载中断的空文件不能当成可用模型
        self.assertFalse(inpaint._is_usable_model(__file__))

    def test_feather_alpha_is_soft_edged(self) -> None:
        from subremover import inpaint

        try:
            import numpy as np  # noqa: F401
        except Exception:
            self.skipTest("numpy 未安装")
        np = __import__("numpy")
        mask = np.zeros((40, 60), dtype=np.uint8)
        mask[10:30, 15:45] = 1
        alpha = inpaint.feather_alpha(mask, radius=3)
        self.assertEqual(alpha.shape, mask.shape)
        self.assertAlmostEqual(float(alpha[20, 30]), 1.0, places=2)
        self.assertAlmostEqual(float(alpha[0, 0]), 0.0, places=2)
        # 边界处存在 0~1 的过渡，拼接不会有硬缝
        self.assertTrue(((alpha > 0.05) & (alpha < 0.95)).any())

    def test_context_box_pads_at_frame_edge(self) -> None:
        from subremover import inpaint

        # 贴左上角的角标：左侧和上方没有画面，需要镜像补边
        box, pad = inpaint.context_box(ft.Region(0, 0, 300, 60), 1920, 1080)
        self.assertEqual(box.x, 0)
        self.assertEqual(box.y, 0)
        self.assertGreater(pad[0], 0)
        self.assertGreater(pad[1], 0)

    def test_context_box_no_pad_in_middle(self) -> None:
        from subremover import inpaint

        box, pad = inpaint.context_box(ft.Region(600, 400, 400, 100), 1920, 1080)
        self.assertEqual(pad, (0, 0, 0, 0))
        self.assertLessEqual(box.x, 600)
        self.assertLess(box.y, 400)

    def test_plan_tiles_covers_region_and_fits_model(self) -> None:
        from subremover import inpaint

        try:
            import numpy as np  # noqa: F401
        except Exception:
            self.skipTest("numpy 未安装")
        np = __import__("numpy")
        region = ft.Region(0, 300, 1280, 240)
        tiles = inpaint.plan_tiles(region, 1280, 600)
        self.assertGreaterEqual(len(tiles), 2)
        covered = np.zeros((600, 1280), dtype=np.uint8)
        for box, pad, mask, _alpha in tiles:
            self.assertLessEqual(mask.shape[0] + 0, 512)
            self.assertLessEqual(mask.shape[1] + 0, 512)
            covered[box.y : box.y + box.h, box.x : box.x + box.w] = 1
        # 拆分后整块区域都要被覆盖到
        self.assertEqual(int(covered[300:540, :].min()), 1)

    def test_thin_band_is_split_for_1to1_detail(self) -> None:
        from subremover import inpaint

        # 1280×60 的字幕带要切块：整条塞进 512 会横向缩到 0.4 倍，笔画糊成色带。
        # 目标：每块的「核心+上下文」横向不超过 512，原始像素 1:1 进模型。
        tiles = inpaint.plan_tiles(ft.Region(0, 620, 1280, 60), 1280, 720)
        self.assertGreaterEqual(len(tiles), 2)
        for box, pad, mask, _alpha in tiles:
            self.assertLessEqual(box.w + pad[1] + pad[3], 512)
            self.assertEqual(mask.shape[1], box.w + pad[1] + pad[3])
        covered = 0
        for _box, _pad, mask, _alpha in tiles:
            covered += int(mask.sum())
        self.assertGreaterEqual(covered, 1280 * 60)

    def test_dilate_mask_grows_outward(self) -> None:
        from subremover import inpaint

        try:
            import numpy as np  # noqa: F401
        except Exception:
            self.skipTest("numpy 未安装")
        np = __import__("numpy")
        mask = np.zeros((40, 60), dtype=np.uint8)
        mask[15:25, 20:40] = 1
        grown = inpaint.dilate_mask(mask, 3)
        self.assertEqual(grown.shape, mask.shape)
        self.assertGreater(int(grown.sum()), int(mask.sum()))
        # 原掩码全保留，且只向外扩 3px
        self.assertEqual(int(grown[18, 30]), 1)
        self.assertEqual(int(grown[15 - 2, 30]), 1)
        self.assertEqual(int(grown[15 - 5, 30]), 0)
        self.assertEqual(int(grown[0, 0]), 0)

    def test_plan_tiles_mask_covers_own_core(self) -> None:
        from subremover import inpaint

        try:
            import numpy as np  # noqa: F401
        except Exception:
            self.skipTest("numpy 未安装")
        np = __import__("numpy")
        tiles = inpaint.plan_tiles(ft.Region(0, 300, 1280, 240), 1280, 600)
        total = sum(int(mask.sum()) for _b, _p, mask, _a in tiles)
        # 每块负责自己的那一格，掩码再向外膨胀一圈（吃掉描边和抗锯齿边缘）
        self.assertGreaterEqual(total, 1280 * 240)
        self.assertLess(total, 1280 * 240 + 24 * (1280 + 240) * 4)

    def test_provider_candidates_fallback_to_cpu(self) -> None:
        from subremover import inpaint

        avail = ["CUDAExecutionProvider", "CPUExecutionProvider"]
        # auto：先 GPU + CPU，失败再退纯 CPU
        self.assertEqual(
            inpaint.provider_candidates("auto", avail),
            [["CUDAExecutionProvider", "CPUExecutionProvider"], ["CPUExecutionProvider"]],
        )
        # 强制 cuda 但运行时没有 CUDA：直接退 CPU，不再抛异常
        self.assertEqual(
            inpaint.provider_candidates("cuda", ["CPUExecutionProvider"]),
            [["CPUExecutionProvider"]],
        )
        self.assertEqual(
            inpaint.provider_candidates("cpu", avail), [["CPUExecutionProvider"]]
        )

    def test_inpaint_duplicate_region_is_merged(self) -> None:
        """GUI / CLI 会同时传 region 与 regions，同一块区域不能修两次。"""
        from subremover import inpaint

        captured = {}

        def fake_process(src, dst, info, regions, **kwargs):
            captured["regions"] = list(regions)
            return ft.RunResult(returncode=0, cancelled=False, seconds=0.0, log_tail="")

        original = inpaint.process_video
        inpaint.process_video = fake_process
        try:
            subtitle = ft.Region(0, 620, 1280, 90)
            watermark = ft.Region(0, 0, 360, 64)
            remover.remove_hard_subtitles(
                os.path.join(tempfile.gettempdir(), "in.mp4"),
                os.path.join(tempfile.gettempdir(), "out.mp4"),
                make_info(),
                ft.INPAINT_STYLE,
                region=subtitle,
                regions=[subtitle, watermark],
            )
        finally:
            inpaint.process_video = original

        self.assertEqual(len(captured["regions"]), 2)
        self.assertEqual(
            [(r.x, r.y, r.w, r.h) for r in captured["regions"]],
            [(0, 620, 1280, 90), (0, 0, 360, 64)],
        )


class TextDetectTests(unittest.TestCase):
    """动态字幕检测的纯几何逻辑（不需要检测模型）。"""

    def setUp(self) -> None:
        try:
            import numpy as np  # noqa: F401
        except Exception:
            self.skipTest("numpy 未安装")

    def test_boxes_from_prob_keeps_text_blob(self) -> None:
        import numpy as np

        from subremover import textdet

        prob = np.zeros((64, 128), dtype=np.float32)
        prob[20:40, 10:110] = 0.9
        boxes = textdet.boxes_from_prob(prob)
        self.assertTrue(boxes)
        x, y, w, h = max(boxes, key=lambda b: b[2] * b[3])
        self.assertGreaterEqual(w, 100)
        self.assertGreaterEqual(h, 20)

    def test_boxes_from_prob_drops_tiny_specks(self) -> None:
        import numpy as np

        from subremover import textdet

        prob = np.zeros((64, 128), dtype=np.float32)
        prob[30:33, 30:33] = 0.9  # 3×3 噪点，小于 MIN_AREA
        self.assertEqual(textdet.boxes_from_prob(prob), [])

    def test_expand_box_covers_glyph_edges(self) -> None:
        """DB 框只包字形核心，纵向要额外吃掉描边和上下缘。"""
        from subremover import textdet

        box = textdet.expand_box((200, 500, 600, 24), 1280, 720)
        pad_y = max(textdet.EXPAND_MIN_Y, int(24 * textdet.EXPAND_RATIO_Y))
        self.assertEqual(box.y, 500 - pad_y)
        self.assertEqual(box.h, 24 + 2 * pad_y)
        # 窄高框（竖排字 / 角标）纵向吃到 70%
        tall = textdet.expand_box((100, 100, 30, 60), 1280, 720)
        self.assertEqual(tall.h, 60 + 2 * int(60 * textdet.EXPAND_RATIO_Y))

    def test_expand_box_stays_inside_frame(self) -> None:
        from subremover import textdet

        box = textdet.expand_box((0, 0, 300, 20), 1280, 720)
        self.assertGreaterEqual(box.x, 0)
        self.assertGreaterEqual(box.y, 0)
        self.assertLessEqual(box.x + box.w, 1280)
        self.assertLessEqual(box.y + box.h, 720)

    def test_merge_boxes_joins_same_line_pieces(self) -> None:
        from subremover import textdet

        regions = [ft.Region(0, 600, 200, 40), ft.Region(203, 600, 200, 40)]
        merged = textdet.merge_boxes(regions)
        self.assertEqual(len(merged), 1)
        self.assertGreaterEqual(merged[0].w, 400)

    def test_merge_boxes_keeps_different_lines(self) -> None:
        from subremover import textdet

        regions = [ft.Region(0, 600, 200, 40), ft.Region(0, 500, 200, 40)]
        self.assertEqual(len(textdet.merge_boxes(regions)), 2)

    def test_limit_boxes_filters_by_scope(self) -> None:
        from subremover import textdet

        regions = [ft.Region(100, 600, 400, 40), ft.Region(900, 20, 300, 30)]
        kept = textdet.limit_boxes(regions, scope=[ft.Region(0, 560, 1280, 160)])
        self.assertEqual(len(kept), 1)
        self.assertEqual(kept[0].y, 600)

    def test_limit_boxes_caps_count(self) -> None:
        from subremover import textdet

        regions = [ft.Region(0, i * 20, 100, 18) for i in range(40)]
        kept = textdet.limit_boxes(regions)
        self.assertLessEqual(len(kept), textdet.MAX_BOXES)

    def test_det_model_size_is_checked(self) -> None:
        import os
        import tempfile

        from subremover import textdet

        # 截断的下载文件不能当成可用模型
        with tempfile.NamedTemporaryFile("wb", suffix=".onnx", delete=False) as handle:
            handle.write(b"\0" * 1024)
            path = handle.name
        try:
            import unittest.mock as mock

            with mock.patch.dict(os.environ, {textdet.DET_ENV: path}):
                self.assertIsNone(textdet.find_det_model())
        finally:
            os.remove(path)


class StrokeMaskTests(unittest.TestCase):
    """像素级掩码：只修笔画，框内其他地方保持原样。"""

    def setUp(self) -> None:
        try:
            import numpy as np  # noqa: F401
        except Exception:
            self.skipTest("numpy 未安装")

    def test_prob_to_mask_keeps_halo_and_drops_background(self) -> None:
        import numpy as np

        from subremover import textdet

        prob = np.zeros((64, 128), dtype=np.float32)
        prob[10:20, 10:40] = 0.35     # 笔画本体
        prob[10:20, 40:60] = 0.05     # 淡淡的背景响应，不该被修
        mask = textdet.prob_to_mask(prob)
        self.assertEqual(int(mask[10:20, 10:40].sum()), 10 * 30)
        self.assertEqual(int(mask[10:20, 40:60].sum()), 0)
        self.assertEqual(int(mask.sum()), 10 * 30)

    def test_stroke_mask_keeps_one_pixel_stroke(self) -> None:
        """检测尺度比原图小的时候，1px 宽的笔画也不能被采样漏掉。"""
        import numpy as np

        from subremover import inpaint

        mask_low = np.zeros((34, 60), dtype=np.uint8)
        mask_low[:, 30] = 1
        field = inpaint.StrokeMask(mask_low, 60, 34, 660, 340)
        patch = field.patch(ft.Region(300, 100, 200, 100), (0, 0, 0, 0))
        self.assertEqual(patch.shape, (100, 200))
        self.assertGreater(int(patch.sum()), 0)
        cols = np.nonzero(patch.any(axis=0))[0]
        # 竖线落在块内 x≈330 处：低分辨率一格对应原图约 11px，加上 2×2 取样，
        # 只占这两格左右，不会糊成整块
        self.assertLess(int(cols.max() - cols.min()), 26)
        self.assertLess(int(patch.sum()), 100 * 26)

    def test_scope_mask_fn_drops_pixels_outside_boxes(self) -> None:
        import numpy as np

        from subremover import inpaint

        mask_low = np.zeros((34, 60), dtype=np.uint8)
        mask_low[10:14, 20:40] = 1
        field = inpaint.StrokeMask(mask_low, 60, 34, 1200, 680)
        boxes = [ft.Region(380, 180, 420, 100)]
        mask_fn = inpaint.scope_mask_fn(field, boxes)
        patch = mask_fn(ft.Region(300, 100, 600, 300), (0, 0, 0, 0))
        self.assertEqual(patch.shape, (300, 600))
        # 框外（y<180）那部分必须被清掉
        self.assertEqual(int(patch[0:60].sum()), 0)
        self.assertGreater(int(patch.sum()), 0)

    def test_plan_tiles_uses_stroke_mask(self) -> None:
        import numpy as np

        from subremover import inpaint

        region = ft.Region(0, 600, 400, 80)
        stroke = np.zeros((152, 472), dtype=np.uint8)
        stroke[40:70, 100:200] = 1

        def mask_fn(_box, _pad):
            return stroke

        tiles = inpaint.plan_tiles(region, 1280, 720, mask_fn=mask_fn)
        self.assertEqual(len(tiles), 1)
        _box, _pad, mask, _alpha = tiles[0]
        rect = 400 * 80
        # 只比笔画本身多出膨胀那几圈，远小于整块矩形
        self.assertGreater(int(mask.sum()), int(stroke.sum()))
        self.assertLess(int(mask.sum()), rect // 2)

    def test_plan_tiles_falls_back_to_rect_when_mask_empty(self) -> None:
        import numpy as np

        from subremover import inpaint

        def mask_fn(_box, _pad):
            return np.zeros((152, 472), dtype=np.uint8)

        region = ft.Region(0, 600, 400, 80)
        tiles = inpaint.plan_tiles(region, 1280, 720, mask_fn=mask_fn)
        self.assertEqual(len(tiles), 1)
        _box, _pad, mask, _alpha = tiles[0]
        self.assertGreaterEqual(int(mask.sum()), 400 * 80)

    def test_cli_full_frame_flag(self) -> None:
        from subremover import cli

        args = cli.build_parser().parse_args(["--style", "auto", "--full-frame"])
        self.assertTrue(args.full_frame)
        self.assertFalse(args.refine)
        self.assertTrue(
            cli.build_parser().parse_args(["--style", "auto", "--refine"]).refine
        )


if __name__ == "__main__":
    unittest.main()
