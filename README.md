# oil-subtitle

`oil-subtitle` 负责为已经导出的本地视频转录、校对、预览并烧录字幕。

## 能力

- 百炼 FunAudio ASR，保留原始识别结果；
- 自动应用 hotwords 和 glossary；
- 默认中文字幕，按需生成中英双语字幕；
- 浏览器本地预览和人工校对；
- SRT、ASS 和烧录 MP4；
- 三分钟以上视频的宽粒度章节进度条；
- 自动识别持续人脸区域并执行固定轻度美颜。

它不修改 `.screenstudio` 时间线。工程剪辑使用 `screen-studio-editor`。

## 安装

```bash
bash /absolute/path/to/oil-subtitle/setup.sh
```

依赖 macOS、Python 3、FFmpeg 和已登录的百炼 CLI `bl`。本地 Whisper 仅用于明确降级或对比。

## 使用

直接提供视频路径并说明需求，例如：

- `给这个视频加中文字幕 /path/demo.mp4`
- `把 /path/talk.mp4 做成中英双语字幕`
- `先生成并校对 SRT 再烧录`

完整流程见 [SKILL.md](SKILL.md)。

## 主要脚本

| 脚本 | 作用 |
|---|---|
| `scripts/bailian_transcribe.py` | 远程 ASR、术语表和字幕分行 |
| `scripts/local_transcribe.py` | 本地 Whisper 降级转录 |
| `scripts/prepare_subtitles.py` | 准备中文/双语字幕、章节和 manifest |
| `scripts/preview_editor.py` | 本地字幕预览编辑器 |
| `scripts/burn_subtitles.py` | 中文字幕、SRT/ASS 与烧录 |
| `scripts/burn_bilingual_subtitles.py` | 中英双语字幕烧录 |
