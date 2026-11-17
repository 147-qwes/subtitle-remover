"""AI 修复质量对比：同一段素材，比较 AI 修复 / delogo / 模糊 / 原始带字。

做法：
  1. 用 ffmpeg 造一段"干净"素材，再把水印和字幕烧上去造"脏"素材（都是无损编码）；
  2. 分别用三种方式处理脏素材；
  3. 取同一帧，统计"文字区域内 PSNR"（越接近干净画面说明补得越像真的）；
  4. 导出文字区域放大图，方便肉眼检查有没有遮挡带 / 糊痕。

用法（在项目根目录）：
    python tools/eval_quality.py
    python tools/eval_quality.py --sample photo --device cpu
产物在 %TEMP%\\inpaint_eval 下。
"""

import argparse
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

FFMPEG = os.path.join(ROOT, "bin", "ffmpeg.exe") if os.name == "nt" else "ffmpeg"
WORK = os.path.join(os.environ.get("TEMP") or "/tmp", "inpaint_eval")
W, H = 1280, 720
FPS, DURATION = 25, 1.2

WM_REGION = (0, 0, 360, 64)      # 左上角标水印
SUB_REGION = (0, 620, 1280, 90)  # 底部字幕

LATIN = "WATERMARK 2026"
CJK = "这是一行为测试生成的字幕文字内容"


