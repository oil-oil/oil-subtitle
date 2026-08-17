---
name: oil-subtitle
description: >
  为本地 MP4、MOV 等视频转录、校对、预览并烧录准确的中文字幕。
  用户只要提供成片视频并要求加字幕、修字幕、生成 SRT/ASS 或字幕进度条，
  就使用本 Skill。不要用它修改 .screenstudio 工程时间线；工程剪辑使用 screen-studio-editor。
---

# Oil Subtitle

只负责已经导出的视频字幕，不修改 Screen Studio 工程。

脚本负责 ASR、术语表、分行、章节、排版、人脸区域检测和 FFmpeg 烧录。Agent 负责确认输入输出、校对不确定文本、组织用户预览、检查最终结果，不要手工复述或重做脚本内部逻辑。

## 默认行为

- 只生成中文字幕。默认使用 DashScope Python SDK 调用百炼 FunAudio ASR；字幕断句和长视频章节由百炼 Qwen 完成，全部共用一个 API Key。旧本地 Whisper 仅用于明确比较或远程服务不可用时的降级。
- ASR 自动应用外部 hotwords 和 glossary；人工只补充脚本没有覆盖的高置信错误。
- 字幕默认不显示标点；人工审阅过的 SRT 通过 `--srt-input` 直烧时保持文本、换行和时间码不变。
- 章节进度功能默认开启。视频严格超过三分钟时，在视频画面底部用半透明渐变阴影承载宽粒度章节进度；不增加实色画布，也不把每个小点拆成章节。
- 用户只要用自然语言说“关闭章节进度条”或同义表达，本次任务就在准备和烧录命令中都传 `--no-progress`，不生成章节，也不显示进度条；用户说“开启章节进度条”时传 `--progress`。这是单次任务开关，不要求用户修改配置文件。
- 正常烧录会识别持续出现的人脸区域并执行固定轻度美颜；用户要求保持原画时传 `--no-beauty`。
- 不覆盖已有 MP4、SRT 或 ASS。目标存在时使用新文件名，除非用户明确同意覆盖。

## 初始化

```bash
SKILL_DIR="<oil-subtitle 的绝对目录>"
PYTHON="$SKILL_DIR/.venv/bin/python3"
CONFIG="${OIL_SUBTITLE_CONFIG:-$HOME/.config/oil-subtitle/config.json}"
API_KEY_FILE="${OIL_SUBTITLE_API_KEY_FILE:-$HOME/.config/oil-subtitle/dashscope_api_key}"
```

首次使用时运行：

```bash
bash "$SKILL_DIR/setup.sh"
"$PYTHON" "$SKILL_DIR/scripts/configure_api_key.py"
```

API Key 优先读取 `DASHSCOPE_API_KEY`，否则读取 `API_KEY_FILE`。只需配置一次；文件权限固定为 `600`，不得提交到仓库。已有 `~/.bailian/config.json` 会在初始化时自动迁移。

可选配置：

```json
{
  "video_library_root": "/optional/path/to/video-library",
  "hotwords": "/optional/path/to/hotwords.json",
  "glossary": "/optional/path/to/glossary.json",
  "vocabulary_cache": "/optional/path/to/vocabulary-cache.json",
  "subtitles": {
    "progress_enabled": true,
    "progress_min_duration_seconds": 180
  }
}
```

新配置优先使用 `OIL_SUBTITLE_CONFIG`。为兼容迁移，脚本在新配置不存在时仍会读取 `SCREEN_STUDIO_EDITOR_CONFIG` 及旧环境变量。不要把用户配置、API Key、个人术语表或绝对路径提交进 Skill。

## 工作流

### 1. 确定视频和工作目录

如果配置了 `video_library_root`，先把视频放到 `<video_library_root>/<视频标题>/`；否则保留在源视频旁边。不要覆盖同名文件。

```bash
VIDEO="/path/to/video.mp4"
VIDEO_DIR="$(dirname "$VIDEO")"
VIDEO_NAME="$(basename "$VIDEO")"
VIDEO_STEM="${VIDEO_NAME%.*}"
WORK="$VIDEO_DIR/$VIDEO_STEM.subtitle-work"
mkdir -p "$WORK"
```

### 2. 转录

默认路径：

```bash
"$PYTHON" "$SKILL_DIR/scripts/bailian_transcribe.py" \
  "$VIDEO" \
  --output "$WORK/transcript.json" \
  --raw-output "$WORK/bailian_asr.json" \
  --language zh
```

