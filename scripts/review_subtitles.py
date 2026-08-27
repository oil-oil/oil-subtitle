#!/usr/bin/env python3
"""Prepare a transcript and a deterministic review packet for an Agent."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any


VERSION_TERM = re.compile(
    r"\b[A-Za-z][A-Za-z0-9]*(?:[ ._-]+[A-Za-z][A-Za-z0-9]*){0,3}"
    r"[ ._-]+(?:v(?:ersion)?[ ._-]*)?\d+(?:[.-]\d+)*"
    r"(?:[ ._-]+(?:Pro|Max|Plus|Flash|Turbo|Preview|Thinking|Coder|High))?\b",
    re.IGNORECASE,
)
COMMAND_OR_FILE = re.compile(
    r"(?:`[^`]+`|--[a-z][\w-]+|(?:^|\s)[~/][^\s，。！？]+|"
    r"\b[\w.-]+\.(?:js|jsx|ts|tsx|py|json|yaml|yml|toml|md|sh|css|html)\b)",
    re.IGNORECASE,
)
ASCII_TOKEN = re.compile(r"(?<![A-Za-z0-9])[A-Za-z][A-Za-z0-9._+-]*(?![A-Za-z0-9])")


def log(message: str) -> None:
    print(f"[subtitle-review] {message}", flush=True)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Copy transcript text unchanged and prepare technical terms for Agent review."
    )
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--transcript", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--frames-dir", type=Path, required=True)
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


def heuristic_review_candidates(
    segments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Collect technical-looking substrings without proposing replacements."""
    candidates: dict[tuple[int, str], dict[str, Any]] = {}
    patterns = (
        (VERSION_TERM, "型号或版本号"),
        (COMMAND_OR_FILE, "命令或文件名"),
        (ASCII_TOKEN, "英文或技术词"),
    )
    for segment_id, segment in enumerate(segments):
        text = str(segment.get("text") or "")
        for pattern, reason in patterns:
            for match in pattern.finditer(text):
                original = match.group(0).strip(" `")
                if not original:
                    continue
                key = (segment_id, original.casefold())
                existing = candidates.get(key)
                if existing:
                    if reason not in existing["reason"]:
                        existing["reason"] += f"、{reason}"
                    continue
                candidates[key] = {
                    "segment_id": segment_id,
                    "start": round(float(segment.get("start", 0)), 3),
                    "end": round(float(segment.get("end", 0)), 3),
                    "text": text,
                    "original": original,
                    "reason": reason,
                    "status": "needs-agent-review",
                }
    return sorted(candidates.values(), key=lambda item: (item["segment_id"], item["original"]))


def prepare_agent_review(
    *,
    video: Path,
    transcript: Path,
    output: Path,
    report_path: Path,
    frames_dir: Path,
) -> dict[str, Any]:
    if not video.is_file():
        raise FileNotFoundError(f"Video not found: {video}")
    payload, segments = load_transcript(transcript)
    candidates = heuristic_review_candidates(segments)

    output.parent.mkdir(parents=True, exist_ok=True)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    report = {
        "mode": "agent-only",
        "source_transcript": str(transcript.resolve()),
        "reviewed_transcript": str(output.resolve()),
        "video": str(video.resolve()),
        "frames_dir": str(frames_dir.resolve()),
        "summary": {
            "segments": len(segments),
            "candidates": len(candidates),
            "automatic_text_changes": 0,
        },
        "instructions": (
            "The output text is an unchanged copy. The Agent must review every segment, "
            "using candidates only as a focus list, and is the sole semantic editor."
        ),
        "candidates": candidates,
        "applied_or_verified": [],
        "unresolved": candidates,
    }
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def main() -> int:
    args = parse_args()
    report = prepare_agent_review(
        video=args.video,
        transcript=args.transcript,
        output=args.output,
        report_path=args.report,
        frames_dir=args.frames_dir,
    )
    log(
        f"Prepared {report['summary']['segments']} segment(s) and "
        f"{report['summary']['candidates']} focus candidate(s); changed 0 subtitle texts."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
