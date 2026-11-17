# 视频去字幕工具 · Subtitle Remover

一个完全本地运行的 Windows 桌面工具，用来处理视频里两种完全不同性质的「字幕」：

| 类型 | 是什么 | 处理方式 | 是否重新编码 | 耗时 |
| --- | --- | --- | --- | --- |
| **软字幕轨** | 视频文件里独立的字幕流（`.mkv` 内封、`.mp4` 的 `mov_text` 等） | 直接剔除字幕流，视频/音频原样复制 | 否（`-c copy`） | 秒级 |
| **硬字幕** | 字幕已经烧进画面像素，关不掉 | 裁切 / 模糊遮罩 / delogo 修复 | 是 | 取决于视频时长与编码速度 |

视频不上传、不联网，全部由本机 ffmpeg 处理。

## 功能特性

- 两种模式一个界面搞定：软字幕轨无损移除、硬字幕去除；
- 硬字幕三种手段：**裁切**（最快最干净）、**模糊遮罩**（画面尺寸不变）、**delogo 修复**（按周围像素插值）；
- **框选字幕区域**：从视频里抽一帧，在真实画面上拖框选字幕位置，自动换算成视频像素/百分比坐标；
- 参数按**百分比**表达，一批不同分辨率的视频可以共用一套参数；
- 批量队列、逐文件进度、ffmpeg 日志、失败原因一目了然；
- 输出永不覆盖原文件（同名自动加序号）；
- 命令行模式，可接自动化脚本；
- 隐私：纯本地处理，不产生任何上传。

## 快速开始

### 1. 环境

- Windows 10/11（其他平台源码同样可跑，界面基于 tkinter）
- Python 3.9+（仅源码运行需要，打包后的 exe 不需要）
- ffmpeg / ffprobe

### 2. 准备 ffmpeg（二选一）

**方式 A：一键脚本（推荐）**

```powershell
powershell -ExecutionPolicy Bypass -File tools\setup_ffmpeg.ps1
```

脚本会把 `ffmpeg.exe`、`ffprobe.exe` 下载并放到项目的 `bin\` 目录，程序启动时自动识别。

**方式 B：手动安装**

- 下载 <https://www.gyan.dev/ffmpeg/builds/> 的 `ffmpeg-release-essentials.zip`，解压后把 `bin` 目录加入系统 `PATH`；或
- 把 `ffmpeg.exe` / `ffprobe.exe` 直接放进项目 `bin\` 目录；或
- 设置环境变量 `SUBTITLE_REMOVER_FFMPEG_DIR` 指向 ffmpeg 的 `bin` 目录。

### 3. 运行

```powershell
python main.py
```

窗口打开后：

1. 点「添加视频…」或直接把文件拖进窗口（拖拽需要 `pip install tkinterdnd2`，不装也能用按钮添加）；
2. 选择「软字幕轨移除」或「硬字幕去除」；
3. 硬字幕模式下，模糊 / delogo 建议点「框选区域…」，在预览画面上拖出字幕位置；
4. 设置输出目录（留空 = 与源文件同目录）；
5. 点「开始处理」。

### 4. 打包成 exe（可选）

```powershell
pip install pyinstaller
powershell -ExecutionPolicy Bypass -File build_exe.ps1
```

产物为 `dist\SubtitleRemover.exe` 与 `dist\bin\`（ffmpeg），把这两者放在同一目录，整个文件夹拷给别人即可直接双击使用。

## 怎么判断该用哪种模式

- 播放器里**可以开关字幕**、或在文件信息里能看到「字幕流 / subtitle track」→ 用**软字幕轨移除**；
- 播放器里字幕**关不掉**，字幕和画面一起缩放、截图里也有字幕 → 是**硬字幕**；
- 视频旁边有一个同名 `.srt` / `.ass` 文件 → 那只是外挂字幕，直接删文件即可，不用处理视频。

## 硬字幕三种方式怎么选

| 方式 | 原理 | 优点 | 代价 / 限制 |
| --- | --- | --- | --- |
| 裁切底部 / 顶部 | `crop` 直接把字幕那条切掉 | 最快、最干净，没有残影 | 画面变矮（可配合播放器拉伸） |
| 模糊遮罩 | 字幕区域做 `boxblur`，再叠回原位 | 画面尺寸不变，字幕完全看不清 | 会留下一条模糊带；字幕跑出区域会漏 |
| delogo 修复 | 用字幕区域四周的像素插值填补 | 背景是纯色 / 渐变时几乎看不出痕迹 | 背景复杂时会糊成一片；区域要完全框住字幕 |

**建议**：字幕位置固定、能接受画面变矮 → 裁切；必须保持原尺寸 → delogo（纯背景）或模糊。

## 命令行用法

```powershell
# 整个文件夹，移除软字幕轨（无损）
python main.py --cli "D:\videos" --mode soft

