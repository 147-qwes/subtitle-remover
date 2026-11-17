# 视频去字幕 / 去水印工具 · Subtitle Remover

一个完全本地运行的 Windows 桌面工具，用来处理视频里三种不同性质的「文字」：

| 类型 | 是什么 | 处理方式 | 是否重新编码 | 耗时 |
| --- | --- | --- | --- | --- |
| **软字幕轨** | 视频文件里独立的字幕流（`.mkv` 内封、`.mp4` 的 `mov_text` 等） | 直接剔除字幕流，视频/音频原样复制 | 否（`-c copy`） | 秒级 |
| **硬字幕 / 水印** | 文字已经烧进画面像素，关不掉 | **AI 修复**：按四周画面重绘文字区域 | 是 | 逐帧推理，较慢 |
| **硬字幕 / 水印（快速档）** | 同上 | 裁切 / 模糊遮罩 / delogo 插值 | 是 | 取决于编码速度 |

三种硬字幕处理方式的区别，一句话：**裁切和模糊会留下"遮挡感"（画面变矮或出现一条模糊带），delogo 背景复杂时会糊；AI 修复是让模型把文字底下的画面重新画出来，不留遮挡，人物和场景细节尽量保留。**

视频不上传、不联网，全部由本机处理。

## 功能特性

- 两种模式一个界面搞定：软字幕轨无损移除、画面内文字去除；
- 画面内文字四种手段：
  - **AI 修复（推荐）**：LaMa 模型重绘文字区域，去字不留遮挡，支持**同时去角标水印 + 底部字幕**；
  - 裁切：切掉底部 / 顶部一条，最快最干净，但画面会变矮；
  - 模糊遮罩：区域打模糊，画面尺寸不变，会留一条模糊带；
  - delogo：按周围像素插值，背景纯色时接近无痕；
- **框选区域**：从视频里抽一帧，在真实画面上拖框选文字位置，自动换算成像素 / 百分比坐标；
- **多区域列表**：水印一块、字幕一块，一次处理完；
- 参数按**百分比**表达，一批不同分辨率的视频可以共用一套参数；
- 批量队列、逐文件进度、日志、失败原因一目了然；
- 输出永不覆盖原文件（同名自动加序号）；
- 命令行模式，可接自动化脚本；
- 隐私：纯本地处理，不产生任何上传。

## 快速开始

### 1. 环境

- Windows 10/11（其他平台源码同样可跑，界面基于 tkinter）
- Python 3.9+（仅源码运行需要，打包后的 exe 不需要）
- ffmpeg / ffprobe
- （可选，AI 修复需要）onnxruntime + numpy + LaMa 模型（约 208 MB）

### 2. 准备 ffmpeg（二选一）

**方式 A：一键脚本（推荐）**

```powershell
powershell -ExecutionPolicy Bypass -File tools\setup_ffmpeg.ps1
```

