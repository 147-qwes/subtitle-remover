"""命令行模式：批量处理目录或单个文件，适合脚本、自动化任务。"""

from __future__ import annotations

import argparse
import os
import sys
from typing import List

from . import ffmpeg_tools as ft
from . import remover

VIDEO_EXTS = {
    ".mp4",
    ".mkv",
    ".mov",
    ".avi",
    ".flv",
    ".wmv",
    ".m4v",
    ".webm",
    ".ts",
    ".mpg",
    ".mpeg",
    ".3gp",
    ".rmvb",
    ".m2ts",
}


def collect_inputs(paths: List[str], recursive: bool) -> List[str]:
    files: List[str] = []
    for path in paths:
        if os.path.isdir(path):
            if recursive:
                for root, _dirs, names in os.walk(path):
                    for name in names:
                        if os.path.splitext(name)[1].lower() in VIDEO_EXTS:
                            files.append(os.path.join(root, name))
            else:
                for name in sorted(os.listdir(path)):
                    full = os.path.join(path, name)
                    if os.path.isfile(full) and os.path.splitext(name)[1].lower() in VIDEO_EXTS:
                        files.append(full)
        elif os.path.isfile(path):
            files.append(path)
    return files


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="subtitle-remover",
        description="视频去字幕工具（软字幕轨无损移除 / 硬字幕裁切·模糊·delogo / AI 修复）",
    )
    parser.add_argument("inputs", nargs="*", help="视频文件或目录")
    parser.add_argument(
        "--mode",
        choices=[remover.SOFT_MODE, remover.HARD_MODE],
        default=remover.SOFT_MODE,
        help="soft=移除字幕轨（默认），hard=去除画面内硬字幕",
    )
    parser.add_argument(
        "--style",
        choices=list(ft.ALL_STYLES),
        default="crop_bottom",
        help="处理方式：crop_bottom / crop_top / blur / delogo / inpaint（AI 修复，去字不留遮挡）",
    )
    parser.add_argument(
        "--region",
        action="append",
        help="文字/水印区域（像素）：x,y,w,h，可重复给出多个区域",
    )
    parser.add_argument(
        "--region-percent",
        action="append",
        help="文字/水印区域（百分比）：x,y,w,h，可重复给出多个区域",
    )
    parser.add_argument(
        "--height-ratio",
        type=float,
        default=0.18,
        help="裁切高度占画面高度的比例，默认 0.18",
    )
    parser.add_argument("--crf", type=int, default=23, help="画质 CRF，默认 23")
    parser.add_argument(
        "--preset",
        default="medium",
        choices=["ultrafast", "veryfast", "faster", "fast", "medium", "slow"],
    )
    parser.add_argument(
        "--encoder", default="libx264", choices=["libx264", "libx265"]
    )
    parser.add_argument("--blur-radius", type=int, default=None, help="模糊半径，默认自动")
    parser.add_argument(
        "--device",
        choices=["auto", "cpu", "cuda"],
        default="auto",
        help="AI 修复推理设备：auto 有 GPU 就先用 GPU，默认 auto",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="AI 修复模型路径，默认自动在 models/ 与 env SUBTITLE_REMOVER_MODEL 里找",
    )
    parser.add_argument("--outdir", default=None, help="输出目录，默认与源文件同目录")
    parser.add_argument("--suffix", default=remover.DEFAULT_SUFFIX, help="输出文件名后缀")
    parser.add_argument(
        "--keep-subs",
        default="",
        help="soft 模式下要保留的字幕流序号，逗号分隔（默认全部移除）",
    )
    parser.add_argument("-r", "--recursive", action="store_true", help="递归处理子目录")
    parser.add_argument("-q", "--quiet", action="store_true", help="不打印 ffmpeg 日志")
    return parser


def _percent(fraction: float) -> str:
    return f"{min(100.0, max(0.0, fraction * 100)):5.1f}%"


