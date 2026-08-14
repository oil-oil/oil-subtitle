#!/usr/bin/env python3
"""Prepare Chinese or bilingual subtitles and broad video chapters."""

from __future__ import annotations

import argparse
import concurrent.futures
import hashlib
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from subtitle_api import (
    DEFAULT_API_BASE,
    DEFAULT_API_KEY_FILE,
    extract_json_from_text,
    post_json,
)
from user_config import env_value, load_user_config


DEFAULT_MODEL = "google/gemini-3.5-flash"
TRANSLATION_VERSION = 1
CHAPTER_PLANNING_VERSION = 2
DEFAULT_PROGRESS_MIN_DURATION = 180.0
DISPLAY_PUNCTUATION = re.compile(r"[，。！？；：、,.!?;:…]+")
VALID_SUBTITLE_MODES = {"zh", "bilingual"}


def fail(message: str) -> None:
    raise SystemExit(f"Error: {message}")


def resolve_subtitle_mode(requested: str) -> str:
    if requested != "auto":
        return requested
    env_mode = env_value(
        "OIL_SUBTITLE_MODE", "SCREEN_STUDIO_EDITOR_SUBTITLE_MODE"
    )
    config = load_user_config()
    subtitle_config = config.get("subtitles") or {}
    if not isinstance(subtitle_config, dict):
        fail("subtitles must be a JSON object in the oil-subtitle config")
    mode = env_mode or str(subtitle_config.get("mode") or "zh").strip()
    if mode not in VALID_SUBTITLE_MODES:
        fail("Subtitle mode must be 'zh' or 'bilingual'")
    return mode


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
        description="Prepare Chinese or bilingual subtitles and broad chapters."
    )
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--video", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--chapters-output", type=Path, required=True)
    parser.add_argument("--manifest-output", type=Path)
    parser.add_argument("--work-dir", type=Path, required=True)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--api-key", default="")
    parser.add_argument("--api-key-file", type=Path, default=DEFAULT_API_KEY_FILE)
    parser.add_argument("--timeout", type=int, default=240)
    parser.add_argument("--batch-size", type=int, default=60)
    parser.add_argument("--workers", type=int, default=3)
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
    parser.add_argument("--min-chapter-duration", type=float, default=75.0)
    parser.add_argument("--max-chapters", type=int, default=6)
    parser.add_argument(
        "--subtitle-mode",
        choices=["auto", "zh", "bilingual"],
        default="auto",
        help=(
            "Subtitle language mode. auto reads OIL_SUBTITLE_MODE "
            "or subtitles.mode from user config, then defaults to zh."
        ),
    )
    parser.add_argument("--resume", action="store_true")
    return parser.parse_args()


def api_key_from_args(args: argparse.Namespace) -> str:
    key = args.api_key or os.environ.get("ZENMUX_API_KEY", "")
    if not key and args.api_key_file.exists():
        key = args.api_key_file.read_text(encoding="utf-8").strip()
    if not key:
        fail(f"ZenMux key not found in the environment or {args.api_key_file}")
    return key


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


def response_text(response: dict[str, Any]) -> str:
    choices = response.get("choices") or []
    if not choices:
        fail("Model response contains no choices")
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            str(item.get("text") or "")
            for item in content
            if isinstance(item, dict)
        )
    fail("Model response contains no text")


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
    api_base: str,
    api_key: str,
    timeout: int,
    max_tokens: int,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    retry_note = ""
    for attempt in range(2):
        request_payload: dict[str, Any] = {
            "model": model,
            "messages": [
                {
                    "role": "system",
                    "content": "You are a meticulous bilingual subtitle editor Return strict JSON only",
                },
                {"role": "user", "content": prompt + retry_note},
            ],
            "max_completion_tokens": max_tokens,
            "response_format": {"type": "json_object"},
        }
        if not model.startswith("anthropic/"):
            request_payload["temperature"] = 0
        response = post_json(
            f"{api_base.rstrip('/')}/chat/completions",
            request_payload,
            api_key,
            timeout,
        )
        try:
            return extract_json_from_text(response_text(response)), response.get("usage")
        except json.JSONDecodeError:
            if attempt == 1:
                raise
            retry_note = (
                "\nYour previous response was invalid JSON Return only valid JSON "
                "with double quoted keys and strings and no trailing commas"
            )
    raise AssertionError("unreachable")


def translation_prompt(rows: list[dict[str, Any]]) -> str:
    source = "\n".join(f"[{row['id']}] {row['text']}" for row in rows)
    return f"""
Translate every TARGET Mandarin subtitle below into concise natural English
for a bilingual talking-head video The Mandarin line will appear above and
your English line below it Preserve product names and established spellings
such as Twitter Vibe Coding Vibe Hub Selector oil-motion Skill AI Agent UI
Proof of Work MBA and AI Product Builder

Rules
- Return exactly one translation for every supplied ID in the same order
- Translate the meaning faithfully without adding explanations
- Keep each English line compact enough to read during the original timing
- Do not end lines with commas periods question marks exclamation marks colons
  semicolons or similar display punctuation
- If the source is already English preserve it with correct capitalization

Return strict JSON only
{{"translations":[{{"id":0,"en":"Hello everyone"}}]}}

TARGETS
{source}
""".strip()