脚本会把 `ffmpeg.exe`、`ffprobe.exe` 下载并放到项目的 `bin\` 目录（自带 gyan.dev 与 BtbN 两个下载源，一个失败自动换另一个），程序启动时自动识别。

**方式 B：手动安装**

- 下载 <https://www.gyan.dev/ffmpeg/builds/> 的 `ffmpeg-release-essentials.zip`，解压后把 `bin` 目录加入系统 `PATH`；或
- 把 `ffmpeg.exe` / `ffprobe.exe` 直接放进项目 `bin\` 目录；或
- 设置环境变量 `SUBTITLE_REMOVER_FFMPEG_DIR` 指向 ffmpeg 的 `bin` 目录。

### 3. 准备 AI 修复（想"不留遮挡"才需要）

```powershell
pip install -r requirements-inpaint.txt
python tools\download_model.py            # 下载到 models\lama_fp32.onnx
python tools\download_model.py --mirror   # 国内网络慢时换镜像源，支持断点续传
```

有 NVIDIA 显卡（CUDA 12 及以上）时，再装 GPU 加速依赖，速度差一个数量级：

```powershell
pip install -r requirements-inpaint-gpu.txt
```

这些 `nvidia-*` 包只提供运行库，不需要另装 CUDA Toolkit；程序启动时会把
`site-packages\nvidia\*\bin` 自动挂进 DLL 搜索路径，并在处理日志里打印实际用的后端
（`CUDAExecutionProvider` / `CPUExecutionProvider`）。

模型是 Apache-2.0 的 LaMa（Samsung Research），只用于本机推理。
程序启动时会显示模型是否就绪；没装模型也能用，只是少了 AI 修复这一种方式。

实测速度（同一台机器：i5-12450H + RTX 4060 Laptop，720p，角标 + 底部字幕两块）：

| 后端 | 单块 512×512 推理 | 720p 双区域整片处理 |
| --- | --- | --- |
| CUDA（onnxruntime-gpu） | 0.15–0.30 s | 约 0.5–0.8 帧/秒 |
| CPU（onnxruntime） | 4–7 s | 约 0.1–0.2 帧/秒 |

### 4. 运行

```powershell
python main.py
```

窗口打开后：

1. 点「添加视频…」或直接把文件拖进窗口（拖拽需要 `pip install tkinterdnd2`，不装也能用按钮添加）；
2. 选择「软字幕轨移除」或「硬字幕去除」；
3. 硬字幕模式下，点「框选区域…」在画面上拖出文字位置，点「添加当前区域」加入列表；
   角标水印和底部字幕可以分两次框选，一起处理；
4. 设置输出目录（留空 = 与源文件同目录）；
5. 点「开始处理」。

### 5. 打包成 exe（可选）

```powershell
pip install pyinstaller
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```

产物为 `dist\SubtitleRemover.exe` 与 `dist\bin\`（ffmpeg），把这两者放在同一目录即可双击使用。
AI 修复还需要 `models\lama_fp32.onnx`：把它放到 exe 同级的 `models\` 目录下即可。

## 怎么判断该用哪种模式

- 播放器里**可以开关字幕**、或在文件信息里能看到「字幕流 / subtitle track」→ 用**软字幕轨移除**；
- 播放器里字幕**关不掉**，字幕和画面一起缩放、截图里也有字幕 → 是**硬字幕**，用画面内处理；
- 视频旁边有一个同名 `.srt` / `.ass` 文件 → 那只是外挂字幕，直接删文件即可，不用处理视频。

## 四种画面内处理方式怎么选

| 方式 | 原理 | 优点 | 代价 / 限制 |
| --- | --- | --- | --- |
| **AI 修复（inpaint）** | LaMa 参考区域四周内容重绘 | 去字不留遮挡，画面尺寸不变，人物与背景细节保留最好（实测文字区 PSNR 比模糊 / delogo 高 3 dB 以上） | 逐帧推理，必须有模型（208 MB）；GPU 约 0.5–0.8 帧/秒，纯 CPU 很慢 |
| 裁切底部 / 顶部 | `crop` 直接把文字那条切掉 | 最快、最干净，没有残影 | 画面变矮，构图被改 |
| 模糊遮罩 | 区域做 `boxblur` 再叠回原位 | 画面尺寸不变，实现简单、快 | 明显的一条模糊带，等于"遮挡" |
| delogo 修复 | 用区域四周像素插值填补 | 纯色 / 渐变背景几乎无痕，快 | 背景复杂时糊成一片 |

**建议**：要求"字去掉但画面看着还是原样、人物清晰"→ AI 修复；只想去掉底部字幕、能接受画面变矮 → 裁切；必须实时 / 批量而且背景简单 → delogo。

## 命令行用法

```powershell
# 整个文件夹，移除软字幕轨（无损）
python main.py --cli "D:\videos" --mode soft

# AI 修复：同时去角标水印 + 底部字幕（两个区域）
python main.py --cli "D:\videos\ep1.mp4" --mode hard --style inpaint `
  --region-percent 0,0,20,8 --region-percent 0,84,100,16

# AI 修复：像素坐标指定两个区域，输出 CRF 20
python main.py --cli "D:\videos\ep1.mp4" --mode hard --style inpaint `
  --region 0,0,300,60 --region 0,620,1280,90 --crf 20

# 硬字幕：裁掉底部 22%，CRF 20，x265 编码
python main.py --cli "D:\videos\ep1.mp4" --mode hard --style crop_bottom --height-ratio 0.22 --crf 20 --encoder libx265

