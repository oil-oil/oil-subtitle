#!/bin/bash
# oil-subtitle — one-time setup

set -e

SKILL_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$SKILL_DIR"

if [[ "$(uname)" != "Darwin" ]]; then
    echo "ERROR: oil-subtitle requires macOS."
    exit 1
fi

if ! command -v ffmpeg &>/dev/null; then
    if ! command -v brew &>/dev/null; then
        echo "ERROR: Homebrew is required."
        exit 1
    fi
    brew install ffmpeg
fi

if [[ ! -d ".venv" ]]; then
    python3 -m venv .venv
fi

.venv/bin/pip install --quiet --upgrade pip
.venv/bin/pip install --quiet flask jieba 'dashscope>=1.26.7,<2' -r scripts/requirements-credentials.txt

if [[ "$(uname -m)" == "arm64" ]]; then
    .venv/bin/pip install --quiet mlx-whisper
    "$SKILL_DIR/.venv/bin/python3" -c "import mlx_whisper"
else
    .venv/bin/pip install --quiet openai-whisper
    "$SKILL_DIR/.venv/bin/python3" -c "import whisper"
fi

"$SKILL_DIR/.venv/bin/python3" -c "import dashscope, flask, jieba"
# 凭据配置与安装分开，避免安装时自动迁移旧凭据。
echo "oil-subtitle setup complete."
