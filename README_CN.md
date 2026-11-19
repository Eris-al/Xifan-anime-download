# 番剧视频下载工具

## 项目介绍

这是一个面向稀饭视频（`next.xifanacg.com`）的命令行番剧批量下载工具。程序会读取番剧页面中的剧集和播放源，允许选择下载源，并并行下载剧集视频。（主要目的是批量下载到本地）

主要功能：

- 自动解析番剧剧集和可用播放源，可指定首选源并在失败时尝试备用源。
- 支持 MP4 直链断点续传。
- 支持 HLS（m3u8）分片下载；安装 ffmpeg 后可将分片转封装为 MP4。
- 已存在的剧集文件会跳过；下载过程中可按`Ctrl+C` 停止。

## 项目安装

要求 Python 3.10 或更高版本，推荐（3.12.*）。

在项目根目录运行：

```powershell
.\activate.ps1 #创建虚拟环境并启用虚拟环境下载依赖
```

如果 PowerShell 不允许执行激活脚本，也可以不激活虚拟环境，直接使用虚拟环境中的 Python 执行安装和运行命令。

HLS 转封装为 MP4 需要另外安装 ffmpeg，并确保 `ffmpeg` 在 `PATH` 中。也可以通过 `FFMPEG` 环境变量指定其可执行文件路径。未找到 ffmpeg 时，HLS 下载会保留为 TS 文件。（浏览器搜索ffmpeg，或直接访问`https://www.gyan.dev/ffmpeg/builds/`自己选择合适的release builds版本，并将其添加到`PATH`环境变量中）

## 项目使用

从项目根目录运行：

```powershell
.\venv\Scripts\activate #激活虚拟环境
python src\main.py <番剧ID> "<保存文件夹名称>" [选项] #运行脚本
```

例如：

```powershell
python src\main.py 12345 "<保存文件夹名称>"
python src\main.py 12345 "<保存文件夹名称>" --source CS --workers 4
```

番剧 ID 或保存文件夹名称可以省略；省略时程序会提示输入。（番剧 ID来自稀饭数据库，可通过稀饭官页搜索视频查看）

### 参数

| 参数 | 说明 |
| --- | --- |
| `-s`, `--source` | 指定下载源序号或代码，例如 `1`、`xfxf1`、`AL`、`CS`。未指定时可在运行中选择。 |
| `-j`, `--workers` | 并行下载数，默认 `3`。 |
| `--headful` | 显示浏览器窗口，用于调试。 |
| `-h`, `--help` | 显示帮助信息。 |

下载文件保存在当前工作目录下以保存文件夹名称命名的目录中。进度条默认只在交互式终端显示；可通过 `XIFAN_PROGRESS=0` 或 `XIFAN_PROGRESS=1` 强制关闭或开启。