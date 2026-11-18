"""主窗口逻辑：文件队列、参数收集、批量处理、进度与日志事件。

界面控件集中在 ui.py（UiMixin），ffmpeg 细节在 ffmpeg_tools.py，
这样窗口类只处理「状态」和「调度」。
"""

from __future__ import annotations

import os
import queue
import threading
import time
import traceback
import tkinter as tk
from tkinter import filedialog, messagebox, ttk
from typing import Dict, List, Optional

from . import ffmpeg_tools as ft
from . import inpaint, remover, textdet
from .constants import DEVICE_LABELS, STYLE_BY_KEY, STYLE_LABELS, VIDEO_EXTS, VIDEO_FILETYPES
from .dnd import AVAILABLE as DND_AVAILABLE
from .dnd import TkinterDnD
from .models import FileRow
from .region_picker import pick_region
from .ui import UiMixin

_BaseWindow = TkinterDnD.Tk if DND_AVAILABLE else tk.Tk


class SubtitleRemoverApp(UiMixin, _BaseWindow):
    def __init__(self) -> None:
        super().__init__()
        self.title("视频去字幕工具")
        self.geometry("1080x880")
        self.minsize(920, 720)

        self._rows: Dict[str, FileRow] = {}
        self._order: List[str] = []
        self._next_id = 0
        self._region_items: List[tuple] = []
        self._events: "queue.Queue[tuple]" = queue.Queue()
        self._cancel = threading.Event()
        self._running = False

        self._init_style()
        self._build_menu()
        self._build_ui()
        self._on_mode_change()
        self._on_style_change()

        # 模型齐全时默认用"全屏去除字幕"（动态字幕也能去干净），只有 LaMa 时用固定区域重绘
        if inpaint.find_model_path() and textdet.find_det_model():
            self.style_var.set(STYLE_BY_KEY.get("auto", self.style_var.get()))
        elif inpaint.find_model_path():
            self.style_var.set(STYLE_BY_KEY.get("inpaint", self.style_var.get()))

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(120, self._poll_events)
        self._append_log("就绪。添加视频后选择模式，点「开始处理」。")
        self._check_ffmpeg_quiet()

    # ------------------------------------------------------------------ 列表
    def _add_files(self) -> None:
        paths = filedialog.askopenfilenames(
            title="选择视频文件", filetypes=VIDEO_FILETYPES, parent=self
        )
        if paths:
            self._add_paths(list(paths))

    def _add_paths(self, paths: List[str]) -> None:
        new_rows: List[FileRow] = []
        for path in paths:
            path = os.path.abspath(path)
            if not os.path.isfile(path):
                continue
            if any(row.path == path for row in self._rows.values()):
                continue
            if os.path.splitext(path)[1].lower() not in VIDEO_EXTS:
                continue
            self._next_id += 1
            iid = f"row{self._next_id}"
            row = FileRow(iid=iid, path=path, status="读取信息…")
            self._rows[iid] = row
            self._order.append(iid)
            self.tree.insert("", "end", iid=iid, values=self._row_values(row))
            new_rows.append(row)

        self._update_count()
        if new_rows:
            self._append_log(f"添加 {len(new_rows)} 个文件")
            threading.Thread(
                target=self._probe_rows, args=(new_rows,), daemon=True
            ).start()

    def _probe_rows(self, rows: List[FileRow]) -> None:
        for row in rows:
            try:
                info = ft.probe(row.path)
                self._events.put(("probed", row.iid, info, ""))
            except ft.FFmpegNotFound as exc:
                self._events.put(("probed", row.iid, None, str(exc)))
                return
            except Exception as exc:  # 单个文件失败不影响其他文件
                self._events.put(("probed", row.iid, None, str(exc)))

    def _row_values(self, row: FileRow) -> tuple:
        info = row.info
        if not info:
            return (
                os.path.basename(row.path),
                "-",
                "-",
                "-",
                row.status,
            )
        resolution = f"{info.width}×{info.height}" if info.width and info.height else "-"
        return (
            os.path.basename(row.path),
            ft.format_duration(info.duration),
            resolution,
            info.subtitle_label,
            row.status,
        )

    def _refresh_row(self, row: FileRow) -> None:
        if self.tree.exists(row.iid):
            self.tree.item(row.iid, values=self._row_values(row))

    def _update_count(self) -> None:
        self.count_var.set(f"共 {len(self._order)} 个文件")

    def _selected_rows(self) -> List[FileRow]:
        return [self._rows[iid] for iid in self.tree.selection() if iid in self._rows]

    def _remove_selected(self) -> None:
        if self._running:
            messagebox.showinfo("正在处理", "处理过程中不能修改列表。", parent=self)
            return
        for iid in list(self.tree.selection()):
            if iid in self._rows:
                del self._rows[iid]
            if iid in self._order:
                self._order.remove(iid)
            if self.tree.exists(iid):
                self.tree.delete(iid)
        self._update_count()

    def _clear(self) -> None:
        if self._running:
            messagebox.showinfo("正在处理", "处理过程中不能修改列表。", parent=self)
            return
        for iid in list(self.tree.get_children()):
            self.tree.delete(iid)
        self._rows.clear()
        self._order.clear()
        self._update_count()

    def _on_drop(self, event) -> None:
        try:
            paths = list(self.tk.splitlist(event.data))
        except Exception:
            paths = [event.data]
        self._add_paths(paths)

    def _show_row_detail(self, _event=None) -> None:
        rows = self._selected_rows()
        if not rows or not rows[0].info:
            return
        row = rows[0]
        info = row.info
        lines = [
            row.path,
            f"时长：{ft.format_duration(info.duration)}（{info.duration:.2f} 秒）",
            f"分辨率：{info.width}×{info.height}",
            f"封装格式：{info.format_name}",
            "",
            "流列表：",
        ]
        for stream in info.streams:
            lines.append(f"  {stream.codec_type:<9} {stream.label}")
        messagebox.showinfo("文件信息", "\n".join(lines), parent=self)

    # ------------------------------------------------------------------ 参数
    def _on_mode_change(self) -> None:
        hard = self.mode_var.get() == remover.HARD_MODE
        for widget in self.hard_widgets:
            if isinstance(widget, ttk.Combobox):
                widget.configure(state="readonly" if hard else "disabled")
            else:
                widget.configure(state="normal" if hard else "disabled")
        if hard:
            self._on_style_change()
        self.hard_frame.configure(
            text="硬字幕参数（重新编码视频流）" if hard else "硬字幕参数（当前为软字幕模式，无需设置）"
        )

    def _on_style_change(self) -> None:
        if self.mode_var.get() != remover.HARD_MODE:
            return
        style = STYLE_LABELS.get(self.style_var.get(), "crop_bottom")
        crop_mode = style in ("crop_bottom", "crop_top")
        self.ratio_spin.configure(state="normal" if crop_mode else "disabled")

        # 全屏扫描只对「全屏去除字幕」有意义；勾上就整幅画面找字，区域控件全部失效
        full_scan = style == "auto" and bool(self.full_scan_var.get())
        self.full_scan_check.configure(state="normal" if style == "auto" else "disabled")
        self.refine_check.configure(
            state="normal" if style in ("inpaint", "auto") else "disabled"
        )

        region_state = "disabled" if (crop_mode or full_scan) else "normal"
        for widget in self.region_widgets:
            widget.configure(state=region_state)
        for widget in getattr(self, "device_widgets", []):
            if isinstance(widget, ttk.Combobox):
                widget.configure(
                    state="readonly" if style in ("inpaint", "auto") else "disabled"
                )

    def _apply_preset(self, y_ratio: float, w_ratio: float, h_ratio: float) -> None:
        self.region_vars["x"].set("0")
        self.region_vars["y"].set(f"{y_ratio * 100:.1f}")
        self.region_vars["w"].set(f"{w_ratio * 100:.1f}")
        self.region_vars["h"].set(f"{h_ratio * 100:.1f}")
        style = STYLE_LABELS.get(self.style_var.get(), "crop_bottom")
        if style == "crop_bottom":
            self.ratio_var.set(f"{h_ratio * 100:.0f}")
        elif style == "crop_top" and y_ratio == 0.0:
            self.ratio_var.set(f"{h_ratio * 100:.0f}")

    def _pick_region(self) -> None:
        rows = self._selected_rows() or [
            self._rows[iid] for iid in self._order if self._rows[iid].info
        ]
        if not rows:
            messagebox.showinfo("先添加视频", "请先添加一个视频文件再框选区域。", parent=self)
            return
        row = rows[0]
        if row.info is None:
            messagebox.showinfo("还在读取", "文件信息还没读完，请稍等再试。", parent=self)
            return
        if not row.info.has_video_dimensions:
            messagebox.showwarning("没有画面", "该文件没有可识别的视频画面。", parent=self)
            return

        initial = None
        try:
            initial = ft.Region(
                int(float(self.region_vars["x"].get()) * row.info.width / 100),
                int(float(self.region_vars["y"].get()) * row.info.height / 100),
                int(float(self.region_vars["w"].get()) * row.info.width / 100),
                int(float(self.region_vars["h"].get()) * row.info.height / 100),
            )
        except ValueError:
            initial = None

        region = pick_region(self, row.path, row.info, initial)
        if not region:
            return

        relative = ft.RelativeRegion.from_pixels(region, row.info.width, row.info.height)
        self.region_vars["x"].set(f"{relative.x * 100:.1f}")
        self.region_vars["y"].set(f"{relative.y * 100:.1f}")
        self.region_vars["w"].set(f"{relative.w * 100:.1f}")
        self.region_vars["h"].set(f"{relative.h * 100:.1f}")
        style = STYLE_LABELS.get(self.style_var.get(), "crop_bottom")
        if style in ("crop_bottom", "crop_top"):
            self.ratio_var.set(f"{relative.h * 100:.1f}")
            best = "inpaint" if inpaint.find_model_path() else "blur"
            self.style_var.set(STYLE_BY_KEY.get(best, self.style_var.get()))
            self._on_style_change()
            if best == "inpaint":
                self._append_log("已切到「AI 修复」：只重绘框选区域，画面尺寸和人物细节都保留")
        self._append_region(relative.x * 100, relative.y * 100, relative.w * 100, relative.h * 100)
        self._append_log(
            f"区域已加入列表：x={region.x} y={region.y} w={region.w} h={region.h}"
            f"（{row.info.width}×{row.info.height}）"
        )

    def _current_region_values(self) -> tuple:
        return (
            float(self.region_vars["x"].get()),
            float(self.region_vars["y"].get()),
            float(self.region_vars["w"].get()),
            float(self.region_vars["h"].get()),
        )

    def _add_region(self) -> None:
        try:
            x, y, w, h = self._current_region_values()
        except ValueError:
            messagebox.showwarning("参数有误", "区域必须是数字（百分比）", parent=self)
            return
        self._append_region(x, y, w, h)

    def _append_region(self, x: float, y: float, w: float, h: float) -> None:
        if w <= 0 or h <= 0:
            messagebox.showwarning("参数有误", "区域的宽、高必须大于 0", parent=self)
            return
        if x < 0 or y < 0 or x + w > 100.5 or y + h > 100.5:
            messagebox.showwarning(
                "参数有误", "区域超出了画面范围（X/宽、Y/高 相加不能超过 100%）", parent=self
            )
            return
        item = (round(x, 1), round(y, 1), round(w, 1), round(h, 1))
        if item in self._region_items:
            self._append_log("该区域已在列表中")
            return
        self._region_items.append(item)
        # 手动加了区域，说明用户想限定范围：自动关掉全屏扫描，让区域当搜索范围用
        if (
            STYLE_LABELS.get(self.style_var.get()) == "auto"
            and getattr(self, "full_scan_var", None) is not None
            and self.full_scan_var.get()
        ):
            self.full_scan_var.set(False)
            self._on_style_change()
            self._append_log("已关闭全屏扫描：区域将作为检测范围使用（清空列表可回到全屏）")
        self._refresh_region_list()

    def _refresh_region_list(self) -> None:
        self.region_list_box.delete(0, "end")
        for index, (x, y, w, h) in enumerate(self._region_items, 1):
            self.region_list_box.insert(
                "end", f"{index}) x={x:.1f}%  y={y:.1f}%  w={w:.1f}%  h={h:.1f}%"
            )

    def _remove_region(self) -> None:
        selection = list(self.region_list_box.curselection())
        if not selection:
            return
        for index in reversed(selection):
            if 0 <= index < len(self._region_items):
                self._region_items.pop(index)
        self._refresh_region_list()

    def _clear_regions(self) -> None:
        self._region_items.clear()
        self._refresh_region_list()

    def _choose_outdir(self) -> None:
        directory = filedialog.askdirectory(title="选择输出目录", parent=self)
        if directory:
            self.outdir_var.set(directory)

    def _collect_config(self) -> dict:
        mode = self.mode_var.get()
        suffix = self.suffix_var.get().strip() or remover.DEFAULT_SUFFIX
        if any(ch in suffix for ch in '\\/:*?"<>|'):
            raise ValueError("文件名后缀不能包含 \\ / : * ? \" < > |")
        config: dict = {
            "mode": mode,
            "suffix": suffix,
            "outdir": self.outdir_var.get().strip() or None,
        }
        if mode != remover.HARD_MODE:
            return config

        style = STYLE_LABELS.get(self.style_var.get(), "crop_bottom")
        config["style"] = style
        try:
            ratio = float(self.ratio_var.get()) / 100.0
        except ValueError:
            raise ValueError("裁切高度必须是数字（百分比）")
        config["height_ratio"] = min(0.9, max(0.02, ratio))

        try:
            crf = int(float(self.crf_var.get()))
        except ValueError:
            raise ValueError("CRF 必须是数字")
        config["crf"] = min(35, max(0, crf))
        config["preset"] = self.preset_var.get() or "medium"
        config["encoder"] = self.encoder_var.get() or "libx264"
        config["device"] = DEVICE_LABELS.get(self.device_var.get(), "auto")
        config["refine"] = 1 if self.refine_var.get() else 0
        config["region"] = None
        config["regions"] = []

        if style in ("blur", "delogo", "inpaint", "auto"):
            items = list(self._region_items)
            if style == "auto" and self.full_scan_var.get():
                items = []  # 全屏扫描：整幅画面找字，不用框选
            if not items and style != "auto":
                try:
                    items = [self._current_region_values()]
                except ValueError:
                    raise ValueError("字幕区域的 X / Y / 宽 / 高 必须是数字（百分比）")
            if style in ("blur", "delogo") and len(items) > 1:
                raise ValueError(
                    "模糊 / delogo 一次只能处理一个区域；要同时去水印和字幕，请用 AI 修复"
                )
            regions = []
            for x, y, w, h in items:
                if w <= 0 or h <= 0:
                    raise ValueError("字幕区域的宽、高必须大于 0")
                if x < 0 or y < 0 or x + w > 100.5 or y + h > 100.5:
                    raise ValueError("字幕区域超出了画面范围（X+宽、Y+高 不能超过 100%）")
                regions.append(ft.RelativeRegion(x / 100.0, y / 100.0, w / 100.0, h / 100.0))
            config["regions"] = regions
            config["region"] = regions[0] if regions else None
        return config

    # ------------------------------------------------------------------ 执行
    def _start(self) -> None:
        if self._running:
            return
        if not self._order:
            messagebox.showinfo("没有文件", "请先添加要处理的视频文件。", parent=self)
            return
        try:
            ft.require_binaries()
        except ft.FFmpegNotFound as exc:
            messagebox.showerror("缺少 ffmpeg", str(exc), parent=self)
            return
        try:
            config = self._collect_config()
        except ValueError as exc:
            messagebox.showwarning("参数有误", str(exc), parent=self)
            return

        rows = [self._rows[iid] for iid in self._order]
        self._running = True
        self._cancel.clear()
        self.start_btn.configure(state="disabled")
        self.cancel_btn.configure(state="normal")
        self.progress.configure(value=0)
        for row in rows:
            row.status = "排队中"
            row.note = ""
            self._refresh_row(row)
        self._append_log("\n———— 开始批量处理 ————")
        threading.Thread(
            target=self._process_rows, args=(rows, config), daemon=True
        ).start()

    def _process_rows(self, rows: List[FileRow], config: dict) -> None:
        total = len(rows)
        success = 0
        failed = 0
        cancelled = False
        started_at = time.time()

        for index, row in enumerate(rows, 1):
            if self._cancel.is_set():
                cancelled = True
                break

            self._events.put(("status", row.iid, "处理中…"))
            self._events.put(("overall", index, total, os.path.basename(row.path)))
            self._events.put(("progress", 0.0, 0.0))
            self._events.put(
                ("log", f"\n=== [{index}/{total}] {os.path.basename(row.path)} ===")
            )

            if not os.path.isfile(row.path):
                self._events.put(("status", row.iid, "文件不存在"))
                failed += 1
                continue

            info = row.info
            if info is None:
                try:
                    info = ft.probe(row.path)
                    self._events.put(("probed", row.iid, info, ""))
                except Exception as exc:
                    self._events.put(("status", row.iid, "读取失败"))
                    self._events.put(("log", f"读取失败：{exc}"))
                    failed += 1
                    continue

            dst = remover.output_path(row.path, config.get("outdir"), config["suffix"])

            mode = config["mode"]
            region = None
            regions_px: List[ft.Region] = []
            if mode == remover.HARD_MODE:
                if config.get("regions"):
                    if not info.has_video_dimensions:
                        self._events.put(("status", row.iid, "无视频画面"))
                        failed += 1
                        continue
                    regions_px = [
                        r.to_pixels(info.width, info.height) for r in config["regions"]
                    ]
                    region = regions_px[0] if regions_px else None
                    for item in regions_px:
                        self._events.put(
                            (
                                "log",
                                f"修复区域（像素）：x={item.x} y={item.y} "
                                f"w={item.w} h={item.h}",
                            )
                        )
                if config["style"] == "auto" and not config.get("regions"):
                    self._events.put(("log", "模式：硬字幕去除 / 全屏扫描（整幅画面逐帧找字）"))
                else:
                    self._events.put(("log", f"模式：硬字幕去除 / {config['style']}"))
            else:
                self._events.put(("log", "模式：软字幕轨移除（-c copy）"))

            self._events.put(("log", f"输出：{dst}"))

            def on_progress(fraction: float, seconds: float) -> None:
                self._events.put(("progress", fraction, seconds))

            def on_log(line: str) -> None:
                self._events.put(("log", line))

            try:
                if mode == remover.HARD_MODE:
                    result = remover.remove_hard_subtitles(
                        row.path,
                        dst,
                        info,
                        config["style"],
                        region=region,
                        regions=regions_px,
                        height_ratio=config.get("height_ratio"),
                        crf=config.get("crf", 23),
                        preset=config.get("preset", "medium"),
                        encoder=config.get("encoder", "libx264"),
                        device=config.get("device", "auto"),
                        refine=config.get("refine", 0),
                        on_progress=on_progress,
                        on_log=on_log,
                        cancel=self._cancel,
                    )
                else:
                    result = remover.remove_soft_subtitles(
                        row.path,
                        dst,
                        keep_sub_indices=None,
                        duration=info.duration,
                        on_progress=on_progress,
                        on_log=on_log,
                        cancel=self._cancel,
                    )
            except Exception as exc:
                self._events.put(("status", row.iid, "失败"))
                self._events.put(("log", f"处理异常：{exc}"))
                self._events.put(("log", traceback.format_exc(limit=3)))
                failed += 1
                continue

            if result.cancelled:
                self._events.put(("status", row.iid, "已取消"))
                self._events.put(("progress", 0.0, 0.0))
                cancelled = True
                break

            if result.ok and os.path.exists(dst):
                success += 1
                size = ft.format_size(os.path.getsize(dst))
                self._events.put(("status", row.iid, "完成"))
                self._events.put(
                    ("log", f"✔ 完成，用时 {result.seconds:.1f}s，输出大小 {size}")
                )
            else:
                failed += 1
                self._events.put(("status", row.iid, "失败"))
                self._events.put(("log", f"✘ ffmpeg 返回码 {result.returncode}"))
                if result.log_tail:
                    self._events.put(("log", result.log_tail))

        elapsed = time.time() - started_at
        self._events.put(("finished", success, failed, cancelled, elapsed))

    def _cancel_run(self) -> None:
        if not self._running:
            return
        self._cancel.set()
        self.status_var.set("正在取消，等待当前文件停止…")
        self._append_log("已请求取消…")

    # ------------------------------------------------------------------ 事件
    def _poll_events(self) -> None:
        try:
            while True:
                event = self._events.get_nowait()
                self._handle_event(event)
        except queue.Empty:
            pass
        self.after(120, self._poll_events)

    def _handle_event(self, event: tuple) -> None:
        kind = event[0]
        if kind == "probed":
            _, iid, info, error = event
            row = self._rows.get(iid)
            if not row:
                return
            if info:
                row.info = info
                row.status = "待处理"
            else:
                row.status = "读取失败"
                row.note = error
                self._append_log(f"⚠ {os.path.basename(row.path)}：{error.splitlines()[0]}")
            self._refresh_row(row)
        elif kind == "status":
            _, iid, text = event
            row = self._rows.get(iid)
            if row:
                row.status = text
                self._refresh_row(row)
        elif kind == "overall":
            _, index, total, name = event
            self.status_var.set(f"第 {index}/{total} 个：{name}")
        elif kind == "progress":
            _, fraction, seconds = event
            self.progress.configure(value=max(0.0, min(100.0, fraction * 100)))
        elif kind == "log":
            self._append_log(event[1])
        elif kind == "finished":
            _, success, failed, cancelled, elapsed = event
            self._running = False
            self.start_btn.configure(state="normal")
            self.cancel_btn.configure(state="disabled")
            self.progress.configure(value=100 if not cancelled else 0)
            summary = f"处理结束：成功 {success} 个，失败 {failed} 个，用时 {elapsed:.1f}s"
            if cancelled:
                summary = f"已取消：成功 {success} 个，失败 {failed} 个，用时 {elapsed:.1f}s"
            self.status_var.set(summary)
            self._append_log(summary)
            if self.open_after_var.get() and success:
                self._open_outdir()
            messagebox.showinfo("处理结束", summary, parent=self)

    def _append_log(self, text: str) -> None:
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # ------------------------------------------------------------------ 杂项
    def _check_ffmpeg_quiet(self) -> None:
        try:
            ffmpeg, _ffprobe = ft.require_binaries()
        except ft.FFmpegNotFound:
            self._append_log("⚠ 未检测到 ffmpeg，请先运行 tools/setup_ffmpeg.ps1 或安装 ffmpeg。")
            return
        self._append_log(f"ffmpeg：{ffmpeg}")
        model = inpaint.find_model_path()
        if model:
            self._append_log(f"AI 修复模型：{model}")
        else:
            self._append_log(
                "AI 修复未就绪：运行 python tools\\download_model.py 下载模型后，可做到去字不留遮挡"
            )
        det = textdet.find_det_model()
        if det:
            self._append_log(f"文字检测模型：{det}（动态字幕用）")
        else:
            self._append_log(
                "动态字幕未就绪：运行 python tools\\download_model.py --det 下载检测模型（4.5 MB）"
            )

    def _show_ffmpeg_info(self) -> None:
        ft.clear_binary_cache()
        try:
            ffmpeg, ffprobe = ft.require_binaries()
        except ft.FFmpegNotFound as exc:
            messagebox.showerror("ffmpeg 不可用", str(exc), parent=self)
            return
        version = ""
        completed = ft.run_quiet([ffmpeg, "-version"])
        if completed.returncode == 0 and completed.stdout:
            version = completed.stdout.splitlines()[0]
        messagebox.showinfo(
            "ffmpeg 环境",
            f"ffmpeg：{ffmpeg}\nffprobe：{ffprobe}\n\n{version}",
            parent=self,
        )

    def _open_outdir(self) -> None:
        directory = self.outdir_var.get().strip()
        if not directory:
            rows = [self._rows[iid] for iid in self._order]
            if not rows:
                messagebox.showinfo("没有输出目录", "输出目录为空时与源文件同目录。", parent=self)
                return
            directory = os.path.dirname(rows[0].path)
        if not os.path.isdir(directory):
            messagebox.showinfo("目录不存在", directory, parent=self)
            return
        if os.name == "nt":
            os.startfile(directory)  # noqa: S606 - 仅打开资源管理器
        else:
            self._append_log(f"输出目录：{directory}")

    def _show_help(self) -> None:
        text = (
            "使用步骤\n"
            "1. 添加视频（可拖拽；未安装 tkinterdnd2 时用「添加视频」按钮）\n"
            "2. 选择模式：\n"
            "   · 软字幕轨移除：视频内的字幕流（mkv 内封、mp4 的 mov_text 等），\n"
            "     -c copy 无损移除，几秒完成。\n"
            "   · 硬字幕 / 水印去除：文字已经烧进画面。\n"
            "     - 全屏去除字幕（推荐）：保持「全屏扫描」勾着，整幅画面逐帧找字，\n"
            "       滚动字幕、逐句换位、字幕会动都能去；只重绘文字笔画那几个像素，\n"
            "       画面主体和背景保持原样，被字压住的纹理能顺结构接回去；\n"
            "     - AI 修复：只有字幕位置固定时用它，点「框选区域…」框住文字位置；\n"
            "     - 裁切：直接切掉底部/顶部一条，画面变矮，最快最好；\n"
            "     - 模糊：字幕位置打模糊，会留下模糊带；\n"
            "     - delogo：按周围像素插值，背景复杂时会糊。\n"
            "3. 全屏扫描时「待修复区域」不需要用；如果你想限定范围（比如只去底部字幕、\n"
            "   别动画面里的招牌），框一块或加一块区域，程序会自动退出全屏扫描、\n"
            "   把区域当搜索范围用。\n"
            "4. 「修完自检」：勾上后每帧修完再检测一遍，有漏字就地补修（慢约 30%，默认关）。\n"
            "5. 设置输出目录（留空 = 源文件同目录），点「开始处理」。\n\n"
            "提示：全屏去除每帧都要检测 + 推理，显卡上大约 0.8-1.1 帧/秒；\n"
            "字幕位置固定、又想要更快的，可以用「AI 修复」只修框住的那一块。"
        )
        messagebox.showinfo("使用说明", text, parent=self)

    def _show_about(self) -> None:
        messagebox.showinfo(
            "关于",
            "视频去字幕工具 1.0\n\n"
            "软字幕轨无损移除 + 硬字幕逐帧检测/AI 重绘/裁切/模糊/delogo\n"
            "基于 Python + tkinter + ffmpeg，完全本地处理，不上传视频。",
            parent=self,
        )

    def _on_close(self) -> None:
        if self._running:
            if not messagebox.askyesno("正在处理", "任务还在进行，确定要退出吗？", parent=self):
                return
            self._cancel.set()
            time.sleep(0.3)
        self.destroy()


def run_gui() -> None:
    if os.name == "nt":
        try:
            from ctypes import windll

            windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
    app = SubtitleRemoverApp()
    app.mainloop()
