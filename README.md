# 视频去字幕 / 去水印工具 · Subtitle Remover

一个完全本地运行的 Windows 桌面工具，用来处理视频里三种不同性质的「文字」：

| 类型 | 是什么 | 处理方式 | 是否重新编码 | 耗时 |
| --- | --- | --- | --- | --- |
| **软字幕轨** | 视频文件里独立的字幕流（`.mkv` 内封、`.mp4` 的 `mov_text` 等） | 直接剔除字幕流，视频/音频原样复制 | 否（`-c copy`） | 秒级 |
| **硬字幕 / 水印** | 文字已经烧进画面像素，关不掉 | **AI 修复**：按四周画面重绘文字区域 | 是 | 逐帧推理，较慢 |
| **会动的硬字幕**（滚动、逐句换位、弹幕） | 字幕每帧位置都在变 | **自动检测**：每帧先找字、再就地重绘 | 是 | 逐帧检测 + 推理，GPU 约 1.4 帧/秒 |
| **硬字幕 / 水印（快速档）** | 同上 | 裁切 / 模糊遮罩 / delogo 插值 | 是 | 取决于编码速度 |

三种硬字幕处理方式的区别，一句话：**裁切和模糊会留下\"遮挡感\"（画面变矮或出现一条模糊带），delogo 背景复杂时会糊；AI 修复是让模型把文字底下的画面重新画出来，不留遮挡，人物和场景细节尽量保留。**

视频不上传、不联网，全部由本机处理。

## 功能特性

- 两种模式一个界面搞定：软字幕轨无损移除、画面内文字去除；
- 画面内文字五种手段：
  - **自动检测字幕（动态字幕首选）**：每帧先用 PP-OCRv4 检测出文字位置，字在哪就在哪重绘。
    滚动字幕、逐句换位、上下跳动的台词都能去干净；框选区域只当\"搜索范围\"，一个框都不用给；
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
powershell -ExecutionPolicy Bypass -File tools\\setup_ffmpeg.ps1
```

脚本会把 `ffmpeg.exe`、`ffprobe.exe` 下载并放到项目的 `bin\\` 目录（自带 gyan.dev 与 BtbN 两个下载源，一个失败自动换另一个），程序启动时自动识别。

**方式 B：手动安装**

- 下载 <https://www.gyan.dev/ffmpeg/builds/> 的 `ffmpeg-release-essentials.zip`，解压后把 `bin` 目录加入系统 `PATH`；或
- 把 `ffmpeg.exe` / `ffprobe.exe` 直接放进项目 `bin\\` 目录；或
- 设置环境变量 `SUBTITLE_REMOVER_FFMPEG_DIR` 指向 ffmpeg 的 `bin` 目录。

### 3. 准备 AI 修复（想\"不留遮挡\"才需要）

```powershell
pip install -r requirements-inpaint.txt
python tools\\download_model.py            # 下载到 models\\lama_fp32.onnx（重绘用）
python tools\\download_model.py --det      # 下载到 models\\ppocr_v4_det.onnx（找字用，4.5 MB）
python tools\\download_model.py --all      # 两个一起下
python tools\\download_model.py --mirror   # 国内网络慢时换镜像源，支持断点续传
```

有 NVIDIA 显卡（CUDA 12 及以上）时，再装 GPU 加速依赖，速度差一个数量级：

```powershell
pip install -r requirements-inpaint-gpu.txt
```

这些 `nvidia-*` 包只提供运行库，不需要另装 CUDA Toolkit；程序启动时会把
`site-packages\\nvidia\\*\\bin` 自动挂进 DLL 搜索路径，并在处理日志里打印实际用的后端
（`CUDAExecutionProvider` / `CPUExecutionProvider`）。

模型是 Apache-2.0 的 LaMa（Samsung Research）；找字用的是 PaddleOCR PP-OCRv4 文字检测（Apache-2.0，经 RapidOCR 导出的 ONNX），两个模型都只用于本机推理。
程序启动时会显示模型是否就绪；没装模型也能用，只是少了 AI 修复 / 自动检测这两档。

实测速度（同一台机器：i5-12450H + RTX 4060 Laptop，720p，角标 + 底部字幕两块）：

| 后端 | 单块 512×512 推理 | 720p 双区域整片处理（不含模型加载） |
| --- | --- | --- |
| CUDA（onnxruntime-gpu） | 0.16–0.32 s | 1.15 帧/秒；开两路并行 1.48 帧/秒 |
| CPU（onnxruntime） | 4–7 s | 约 0.1–0.2 帧/秒 |

长片默认开两路并行推理：CUDA 里有相当一部分算子会回退到 CPU（图里被插入了 288 个
`Memcpy` 节点），单路跑的时候显卡有一半时间在等这些停顿；两条独立会话交替推理就能把
停顿叠起来吃掉。200 帧 720p 双区域实测 174.6 s → 134.9 s（+29%），输出逐像素一致。
短于 100 帧的片子不开并行——多加载一次模型的那十几秒反而更亏。命令行可用
`--parallel 1` 强制单路，`--parallel 2` 强制双路。

### 4. 运行

```powershell
python main.py
```

窗口打开后：

1. 点「添加视频…」或直接把文件拖进窗口（拖拽需要 `pip install tkinterdnd2`，不装也能用按钮添加）；
2. 选择「软字幕轨移除」或「硬字幕去除」；
3. 硬字幕模式下选处理方式：
   - **自动检测字幕**（默认，模型齐全时）：字幕会动就选它，一个框都不用框；
   - **AI 修复**：字幕位置固定时用它，点「框选区域…」在画面上拖出文字位置，点「添加当前区域」加入列表；
     角标水印和底部字幕可以分两次框选，一起处理；
   - 裁切 / 模糊 / delogo：快速档，按需选；
4. 设置输出目录（留空 = 与源文件同目录）；
5. 点「开始处理」。

### 5. 打包成 exe（可选）

两个版本，功能完全一致，只是推理引擎不同：

| 版本 | 体积 | 依赖 | 实测速度（720p 双区域，200 帧） |
| --- | --- | --- | --- |
| **CPU 版**（单文件 exe） | exe 37 MB，整包约 880 MB（含 ffmpeg 与模型） | 无显卡要求 | 约 14 秒/帧 |
| **GPU 版**（文件夹版） | 约 3.0 GB（exe 13.6 MB + CUDA 12 运行库 + ffmpeg + 模型） | 只需 NVIDIA 驱动 | 单路 174–245 秒，两路并行 135–202 秒 |

> 数字随显卡当时的频率浮动（同一台机器连跑，两路并行比单路快 20–29%）。
> 打包用的 Python 必须带 tkinter（python.org 的官方安装包自带；某些精简版 / 虚拟环境自带
> 的 Python 没有 tcl-tk，打出来的 GUI 版会报「Unhandled exception in script」而命令行版正常）。


**CPU 版**

```powershell
pip install onnxruntime numpy pyinstaller
python -m PyInstaller --noconfirm --clean --windowed --onefile `
  --name SubtitleRemover-CPU --hidden-import onnxruntime main.py
```