# 硬字幕：模糊底部 20%（百分比区域，适配不同分辨率）
python main.py --cli "D:\videos" --mode hard --style blur --region-percent 0,80,100,20
```

### 参数

| 参数 | 说明 |
| --- | --- |
| `--mode soft\|hard` | 处理模式，默认 `soft` |
| `--style crop_bottom\|crop_top\|blur\|delogo\|inpaint` | 画面内文字的处理方式 |
| `--region x,y,w,h` | 像素坐标区域，**可重复给多个** |
| `--region-percent x,y,w,h` | 百分比区域，**可重复给多个**，推荐批量使用 |
| `--height-ratio` | 裁切高度比例，默认 `0.18` |
| `--crf` / `--preset` / `--encoder` | 画质与编码速度，默认 `23` / `medium` / `libx264`（AI 修复默认 CRF 走界面设置） |
| `--device auto\|cpu\|cuda` | AI 修复推理设备，默认 `auto`（有 GPU 先用 GPU，失败自动降级 CPU） |
| `--model PATH` | 指定 LaMa 模型路径，默认自动找 `models\` 或环境变量 |
| `--outdir` / `--suffix` | 输出目录 / 文件名后缀（默认 `_nosub`） |
| `--keep-subs 3,5` | soft 模式下保留指定字幕流序号 |
| `-r` / `--recursive` | 递归子目录 |
| `-q` / `--quiet` | 不打印 ffmpeg 日志 |

## 项目结构

```
subtitle-remover/
├── main.py                    # 入口：默认 GUI，--cli 走命令行
├── subremover/
│   ├── ffmpeg_tools.py        # 定位 ffmpeg、ffprobe 解析、命令构建、进度执行
│   ├── remover.py             # 输出路径与单文件处理流程
│   ├── inpaint.py             # AI 修复：LaMa 推理 + 逐帧管道 + 羽化拼接
│   ├── app.py                 # 主窗口逻辑：文件队列 / 参数收集 / 批量执行 / 事件
│   ├── ui.py                  # 界面布局：样式、菜单、控件（app.py 继承 UiMixin）
│   ├── constants.py           # 文件类型、样式文案、预设区域
│   ├── models.py              # 文件列表行数据模型
│   ├── dnd.py                 # 拖拽依赖 tkinterdnd2 的可选探测
│   ├── region_picker.py       # 抽帧 + 拖拽框选区域
│   └── cli.py                 # 命令行
├── tests/test_core.py         # 单元测试（无需安装 ffmpeg / 模型）
├── tools/setup_ffmpeg.ps1     # 下载 ffmpeg 到 bin/
├── tools/download_model.py    # 下载 LaMa 模型到 models/（断点续传）
├── tools/eval_quality.py      # AI 修复 vs delogo vs 模糊 的 PSNR / 裁剪图对比
├── requirements-inpaint.txt   # AI 修复基础依赖（onnxruntime + numpy）
├── requirements-inpaint-gpu.txt # AI 修复 GPU 加速依赖（CUDA 12 运行库）
├── build_exe.ps1              # PyInstaller 打包
└── .github/workflows/ci.yml   # 每次提交跑单元测试
```

## 技术细节

- 软字幕轨移除：
  `ffmpeg -i in.mkv -map 0 -map -0:s -c copy -map_metadata 0 -map_chapters 0 out.mkv`
  （指定保留字幕流时改为显式映射 `-map 0:V? -map 0:a? -map 0:<idx>`）
- AI 修复：
  1. ffmpeg 解码成 `rgb24` 原始帧，通过管道交给 Python；
  2. 每个区域按 `plan_tiles` 拆成若干块（每块核心 440px + 36px 上下文 = 最多 512px），
     这样原始像素**按比例铺满 512×512 输入**，不会因为整条字幕带缩到 512 而把细节抹平；
  3. 区域贴画面边缘时没有外侧上下文，用镜像补边补齐，喂给模型的是真实内容；
  4. 模型输出是 0–255 的 RGB（这个导出不带归一化，代码里不做二次 ×255），
     推理后等比缩回原尺寸，再用羽化 alpha（3px 渐变）与原画面混合，接缝无硬边；
  5. 处理后的帧写回第二个 ffmpeg 进程，`libx264` 编码，音频 `-c:a copy` 直接拷贝；
  6. 支持多区域、取消（两个进程一起终止并删除半成品）；日志会打印模型路径、推理后端和拆块数量；
- 硬字幕裁切：`-vf crop=W:H:0:0`，输出宽高取偶数，兼容 yuv420p；
- 硬字幕模糊：
  `-filter_complex "[0:V]split=2[base][part];[part]crop=W:H:X:Y,boxblur=luma_radius=R:luma_power=2[b];[base][b]overlay=X:Y[v]"`；
- 硬字幕 delogo：`-vf delogo=x=X:y=Y:w=W:h=H`，区域自动收缩到画面内部 1 像素；
- 音频始终 `-c:a copy`，不二次压缩。

## 常见问题

**AI 修复提示「未找到模型」**
运行 `python tools\download_model.py`（国内网络加 `--mirror`）；或设置环境变量 `SUBTITLE_REMOVER_MODEL` 指向模型文件 / 所在目录。程序会拒绝使用下载不完整的模型。

**AI 修复有多快**
只对文字区域推理，不是整帧；区域越大、拆的块越多越慢。720p 两区域在 RTX 4060 上约 0.5–0.8 帧/秒，
纯 CPU 约 0.1–0.2 帧/秒。长视频建议先拿一小段试效果再跑整集，`--preset veryfast` 能省编码时间。

**AI 修复后文字位置还有模糊 / 变形**
先看框选是否盖住了全部文字（尤其是描边、发光、投影）；再确认区域没有小到只剩文字本身——模型需要周围画面作参考，
给区域四周多留 10–20% 空间效果更好。底部字幕可以整条框，程序会自动拆块，不会因为区域宽就变糊。

**画面是纯色块 / 图表 / 大面积渐变的素材，AI 修复会"抹"出一条平滑色带**
这类画面本身没有可供参考的纹理，模型只能用两侧颜色插值，结果就是渐变而不是原文纹理。
纯色背景优先用 delogo（几乎无痕），复杂实拍画面才用 AI 修复。

**日志里显示 CPUExecutionProvider，没用上显卡**
说明 CUDA 运行库没装全或版本不对：`pip install -r requirements-inpaint-gpu.txt` 后重启程序；
或先用 `--device cpu` 排除后端问题。DirectML 后端不可用于这个模型（LaMa 的 FFC 层会报 `80070057`）。

**处理完字幕还在**
多半是外挂字幕：视频同目录有同名 `.srt` / `.ass`，播放器会自动加载，删掉字幕文件即可。

**硬字幕处理很慢**
必须重新编码整段视频。4K 视频用 `--preset veryfast`、`--encoder libx265` 会好一些；裁切比 AI 修复快得多。

**批量视频分辨率不同，区域对不上**
用百分比区域（界面里就是百分比），程序会按每个文件的分辨率换算像素。

## 开发与测试

```powershell
python -m unittest discover -s tests -v
```

测试覆盖：ffprobe JSON 解析、帧率解析、区域换算与边界裁剪、软 / 硬字幕命令构建、输出命名去重、AI 修复的区域扩展 / 掩码映射 / 区域合并 / 羽化 alpha / 贴边镜像补边 / 拆块覆盖。测试不依赖真实 ffmpeg 和模型。

### 效果对比（可复现）

```powershell
python tools\eval_quality.py --sample photo      # 实拍推镜素材
python tools\eval_quality.py --sample testsrc    # 合成彩条：硬边界最考验算法
python tools\eval_quality.py --sample mandel     # 高噪声细节
```

脚本会造一段**无损干净素材**，再把角标水印 + 中文底部字幕烧上去，然后分别用
AI 修复 / delogo / 模糊处理，取同一帧与原图比对「文字区域内 PSNR」，
并在 `%TEMP%\inpaint_eval` 下导出放大裁剪图供肉眼检查。

720p / 25fps / 1.2s 素材，区域内外各取一帧的结果（数值越高越接近原始画面）：

| 素材 | 原始带字 | AI 修复 | delogo | 模糊 |
| --- | --- | --- | --- | --- |
| 实拍推镜（photo） | 15.37 dB | **21.42 dB** | 17.59 dB | 18.30 dB |
| 合成彩条（testsrc） | 14.65 dB | **17.77 dB** | 17.70 dB | 16.01 dB |
| 高噪声细节（mandel） | 19.72 dB | **21.30 dB** | 19.82 dB | 21.08 dB |

三套素材里 AI 修复都优于两种遮挡式做法；差距最大的是实拍画面（比模糊高约 3.1 dB）。
这里只统计文字区域——AI 修复不会留下遮挡带，模糊和 delogo 会把整条区域抹平。
需要说明的是：整片重新编码（CRF 18）本身会让其他区域的 PSNR 从 99 dB 降到约 40 dB，
这是所有需要重编码的做法共同付出的代价，与遮不遮挡无关。

## 许可

MIT License，详见 [LICENSE](LICENSE)。AI 修复模型为 Apache-2.0 的 LaMa（Samsung Research），模型文件不随仓库分发。