def font_path() -> str:
    """找一个支持中文的系统字体，复制到无空格目录（ffmpeg 滤镜对路径很挑）。"""
    target = os.path.join(WORK, "font.ttf")
    if os.path.exists(target):
        return target
    candidates = [
        r"C:\Windows\Fonts\msyh.ttc",
        r"C:\Windows\Fonts\simhei.ttf",
        r"C:\Windows\Fonts\msyhbd.ttc",
        r"C:\Windows\Fonts\simsun.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        if os.path.exists(path):
            try:
                shutil.copyfile(path, target)
                return target
            except OSError:
                continue
    raise SystemExit("找不到可用字体，请把任意中文 ttf 放到 %TEMP%\\inpaint_eval\\font.ttf")


def ff_escape(path: str) -> str:
    # drawtext 的 fontfile 里冒号要转义，反斜杠路径会解析失败
    return path.replace("\\", "/").replace(":", "\\:")


def run(cmd, **kw):
    return subprocess.run(cmd, check=True, capture_output=True, text=True, **kw)


def build_samples(sample: str) -> None:
    os.makedirs(WORK, exist_ok=True)
    font = ff_escape(font_path())
    clean = os.path.join(WORK, f"{sample}_clean.mp4")
    dirty = os.path.join(WORK, f"{sample}_dirty.mp4")
    duration = ["-t", str(DURATION)]

    if sample == "photo":
        photo = r"C:\Windows\Web\Wallpaper\Spotlight\img14.jpg"
        if not os.path.exists(photo):
            raise SystemExit("没有找到示例照片，请用 --sample testsrc / mandel")
        # 缓慢推镜，模拟真实运镜画面
        run([
            FFMPEG, "-v", "error", "-y", "-loop", "1", "-i", photo, *duration,
            "-vf", f"scale={W * 5 // 4}:{H * 5 // 4},"
                   f"zoompan=z='1.02+0.02*on/30':d=1:x='iw/2-(iw/zoom/2)+8*on':"
                   f"y='ih/2-(ih/zoom/2)':s={W}x{H}:fps={FPS}",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-qp", "0", clean,
        ])
    elif sample == "testsrc":
        run([
            FFMPEG, "-v", "error", "-y", "-f", "lavfi",
            "-i", f"testsrc2=size={W}x{H}:rate={FPS}:duration={DURATION}",
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-qp", "0", clean,
        ])
    elif sample == "mandel":
        run([
            FFMPEG, "-v", "error", "-y", "-f", "lavfi",
            "-i", f"mandelbrot=size={W}x{H}:rate={FPS}", *duration,
            "-pix_fmt", "yuv420p", "-c:v", "libx264", "-qp", "0", clean,
        ])
    else:
        raise SystemExit(f"未知素材 {sample}")

    draw = (
        f"drawtext=fontfile='{font}':text='{LATIN}':x=10:y=8:fontsize=34:fontcolor=white,"
        f"drawtext=fontfile='{font}':text='{CJK}':x=(w-text_w)/2:y=640:fontsize=40:fontcolor=white"
    )
    run([
        FFMPEG, "-v", "error", "-y", "-i", clean, "-vf", draw,
        "-pix_fmt", "yuv420p", "-c:v", "libx264", "-qp", "0", dirty,
    ])
    print(f"  素材就绪：{sample}_clean.mp4 / {sample}_dirty.mp4")


def read_frame(path: str, seconds: float):
    import numpy as np

    raw = subprocess.run(
        [FFMPEG, "-v", "error", "-ss", f"{seconds:.2f}", "-i", path, "-frames:v", "1",
         "-f", "rawvideo", "-pix_fmt", "rgb24", "-"],
        capture_output=True, check=True,
    ).stdout
    return np.frombuffer(raw, dtype=np.uint8).reshape(H, W, 3)


def region_mask():
    import numpy as np

    mask = np.zeros((H, W), dtype=bool)
    for x, y, w, h in (WM_REGION, SUB_REGION):
        mask[y : y + h, x : x + w] = True
    return mask


def psnr(a, b) -> float:
    import numpy as np

    mse = float(np.mean((a.astype(np.float64) - b.astype(np.float64)) ** 2))
    if mse <= 1e-9:
        return 99.0
    return float(10 * np.log10(255.0 ** 2 / mse))


def evaluate(tag: str, path: str, clean, mask) -> None:
    out = read_frame(path, 0.6)
    inside = psnr(out[mask], clean[mask])
    outside = psnr(out[~mask], clean[~mask])
    print(f"  {tag:<10} 文字区 {inside:6.2f} dB | 其他区域 {outside:6.2f} dB")


def export_crop(path: str, rect, out_path: str) -> None:
    import numpy as np

    frame = read_frame(path, 0.6)
    x, y, w, h = rect
    sub = frame[y : y + h, x : x + w]
    sub = np.repeat(np.repeat(sub, 2, axis=0), 2, axis=1)
    subprocess.run(
        [FFMPEG, "-v", "error", "-y", "-f", "rawvideo", "-pix_fmt", "rgb24",
         "-s", f"{sub.shape[1]}x{sub.shape[0]}", "-i", "-", "-frames:v", "1", out_path],
        input=sub.tobytes(), check=True,
    )


def process(sample: str, style: str, device: str) -> str:
    import glob

    dirty = os.path.join(WORK, f"{sample}_dirty.mp4")
    for old in glob.glob(os.path.join(WORK, f"{sample}_dirty_{style}*.mp4")):
        try:
            os.remove(old)
        except OSError:
            pass
    cmd = [
        sys.executable, "main.py", "--cli", dirty, "--mode", "hard", "--style", style,
        "--crf", "18", "--preset", "veryfast", "--suffix", f"_{style}", "--outdir", WORK,
        "-q", "--device", device,
    ]
    if style == "inpaint":
        cmd += [
            "--region", ",".join(map(str, WM_REGION)),
            "--region", ",".join(map(str, SUB_REGION)),
        ]
    else:
        # delogo / 模糊一次只能一个区域，这里统一测底部字幕区
        cmd += ["--region", ",".join(map(str, SUB_REGION))]
    proc = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    if proc.returncode != 0:
        print(proc.stdout[-800:] + proc.stderr[-800:])
        return ""
    found = glob.glob(os.path.join(WORK, f"{sample}_dirty_{style}*.mp4"))
    return found[0] if found else ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sample", default="photo", choices=["photo", "testsrc", "mandel"])
    parser.add_argument("--device", default="auto", choices=["auto", "cpu", "cuda"])
    parser.add_argument("--reuse", action="store_true", help="复用已生成素材，不重新烧字")
    args = parser.parse_args()

    print(f"[{args.sample}] 准备素材")
    if not args.reuse:
        build_samples(args.sample)

    clean = read_frame(os.path.join(WORK, f"{args.sample}_clean.mp4"), 0.6)
    mask = region_mask()
    dirty_path = os.path.join(WORK, f"{args.sample}_dirty.mp4")
    print("  对照：")
    evaluate("原始带字", dirty_path, clean, mask)

    for style in ("inpaint", "delogo", "blur"):
        out = process(args.sample, style, args.device)
        if not out:
            print(f"  {style} 处理失败")
            continue
        evaluate(style, out, clean, mask)
        export_crop(out, SUB_REGION, os.path.join(WORK, f"{args.sample}_{style}_sub.png"))
        if style == "inpaint":
            export_crop(out, WM_REGION, os.path.join(WORK, f"{args.sample}_{style}_wm.png"))

    export_crop(dirty_path, SUB_REGION, os.path.join(WORK, f"{args.sample}_dirty_sub.png"))
    export_crop(os.path.join(WORK, f"{args.sample}_clean.mp4"), SUB_REGION,
                os.path.join(WORK, f"{args.sample}_clean_sub.png"))
    print(f"裁剪对比图：{WORK}")


if __name__ == "__main__":
    main()
