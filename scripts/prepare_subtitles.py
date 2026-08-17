#!/usr/bin/env python3
"""Prepare Chinese subtitles and broad video chapters."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from dashscope_client import call_qwen_json
from user_config import env_value, load_user_config, resolve_progress_enabled


DEFAULT_MODEL = "qwen-plus"
CHAPTER_PLANNING_VERSION = 3
DEFAULT_PROGRESS_MIN_DURATION = 180.0
DISPLAY_PUNCTUATION = re.compile(r"[，。！？；：、,.!?;:…]+")


def fail(message: str) -> None:
    raise SystemExit(f"Error: {message}")


def resolve_progress_min_duration(requested: float | None) -> float:
    if requested is not None:
        value = requested
    else:
        configured_env = env_value(
            "OIL_SUBTITLE_PROGRESS_MIN_DURATION",
            "SCREEN_STUDIO_EDITOR_PROGRESS_MIN_DURATION",
        )
        config = load_user_config()
        subtitle_config = config.get("subtitles") or {}
        if not isinstance(subtitle_config, dict):
            fail("subtitles must be a JSON object in the oil-subtitle config")
        value = configured_env or subtitle_config.get(
            "progress_min_duration_seconds", DEFAULT_PROGRESS_MIN_DURATION
        )
    try:
        resolved = float(value)
    except (TypeError, ValueError):
        fail("Progress minimum duration must be a number of seconds")
    if resolved < 0:
        fail("Progress minimum duration must not be negative")
    return resolved


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Prepare Chinese subtitles and broad chapters with Bailian Qwen."
    )
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chapters-output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument(
        "--min-progress-duration",
        type=float,
        default=None,
        help=(
            "Show broad chapter progress only above this duration in seconds. "
            "Defaults to subtitles.progress_min_duration_seconds in user config, "
            "then 180."
        ),
    )
    parser.add_argument(
        "--progress",
        action=argparse.BooleanOptionalAction,
        default=None,
        help=(
            "Enable chapter generation and progress display (default: enabled). "
            "Use --no-progress to disable both for this run."
        ),
    )
    parser.add_argument("--min-chapter-duration", type=float, default=75.0)
    parser.add_argument("--max-chapters", type=int, default=6)
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def load_segments(path: Path) -> list[dict[str, Any]]:
    data = json.loads(path.read_text(encoding="utf-8"))
    segments = data.get("segments", data) if isinstance(data, dict) else data
    if not isinstance(segments, list) or not segments:
        fail("Transcript has no segments")
    cleaned: list[dict[str, Any]] = []
    for index, raw in enumerate(segments):
        if not isinstance(raw, dict):
            fail(f"Transcript segment {index} is not an object")
        text = str(raw.get("text") or "").strip()
        start = float(raw.get("start", 0.0))
        end = float(raw.get("end", start))
        if not text or end <= start:
            fail(f"Transcript segment {index} is empty or has invalid timing")
        cleaned.append(dict(raw))
    return cleaned


def video_duration(path: Path | None, segments: list[dict[str, Any]]) -> float:
    if path:
        if not path.exists():
            fail(f"Video does not exist: {path}")
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-show_entries",
                "format=duration",
                "-of",
                "default=noprint_wrappers=1:nokey=1",
                str(path),
            ],
            check=True,
            capture_output=True,
            text=True,
        )
        return float(probe.stdout.strip())
    return max(float(segment["end"]) for segment in segments)


def display_text(text: str) -> str:
    return re.sub(r"\s+", " ", DISPLAY_PUNCTUATION.sub("", text)).strip()


def signature(payload: Any) -> str:
    encoded = json.dumps(
        payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def model_json(
    *,
    prompt: str,
    model: str,
    timeout: int,
    max_tokens: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    return call_qwen_json(
        prompt=prompt,
        system="你是视频章节编辑，只返回严格 JSON，不要解释",
        model=model,
        max_tokens=max_tokens,
        temperature=0,
        timeout=timeout,
    )


def chapter_prompt(segments: list[dict[str, Any]], duration: float, max_chapters: int) -> str:
    if duration <= 300:
        min_chapters = 2
        preferred_max_chapters = min(4, max_chapters)
    else:
        min_chapters = min(4, max_chapters)
        preferred_max_chapters = max_chapters
    rows = "\n".join(
        f"[{index} {float(segment['start']):.2f}s] {segment['text']}"
        for index, segment in enumerate(segments)
    )
    return f"""
Plan broad content chapters for this {duration:.1f}-second Mandarin video
Use {min_chapters} to {preferred_max_chapters} chapters total and avoid fragmented topic changes
Each chapter should normally last at least 75 seconds except a short closing
Use a new chapter only when the speaker moves to a different major question
story phase or answer block Do not split every list item into its own chapter

The first chapter must start at segment ID 0 Every later start_id must be an
existing segment ID Title each chapter in concise Chinese using 4 to 10 Chinese
characters and describe the content rather than the editing process

Return strict JSON only
{{"chapters":[{{"start_id":0,"title":"推特意外爆火"}}]}}