def main(argv: List[str]) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not args.inputs:
        parser.print_help()
        return 2

    try:
        ft.require_binaries()
    except ft.FFmpegNotFound as exc:
        print(exc, file=sys.stderr)
        return 3

    files = collect_inputs(args.inputs, args.recursive)
    if not files:
        print("没有找到可处理的视频文件。", file=sys.stderr)
        return 2

    percent_regions: List[ft.RelativeRegion] = []
    pixel_regions: List[ft.Region] = []
    if args.mode == remover.HARD_MODE and args.style in ("blur", "delogo", ft.INPAINT_STYLE):
        for text in args.region_percent or []:
            parts = [float(x) for x in text.split(",")]
            if len(parts) != 4:
                print("--region-percent 需要 4 个数字", file=sys.stderr)
                return 2
            percent_regions.append(
                ft.RelativeRegion(parts[0] / 100, parts[1] / 100, parts[2] / 100, parts[3] / 100)
            )
        for text in args.region or []:
            try:
                pixel_regions.append(ft.parse_region(text))
            except ValueError as exc:
                print(f"--region 格式有误：{exc}", file=sys.stderr)
                return 2
        if not percent_regions and not pixel_regions:
            print(
                f"{args.style} 模式需要用 --region 或 --region-percent 指定区域",
                file=sys.stderr,
            )
            return 2

    keep_subs = (
        [int(x) for x in args.keep_subs.split(",") if x.strip().isdigit()]
        if args.keep_subs
        else None
    )

    total = len(files)
    success = 0
    failed = 0

    for index, path in enumerate(files, 1):
        print(f"\n[{index}/{total}] {os.path.basename(path)}")
        try:
            info = ft.probe(path)
        except Exception as exc:
            print(f"  读取失败：{exc}", file=sys.stderr)
            failed += 1
            continue

        if info.subtitle_count and args.mode == remover.SOFT_MODE:
            print(f"  发现 {info.subtitle_count} 条字幕轨：{info.subtitle_label}")
        if args.mode == remover.HARD_MODE and not info.has_video_dimensions:
            print("  没有视频画面，跳过", file=sys.stderr)
            failed += 1
            continue

        dst = remover.output_path(path, args.outdir, args.suffix)
        print(f"  输出：{dst}")

        region = None
        regions_px: List[ft.Region] = []
        if args.mode == remover.HARD_MODE and args.style in ("blur", "delogo", ft.INPAINT_STYLE):
            for relative_region in percent_regions:
                regions_px.append(relative_region.to_pixels(info.width, info.height))
            regions_px.extend(pixel_regions)
            if regions_px:
                region = regions_px[0]

        last_line = {"pct": -10.0}

        def on_progress(fraction: float, seconds: float) -> None:
            pct = fraction * 100
            if pct - last_line["pct"] >= 1.0 or pct >= 100:
                last_line["pct"] = pct
                print(f"\r  进度 {_percent(fraction)}", end="", flush=True)

        def on_log(line: str) -> None:
            if not args.quiet:
                print(f"\r  ffmpeg: {line[:160]}")

        try:
            if args.mode == remover.HARD_MODE:
                result = remover.remove_hard_subtitles(
                    path,
                    dst,
                    info,
                    args.style,
                    region=region,
                    regions=regions_px,
                    height_ratio=args.height_ratio,
                    crf=args.crf,
                    preset=args.preset,
                    encoder=args.encoder,
                    blur_radius=args.blur_radius,
                    model_path=args.model,
                    device=args.device,
                    on_progress=on_progress,
                    on_log=on_log,
                )
            else:
                result = remover.remove_soft_subtitles(
                    path,
                    dst,
                    keep_sub_indices=keep_subs,
                    duration=info.duration,
                    on_progress=on_progress,
                    on_log=on_log,
                )
        except Exception as exc:
            print(f"\n  处理失败：{exc}", file=sys.stderr)
            failed += 1
            continue

        if result.ok:
            success += 1
            size = ft.format_size(os.path.getsize(dst))
            print(f"\r  完成（{result.seconds:.1f}s，{size}）")
        else:
            failed += 1
            print(f"\r  失败（ffmpeg 返回码 {result.returncode}）", file=sys.stderr)
            if result.log_tail:
                print(result.log_tail, file=sys.stderr)

    print(f"\n总计：成功 {success} 个，失败 {failed} 个")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
