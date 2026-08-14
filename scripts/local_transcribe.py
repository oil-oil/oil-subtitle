#!/usr/bin/env python3
"""
Transcribe local audio with a local Whisper-family model.

Default backend:
  - Apple Silicon: mlx-whisper, using a locally cached MLX model when available
  - Fallback: openai-whisper, using turbo

Output format:
  [{"start": 0.0, "end": 1.23, "text": "...", "words": [...]}]
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from pathlib import Path
from typing import Any


DEFAULT_MLX_MODEL = "mlx-community/whisper-large-v3-mlx"
DEFAULT_WHISPER_MODEL = "turbo"


def log(message: str):
    print(f"[local-transcribe] {message}", flush=True)


def _clean_word(word: dict[str, Any]) -> dict[str, Any] | None:
    text = (word.get("word") or word.get("text") or "").strip()
    if not text:
        return None
    start = word.get("start")
    end = word.get("end")
    if start is None or end is None:
        return None
    return {
        "word": text,
        "start": round(float(start), 3),
        "end": round(float(end), 3),
    }


def _result_to_segments(result: dict[str, Any]) -> list[dict[str, Any]]:
    segments: list[dict[str, Any]] = []
    for seg in result.get("segments", []):
        text = (seg.get("text") or "").strip()
        if not text:
            continue

        words = []
        for word in seg.get("words") or []:
            clean = _clean_word(word)
            if clean:
                words.append(clean)

        segments.append({
            "start": round(float(seg.get("start") or 0), 3),
            "end": round(float(seg.get("end") or 0), 3),
            "text": text,
            "words": words,
        })

    segments.sort(key=lambda s: (s["start"], s["end"]))
    return segments


def _transcribe_with_mlx(audio_path: Path, language: str | None, model: str) -> dict[str, Any]:
    import mlx_whisper

    model_path = _resolve_hf_cache_snapshot(model) or model
    kwargs: dict[str, Any] = {
        "path_or_hf_repo": model_path,
        "word_timestamps": True,
    }
    if language:
        kwargs["language"] = language

    return mlx_whisper.transcribe(str(audio_path), **kwargs)


def _resolve_hf_cache_snapshot(model: str) -> str | None:
    """Return a complete local Hugging Face snapshot path for repo ids such as org/name."""
    if "/" not in model or Path(model).exists():
        return None

    cache_home = Path(os.getenv("HF_HOME", Path.home() / ".cache" / "huggingface"))
    repo_dir = cache_home / "hub" / f"models--{model.replace('/', '--')}"
    snapshots_dir = repo_dir / "snapshots"
    if not snapshots_dir.exists():
        return None

    candidates: list[Path] = []
    ref_path = repo_dir / "refs" / "main"
    if ref_path.exists():
        ref = ref_path.read_text(encoding="utf-8").strip()
        if ref:
            candidates.append(snapshots_dir / ref)
    candidates.extend(sorted(snapshots_dir.iterdir(), key=lambda p: p.stat().st_mtime, reverse=True))

    for snapshot in candidates:
        if (snapshot / "config.json").exists() and (snapshot / "weights.npz").exists():
            log(f"Using cached model snapshot: {snapshot}")
            return str(snapshot)
    return None


def _transcribe_with_openai_whisper(audio_path: Path, language: str | None, model: str) -> dict[str, Any]:
    import whisper

    loaded = whisper.load_model(model)
    kwargs: dict[str, Any] = {"word_timestamps": True}
    if language:
        kwargs["language"] = language
    return loaded.transcribe(str(audio_path), **kwargs)


def _select_backend(requested: str) -> str:
    if requested != "auto":
        return requested

    if platform.system() == "Darwin" and platform.machine() == "arm64":
        try:
            import mlx_whisper  # noqa: F401
            return "mlx"
        except ImportError:
            pass

    try:
        import whisper  # noqa: F401
        return "openai-whisper"
    except ImportError:
        pass

    raise RuntimeError(
        "No local transcription backend is installed. Run setup.sh, or install "
        "mlx-whisper on Apple Silicon / openai-whisper as a fallback."
    )


def transcribe_file(
    audio_path: Path,
    *,
    output_path: Path | None = None,
    language: str | None = "zh",
    backend: str = "auto",
    model: str | None = None,
) -> list[dict[str, Any]]:
    audio_path = Path(audio_path)
    selected = _select_backend(backend)

    if selected == "mlx":
        model_name = model or DEFAULT_MLX_MODEL
        log(f"Transcribing with mlx-whisper model: {model_name}")
        result = _transcribe_with_mlx(audio_path, language, model_name)
    elif selected == "openai-whisper":
        model_name = model or DEFAULT_WHISPER_MODEL
        log(f"Transcribing with openai-whisper model: {model_name}")
        result = _transcribe_with_openai_whisper(audio_path, language, model_name)
    else:
        raise RuntimeError(f"Unsupported backend: {selected}")

    segments = _result_to_segments(result)
    if output_path:
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(segments, ensure_ascii=False, indent=2), encoding="utf-8")
        log(f"Saved transcript: {output_path}")

    log(f"Transcribed {len(segments)} segment(s)")
    return segments


def main():
    parser = argparse.ArgumentParser(description="Transcribe audio with a local Whisper-family model")
    parser.add_argument("--audio", required=True, help="Local audio file path")
    parser.add_argument("--output", required=True, help="Output transcript.json path")
    parser.add_argument("--language", default="zh", help="Language code, e.g. zh or en. Use None for auto.")
    parser.add_argument("--backend", default="auto", choices=["auto", "mlx", "openai-whisper"],
                        help="Local ASR backend. Default: auto.")
    parser.add_argument("--model", default=None,
                        help="Model name/path. Defaults to the locally cached mlx-community/whisper-large-v3-mlx on MLX.")
    args = parser.parse_args()

    language = None if args.language == "None" else args.language
    try:
        transcribe_file(
            Path(args.audio),
            output_path=Path(args.output),
            language=language,
            backend=args.backend,
            model=args.model,
        )
    except Exception as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
