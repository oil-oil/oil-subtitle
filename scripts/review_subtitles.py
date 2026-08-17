#!/usr/bin/env python3
"""Review ASR subtitles with Qwen and verify uncertain terms against video frames."""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

from dashscope_client import call_qwen_multimodal_json, call_qwen_vision_json


FFMPEG = os.environ.get("FFMPEG") or shutil.which("ffmpeg") or "/opt/homebrew/bin/ffmpeg"
VERSION_TERM = re.compile(
    r"\b[A-Za-z][A-Za-z0-9]*(?:[ ._-]+[A-Za-z][A-Za-z0-9]*){0,3}"
    r"[ ._-]+(?:v(?:ersion)?[ ._-]*)?\d+(?:[.-]\d+)*"
    r"(?:[ ._-]+(?:Pro|Max|Plus|Flash|Turbo|Preview|Thinking|Coder))?\b",
    re.IGNORECASE,
)
COMMAND_OR_FILE = re.compile(
    r"(?:`[^`]+`|--[a-z][\w-]+|(?:^|\s)[~/][^\s，。！？]+|"
    r"\b[\w.-]+\.(?:js|jsx|ts|tsx|py|json|yaml|yml|toml|md|sh|css|html)\b)",
    re.IGNORECASE,
)


def log(message: str):
    print(f"[subtitle-review] {message}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Review transcript text and visually verify uncertain technical terms."
    )
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--frames-dir", type=Path, required=True)
    parser.add_argument("--text-model", default="qwen3.7-flash")
    parser.add_argument("--vision-model", default="qwen3.7-flash")
    parser.add_argument("--chunk-size", type=int, default=80)
    parser.add_argument("--text-confidence", type=float, default=0.97)
    parser.add_argument("--vision-confidence", type=float, default=0.90)
    return parser.parse_args()


def load_transcript(path: Path) -> tuple[Any, list[dict[str, Any]]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    segments = payload.get("segments") if isinstance(payload, dict) else payload
    if not isinstance(segments, list) or not segments:
        raise ValueError("Transcript has no segments")
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict) or not str(segment.get("text") or "").strip():
            raise ValueError(f"Transcript segment {index} is invalid")
        segment.setdefault("start", 0.0)
        segment.setdefault("end", segment["start"])
    return payload, segments


def text_review_prompt(segments: list[dict[str, Any]], offset: int) -> str:
    rows = [
        {
            "segment_id": offset + index,
            "start": round(float(segment.get("start", 0)), 3),
            "end": round(float(segment.get("end", 0)), 3),
            "text": str(segment.get("text") or ""),
        }
        for index, segment in enumerate(segments)
    ]
    return f"""校对下面的中文字幕，只找真实的 ASR 错误，不润色，不改写语气。

分类规则：
- 能从上下文唯一确定的错字、产品名和大小写，decision=replace。
- 型号、版本号、命令、文件名、组织名和界面文字不得凭模型知识猜测，decision=visual。
- 原文正确则不要输出。
- original 必须是该 segment 原文中的连续子串；suggested 只写替换后的子串。
- confidence 使用 0 到 1。只有完全确定才可高于 0.97。

只返回 JSON：
{{"items":[{{"segment_id":0,"original":"错误子串","suggested":"建议子串或空字符串","decision":"replace或visual","confidence":0.0,"reason":"简短原因"}}]}}

字幕：
{json.dumps(rows, ensure_ascii=False)}"""


def normalize_candidate(raw: dict[str, Any], segment_count: int) -> dict[str, Any] | None:
    try:
        segment_id = int(raw.get("segment_id"))
        confidence = float(raw.get("confidence", 0))
    except (TypeError, ValueError):
        return None
    original = str(raw.get("original") or "").strip()
    suggested = str(raw.get("suggested") or "").strip()
    decision = str(raw.get("decision") or "").strip().lower()
    if not 0 <= segment_id < segment_count or not original:
        return None
    if decision not in {"replace", "visual"}:
        return None
    return {
        "segment_id": segment_id,
        "original": original,
        "suggested": suggested,
        "decision": decision,
        "confidence": max(0.0, min(1.0, confidence)),
        "reason": str(raw.get("reason") or "").strip(),
        "source": "text-model",
    }