脚本会保留原始 ASR、自动应用术语表并完成字幕级分行。只有剪辑分析才使用 `--split-mode raw`，不要把 raw 转录稿直接用于预览或烧录。

用户明确要求本地转录或百炼不可用时：

```bash
ffmpeg -i "$VIDEO" -ar 16000 -ac 1 "$WORK/audio.wav" -y
"$PYTHON" "$SKILL_DIR/scripts/local_transcribe.py" \
  --audio "$WORK/audio.wav" \
  --output "$WORK/transcript.json" \
  --language zh
```

### 3. 校对转录稿

阅读 `transcript.json`，只修改 segment 的 `text`：

- 明确的产品名、专有名词和大小写错误；
- 从上下文或画面能够确认的 ASR 错误；
- 新出现、尚未被 glossary 覆盖的重复误识别。

不确定的命令、文件名或界面文字，先抽取目标时间附近的画面再判断。word 级 token 仅用于排查时间，不得覆盖已经确认的 segment 文本。

### 4. 准备字幕和章节

```bash
"$PYTHON" "$SKILL_DIR/scripts/prepare_subtitles.py" \
  --transcript "$WORK/transcript.json" \
  --video "$VIDEO" \
  --output "$WORK/subtitle-transcript.json" \
  --chapters-output "$WORK/subtitle-chapters.json" \
  --manifest-output "$WORK/subtitle-manifest.json" \
  --work-dir "$WORK/cache" \
  --resume
```

章节进度默认开启。用户要求本次关闭时，在以上命令追加 `--no-progress`；用户明确要求开启时追加 `--progress`。即使用户关闭进度条，也继续正常准备、预览和烧录中文字幕。

检查准备后的字幕和章节。三到五分钟的视频通常保留 2–4 个章节，更长视频通常保留 4–6 个，每个标题简短且对应主要内容阶段。

### 5. 用户预览

预览服务会阻塞，后台启动。不要杀死占用端口的未知进程；8765 被占用时改用空闲端口，例如 8766。

```bash
PREVIEW_EDITOR_PORT=8765 "$PYTHON" "$SKILL_DIR/scripts/preview_editor.py" \
  "$WORK/subtitle-manifest.json" &
```

提供 `http://localhost:8765`。除非用户明确要求，不调用浏览器自动化。

告诉用户：

> 已准备好字幕预览，请检查文字和时间是否准确。可以双击修改、取消勾选删除，确认后点击「保存并关闭」。

等用户确认，或确认 `subtitle-transcript.json` 已被保存后再继续。务必核对首句属于当前视频，不能复用其他视频的 manifest。

### 6. 更新术语表

比较 `subtitle-transcript.json.orig.json` 和保存后的文件。只把会重复出现的 ASR 错误写入用户配置指向的 glossary；不要把一次性改写、删除或标点调整写进术语表。

### 7. 草稿检查和烧录

先生成 SRT 草稿：

```bash
"$PYTHON" "$SKILL_DIR/scripts/burn_subtitles.py" \
  --video "$VIDEO" \
  --transcript "$WORK/subtitle-transcript.json" \
  --chapters "$WORK/subtitle-chapters.json" \
  --draft-output "$VIDEO_DIR/${VIDEO_STEM}_subtitled.srt" \
  --draft-only
```

检查产品名、断句、落单语气词、可见标点和时间。确认输出 MP4 不存在后，一次完成 ASS 生成和烧录：

```bash
"$PYTHON" "$SKILL_DIR/scripts/burn_subtitles.py" \
  --video "$VIDEO" \
  --transcript "$WORK/subtitle-transcript.json" \
  --chapters "$WORK/subtitle-chapters.json" \
  --output "$VIDEO_DIR/${VIDEO_STEM}_subtitled.mp4"
```

用户要求关闭章节进度条时，烧录命令也必须追加 `--no-progress`，避免已有章节文件被误用。

如果用户直接审阅了 SRT，按原样烧录：

```bash
"$PYTHON" "$SKILL_DIR/scripts/burn_subtitles.py" \
  --video "$VIDEO" \
  --srt-input "/path/to/reviewed.srt" \
  --output "$VIDEO_DIR/${VIDEO_STEM}_subtitled.mp4"
```

### 8. 验证和交付

检查：

- 输出文件可播放，时长与源视频一致；
- 开头、中段、结尾字幕与声音同步；
- 字幕没有遮挡持续出现的人脸区域；
- 章节标题在各自时间段内保持稳定；
- 是否启用美颜、输出路径与用户要求一致。

报告最终视频、SRT、ASS 和工作目录路径，并说明需要用户重点预览的位置。
