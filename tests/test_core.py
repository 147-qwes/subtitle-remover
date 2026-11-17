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


if __name__ == "__main__":
    unittest.main()