def translate_batch(
    rows: list[dict[str, Any]],
    *,
    args: argparse.Namespace,
    api_key: str,
) -> tuple[dict[int, str], dict[str, Any] | None]:
    batch_signature = signature(
        {
            "version": TRANSLATION_VERSION,
            "model": args.model,
            "rows": rows,
        }
    )
    cache_path = args.work_dir / f"translation-{rows[0]['id']:04d}-{rows[-1]['id']:04d}.json"
    payload: dict[str, Any]
    usage: dict[str, Any] | None = None
    if args.resume and cache_path.exists():
        cached = json.loads(cache_path.read_text(encoding="utf-8"))
        if cached.get("signature") == batch_signature:
            payload = cached["payload"]
        else:
            payload, usage = model_json(
                prompt=translation_prompt(rows),
                model=args.model,
                api_base=args.api_base,
                api_key=api_key,
                timeout=args.timeout,
                max_tokens=12_000,
            )
    else:
        payload, usage = model_json(
            prompt=translation_prompt(rows),
            model=args.model,
            api_base=args.api_base,
            api_key=api_key,
            timeout=args.timeout,
            max_tokens=12_000,
        )

    expected = [int(row["id"]) for row in rows]
    translated: dict[int, str] = {}
    for item in payload.get("translations") or []:
        if not isinstance(item, dict):
            continue
        try:
            item_id = int(item.get("id"))
        except (TypeError, ValueError):
            continue
        text = display_text(str(item.get("en") or ""))
        if item_id in expected and text:
            translated[item_id] = text
    if sorted(translated) != expected:
        missing = sorted(set(expected) - set(translated))
        fail(f"Translation batch is incomplete missing IDs {missing}")

    cache_path.write_text(
        json.dumps(
            {
                "signature": batch_signature,
                "payload": payload,
                "usage": usage,
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return translated, usage


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
    api_key: str,
) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    if duration <= args.min_progress_duration:
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
                api_base=args.api_base,
                api_key=api_key,
                timeout=args.timeout,
                max_tokens=2500,
            )
    else:
        payload, usage = model_json(
            prompt=chapter_prompt(segments, duration, args.max_chapters),
            model=args.model,
            api_base=args.api_base,
            api_key=api_key,
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
    subtitle_mode = resolve_subtitle_mode(args.subtitle_mode)
    needs_model = subtitle_mode == "bilingual" or duration > args.min_progress_duration
    api_key = api_key_from_args(args) if needs_model else ""
    usages: list[dict[str, Any]] = []
    translations: dict[int, str] = {}
    if subtitle_mode == "bilingual":
        rows = [
            {"id": index, "text": str(segment["text"]).strip()}
            for index, segment in enumerate(segments)
        ]
        batches = [
            rows[index : index + args.batch_size]
            for index in range(0, len(rows), args.batch_size)
        ]
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as executor:
            futures = [
                executor.submit(translate_batch, batch, args=args, api_key=api_key)
                for batch in batches
            ]
            for future in concurrent.futures.as_completed(futures):
                translated, usage = future.result()
                translations.update(translated)
                if usage:
                    usages.append(usage)
        if sorted(translations) != list(range(len(segments))):
            fail("Final translation set does not match the transcript")

    prepared_segments: list[dict[str, Any]] = []
    for index, segment in enumerate(segments):
        prepared = {
            "start": float(segment["start"]),
            "end": float(segment["end"]),
        }
        if subtitle_mode == "bilingual":
            prepared.update(
                {
                    "zh": display_text(str(segment["text"])),
                    "en": translations[index],
                }
            )
        else:
            prepared["text"] = display_text(str(segment["text"]))
        prepared_segments.append(prepared)
    chapters, chapter_usage = plan_chapters(
        segments, duration, args=args, api_key=api_key
    )
    if chapter_usage:
        usages.append(chapter_usage)

    subtitle_payload: dict[str, Any] = {
        "schema_version": 1,
        "subtitle_mode": subtitle_mode,
        "duration": round(duration, 3),
        "segments": prepared_segments,
    }
    if subtitle_mode == "bilingual":
        subtitle_payload["language_order"] = ["zh", "en"]
    else:
        subtitle_payload["language"] = "zh"
    args.output.write_text(
        json.dumps(subtitle_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    args.chapters_output.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "enabled": bool(chapters),
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
        language = (
            {
                "code": "bilingual",
                "name": "中英双语",
                "transcript": transcript_ref,
                "source": True,
            }
            if subtitle_mode == "bilingual"
            else {
                "code": "zh",
                "name": "中文",
                "transcript": transcript_ref,
                "source": True,
            }
        )
        args.manifest_output.write_text(
            json.dumps(
                {
                    "video": video_ref,
                    "bilingual": subtitle_mode == "bilingual",
                    "subtitle_mode": subtitle_mode,
                    "duration": round(duration, 3),
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
        "subtitle_mode": subtitle_mode,
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
