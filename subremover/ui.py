"""主窗口界面构建：样式、菜单、布局。

界面代码集中在这里，避免主窗口类被上千行控件代码淹没；
SubtitleRemoverApp 通过继承 UiMixin 获得这些方法。
"""

from __future__ import annotations

import os
import tkinter as tk
from tkinter import ttk
from typing import Dict, List

from . import remover
from .constants import DEVICE_KEYS, PRESETS, STYLE_KEYS
from .dnd import AVAILABLE as DND_AVAILABLE
from .dnd import DND_FILES


class UiMixin:
    # ------------------------------------------------------------------ 样式
    def _init_style(self) -> None:
        base_font = ("Microsoft YaHei UI", 9) if os.name == "nt" else ("Helvetica", 10)
        self.option_add("*Font", base_font)
        style = ttk.Style(self)
        try:
            style.theme_use("vista" if os.name == "nt" else "clam")
        except tk.TclError:
            pass
        style.configure(".", font=base_font)
        style.configure("Treeview", rowheight=24)
        style.configure("Title.TLabel", font=(base_font[0], 11, "bold"))

    # ------------------------------------------------------------------ 菜单
    def _build_menu(self) -> None:
        menubar = tk.Menu(self)

        file_menu = tk.Menu(menubar, tearoff=0)
        file_menu.add_command(label="添加视频…", command=self._add_files)
        file_menu.add_command(label="移除选中", command=self._remove_selected)
        file_menu.add_separator()
        file_menu.add_command(label="退出", command=self._on_close)
        menubar.add_cascade(label="文件", menu=file_menu)

        tool_menu = tk.Menu(menubar, tearoff=0)
        tool_menu.add_command(label="检查 ffmpeg 环境", command=self._show_ffmpeg_info)
        tool_menu.add_command(label="打开输出目录", command=self._open_outdir)
        menubar.add_cascade(label="工具", menu=tool_menu)

        help_menu = tk.Menu(menubar, tearoff=0)
        help_menu.add_command(label="使用说明", command=self._show_help)
        help_menu.add_command(label="关于", command=self._show_about)
        menubar.add_cascade(label="帮助", menu=help_menu)

        self.config(menu=menubar)

    # ------------------------------------------------------------------ UI
    def _build_ui(self) -> None:
        main = ttk.Frame(self, padding=10)
        main.pack(fill="both", expand=True)
        main.columnconfigure(0, weight=1)
        main.rowconfigure(1, weight=1)

        # 工具栏
        toolbar = ttk.Frame(main)
        toolbar.grid(row=0, column=0, sticky="ew", pady=(0, 6))
        ttk.Button(toolbar, text="添加视频…", command=self._add_files).pack(side="left")
        ttk.Button(toolbar, text="移除选中", command=self._remove_selected).pack(
            side="left", padx=6
        )
        ttk.Button(toolbar, text="清空列表", command=self._clear).pack(side="left")
        self.count_var = tk.StringVar(value="共 0 个文件")
        ttk.Label(toolbar, textvariable=self.count_var, foreground="#6b7480").pack(
            side="right"
        )

        # 文件列表
        list_frame = ttk.Frame(main)
        list_frame.grid(row=1, column=0, sticky="nsew")
        list_frame.columnconfigure(0, weight=1)
        list_frame.rowconfigure(0, weight=1)

        columns = ("name", "duration", "resolution", "subs", "status")
        self.tree = ttk.Treeview(
            list_frame,
            columns=columns,
            show="headings",
            selectmode="extended",
            height=7,
        )
        headings = {
            "name": ("文件名", 380, "w"),
            "duration": ("时长", 80, "center"),
            "resolution": ("分辨率", 100, "center"),
            "subs": ("字幕轨", 260, "w"),
            "status": ("状态", 120, "center"),
        }
        for key, (text, width, anchor) in headings.items():
            self.tree.heading(key, text=text)
            self.tree.column(key, width=width, anchor=anchor, stretch=key in ("name", "subs"))
        self.tree.grid(row=0, column=0, sticky="nsew")

        scroll = ttk.Scrollbar(list_frame, orient="vertical", command=self.tree.yview)
        scroll.grid(row=0, column=1, sticky="ns")
        self.tree.configure(yscrollcommand=scroll.set)
        self.tree.bind("<Double-1>", self._show_row_detail)

        if DND_AVAILABLE:
            for widget in (self, self.tree):
                try:
                    widget.drop_target_register(DND_FILES)
                    widget.dnd_bind("<<Drop>>", self._on_drop)
                except Exception:
                    pass

        # 处理模式
        mode_frame = ttk.LabelFrame(main, text="处理模式", padding=10)
        mode_frame.grid(row=2, column=0, sticky="ew", pady=6)
        self.mode_var = tk.StringVar(value=remover.SOFT_MODE)
        ttk.Radiobutton(
            mode_frame,
            text="软字幕轨移除（无损，不重新编码，秒级完成）",
            value=remover.SOFT_MODE,
            variable=self.mode_var,
            command=self._on_mode_change,
        ).grid(row=0, column=0, sticky="w")
        ttk.Radiobutton(
            mode_frame,
            text="硬字幕去除（字幕烧在画面里，需重新编码，耗时较长）",
            value=remover.HARD_MODE,
            variable=self.mode_var,
            command=self._on_mode_change,
        ).grid(row=1, column=0, sticky="w", pady=(4, 0))

        # 硬字幕参数
        self.hard_frame = ttk.LabelFrame(main, text="硬字幕参数", padding=10)
        self.hard_frame.grid(row=3, column=0, sticky="ew")
        self.hard_frame.columnconfigure(5, weight=1)

        ttk.Label(self.hard_frame, text="去除方式").grid(row=0, column=0, sticky="w")
        self.style_var = tk.StringVar(value=STYLE_KEYS[0])
        self.style_combo = ttk.Combobox(
            self.hard_frame,
            textvariable=self.style_var,
            values=STYLE_KEYS,
            state="readonly",
            width=24,
        )
        self.style_combo.grid(row=0, column=1, sticky="w", padx=(8, 20))
        self.style_combo.bind("<<ComboboxSelected>>", lambda _e: self._on_style_change())

        ttk.Label(self.hard_frame, text="裁切高度(%)").grid(row=0, column=2, sticky="w")
        self.ratio_var = tk.StringVar(value="18")
        self.ratio_spin = ttk.Spinbox(
            self.hard_frame, from_=2, to=45, increment=1, width=6, textvariable=self.ratio_var
        )
        self.ratio_spin.grid(row=0, column=3, sticky="w", padx=(8, 20))

        ttk.Label(self.hard_frame, text="质量 CRF").grid(row=0, column=4, sticky="w")
        self.crf_var = tk.StringVar(value="23")
        self.crf_spin = ttk.Spinbox(
            self.hard_frame, from_=16, to=32, increment=1, width=6, textvariable=self.crf_var
        )
        self.crf_spin.grid(row=0, column=5, sticky="w", padx=(8, 0))

        # 区域行
        self.region_widgets: List[tk.Widget] = []
        ttk.Label(self.hard_frame, text="字幕区域(%)").grid(
            row=1, column=0, sticky="w", pady=(10, 0)
        )
        region_row = ttk.Frame(self.hard_frame)
        region_row.grid(row=1, column=1, columnspan=5, sticky="w", pady=(10, 0))

        self.region_vars: Dict[str, tk.StringVar] = {
            "x": tk.StringVar(value="0"),
            "y": tk.StringVar(value="80"),
            "w": tk.StringVar(value="100"),
            "h": tk.StringVar(value="20"),
        }
        for key, label in (("x", "X"), ("y", "Y"), ("w", "宽"), ("h", "高")):
            ttk.Label(region_row, text=label).pack(side="left")
            spin = ttk.Spinbox(
                region_row,
                from_=0,
                to=100,
                increment=0.5,
                width=6,
                textvariable=self.region_vars[key],
            )
            spin.pack(side="left", padx=(4, 10))
            self.region_widgets.append(spin)

        self.pick_btn = ttk.Button(region_row, text="框选区域…", command=self._pick_region)
        self.pick_btn.pack(side="left", padx=(0, 12))
        self.region_widgets.append(self.pick_btn)

        for label, y_ratio, w_ratio, h_ratio in PRESETS:
            btn = ttk.Button(
                region_row,
                text=label,
                width=9,
                command=lambda y=y_ratio, w=w_ratio, h=h_ratio: self._apply_preset(y, w, h),
            )
            btn.pack(side="left", padx=(0, 4))
            self.region_widgets.append(btn)

        # 区域列表：可以同时框住角标水印和底部字幕
        ttk.Label(self.hard_frame, text="待修复区域").grid(
            row=3, column=0, sticky="nw", pady=(10, 0)
        )
        list_row = ttk.Frame(self.hard_frame)
        list_row.grid(row=3, column=1, columnspan=5, sticky="ew", pady=(10, 0))
        list_row.columnconfigure(0, weight=1)

        self.region_list_box = tk.Listbox(
            list_row, height=3, exportselection=False, activestyle="none"
        )
        self.region_list_box.grid(row=0, column=0, sticky="ew")

        region_buttons = ttk.Frame(list_row)
        region_buttons.grid(row=0, column=1, sticky="n", padx=(8, 0))
        ttk.Button(region_buttons, text="添加当前区域", command=self._add_region).pack(fill="x")
        ttk.Button(region_buttons, text="删除选中", command=self._remove_region).pack(
            fill="x", pady=4
        )
        ttk.Button(region_buttons, text="清空", command=self._clear_regions).pack(fill="x")

        self.region_widgets.append(self.region_list_box)

        # 全屏扫描 / 自检补修：整幅画面找字，一个框都不用框
        scan_row = ttk.Frame(self.hard_frame)
        scan_row.grid(row=4, column=0, columnspan=6, sticky="w", pady=(8, 0))
        self.full_scan_var = tk.BooleanVar(value=True)
        self.full_scan_check = ttk.Checkbutton(
            scan_row,
            text="全屏扫描：整幅画面找字（不框选区域，字幕在哪都能去掉）",
            variable=self.full_scan_var,
            command=self._on_style_change,
        )
        self.full_scan_check.pack(side="left")
        self.refine_var = tk.BooleanVar(value=False)
        self.refine_check = ttk.Checkbutton(
            scan_row,
            text="修完自检：还有淡字影就自动补修（慢约 30%）",
            variable=self.refine_var,
        )
        self.refine_check.pack(side="left", padx=(18, 0))

        # 编码行
        ttk.Label(self.hard_frame, text="编码器 / 预设").grid(
            row=2, column=0, sticky="w", pady=(10, 0)
        )
        encode_row = ttk.Frame(self.hard_frame)
        encode_row.grid(row=2, column=1, columnspan=5, sticky="w", pady=(10, 0))
        self.encoder_var = tk.StringVar(value="libx264")
        self.encoder_combo = ttk.Combobox(
            encode_row,
            textvariable=self.encoder_var,
            values=["libx264", "libx265"],
            state="readonly",
            width=10,
        )
        self.encoder_combo.pack(side="left")
        self.preset_var = tk.StringVar(value="medium")
        self.preset_combo = ttk.Combobox(
            encode_row,
            textvariable=self.preset_var,
            values=["ultrafast", "veryfast", "faster", "fast", "medium", "slow"],
            state="readonly",
            width=11,
        )
        self.preset_combo.pack(side="left", padx=(10, 0))
        ttk.Label(
            encode_row,
            text="CRF 越小画质越好、文件越大；字幕位置固定时才建议裁切",
            foreground="#6b7480",
        ).pack(side="left", padx=(12, 0))
        self.encoder_widgets = [self.encoder_combo, self.preset_combo, self.crf_spin]

        # AI 推理设备（只有 AI 修复 / 全屏去除用得到）
        ttk.Label(self.hard_frame, text="AI 推理设备").grid(
            row=5, column=0, sticky="w", pady=(8, 0)
        )
        device_row = ttk.Frame(self.hard_frame)
        device_row.grid(row=5, column=1, columnspan=5, sticky="w", pady=(8, 0))
        self.device_var = tk.StringVar(value=DEVICE_KEYS[0])
        self.device_combo = ttk.Combobox(
            device_row,
            textvariable=self.device_var,
            values=DEVICE_KEYS,
            state="readonly",
            width=22,
        )
        self.device_combo.pack(side="left")
        ttk.Label(
            device_row,
            text="显卡后端不可用时会自动降级到 CPU，不会中断任务",
            foreground="#6b7480",
        ).pack(side="left", padx=(12, 0))
        self.device_widgets = [self.device_combo]

        # 输出
        out_frame = ttk.LabelFrame(main, text="输出", padding=10)
        out_frame.grid(row=4, column=0, sticky="ew", pady=6)
        out_frame.columnconfigure(1, weight=1)

        ttk.Label(out_frame, text="输出目录").grid(row=0, column=0, sticky="w")
        self.outdir_var = tk.StringVar(value="")
        out_entry = ttk.Entry(out_frame, textvariable=self.outdir_var)
        out_entry.grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(out_frame, text="选择…", command=self._choose_outdir).grid(row=0, column=2)
        ttk.Button(out_frame, text="清空", command=lambda: self.outdir_var.set("")).grid(
            row=0, column=3, padx=(6, 0)
        )

        ttk.Label(out_frame, text="文件名后缀").grid(row=1, column=0, sticky="w", pady=(8, 0))
        self.suffix_var = tk.StringVar(value=remover.DEFAULT_SUFFIX)
        ttk.Entry(out_frame, textvariable=self.suffix_var, width=16).grid(
            row=1, column=1, sticky="w", padx=8, pady=(8, 0)
        )
        ttk.Label(
            out_frame,
            text="输出目录留空 = 与源文件同目录；同名文件自动加序号，不覆盖原文件",
            foreground="#6b7480",
        ).grid(row=1, column=2, columnspan=2, sticky="w", pady=(8, 0))

        self.open_after_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            out_frame, text="完成后打开输出目录", variable=self.open_after_var
        ).grid(row=2, column=0, columnspan=3, sticky="w", pady=(8, 0))

        # 操作区
        action = ttk.Frame(main)
        action.grid(row=5, column=0, sticky="ew", pady=(6, 0))
        action.columnconfigure(1, weight=1)

        self.start_btn = ttk.Button(action, text="开始处理", command=self._start)
        self.start_btn.grid(row=0, column=0, sticky="w")
        self.cancel_btn = ttk.Button(
            action, text="取消", command=self._cancel_run, state="disabled"
        )
        self.cancel_btn.grid(row=0, column=2, sticky="e", padx=(8, 0))

        self.progress = ttk.Progressbar(action, maximum=100, value=0)
        self.progress.grid(row=0, column=1, sticky="ew", padx=12)

        self.status_var = tk.StringVar(value="空闲")
        ttk.Label(main, textvariable=self.status_var, foreground="#42505f").grid(
            row=6, column=0, sticky="w", pady=(6, 0)
        )

        # 日志
        log_frame = ttk.LabelFrame(main, text="处理日志", padding=6)
        log_frame.grid(row=7, column=0, sticky="ew", pady=(6, 0))
        log_frame.columnconfigure(0, weight=1)
        from tkinter import scrolledtext

        self.log_text = scrolledtext.ScrolledText(
            log_frame,
            height=8,
            wrap="word",
            state="disabled",
            font=("Consolas", 9),
            background="#1b1f24",
            foreground="#d6dee8",
            insertbackground="#d6dee8",
        )
        self.log_text.grid(row=0, column=0, sticky="ew")

        # 模式联动
        self.hard_widgets = (
            [self.style_combo, self.ratio_spin, self.full_scan_check, self.refine_check]
            + self.region_widgets
            + self.encoder_widgets
            + self.device_widgets
        )