**GPU 版**（onedir，且要把 pip 装进来的 CUDA 运行库一起带进包里）

```powershell
pip install \"onnxruntime-gpu==1.22.0\" numpy pyinstaller
pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 nvidia-cufft-cu12 `
  nvidia-cuda-runtime-cu12 nvidia-curand-cu12
python -m PyInstaller --noconfirm --clean --windowed --onedir `
  --name SubtitleRemover-GPU --collect-all onnxruntime main.py

# 把 nvidia 运行库放到 _internal\\nvidia\\，程序启动会自动挂进 DLL 搜索路径
$nv = python -c \"import site,os;print(os.path.join(site.getsitepackages()[0],'nvidia'))\"
Copy-Item -Recurse $nv \"dist\\SubtitleRemover-GPU\\_internal\\nvidia\"
```

两个版本都需要把 `bin\\`（ffmpeg、ffprobe）和 `models\\lama_fp32.onnx` 放到 exe 同目录：
CPU 版是单文件，直接和 exe 并排放；GPU 版是文件夹，放在 `SubtitleRemover-GPU\\` 里面。
GPU 版不需要装 CUDA Toolkit、Python 或 pip，只要显卡驱动正常。

窗口版 exe 没有控制台，所以 `--cli` 的输出会写进 exe 同目录的
`subtitle-remover-cli.log`，跑完看那个文件即可。

已实测（i5-12450H + RTX 4060 Laptop）：

- CPU 版：640×360 单区域 4 帧，56.3 s（约 14 s/帧）；720p 双区域约 14 秒/帧
- GPU 版：720p 双区域 200 帧，单路 174–245 s，开两路并行 135–202 s（同场对比快 20–29%）
- GPU 版输出与源码运行结果**逐像素一致**（同一参数下比对三帧，最大差值 0）
- GPU 版若驱动过旧用不上显卡，会自动退回 CPU，日志里显示
  「显卡后端不可用，已自动改用 CPU 推理」并附失败原因

## 怎么判断该用哪种模式

