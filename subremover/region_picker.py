"""字幕区域框选对话框：抽帧预览 + 鼠标拖拽框选。

用于硬字幕「模糊 / delogo」模式，让用户看着真实画面确定字幕位置，
而不是盲填坐标。
"""

from __future__ import annotations

import os
import tempfile
import tkinter as tk
from tkinter import messagebox, ttk
from typing import Optional

from . import ffmpeg_tools as ft

PREVIEW_MAX_WIDTH = 880


class RegionPickerDialog(tk.Toplevel):
    def __init__(
        self,
        master: tk.Misc,
        path: str,
        info: ft.MediaInfo,
        initial: Optional[ft.Region] = None,
    ):
        super().__init__(master)
        self.title("框选字幕区域")
        self.resizable(False, False)
        self.transient(master)

        self._path = path
        self._info = info
        self._initial = initial
        self._photo: Optional[tk.PhotoImage] = None
        self._png_path: Optional[str] = None
        self._scale = 1.0
        self._start: Optional[tuple] = None
        self._rect_id: Optional[int] = None
        self._selected: Optional[ft.Region] = None

        self.result: Optional[ft.Region] = None

        self._build_ui()
        self._load_frame(self._default_time())

        self.grab_set()
        self.protocol("WM_DELETE_WINDOW", self._cancel)
        # 居中显示
        self.update_idletasks()
        width = self.winfo_reqwidth()
        height = self.winfo_reqheight()
        x = master.winfo_rootx() + max(0, (master.winfo_width() - width) // 2)
        y = master.winfo_rooty() + max(0, (master.winfo_height() - height) // 4)
        self.geometry(f"+{x}+{y}")

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        pad = {"padx": 10, "pady": 6}

        header = ttk.Frame(self)
        header.pack(fill="x", **pad)
        ttk.Label(
            header,
            text=f"{os.path.basename(self._path)}   {self._info.width}×{self._info.height}",
        ).pack(side="left")

        ttk.Label(header, text="取帧时间(秒)").pack(side="left", padx=(16, 4))
        self.time_var = tk.StringVar(value=f"{self._default_time():.1f}")
        ttk.Spinbox(
            header,
            from_=0,
            to=max(0.0, self._info.duration),
            increment=0.5,
            width=7,
            textvariable=self.time_var,
        ).pack(side="left")
        ttk.Button(header, text="重新取帧", command=self._reload).pack(side="left", padx=6)

        body = ttk.Frame(self)
        body.pack(**pad)
        self.canvas = tk.Canvas(
            body,
            width=min(self._info.width or 640, PREVIEW_MAX_WIDTH),
            height=360,
            background="#101418",
            highlightthickness=1,
            highlightbackground="#3a4550",
            cursor="crosshair",
        )
        self.canvas.pack()
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)

        self.hint = ttk.Label(
            self,
            text="在画面上按住鼠标拖出字幕所在的矩形区域。",
            foreground="#5b6470",
        )
        self.hint.pack(**pad)

        footer = ttk.Frame(self)
        footer.pack(fill="x", **pad)
        self.region_var = tk.StringVar(value="未选择区域")
        ttk.Label(footer, textvariable=self.region_var).pack(side="left")
        ttk.Button(footer, text="取消", command=self._cancel).pack(side="right")
        ttk.Button(footer, text="使用此区域", command=self._confirm).pack(
            side="right", padx=8
        )

    def _default_time(self) -> float:
        duration = self._info.duration
        if duration <= 0:
            return 0.0
        return min(duration - 0.1 if duration > 1 else 0.0, max(0.5, duration * 0.5))

    # -------------------------------------------------------------- 抽帧
    def _reload(self) -> None:
        try:
            seconds = float(self.time_var.get())
        except ValueError:
            messagebox.showwarning("时间格式", "请输入数字，例如 12.5", parent=self)
            return
        seconds = max(0.0, min(seconds, max(0.0, self._info.duration - 0.05)))
        self.time_var.set(f"{seconds:.1f}")
        self._load_frame(seconds)

    def _load_frame(self, seconds: float) -> None:
        video_width = max(2, self._info.width)
        out_width = min(video_width, PREVIEW_MAX_WIDTH)
        if out_width % 2:
            out_width += 1

        if self._png_path and os.path.exists(self._png_path):
            try:
                os.remove(self._png_path)
            except OSError:
                pass
        self._png_path = os.path.join(
            tempfile.gettempdir(),
            f"subremover_preview_{os.getpid()}.png",
        )

        if not ft.extract_frame(self._path, self._png_path, seconds, out_width):
            self.canvas.delete("all")
            self.canvas.create_text(
                200,
                120,
                text="这一帧提取失败，请换个时间点再试",
                fill="#ff8a80",
            )
            return

        self._photo = tk.PhotoImage(file=self._png_path, master=self)
        image_width = self._photo.width()
        image_height = self._photo.height()
        self._scale = image_width / float(video_width)

        self.canvas.config(width=image_width, height=image_height)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, image=self._photo, anchor="nw")

        self._selected = None
        self._rect_id = None
        self.region_var.set("未选择区域")
        if self._initial:
            self._draw_existing(self._initial)

    def _draw_existing(self, region: ft.Region) -> None:
        x0 = region.x * self._scale
        y0 = region.y * self._scale
        x1 = (region.x + region.w) * self._scale
        y1 = (region.y + region.h) * self._scale
        self._rect_id = self.canvas.create_rectangle(
            x0,
            y0,
            x1,
            y1,
            outline="#22d3ee",
            width=2,
            fill="#22d3ee",
            stipple="gray12",
        )
        self._selected = region
        self.region_var.set(
            f"区域：x={region.x}  y={region.y}  宽={region.w}  高={region.h}"
        )

    # -------------------------------------------------------------- 鼠标
    def _on_press(self, event: tk.Event) -> None:
        self._start = (event.x, event.y)
        if self._rect_id:
            self.canvas.delete(self._rect_id)
        self._rect_id = self.canvas.create_rectangle(
            event.x,
            event.y,
            event.x,
            event.y,
            outline="#22d3ee",
            width=2,
            fill="#22d3ee",
            stipple="gray12",
        )

    def _on_drag(self, event: tk.Event) -> None:
        if not self._start or not self._rect_id:
            return
        x0, y0 = self._start
        self.canvas.coords(self._rect_id, x0, y0, event.x, event.y)
        self._update_from_canvas(x0, y0, event.x, event.y)

    def _on_release(self, event: tk.Event) -> None:
        if not self._start:
            return
        x0, y0 = self._start
        x1, y1 = event.x, event.y
        if abs(x1 - x0) < 4 or abs(y1 - y0) < 4:
            self.hint.config(text="框选区域太小了，请重新拖出一个更大的矩形。")
            return
        self._update_from_canvas(x0, y0, x1, y1)
        self._start = None

    def _update_from_canvas(self, x0: int, y0: int, x1: int, y1: int) -> None:
        left, right = sorted((x0, x1))
        top, bottom = sorted((y0, y1))
        scale = self._scale or 1.0
        region = ft.Region(
            int(round(left / scale)),
            int(round(top / scale)),
            int(round((right - left) / scale)),
            int(round((bottom - top) / scale)),
        ).clamped(self._info.width, self._info.height)
        self._selected = region
        self.region_var.set(
            f"区域：x={region.x}  y={region.y}  宽={region.w}  高={region.h}"
        )

    # -------------------------------------------------------------- 结果
    def _confirm(self) -> None:
        if not self._selected:
            messagebox.showinfo("还没有选区", "请先在画面上拖出字幕区域。", parent=self)
            return
        self.result = self._selected
        self._cleanup()
        self.destroy()

    def _cancel(self) -> None:
        self.result = None
        self._cleanup()
        self.destroy()

    def _cleanup(self) -> None:
        if self._png_path and os.path.exists(self._png_path):
            try:
                os.remove(self._png_path)
            except OSError:
                pass
            self._png_path = None


def pick_region(
    master: tk.Misc,
    path: str,
    info: ft.MediaInfo,
    initial: Optional[ft.Region] = None,
) -> Optional[ft.Region]:
    """弹出框选对话框，返回选中区域（视频像素坐标）；取消返回 None。"""
    dialog = RegionPickerDialog(master, path, info, initial)
    master.wait_window(dialog)
    return dialog.result
