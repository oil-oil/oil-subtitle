#!/usr/bin/env python3
"""Record preview edits for Agent review without changing the glossary."""

from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from user_config import resolve_glossary_path


PUNCTUATION = set("，。！？、；：,.!?;:…—-~·（）()【】[]《》<>“”‘’\"'`")


def _segments(payload: Any) -> list[dict[str, Any]]:
    segments = payload.get("segments") if isinstance(payload, dict) else payload
    if not isinstance(segments, list):
        raise ValueError("Transcript must contain a segments list")
    return [item for item in segments if isinstance(item, dict)]


def _identity(segment: dict[str, Any]) -> tuple[float, float]:
    return (
        round(float(segment.get("start") or 0), 3),
        round(float(segment.get("end") or segment.get("start") or 0), 3),
    )


def _minimal_change(before: str, after: str) -> tuple[str, str]:
    prefix = 0
    limit = min(len(before), len(after))
    while prefix < limit and before[prefix] == after[prefix]:
        prefix += 1
    suffix = 0
    remaining_before = len(before) - prefix
    remaining_after = len(after) - prefix
    while (
        suffix < remaining_before
        and suffix < remaining_after
        and before[len(before) - 1 - suffix] == after[len(after) - 1 - suffix]
    ):
        suffix += 1
    before_end = len(before) - suffix if suffix else len(before)
    after_end = len(after) - suffix if suffix else len(after)
    return before[prefix:before_end].strip(), after[prefix:after_end].strip()


def _punctuation_only(value: str) -> bool:
    return bool(value) and all(char in PUNCTUATION or char.isspace() for char in value)


def collect_manual_edits(
    before_payload: Any, after_payload: Any
) -> list[dict[str, Any]]:
    """Align segments by timestamps and describe every user-visible text change."""
    before_segments = _segments(before_payload)
    after_by_time: dict[tuple[float, float], list[dict[str, Any]]] = {}
    for segment in _segments(after_payload):
        after_by_time.setdefault(_identity(segment), []).append(segment)

    edits: list[dict[str, Any]] = []
    for index, before_segment in enumerate(before_segments):
        matches = after_by_time.get(_identity(before_segment)) or []
        if not matches:
            edits.append(
                {
                    "edit_id": index,
                    "segment_id": index,
                    "start": _identity(before_segment)[0],
                    "before": str(before_segment.get("text") or ""),
                    "after": "",
                    "wrong": "",
                    "correct": "",
                    "eligible": False,
                    "local_reason": "整条字幕被删除，不属于可复用错词",
                }
            )
            continue
        after_segment = matches.pop(0)
        before_text = str(before_segment.get("text") or "").strip()
        after_text = str(after_segment.get("text") or "").strip()
        if before_text == after_text:
            continue
        wrong, correct = _minimal_change(before_text, after_text)
        eligible = True
        local_reason = "等待 Agent 判断是否值得写入 glossary"
        if re.sub(r"\s+", "", before_text) == re.sub(r"\s+", "", after_text):
            eligible = False
            local_reason = "仅修改空白"
        elif not wrong or not correct:
            eligible = False
            local_reason = "只有新增或删除，没有稳定的错词映射"
        elif _punctuation_only(wrong) or _punctuation_only(correct):
            eligible = False
            local_reason = "仅修改标点或空白"
        elif len(wrong) > 48 or len(correct) > 48:
            eligible = False
            local_reason = "修改跨度过大，更像一次性改写"
        elif SequenceMatcher(None, before_text, after_text).ratio() < 0.45:
            eligible = False
            local_reason = "整句变化过大，更像一次性改写"
        edits.append(
            {
                "edit_id": index,
                "segment_id": index,
                "start": _identity(before_segment)[0],
                "before": before_text,
                "after": after_text,
                "wrong": wrong,
                "correct": correct,
                "eligible": eligible,
                "local_reason": local_reason,
                "context_before": [
                    str(item.get("text") or "")
                    for item in before_segments[max(0, index - 1) : index + 2]
                ],
            }
        )
    return edits


def learn_manual_edits(
    before_payload: Any,
    after_payload: Any,
    *,
    report_path: Path,
    glossary_path: Path | None = None,
) -> dict[str, Any]:
    """Compatibility entry point that records edits but never mutates a glossary."""
    edits = collect_manual_edits(before_payload, after_payload)
    pending = [item for item in edits if item["eligible"]]
    ignored = [
        {**item, "reason": item["local_reason"]}
        for item in edits
        if not item["eligible"]
    ]
    target = Path(glossary_path or resolve_glossary_path()).expanduser()
    report = {
        "status": "ok",
        "mode": "agent-review",
        "glossary": str(target),
        "edit_count": len(edits),
        "pending": pending,
        "learned": [],
        "ignored": ignored,
        "conflicts": [],
        "instructions": (
            "No model was called and no glossary entry was written. "
            "The Agent must review pending edits and explicitly update the glossary only "
            "for stable, reusable ASR mappings."
        ),
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Record preview edits for Agent review without changing the glossary."
    )
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--glossary", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    before = json.loads(args.before.read_text(encoding="utf-8"))
    after = json.loads(args.after.read_text(encoding="utf-8"))
    report = learn_manual_edits(
        before,
        after,
        report_path=args.report,
        glossary_path=args.glossary,
    )
    print(
        f"manual edits: {report['edit_count']}; pending Agent review: "
        f"{len(report['pending'])}; ignored: {len(report['ignored'])}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