# 递归处理、指定输出目录
python main.py --cli "D:\videos" -r --mode soft --outdir "D:\clean"

# 硬字幕：裁掉底部 22%，CRF 20，x265 编码
python main.py --cli "D:\videos\ep1.mp4" --mode hard --style crop_bottom --height-ratio 0.22 --crf 20 --encoder libx265

# 硬字幕：模糊底部 20%（百分比区域，适配不同分辨率）
python main.py --cli "D:\videos" --mode hard --style blur --region-percent 0,80,100,20

# 硬字幕：delogo 修复，像素坐标区域
python main.py --cli "D:\videos\ep1.mp4" --mode hard --style delogo --region 0,900,1920,180
```

### 参数

| 参数 | 说明 |
| --- | --- |
| `--mode soft\|hard` | 处理模式，默认 `soft` |
| `--style crop_bottom\|crop_top\|blur\|delogo` | 硬字幕处理方式 |
| `--region x,y,w,h` | 像素坐标区域（blur / delogo 必填之一） |
| `--region-percent x,y,w,h` | 百分比区域，推荐批量使用 |
| `--height-ratio` | 裁切高度比例，默认 `0.18` |
| `--crf` / `--preset` / `--encoder` | 画质与编码速度，默认 `23` / `medium` / `libx264` |
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
│   ├── app.py                 # 主窗口（文件队列 / 参数面板 / 进度 / 日志）
│   ├── region_picker.py       # 抽帧 + 拖拽框选字幕区域
│   └── cli.py                 # 命令行
├── tests/test_core.py         # 单元测试（无需安装 ffmpeg）
├── tools/setup_ffmpeg.ps1     # 下载 ffmpeg 到 bin/
├── build_exe.ps1              # PyInstaller 打包
└── .github/workflows/ci.yml   # 每次提交跑单元测试
```

## 技术细节

- 软字幕轨移除：
  `ffmpeg -i in.mkv -map 0 -map -0:s -c copy -map_metadata 0 -map_chapters 0 out.mkv`
  （指定保留字幕流时改为显式映射 `-map 0:V? -map 0:a? -map 0:<idx>`）
- 硬字幕裁切：`-vf crop=W:H:0:0`，输出宽高取偶数，兼容 yuv420p；
- 硬字幕模糊：
  `-filter_complex "[0:V]split=2[base][part];[part]crop=W:H:X:Y,boxblur=luma_radius=R:luma_power=2[b];[base][b]overlay=X:Y[v]"`；
- 硬字幕 delogo：`-vf delogo=x=X:y=Y:w=W:h=H`，区域自动收缩到画面内部 1 像素；
- 音频始终 `-c:a copy`，不二次压缩。

## 常见问题

**提示「未找到 ffmpeg / ffprobe」**
运行 `tools\setup_ffmpeg.ps1`，或把 ffmpeg 加入 PATH；「工具 → 检查 ffmpeg 环境」可查看当前使用的是哪个路径。

**处理完字幕还在**
多半是外挂字幕：视频同目录有同名 `.srt` / `.ass`，播放器会自动加载，删掉字幕文件即可。

**硬字幕处理很慢**
硬字幕必须重新编码整段视频。4K 视频用 `--preset veryfast`、`--encoder libx265` 会好一些；只是去掉底部一条，裁切比 delogo 快得多。

**delogo 处理后出现一团糊**
区域没完全包住字幕，或字幕背景太复杂。把区域四周各留 4~8 像素，或改用裁切。

**批量视频分辨率不同，区域对不上**
用百分比区域（界面里就是百分比），程序会按每个文件的分辨率换算像素。

**输出文件比原文件小很多 / 画质下降**
硬字幕必须重新编码，`CRF 23` 是肉眼几乎无损的常用值；想更接近原画质用 `18~20`，代价是文件更大、编码更慢。

## 开发与测试

```powershell
python -m unittest discover -s tests -v
```

测试覆盖：ffprobe JSON 解析、区域换算与边界裁剪、软/硬字幕命令构建、输出命名去重、进度输出解析。测试不依赖真实 ffmpeg。

## 许可

MIT License，详见 [LICENSE](LICENSE)。