TRANSCRIPT
{rows}
""".strip()


def plan_chapters(
    segments: list[dict[str, Any]],
    duration: float,
    *,
    args: argparse.Namespace,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if not args.progress_enabled or duration <= args.min_progress_duration:
        return [], None
    chapter_signature = signature(
        {
            "version": CHAPTER_PLANNING_VERSION,
            "model": args.model,
            "duration": duration,
            "segments": [
                [index, item["start"], item["end"], item["text"]]
                for index, item in enumerate(segments)
            ],
            "max_chapters": args.max_chapters,
            "min_chapter_duration": args.min_chapter_duration,
        }
    )
    cache_path = args.work_dir / "chapters-response.json"
    usage: dict[str, Any] | None = None
    if args.resume and cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("signature") == chapter_signature:
            payload = cached["payload"]
        else:
            payload, usage = model_json(
                prompt=chapter_prompt(segments, duration, args.max_chapters),
                model=args.model,
                timeout=args.timeout,
                max_tokens=2500,
            )
    else:
        payload, usage = model_json(
            prompt=chapter_prompt(segments, duration, args.max_chapters),
            model=args.model,
            timeout=args.timeout,
            max_tokens=2500,
        )
    cache_path.write_text(
        json.dumps(
            {"signature": chapter_signature, "payload": payload, "usage": usage},
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )

    proposed: list[tuple[int, str]] = []
    for item in payload.get("chapters") or []:
        if not isinstance(item, dict):
            continue
        try:
            start_id = int(item.get("start_id"))
        except (TypeError, ValueError):
            continue
        title = display_text(str(item.get("title") or ""))
        if 0 <= start_id < len(segments) and title:
            proposed.append((start_id, title))
    proposed = sorted(dict(proposed).items())
    if not proposed or proposed[0][0] != 0:
        proposed.insert(0, (0, "内容开场"))

    broad: list[tuple[int, str]] = []
    for start_id, title in proposed:
        start = float(segments[start_id]["start"])
        if broad:
            previous_start = float(segments[broad[-1][0]]["start"])
            if start - previous_start < args.min_chapter_duration:
                continue
        broad.append((start_id, title))
        if len(broad) >= args.max_chapters:
            break
    if len(broad) < 2:
        fail("Chapter planner did not produce enough broad chapters")

    chapters: list[dict[str, Any]] = []
    for index, (start_id, title) in enumerate(broad):
        start = 0.0 if index == 0 else float(segments[start_id]["start"])
        end = (
            float(segments[broad[index + 1][0]]["start"])
            if index + 1 < len(broad)
            else duration
        )
        chapters.append(
            {
                "index": index + 1,
                "title": title,
                "start": round(start, 3),
                "end": round(end, 3),
                "start_segment_id": start_id,
            }
        )
    return chapters, usage


def main() -> None:
    args = parse_args()
    try:
        args.progress_enabled = resolve_progress_enabled(args.progress)
    except RuntimeError as exc:
        fail(str(exc))
    args.min_progress_duration = resolve_progress_min_duration(
        args.min_progress_duration
    )
    args.work_dir.mkdir(parents=True, exist_ok=True)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.chapters_output.parent.mkdir(parents=True, exist_ok=True)
    if args.manifest_output:
        args.manifest_output.parent.mkdir(parents=True, exist_ok=True)
        if not args.video:
            fail("--manifest-output requires --video")
    segments = load_segments(args.transcript)
    duration = video_duration(args.video, segments)
    usages: list[dict[str, Any]] = []

    prepared_segments: list[dict[str, Any]] = []
    for segment in segments:
        prepared = {
            "start": float(segment["start"]),
            "end": float(segment["end"]),
            "text": display_text(str(segment["text"])),
        }
        prepared_segments.append(prepared)
    chapters, chapter_usage = plan_chapters(segments, duration, args=args)
    if chapter_usage:
        usages.append(chapter_usage)

    subtitle_payload: dict[str, Any] = {
        "schema_version": 1,
        "subtitle_mode": "zh",
        "duration": round(duration, 3),
        "segments": prepared_segments,
        "language": "zh",
    }
    args.output.write_text(
        json.dumps(subtitle_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.chapters_output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "enabled": bool(chapters),
                "progress_requested": args.progress_enabled,
                "min_progress_duration": args.min_progress_duration,
                "duration": round(duration, 3),
                "chapters": chapters,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    if args.manifest_output:
        manifest_dir = args.manifest_output.parent.resolve()
        video_ref = os.path.relpath(args.video.resolve(), manifest_dir)
        transcript_ref = os.path.relpath(args.output.resolve(), manifest_dir)
        chapters_ref = os.path.relpath(args.chapters_output.resolve(), manifest_dir)
        language = {
            "code": "zh",
            "name": "中文",
            "transcript": transcript_ref,
            "source": True,
        }
        args.manifest_output.write_text(
            json.dumps(
                {
                    "video": video_ref,
                    "subtitle_mode": "zh",
                    "duration": round(duration, 3),
                    "progress_requested": args.progress_enabled,
                    "min_progress_duration": args.min_progress_duration,
                    "languages": [language],
                    "chapters_file": chapters_ref,
                    "chapters": chapters,
                },
                ensure_ascii=False,
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
    report = {
        "subtitle_mode": "zh",
        "segments": len(prepared_segments),
        "duration": round(duration, 3),
        "progress_enabled": bool(chapters),
        "chapters": chapters,
        "usage": usages,
        "output": str(args.output),
        "chapters_output": str(args.chapters_output),
        "manifest_output": str(args.manifest_output) if args.manifest_output else None,
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
