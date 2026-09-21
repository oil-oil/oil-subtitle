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
if ! command -v node &>/dev/null || ! command -v npm &>/dev/null; then
    echo "ERROR: credential-ui 需要 Node.js 22.18+ 和 npm。"
    exit 1
fi
if ! node -e 'const [major, minor] = process.versions.node.split(".").map(Number); process.exit(major > 22 || (major === 22 && minor >= 18) ? 0 : 1)'; then
    echo "ERROR: credential-ui 需要 Node.js 22.18+。当前版本：$(node --version)"
    exit 1
fi
npm --prefix "$SKILL_DIR/scripts/credential-ui" ci --quiet --ignore-scripts
# 凭据配置与安装分开，避免安装时自动迁移旧凭据。
echo "oil-subtitle setup complete."
