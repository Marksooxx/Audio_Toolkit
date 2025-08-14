# Audio Toolkit - 音频处理工具集

用于音频文件归一化和静音删除的Python脚本集。使用FFmpeg进行WAV文件的高速并行处理。

## 📋 目录

- [环境要求](#环境要求)
- [脚本列表](#脚本列表)
- [使用的FFmpeg滤镜](#使用的ffmpeg滤镜)
- [安装与配置](#安装与配置)
- [使用方法](#使用方法)
- [技术规格](#技术规格)
- [注意事项](#注意事项)

## 🔧 环境要求

- **Python**: 3.7以上
- **FFmpeg**: 已添加到系统PATH
- **FFprobe**: 与FFmpeg一起安装（v3, v4需要）
- **标准库**: `concurrent.futures`, `pathlib`, `threading`等

## 📁 脚本列表

### a1_WavNormalize.py
**功能**: 将整个音频文件归一化到指定的目标峰值电平。

**使用的FFmpeg滤镜**:
- `volumedetect` - 音量电平检测
- `volume={gain}dB` - 音量调整

### a2_WavNormalize_DeleteEnd.py
**功能**: 对音频文件进行归一化后，删除文件末尾的静音部分。

**使用的FFmpeg滤镜**:
- `volumedetect` - 音量电平检测
- `volume={gain}dB` - 音量调整
- `silenceremove=start_periods=0:stop_periods=-1:stop_duration=1:stop_threshold=-50dB` - 末尾静音删除

### a3_WavNormalize_Channel.py
**功能**: 对立体声音频文件的左右声道分别独立地归一化到目标峰值电平。

**使用的FFmpeg滤镜**:
- `volumedetect` - 音量电平检测
- `pan=mono|c0=1*c0` - 左声道提取
- `pan=mono|c0=1*c1` - 右声道提取
- `volume={gain}dB` - 单声道音量调整
- `channelsplit=channel_layout=stereo[FL][FR]` - 立体声声道分离
- `[FL]volume={gain_l}dB[left];[FR]volume={gain_r}dB[right];[left][right]amerge=inputs=2` - 声道独立增益调整与合成

### a4_WavNormalize_Channel_DeleteEnd.py
**功能**: 对立体声音频文件的左右声道独立归一化，然后删除文件末尾的静音部分。

**使用的FFmpeg滤镜**:
- `volumedetect` - 音量电平检测
- `pan=mono|c0=1*c0` - 左声道提取
- `pan=mono|c0=1*c1` - 右声道提取
- `volume={gain}dB` - 单声道音量调整
- `channelsplit=channel_layout=stereo[FL][FR]` - 立体声声道分离
- `[FL]volume={gain_l}dB[left];[FR]volume={gain_r}dB[right];[left][right]amerge=inputs=2` - 声道独立增益调整与合成
- `silenceremove=start_periods=0:stop_periods=-1:stop_duration=1:stop_threshold=-50dB` - 末尾静音删除

## 🎛️ 使用的FFmpeg滤镜

### 基本滤镜

| 滤镜 | 说明 | 使用示例 |
|------|------|----------|
| `volumedetect` | 检测音频的最大音量 | 峰值测量 |
| `volume={gain}dB` | 以dB为单位调整音量 | 归一化处理 |

### 立体声处理滤镜

| 滤镜 | 说明 | 使用示例 |
|------|------|----------|
| `pan=mono\|c0=1*c0` | 将左声道提取为单声道 | 左右声道独立分析 |
| `pan=mono\|c0=1*c1` | 将右声道提取为单声道 | 左右声道独立分析 |
| `channelsplit=channel_layout=stereo[FL][FR]` | 将立体声分离为左右声道 | 声道独立处理 |
| `amerge=inputs=2` | 合成多个音频流 | 左右声道重新合成 |

### 静音处理滤镜

| 滤镜 | 说明 | 参数 |
|------|------|------|
| `silenceremove` | 删除静音部分 | `start_periods=0` - 不处理开始部分 |
|  |  | `stop_periods=-1` - 删除所有末尾静音 |
|  |  | `stop_duration=1` - 针对1秒以上的静音 |
|  |  | `stop_threshold=-50dB` - 将-50dB以下判定为静音 |

## ⚙️ 安装与配置

### 1. FFmpeg安装

#### Windows
```bash
# 使用Chocolatey
choco install ffmpeg

# 或者从官方网站下载
# https://ffmpeg.org/download.html
```

#### macOS
```bash
# 使用Homebrew
brew install ffmpeg
```

#### Linux (Ubuntu/Debian)
```bash
sudo apt update
sudo apt install ffmpeg
```

### 2. 环境验证
```bash
# 确认FFmpeg是否正确安装
ffmpeg -version
ffprobe -version
```

### 3. Python库
仅使用标准库，无需额外安装。

## 🚀 使用方法

### 基本使用方法

1. **将脚本文件放置在音频文件所在的同一目录**
2. **在命令行中运行脚本**
   ```bash
   python a1_WavNormalize.py
   ```
3. **输入目标峰值** (例: -1, -3, -6)
4. **等待处理完成**

### 推荐设置值

| 用途 | 推荐峰值 | 理由 |
|------|----------|------|
| 音乐制作 | -3dB ～ -6dB | 保留动态余量 |
| 播客 | -1dB ～ -3dB | 最大化音量 |
| 广播用 | -6dB ～ -9dB | 符合规范 |
| 流媒体 | -1dB ～ -2dB | 平台适配 |

### 文件命名规则

处理过程中会创建以下临时文件，处理完成后会自动删除：

- `___temp_v1_thread_*` - v1临时文件
- `___temp_v2_thread_*` - v2临时文件  
- `___temp_v3_thread_*` - v3临时文件
- `___temp_v4_thread_*` - v4临时文件

## 🔧 技术规格

### 性能

- **并行处理**: 基于CPU逻辑核心数的自动线程数设置
- **内存使用**: 取决于文件大小和处理线程数
- **处理速度**: 取决于文件大小和CPU性能

### 支持格式

- **输入**: 仅支持WAV格式
- **输出**: WAV格式（覆盖原文件）
- **采样率**: FFmpeg支持的所有采样率
- **位深度**: FFmpeg支持的所有位深度
- **声道数**: 
  - v1, v2: 单声道/立体声
  - v3, v4: 单声道/立体声（支持声道独立处理）

### 可配置参数

#### CONFIG字典内的设置
```python
CONFIG = {
    "ffmpeg_path": "ffmpeg",           # FFmpeg可执行文件路径
    "max_workers": os.cpu_count() or 4, # 最大线程数
    "gain_tolerance": 0.1,             # 增益容差值（v3, v4）
    "silence_stop_duration": "1",      # 静音判定时间（v2, v4）
    "silence_stop_threshold": "-50dB"  # 静音判定电平（v2, v4）
}
```

## ⚠️ 注意事项

### 文件处理

1. **备份创建**: 原文件会被覆盖。重要文件请提前备份。
2. **文件格式**: 仅支持WAV文件。其他格式请先转换。
3. **文件名**: 以三个下划线开头的文件名（`___`）会被视为临时文件，不会被处理。

### 系统要求

1. **磁盘空间**: 处理期间需要与原文件相当的额外空间。
2. **内存**: 处理大文件或大量文件时，需要足够的RAM。
3. **CPU**: 为了充分利用并行处理，推荐使用多核CPU。

### 故障排除

#### 常见错误

| 错误信息 | 原因 | 解决方法 |
|----------|------|----------|
| `ffmpeg 命令未找到` | FFmpeg未安装 | 安装FFmpeg并添加到PATH |
| `ffprobe 命令未找到` | FFprobe未安装 | 确认与FFmpeg一起安装的FFprobe |
| `无法获取声道数` | 不支持的文件格式 | 转换为WAV格式 |
| `无法删除临时文件` | 文件被锁定 | 确认文件未被其他应用程序打开 |

## 📄 许可证

本项目基于MIT许可证发布。

## 🤝 贡献

欢迎报告错误和功能建议。

---

**创建者**: Mark  
**最后更新**: 2024年  
**版本**: 1.0