def model_candidates(
    segments: list[dict[str, Any]], model: str, chunk_size: int
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    items: list[dict[str, Any]] = []
    usage: list[dict[str, Any]] = []
    for offset in range(0, len(segments), chunk_size):
        chunk = segments[offset : offset + chunk_size]
        result, chunk_usage = call_qwen_multimodal_json(
            prompt=text_review_prompt(chunk, offset),
            model=model,
            max_tokens=3000,
            temperature=0,
            timeout=120,
        )
        for raw in result.get("items") or []:
            if isinstance(raw, dict):
                item = normalize_candidate(raw, len(segments))
                if item:
                    items.append(item)
        if chunk_usage:
            usage.append(chunk_usage)
    return items, usage


def heuristic_visual_candidates(segments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    for segment_id, segment in enumerate(segments):
        text = str(segment.get("text") or "")
        matches = list(VERSION_TERM.finditer(text)) + list(COMMAND_OR_FILE.finditer(text))
        for match in matches:
            original = match.group(0).strip(" `")
            if not original:
                continue
            candidates.append(
                {
                    "segment_id": segment_id,
                    "original": original,
                    "suggested": "",
                    "decision": "visual",
                    "confidence": 0.0,
                    "reason": "规则命中型号、版本号、命令或文件名",
                    "source": "rule",
                }
            )
    return candidates


def merge_candidates(candidates: list[dict[str, Any]]) -> list[dict[str, Any]]:
    merged: dict[tuple[int, str], dict[str, Any]] = {}
    for item in candidates:
        key = (item["segment_id"], item["original"].casefold())
        current = merged.get(key)
        if current is None:
            merged[key] = item
            continue
        if item["decision"] == "visual" or current["decision"] == "visual":
            current["decision"] = "visual"
        if item["confidence"] > current["confidence"]:
            current["confidence"] = item["confidence"]
            if item.get("suggested"):
                current["suggested"] = item["suggested"]
        current["source"] = "+".join(sorted(set(current["source"].split("+") + item["source"].split("+"))))
        if item.get("reason") and item["reason"] not in current.get("reason", ""):
            current["reason"] = "；".join(filter(None, [current.get("reason"), item["reason"]]))
    return sorted(merged.values(), key=lambda item: (item["segment_id"], item["original"]))


def safe_replace(text: str, original: str, suggested: str) -> tuple[str, bool]:
    if not original or not suggested or original == suggested:
        return text, False
    count = text.count(original)
    if count != 1:
        return text, False
    return text.replace(original, suggested, 1), True


def frame_times(segment: dict[str, Any]) -> list[float]:
    start = max(0.0, float(segment.get("start", 0)))
    end = max(start, float(segment.get("end", start)))
    center = (start + end) / 2
    values = [max(0.0, start - 0.6), center, end + 0.6]
    unique: list[float] = []
    for value in values:
        rounded = round(value, 3)
        if rounded not in unique:
            unique.append(rounded)
    return unique


def extract_frames(
    video: Path, segment_id: int, segment: dict[str, Any], frames_dir: Path
) -> list[Path]:
    frames_dir.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    for index, timestamp in enumerate(frame_times(segment), start=1):
        output = frames_dir / f"segment-{segment_id:04d}-{index}.jpg"
        command = [
            FFMPEG,
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-ss",
            f"{timestamp:.3f}",
            "-i",
            str(video),
            "-frames:v",
            "1",
            "-q:v",
            "3",
            str(output),
        ]
        result = subprocess.run(command, capture_output=True, text=True)
        if result.returncode != 0 or not output.exists():
            detail = (result.stderr or result.stdout or "unknown ffmpeg error").strip()
            raise RuntimeError(f"Could not extract frame at {timestamp:.3f}s: {detail}")
        paths.append(output)
    return paths


def vision_prompt(
    candidate: dict[str, Any], segments: list[dict[str, Any]]
) -> str:
    segment_id = candidate["segment_id"]
    context = [
        {
            "segment_id": index,
            "text": str(segments[index].get("text") or ""),
        }
        for index in range(max(0, segment_id - 1), min(len(segments), segment_id + 2))
    ]
    return f"""这些图片来自同一条字幕时间附近。请用画面中真实可见的文字核对字幕疑点。

疑点：{candidate['original']}
上下文：{json.dumps(context, ensure_ascii=False)}

规则：
- 只有图片清楚显示对应产品名、型号、版本号、命令或文件名时，才能 replace 或 keep。
- 图片没有显示、被遮挡或看不清时，decision=unresolved；不得使用常识猜测。
- suggested 只写替换疑点的准确子串，不改写整句。

只返回 JSON：
{{"decision":"replace或keep或unresolved","suggested":"准确子串或空字符串","confidence":0.0,"evidence":"画面证据的简短说明"}}"""


def main() -> int:
    args = parse_args()
    if not args.video.is_file():
        raise FileNotFoundError(f"Video not found: {args.video}")
    payload, segments = load_transcript(args.transcript)

    log(f"Reviewing {len(segments)} subtitle segment(s) with {args.text_model}...")
    text_items, text_usage = model_candidates(segments, args.text_model, args.chunk_size)
    candidates = merge_candidates(text_items + heuristic_visual_candidates(segments))

    applied: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    visual_usage: list[dict[str, Any]] = []
    visual_count = 0
    for item in candidates:
        segment = segments[item["segment_id"]]
        current_text = str(segment.get("text") or "")
        if item["original"] not in current_text:
            unresolved.append({**item, "status": "original-not-found"})
            continue

        if item["decision"] == "replace" and item["confidence"] >= args.text_confidence:
            updated, changed = safe_replace(current_text, item["original"], item["suggested"])
            if changed:
                segment["text"] = updated
                applied.append({**item, "status": "applied-text"})
                continue

        frames = extract_frames(args.video, item["segment_id"], segment, args.frames_dir)
        visual_count += len(frames)
        try:
            result, usage = call_qwen_vision_json(
                prompt=vision_prompt(item, segments),
                images=frames,
                model=args.vision_model,
                timeout=120,
            )
            if usage:
                visual_usage.append(usage)
        except Exception as exc:
            unresolved.append({**item, "status": "vision-error", "error": str(exc), "frames": [str(path) for path in frames]})
            continue

        decision = str(result.get("decision") or "").lower()
        suggested = str(result.get("suggested") or "").strip()
        try:
            confidence = float(result.get("confidence", 0))
        except (TypeError, ValueError):
            confidence = 0.0
        reviewed = {
            **item,
            "vision_decision": decision,
            "vision_suggested": suggested,
            "vision_confidence": confidence,
            "evidence": str(result.get("evidence") or "").strip(),
            "frames": [str(path) for path in frames],
        }
        if decision == "replace" and confidence >= args.vision_confidence:
            updated, changed = safe_replace(
                str(segment.get("text") or ""), item["original"], suggested
            )
            if changed:
                segment["text"] = updated
                applied.append({**reviewed, "status": "applied-vision"})
                continue
        if decision == "keep" and confidence >= args.vision_confidence:
            applied.append({**reviewed, "status": "verified-keep"})
        else:
            unresolved.append({**reviewed, "status": "needs-user-review"})

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    report = {
        "source_transcript": str(args.transcript.resolve()),
        "reviewed_transcript": str(args.output.resolve()),
        "video": str(args.video.resolve()),
        "models": {"text": args.text_model, "vision": args.vision_model},
        "summary": {
            "segments": len(segments),
            "candidates": len(candidates),
            "applied_or_verified": len(applied),
            "unresolved": len(unresolved),
            "frames": visual_count,
        },
        "applied_or_verified": applied,
        "unresolved": unresolved,
        "usage": {"text": text_usage, "vision": visual_usage},
    }
    args.report.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    log(
        f"Saved {args.output}; {len(applied)} applied/verified, "
        f"{len(unresolved)} unresolved, {visual_count} frame(s)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
