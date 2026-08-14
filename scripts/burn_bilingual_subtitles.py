#!/usr/bin/env python3
"""Burn Chinese-over-English subtitles and an optional broad chapter bar."""

from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
from pathlib import Path
from typing import Any

import burn_subtitles as base


PUNCTUATION = re.compile(r"[，。！？；：、,.!?;:…]+")


def fail(message: str) -> None:
    raise SystemExit(f"Error: {message}")


def ass_time(seconds: float) -> str:
    return base.seconds_to_ass_time(max(0.0, seconds))


def ass_escape(text: str) -> str:
    return (
        text.replace("\\", "＼")
        .replace("{", "｛")
        .replace("}", "｝")
        .strip()
    )


def clean_display_text(text: str) -> str:
    return re.sub(r"\s+", " ", PUNCTUATION.sub("", text)).strip()


def wrap_zh(text: str, max_chars: int) -> list[str]:
    text = clean_display_text(text)
    return [part for part in base._split_text(text, max_chars) if part.strip()]


def wrap_en(text: str, max_chars: int) -> list[str]:
    words = clean_display_text(text).split()
    if not words:
        return []
    lines: list[str] = []
    current = words[0]
    for word in words[1:]:
        candidate = f"{current} {word}"
        if len(candidate) <= max_chars:
            current = candidate
        else:
            lines.append(current)
            current = word
    lines.append(current)
    return lines


def load_bilingual(path: Path) -> tuple[list[dict[str, Any]], float]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    segments = payload.get("segments", payload) if isinstance(payload, dict) else payload
    if not isinstance(segments, list) or not segments:
        fail("Bilingual transcript has no segments")
    result: list[dict[str, Any]] = []
    for index, item in enumerate(segments):
        if not isinstance(item, dict):
            fail(f"Segment {index} is not an object")
        start = float(item.get("start", 0.0))
        end = float(item.get("end", start))
        zh = clean_display_text(str(item.get("zh") or ""))
        en = clean_display_text(str(item.get("en") or ""))
        if not zh or not en or end <= start:
            fail(f"Segment {index} is missing bilingual text or valid timing")
        result.append({"start": start, "end": end, "zh": zh, "en": en})

    result.sort(key=lambda item: (item["start"], item["end"]))
    normalized: list[dict[str, Any]] = []
    for index, item in enumerate(result):
        end = item["end"]
        if index + 1 < len(result):
            next_start = result[index + 1]["start"]
            if end >= next_start:
                end = max(item["start"] + 0.04, next_start - 0.02)
        normalized.append({**item, "end": end})
    duration = float(payload.get("duration", 0.0)) if isinstance(payload, dict) else 0.0
    return normalized, max(duration, max(item["end"] for item in normalized))


def load_chapters(path: Path | None, duration: float) -> list[dict[str, Any]]:
    if path is None:
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not payload.get("enabled") or duration <= float(payload.get("min_progress_duration", 180.0)):
        return []
    raw_chapters = payload.get("chapters") or []
    if not 2 <= len(raw_chapters) <= 6:
        fail("Progress chapters must contain 2 to 6 broad sections")
    chapters: list[dict[str, Any]] = []
    for index, item in enumerate(raw_chapters):
        start = 0.0 if index == 0 else float(item.get("start", 0.0))
        end = float(item.get("end", duration))
        title = clean_display_text(str(item.get("title") or ""))
        if not title or start < 0 or end <= start or end > duration + 0.5:
            fail(f"Invalid chapter {index + 1}")
        if chapters and start < chapters[-1]["end"] - 0.05:
            fail("Progress chapters overlap")
        chapters.append(
            {"index": index + 1, "title": title, "start": start, "end": end}
        )
    chapters[-1]["end"] = duration
    return chapters