- 播放器里**可以开关字幕**、或在文件信息里能看到「字幕流 / subtitle track」→ 用**软字幕轨移除**；
- 播放器里字幕**关不掉**，字幕和画面一起缩放、截图里也有字幕 → 是**硬字幕**，用画面内处理；
- 视频旁边有一个同名 `.srt` / `.ass` 文件 → 那只是外挂字幕，直接删文件即可，不用处理视频。

## 五种画面内处理方式怎么选

| 方式 | 原理 | 优点 | 代价 / 限制 |
| --- | --- | --- | --- |
| **自动检测字幕（auto）** | 每帧先跑 PP-OCRv4 文字检测，得到该帧文字框，再交给 LaMa 就地重绘 | **字幕会动也能去干净**：滚动、换行、跳动都跟得住；不用框选；无文字的帧原样直写，零质量损失 | 每帧多一次检测（GPU 0.1–0.17 s/帧），整体约 1.4 帧/秒（720p）；画面里的招牌、时间码、角标也会被当成文字一起去掉 |
| **AI 修复（inpaint）** | LaMa 参考区域四周内容重绘固定区域 | 去字不留遮挡，画面尺寸不变，人物与背景细节保留最好（实测文字区 PSNR 比模糊 / delogo 高 3 dB 以上） | 逐帧推理，必须有模型（208 MB）；区域框大了会连带重绘主体，字幕位置一动就漏字 |
| 裁切底部 / 顶部 | `crop` 直接把文字那条切掉 | 最快、最干净，没有残影 | 画面变矮，构图被改 |
| 模糊遮罩 | 区域做 `boxblur` 再叠回原位 | 画面尺寸不变，实现简单、快 | 明显的一条模糊带，等于\"遮挡\" |
| delogo 修复 | 用区域四周像素插值填补 | 纯色 / 渐变背景几乎无痕，快 | 背景复杂时糊成一片 |

**建议**：字幕位置固定、画面主体别贴着字 → AI 修复（固定区域）；字幕会滚动 / 换位 / 跳动 →
自动检测；只想去掉底部字幕、能接受画面变矮 → 裁切；必须实时 / 批量而且背景简单 → delogo。

## 动态字幕（字幕会动）怎么处理

固定框选对付不了滚动字幕：字从右边滑到左边，你框的那块很快就空了，而新位置上的字又没人管。
自动检测的做法是**每帧重新找一遍字**：

1. 每帧在缩小图（长边 960）上跑一次文字检测，得到这一帧的文字框（DB 概率图 → 阈值 0.30 →
   连通域 → 平均置信度 0.55）；
2. 同一行被切碎的框会合并，描边、发光、投影这些\"字以外还看得见的部分\"按纵向 70% / 横向 10%
   外扩，掩码再向外膨胀 4px，避免修完剩一圈淡淡字影；
3. 只在文字处重绘：窄字幕带整条一次推理（不分块），所以拼接处不会出现竖缝；
4. 一帧里没有文字就**原样直写**，不做任何重绘——这些帧的画质和原片一致；
5. 统计会打进日志：「逐帧检测：N 帧检出文字，共修复 M 块；其余帧原样保留」。

不想让它把画面里的招牌、时间码也一起修掉，就在「待修复区域」里框几块当**搜索范围**，
检测只在范围里生效；不框就是整幅画面找字。

实测（720p、100 帧、含\"逐句换位的中文台词 + 从右往左滑的滚动字幕 + 右上角水印\"）：

- 与干净原片逐帧比对：全幅 PSNR 由 19.38 dB（带字幕）提升到 **33.16 dB**，
  字幕带由 15.02 dB 提升到 **29.17 dB**（最差 26.82 dB）；
- 速度：GPU 100 帧 66.1 s（1.5 帧/秒），检测 0.10–0.17 s/帧，其余时间在推理与编码；

## 命令行用法

```powershell
# 整个文件夹，移除软字幕轨（无损）
python main.py --cli \"D:\\videos\" --mode soft

# AI 修复：同时去角标水印 + 底部字幕（两个区域）
python main.py --cli \"D:\\videos\\ep1.mp4\" --mode hard --style inpaint `
  --region-percent 0,0,20,8 --region-percent 0,84,100,16

# AI 修复：像素坐标指定两个区域，输出 CRF 20
python main.py --cli \"D:\\videos\\ep1.mp4\" --mode hard --style inpaint `
  --region 0,0,300,60 --region 0,620,1280,90 --crf 20

# 动态字幕（滚动 / 换位）：整幅画面自动找字，不用给区域
python main.py --cli \"D:\\videos\\ep1.mp4\" --mode hard --style auto

