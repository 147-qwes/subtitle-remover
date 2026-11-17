"""界面里共用的常量与文案。"""

from __future__ import annotations

from typing import Dict, List, Tuple

VIDEO_FILETYPES: List[Tuple[str, str]] = [
    (
        "视频文件",
        "*.mp4 *.mkv *.mov *.avi *.flv *.wmv *.m4v *.webm *.ts *.mpg *.mpeg *.3gp *.rmvb *.m2ts",
    ),
    ("所有文件", "*.*"),
]

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

STYLE_LABELS: Dict[str, str] = {
    "裁切底部（切掉底部字幕条）": "crop_bottom",
    "裁切顶部（切掉顶部字幕条）": "crop_top",
    "模糊遮罩（画面尺寸不变）": "blur",
    "delogo 修复（按周围像素插值）": "delogo",
    "AI 修复（重绘文字区域，不留遮挡）": "inpaint",
}
STYLE_KEYS = list(STYLE_LABELS.keys())
STYLE_BY_KEY = {value: label for label, value in STYLE_LABELS.items()}

# (按钮文案, y 比例, 宽比例, 高比例)
PRESETS = [
    ("底部 16%", 0.84, 1.0, 0.16),
    ("底部 20%", 0.80, 1.0, 0.20),
    ("底部 25%", 0.75, 1.0, 0.25),
    ("顶部 12%", 0.0, 1.0, 0.12),
]
