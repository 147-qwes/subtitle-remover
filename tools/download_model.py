"""下载 AI 修复模型（LaMa fp32 ONNX，约 208 MB）。

用法：
    python tools/download_model.py            # 下到 models/lama_fp32.onnx
    python tools/download_model.py --mirror   # 用国内镜像源

支持断点续传：网络中断后重新执行会从断点继续。
"""

from __future__ import annotations

import argparse
import hashlib
import os
import sys
import time
import urllib.request

MODEL_SIZE = 208044816
MODEL_SHA256 = "1faef5301d78db7dda502fe59966957ec4b79dd64e16f03ed96913c7a4eb68d6"
FILENAME = "lama_fp32.onnx"

SOURCES = [
    "https://github.com/neureps/carousel-fit-models/releases/download/lama-v1/lama-fp32-1faef5301d78.onnx",
]
MIRROR_SOURCES = [
    "https://hf-mirror.com/neureps/carousel-fit-models/resolve/main/lama-fp32-1faef5301d78.onnx",
    "https://github.com/neureps/carousel-fit-models/releases/download/lama-v1/lama-fp32-1faef5301d78.onnx",
]


def project_root() -> str:
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def file_size(path: str) -> int:
    try:
        return os.path.getsize(path)
    except OSError:
        return 0


def sha256_of(path: str) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str, target: str) -> bool:
    """带断点续传的下载；返回是否完整拿到文件。"""
    existing = file_size(target)
    if existing >= MODEL_SIZE:
        return True

    request = urllib.request.Request(url, headers={"User-Agent": "subtitle-remover/1.0"})
    if existing:
        request.add_header("Range", f"bytes={existing}-")
        print(f"继续下载，已 {existing / 1024 / 1024:.1f} MB")

    try:
        with urllib.request.urlopen(request, timeout=60) as response:
            if existing and response.status != 206:
                existing = 0  # 服务端不支持续传，从头来
            mode = "ab" if existing else "wb"
            total = MODEL_SIZE
            done = existing
            last_report = 0.0
            with open(target, mode) as handle:
                while True:
                    chunk = response.read(1024 * 256)
                    if not chunk:
                        break
                    handle.write(chunk)
                    done += len(chunk)
                    now = time.time()
                    if now - last_report > 3:
                        last_report = now
                        pct = done * 100.0 / total
                        print(
                            f"\r{pct:5.1f}%  {done / 1024 / 1024:6.1f} / "
                            f"{total / 1024 / 1024:.1f} MB",
                            end="",
                            flush=True,
                        )
    except Exception as exc:  # 网络抖动就交给外层重试
        print(f"\n下载中断：{exc}")
        return False

    print()
    return file_size(target) >= MODEL_SIZE


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="下载 LaMa 修复模型")
    parser.add_argument("--mirror", action="store_true", help="使用镜像源（国内网络）")
    parser.add_argument("--out", default=None, help="输出路径（默认 models/lama_fp32.onnx）")
    parser.add_argument("--sha", action="store_true", help="下载后校验 SHA256（较慢）")
    args = parser.parse_args(argv)

    target = os.path.abspath(args.out or os.path.join(project_root(), "models", FILENAME))
    os.makedirs(os.path.dirname(target), exist_ok=True)
    sources = MIRROR_SOURCES if args.mirror else SOURCES

    if file_size(target) >= MODEL_SIZE:
        print(f"模型已存在：{target}")
        return 0

    for url in sources:
        for attempt in range(1, 4):
            print(f"[{attempt}/3] {url}")
            if download(url, target):
                break
        if file_size(target) >= MODEL_SIZE:
            break

    if file_size(target) < MODEL_SIZE:
        print(f"下载未完成（{file_size(target) / 1024 / 1024:.1f} MB），请重试或手动下载：")
        for url in MIRROR_SOURCES:
            print(f"  {url}")
        return 1

    print(f"完成：{target}（{file_size(target) / 1024 / 1024:.1f} MB）")
    if args.sha:
        print("校验 SHA256 …")
        actual = sha256_of(target)
        if actual != MODEL_SHA256:
            print(f"校验失败：{actual}\n期望：{MODEL_SHA256}")
            return 1
        print("SHA256 正确")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