# 动态字幕限定搜索范围：只在左上角标与底部字幕带里找字
python main.py --cli \"D:\\videos\" --mode hard --style auto `
  --region-percent 0,0,25,10 --region-percent 0,80,100,20

# 硬字幕：裁掉底部 22%，CRF 20，x265 编码
python main.py --cli \"D:\\videos\\ep1.mp4\" --mode hard --style crop_bottom --height-ratio 0.22 --crf 20 --encoder libx265

# 硬字幕：模糊底部 20%（百分比区域，适配不同分辨率）
python main.py --cli \"D:\\videos\" --mode hard --style blur --region-percent 0,80,100,20
```

### 参数

| 参数 | 说明 |
| --- | --- |
| `--mode soft\|hard` | 处理模式，默认 `soft` |
| `--style auto\|inpaint\|crop_bottom\|crop_top\|blur\|delogo` | 画面内文字的处理方式；`auto` = 逐帧检测动态字幕（可不给区域） |
| `--region x,y,w,h` | 像素坐标区域，**可重复给多个**；`auto` 模式下表示\"搜索范围\"，可省略 |
| `--region-percent x,y,w,h` | 百分比区域，**可重复给多个**，推荐批量使用 |
| `--height-ratio` | 裁切高度比例，默认 `0.18` |
| `--crf` / `--preset` / `--encoder` | 画质与编码速度，默认 `23` / `medium` / `libx264`（AI 修复默认 CRF 走界面设置） |
| `--device auto\|cpu\|cuda` | AI 修复推理设备，默认 `auto`（有显卡先用 GPU，失败自动降级 CPU） |
| `--parallel N` | AI 修复并行路数：`0` 自动（长片在显卡上开两路）、`1` 单路、`2`/`3` 指定路数 |
| `--model PATH` | 指定 LaMa 模型路径，默认自动找 `models\\` 或环境变量 |
| `--det-model PATH` | 指定文字检测模型路径（`auto` 用），默认自动找 `models\\` |
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
│   ├── textdet.py             # 动态字幕：PP-OCRv4 逐帧文字检测（不用框选）
│   ├── app.py                 # 主窗口逻辑：文件队列 / 参数收集 / 批量执行 / 事件
│   ├── ui.py                  # 界面布局：样式、菜单、控件（app.py 继承 UiMixin）
│   ├── constants.py           # 文件类型、样式文案、预设区域
│   ├── models.py              # 文件列表行数据模型
│   ├── dnd.py                 # 拖拽依赖 tkinterdnd2 的可选探测
│   ├── region_picker.py       # 抽帧 + 拖拽框选区域
│   └── cli.py                 # 命令行
├── tests/test_core.py         # 单元测试（无需安装 ffmpeg / 模型）
├── tools/setup_ffmpeg.ps1     # 下载 ffmpeg 到 bin/
├── tools/download_model.py    # 下载 LaMa / 文字检测模型到 models/（断点续传 + SHA256 校验）
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
     同一块区域只修一次（GUI / CLI 同时传 `region` 与 `regions` 时会自动去重）；
  7. 长片自动两路并行：两个线程各持一个推理会话、按帧序写出。CUDA 图里有 288 个 memcpy
     节点会回退到 CPU，单路时显卡半闲，两路交替正好把这些停顿吃掉（200 帧实测 +20~29%），
     输出与单路逐像素一致；
  8. 后端按「CUDA → CPU」顺序尝试，任一档建会话失败就退下一档，不会因显卡问题中断整批任务；
- 动态字幕（auto）：
  1. 每帧先缩到长边 960、宽高取 32 倍数，跑 PP-OCRv4 DB 检测（均值 0.485/0.456/0.406、
     方差 0.229/0.224/0.225 归一化）；
  2. 概率图阈值 0.30 二值化 → 连通域（自实现的栈式泛洪，不依赖 OpenCV/scipy）→
     平均置信度 < 0.55 的丢掉，单边 < 8px 或面积 < 40 的当噪点丢掉，一帧最多 16 块；
  3. 同一行碎块按 6px 间隙合并；外扩纵向 70% / 最小 14px、横向 10% / 最小 12px
     （DB 框只包字形核心，44px 的字框高常常只有 21px），掩码再膨胀 4px 吃掉抗锯齿和描边；
  4. 检测在主线程按帧序跑，重绘在 worker 线程池里跑，帧序由 deque 保序；无文字的帧原样直写；
  5. 窄字幕带（高度 ≤ 120px）整条一次推理，不分块，避免拼块之间出现色阶竖缝；
- 硬字幕裁切：`-vf crop=W:H:0:0`，输出宽高取偶数，兼容 yuv420p；
- 硬字幕模糊：
  `-filter_complex \"[0:V]split=2[base][part];[part]crop=W:H:X:Y,boxblur=luma_radius=R:luma_power=2[b];[base][b]overlay=X:Y[v]\"`；
