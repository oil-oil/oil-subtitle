#!/usr/bin/env python3
"""Learn reusable ASR corrections from edits saved in the preview editor."""

from __future__ import annotations

import argparse
import json
import re
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any

from dashscope_client import call_qwen_multimodal_json
from user_config import resolve_glossary_path


PUNCTUATION = set("，。！？、；：,.!?;:…—-~·（）()【】[]《》<>“”‘’\"'`")
ASCII_TOKEN = re.compile(r"[A-Za-z][A-Za-z0-9._+-]*")
CONTEXT_STOPWORDS = {
    "a", "an", "and", "for", "in", "is", "of", "on", "or", "the", "this", "that", "to", "with"
}


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
        local_reason = "等待模型判断"
        if not wrong or not correct:
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


def _review_prompt(edits: list[dict[str, Any]]) -> str:
    candidates = [
        {
            "edit_id": item["edit_id"],
            "before": item["before"],
            "after": item["after"],
            "changed_wrong": item["wrong"],
            "changed_correct": item["correct"],
            "context": item["context_before"],
        }
        for item in edits
        if item["eligible"]
    ]
    return f"""你是中文字幕 ASR 错题本的守门员。判断用户在预览页的每一处手动修改是否值得在未来视频中自动复用。

decision=learn 仅用于稳定、明确、很可能再次出现的 ASR 错词映射，例如专有名词、产品名、英文大小写、固定同音误识别。decision=ignore 用于润色、语气调整、删句、补充信息、标点空格、整句重写和只在当前上下文成立的表达。无法确定时必须 ignore。

输出的 wrong 和 correct 必须分别是 before 和 after 中真实存在的连续子串，并包含输入给出的 changed_wrong / changed_correct。选择带必要上下文的最小安全词组：产品名的一部分有误时，应保留能够限定产品身份的前缀，不能学习成单个字母的全局替换。不得写入原句不存在的词。confidence 只有在映射明确且可安全全局替换时才能达到 0.97。

只返回 JSON：
{{"items":[{{"edit_id":0,"decision":"learn或ignore","wrong":"原错词","correct":"正确词","confidence":0.0,"reason":"简短原因"}}]}}

人工修改：
{json.dumps(candidates, ensure_ascii=False)}"""


def _glossary_key(value: str) -> str:
    return re.sub(r"\s+", "", value).casefold()


def _contextualize_ascii_mapping(
    edit: dict[str, Any], wrong: str, correct: str
) -> tuple[str, str] | None:
    """Keep a shared neighboring product token around one-word Latin edits."""
    if not (ASCII_TOKEN.fullmatch(wrong) and ASCII_TOKEN.fullmatch(correct)):
        return wrong, correct
    if edit["before"].count(wrong) != 1 or edit["after"].count(correct) != 1:
        return None
    wrong_at = edit["before"].index(wrong)
    correct_at = edit["after"].index(correct)
    before_prefix = edit["before"][:wrong_at]
    after_prefix = edit["after"][:correct_at]
    before_prev = re.search(r"([A-Za-z][A-Za-z0-9._+-]*)(\s+)$", before_prefix)
    after_prev = re.search(r"([A-Za-z][A-Za-z0-9._+-]*)(\s+)$", after_prefix)
    if before_prev and after_prev:
        token = before_prev.group(1)
        if (
            token.casefold() == after_prev.group(1).casefold()
            and token.casefold() not in CONTEXT_STOPWORDS
        ):
            return (
                token + before_prev.group(2) + wrong,
                after_prev.group(1) + after_prev.group(2) + correct,
            )
    before_suffix = edit["before"][wrong_at + len(wrong) :]
    after_suffix = edit["after"][correct_at + len(correct) :]
    before_next = re.match(r"(\s+)([A-Za-z][A-Za-z0-9._+-]*)", before_suffix)
    after_next = re.match(r"(\s+)([A-Za-z][A-Za-z0-9._+-]*)", after_suffix)
    if before_next and after_next:
        token = before_next.group(2)
        if (
            token.casefold() == after_next.group(2).casefold()
            and token.casefold() not in CONTEXT_STOPWORDS
        ):
            return (
                wrong + before_next.group(1) + token,
                correct + after_next.group(1) + after_next.group(2),
            )
    return None


def _load_glossary(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, list):
        raise ValueError(f"Glossary must be a JSON list: {path}")
    return [item for item in payload if isinstance(item, dict)]