def probe_video(path: Path) -> tuple[int, int, float]:
    probe = subprocess.run(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_streams",
            "-show_format",
            "-of",
            "json",
            str(path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(probe.stdout)
    width = height = 0
    rotation = 0
    for stream in payload.get("streams") or []:
        if stream.get("codec_type") != "video":
            continue
        width = int(stream.get("width") or 0)
        height = int(stream.get("height") or 0)
        for side_data in stream.get("side_data_list") or []:
            if "rotation" in side_data:
                rotation = int(side_data["rotation"])
                break
        break
    if abs(rotation) in (90, 270):
        width, height = height, width
    if not width or not height:
        fail("Could not read video dimensions")
    duration = float((payload.get("format") or {}).get("duration") or 0.0)
    return width, height, duration


def box_geometry(
    zh_lines: list[str],
    en_lines: list[str],
    *,
    width: int,
    height: int,
    zh_size: int,
    en_size: int,
    margin_v: int,
) -> tuple[int, int, int, int]:
    zh_width = max((base._visual_len(line) * zh_size * 0.72 for line in zh_lines), default=0)
    en_width = max((len(line) * en_size * 0.54 for line in en_lines), default=0)
    pad_x, pad_y, line_gap = caption_spacing(zh_size)
    box_width = min(int(max(zh_width, en_width) + pad_x * 2), int(width * 0.92))
    box_height = int(
        len(zh_lines) * zh_size * 1.12
        + len(en_lines) * en_size * 1.18
        + line_gap
        + pad_y * 2
    )
    box_x = int((width - box_width) / 2)
    box_y = int(height - margin_v - box_height)
    return box_x, box_y, box_width, box_height


def caption_spacing(zh_size: int) -> tuple[int, int, int]:
    """Return shared horizontal padding, vertical padding, and line gap."""
    return (
        max(1, int(zh_size * 0.22)),
        max(1, int(zh_size * 0.08)),
        max(1, int(zh_size * 0.04)),
    )


def caption_visual_center_offset(zh_size: int) -> int:
    """Compensate for libass font baselines so visible top/bottom gaps match."""
    return max(1, int(round(zh_size * 0.20)))


def rectangle_path(width: int, height: int) -> str:
    return f"m 0 0 l {width} 0 l {width} {height} l 0 {height}"


def generate_ass(
    segments: list[dict[str, Any]],
    chapters: list[dict[str, Any]],
    output: Path,
    *,
    width: int,
    height: int,
    duration: float,
) -> None:
    portrait = height > width
    zh_size = max(46, int(height * (0.034 if portrait else 0.040)))
    en_size = max(30, int(zh_size * 0.70))
    progress_height = max(22, int(height * 0.021)) if chapters else 0
    progress_font = max(12, int(progress_height * 0.58)) if chapters else 12
    margin_v = int(height * (0.18 if portrait else 0.045))
    if chapters:
        margin_v = max(margin_v, progress_height + int(height * 0.020))
    _, caption_pad_y, _ = caption_spacing(zh_size)
    text_margin_v = (
        margin_v + caption_pad_y + caption_visual_center_offset(zh_size)
    )
    zh_max_chars = 17 if portrait else 29
    en_max_chars = 34 if portrait else 62

    header = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 0

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: CaptionText,PingFang SC,{zh_size},&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,2,20,20,{text_margin_v},1
Style: CaptionBox,Arial,10,&H00FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,7,0,0,0,1
Style: ProgressLabel,PingFang SC,{progress_font},&H30FFFFFF,&H000000FF,&H00000000,&H00000000,0,0,0,0,100,100,0,0,1,0,0,5,0,0,0,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    events: list[str] = []

    if chapters:
        y = height - progress_height
        track = rectangle_path(width, progress_height)
        events.append(
            f"Dialogue: 0,{ass_time(0)},{ass_time(duration)},CaptionBox,,0,0,0,,"
            f"{{\\an7\\pos(0,{y})\\p1\\1c&H3E3E40&\\1a&H58&\\bord0\\shad0}}{track}"
        )
        step = 1.0
        tick_count = int(math.ceil(duration / step))
        for tick in range(tick_count):
            start = tick * step
            end = min(duration, (tick + 1) * step)
            fill_width = max(1, int(width * end / duration))
            fill = rectangle_path(fill_width, progress_height)
            events.append(
                f"Dialogue: 1,{ass_time(start)},{ass_time(end)},CaptionBox,,0,0,0,,"
                f"{{\\an7\\pos(0,{y})\\p1\\1c&HBBBBBB&\\1a&H70&\\bord0\\shad0}}{fill}"
            )
        separator_width = max(1, int(width * 0.0007))
        for chapter in chapters[1:]:
            x = int(width * float(chapter["start"]) / duration)
            separator = rectangle_path(separator_width, progress_height)
            events.append(
                f"Dialogue: 2,{ass_time(0)},{ass_time(duration)},CaptionBox,,0,0,0,,"
                f"{{\\an7\\pos({x},{y})\\p1\\1c&HFFFFFF&\\1a&H58&\\bord0\\shad0}}{separator}"
            )
        for chapter in chapters:
            chapter_start = float(chapter["start"])
            chapter_end = float(chapter["end"])
            chapter_x = int(width * (chapter_start + chapter_end) / (2 * duration))
            chapter_y = y + progress_height // 2
            label = ass_escape(str(chapter["title"]))
            events.append(
                f"Dialogue: 3,{ass_time(0)},{ass_time(duration)},"
                f"ProgressLabel,,0,0,0,,{{\\an5\\pos({chapter_x},{chapter_y})}}{label}"
            )

    for segment in segments:
        zh_lines = wrap_zh(segment["zh"], zh_max_chars)
        en_lines = wrap_en(segment["en"], en_max_chars)
        box_x, box_y, box_width, box_height = box_geometry(
            zh_lines,
            en_lines,
            width=width,
            height=height,
            zh_size=zh_size,
            en_size=en_size,
            margin_v=margin_v,
        )
        radius = max(5, int(zh_size * 0.22))
        box_path = base._rounded_rect_path(box_width, box_height, radius)
        start = ass_time(segment["start"])
        end = ass_time(segment["end"])
        zh = "\\N".join(ass_escape(line) for line in zh_lines)
        en = "\\N".join(ass_escape(line) for line in en_lines)
        text = (
            f"{{\\fs{zh_size}\\c&HFFFFFF&}}{zh}"
            f"\\N{{\\fs{en_size}\\c&HECECEC&}}{en}"
        )
        events.append(
            f"Dialogue: 4,{start},{end},CaptionBox,,0,0,0,,"
            f"{{\\an7\\pos({box_x},{box_y})\\p1\\1c&H1A1A1C&\\1a&H58&\\bord0\\shad0}}{box_path}"
        )
        events.append(f"Dialogue: 5,{start},{end},CaptionText,,0,0,0,,{text}")

    output.write_text(header + "\n".join(events) + "\n", encoding="utf-8")
    base.log(
        f"Generated bilingual ASS: {output.name} "
        f"({len(segments)} captions, {len(chapters)} chapters)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Burn Chinese-over-English subtitles and a broad progress bar"
    )
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--chapters", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--ass-only", action="store_true")
    parser.add_argument("--no-beauty", action="store_true")
    parser.add_argument("--encoder", choices=["x264", "videotoolbox"], default="x264")
    args = parser.parse_args()

    if not args.video.exists():
        fail(f"Video does not exist: {args.video}")
    if not args.transcript.exists():
        fail(f"Transcript does not exist: {args.transcript}")
    if args.chapters and not args.chapters.exists():
        fail(f"Chapters do not exist: {args.chapters}")

    output = args.output or args.video.with_name(args.video.stem + "_bilingual.mp4")
    output.parent.mkdir(parents=True, exist_ok=True)
    ass_output = output.with_suffix(".ass")
    width, height, video_duration = probe_video(args.video)
    segments, transcript_duration = load_bilingual(args.transcript)
    duration = video_duration or transcript_duration
    chapters = load_chapters(args.chapters, duration)
    base.log(f"Video resolution: {width}x{height} duration {duration:.2f}s")
    base.log(
        "Progress bar enabled with broad content chapters"
        if chapters
        else "Progress bar disabled because the video is at most three minutes or has no chapters"
    )
    generate_ass(
        segments,
        chapters,
        ass_output,
        width=width,
        height=height,
        duration=duration,
    )
    if args.ass_only:
        return

    camera_region = None
    if not args.no_beauty:
        base.log("Detecting the persistent camera face with macOS Vision...")
        camera_region = base.detect_camera_region(args.video, width, height)
        if camera_region is None:
            base.log("No stable camera face found; continuing without beauty")
    base.burn_subtitles(
        args.video,
        ass_output,
        output,
        total_duration_s=duration,
        encoder=args.encoder,
        video_size=(width, height),
        camera_region=camera_region,
    )
    base.log(f"Done: {output}")


if __name__ == "__main__":
    main()