- 硬字幕 delogo：`-vf delogo=x=X:y=Y:w=W:h=H`，区域自动收缩到画面内部 1 像素；
- 音频始终 `-c:a copy`，不二次压缩。

## 常见问题

**AI 修复提示「未找到模型」**
运行 `python tools\\download_model.py`（国内网络加 `--mirror`）；或设置环境变量 `SUBTITLE_REMOVER_MODEL` 指向模型文件 / 所在目录。程序会拒绝使用下载不完整的模型。

**自动检测提示「未找到文字检测模型」**
运行 `python tools\\download_model.py --det` 下载 `models\\ppocr_v4_det.onnx`（4.5 MB）；或运行
`python tools\\download_model.py --all` 把两个模型一起补齐。

**字幕会动（滚动 / 换位），框选总是漏字怎么办**
换成「自动检测字幕」（`--style auto`）：不框选也能去干净，框选只当搜索范围用。别把区域框得过大——
框越大，允许被重绘的画面越多。

**AI 修复有多快**
只对文字区域推理，不是整帧；区域越大、拆的块越多越慢。720p 两区域在 RTX 4060 上
单路约 1.1–1.2 帧/秒、两路并行 1.3–1.5 帧/秒，纯 CPU 约 0.1 帧/秒。
长视频建议先拿一小段试效果再跑整集，`--preset veryfast` 能省编码时间。

**AI 修复后文字位置还有模糊 / 变形**
先看框选是否盖住了全部文字（尤其是描边、发光、投影）；再确认区域没有小到只剩文字本身——模型需要周围画面作参考，
给区域四周多留 10–20% 空间效果更好。底部字幕可以整条框，程序会自动拆块，不会因为区域宽就变糊。

**画面是纯色块 / 图表 / 大面积渐变的素材，AI 修复会\"抹\"出一条平滑色带**
这类画面本身没有可供参考的纹理，模型只能用两侧颜色插值，结果就是渐变而不是原文纹理。
纯色背景优先用 delogo（几乎无痕），复杂实拍画面才用 AI 修复。

**日志里显示 CPUExecutionProvider，没用上显卡**
说明 CUDA 运行库没装全或版本不对：`pip install -r requirements-inpaint-gpu.txt` 后重启程序；
或先用 `--device cpu` 排除后端问题。DirectML 后端不可用于这个模型（LaMa 的 FFC 层会报 `80070057`）。

**显卡明明在，为什么还是 CPU 在跑**
程序会按「CUDA → CPU」的顺序逐个尝试建会话，显卡那一档失败（驱动过旧、缺运行库、显存被占）
就自动退到 CPU，并在日志里打印「显卡后端不可用，已自动改用 CPU 推理」和失败原因。
想确认到底用的哪个后端，看日志里的「推理后端」那行即可；GUI 里也可以用「AI 推理设备」
强制选 CPU 或 GPU。

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

测试覆盖：ffprobe JSON 解析、帧率解析、区域换算与边界裁剪、软 / 硬字幕命令构建、输出命名去重、AI 修复的区域扩展 / 掩码映射 / 区域合并 / 羽化 alpha / 贴边镜像补边 / 拆块覆盖，动态字幕的文字框外扩 / 合并 / 截断 / 模型校验。测试不依赖真实 ffmpeg 和模型。

### 效果对比（可复现）

```powershell
python tools\\eval_quality.py --sample photo      # 实拍推镜素材
python tools\\eval_quality.py --sample testsrc    # 合成彩条：硬边界最考验算法
python tools\\eval_quality.py --sample mandel     # 高噪声细节
```

脚本会造一段**无损干净素材**，再把角标水印 + 中文底部字幕烧上去，然后分别用
AI 修复 / delogo / 模糊处理，取同一帧与原图比对「文字区域内 PSNR」，
并在 `%TEMP%\\inpaint_eval` 下导出放大裁剪图供肉眼检查。

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

MIT License，详见 [LICENSE](LICENSE)。AI 修复模型为 Apache-2.0 的 LaMa（Samsung Research），找字模型为 Apache-2.0 的 PP-OCRv4（经 RapidOCR 导出），模型文件不随仓库分发。