def _write_glossary(path: Path, entries: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(
        json.dumps(entries, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    temporary.chmod(0o600)
    temporary.replace(path)
    path.chmod(0o600)


def learn_manual_edits(
    before_payload: Any,
    after_payload: Any,
    *,
    report_path: Path,
    glossary_path: Path | None = None,
    model: str = "qwen3.7-flash",
    confidence_threshold: float = 0.97,
) -> dict[str, Any]:
    edits = collect_manual_edits(before_payload, after_payload)
    candidates = [item for item in edits if item["eligible"]]
    model_items: dict[int, dict[str, Any]] = {}
    usage = None
    error = None
    if candidates:
        try:
            result, usage = call_qwen_multimodal_json(
                prompt=_review_prompt(edits),
                model=model,
                max_tokens=1800,
                temperature=0,
                timeout=120,
            )
            for raw in result.get("items") or []:
                if not isinstance(raw, dict):
                    continue
                try:
                    edit_id = int(raw.get("edit_id"))
                    confidence = float(raw.get("confidence", 0))
                except (TypeError, ValueError):
                    continue
                model_items[edit_id] = {
                    "decision": str(raw.get("decision") or "").lower(),
                    "wrong": str(raw.get("wrong") or ""),
                    "correct": str(raw.get("correct") or ""),
                    "confidence": max(0.0, min(1.0, confidence)),
                    "reason": str(raw.get("reason") or "").strip(),
                }
        except Exception as exc:
            error = str(exc)

    target = Path(glossary_path or resolve_glossary_path()).expanduser()
    glossary = _load_glossary(target)
    existing = {
        _glossary_key(str(item.get("wrong") or "")): str(item.get("correct") or "")
        for item in glossary
        if str(item.get("wrong") or "").strip()
    }
    learned: list[dict[str, Any]] = []
    ignored: list[dict[str, Any]] = []
    conflicts: list[dict[str, Any]] = []
    for edit in edits:
        reviewed = model_items.get(edit["edit_id"])
        if not edit["eligible"]:
            ignored.append({**edit, "reason": edit["local_reason"]})
            continue
        if reviewed is None:
            ignored.append({**edit, "reason": error or "模型没有返回判断"})
            continue
        contextualized = _contextualize_ascii_mapping(
            edit, reviewed["wrong"], reviewed["correct"]
        )
        learned_wrong, learned_correct = contextualized or ("", "")
        safe_mapping = (
            bool(learned_wrong and learned_correct)
            and learned_wrong in edit["before"]
            and learned_correct in edit["after"]
            and edit["wrong"] in learned_wrong
            and edit["correct"] in learned_correct
            and learned_wrong != learned_correct
            and len(learned_wrong) <= 48
            and len(learned_correct) <= 48
        )
        if (
            reviewed["decision"] != "learn"
            or reviewed["confidence"] < confidence_threshold
            or not safe_mapping
        ):
            ignored.append({**edit, **reviewed})
            continue
        key = _glossary_key(learned_wrong)
        previous = existing.get(key)
        if previous is not None and previous != learned_correct:
            conflicts.append({**edit, **reviewed, "existing_correct": previous})
            continue
        if previous == learned_correct:
            ignored.append({**edit, **reviewed, "reason": "错题本已存在相同映射"})
            continue
        entry = {"wrong": learned_wrong, "correct": learned_correct}
        glossary.append(entry)
        existing[key] = learned_correct
        learned.append({**entry, "confidence": reviewed["confidence"], "reason": reviewed["reason"]})

    if learned:
        _write_glossary(target, glossary)
    report = {
        "status": "error" if error else "ok",
        "model": model,
        "glossary": str(target),
        "edit_count": len(edits),
        "learned": learned,
        "ignored": ignored,
        "conflicts": conflicts,
        "usage": usage,
        "error": error,
    }
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Learn safe, reusable glossary entries from preview edits."
    )
    parser.add_argument("--before", type=Path, required=True)
    parser.add_argument("--after", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    parser.add_argument("--glossary", type=Path)
    parser.add_argument("--model", default="qwen3.7-flash")
    parser.add_argument("--confidence", type=float, default=0.97)
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
        model=args.model,
        confidence_threshold=args.confidence,
    )
    print(
        f"manual edits: {report['edit_count']}; learned: {len(report['learned'])}; "
        f"ignored: {len(report['ignored'])}; conflicts: {len(report['conflicts'])}"
    )
    return 0 if report["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
