import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import learn_glossary as LEARN  # noqa: E402


def segment(start, text):
    return {"start": start, "end": start + 1, "text": text}


class ManualGlossaryLearningTests(unittest.TestCase):
    def test_collects_text_edits_but_filters_punctuation(self):
        before = {"segments": [
            segment(0, "Claude Core 很好用"),
            segment(2, "大家好"),
            segment(4, "一些AI实战"),
        ]}
        after = {"segments": [
            segment(0, "Claude Code 很好用"),
            segment(2, "大家好！"),
            segment(4, "一些 AI 实战"),
        ]}

        edits = LEARN.collect_manual_edits(before, after)

        self.assertTrue(edits[0]["eligible"])
        self.assertEqual(edits[0]["wrong"], "r")
        self.assertEqual(edits[0]["correct"], "d")
        self.assertFalse(edits[1]["eligible"])
        self.assertIn("新增或删除", edits[1]["local_reason"])
        self.assertFalse(edits[2]["eligible"])
        self.assertEqual(edits[2]["local_reason"], "仅修改空白")

    def test_model_can_select_a_contextual_safe_phrase(self):
        before = {
            "segments": [
                segment(0, "我们来测试 Claude Core"),
                segment(2, "这个功能其实还可以"),
            ]
        }
        after = {
            "segments": [
                segment(0, "我们来测试 Claude Code"),
                segment(2, "这个功能挺好用的"),
            ]
        }
        model_result = {
            "items": [
                {
                    "edit_id": 0,
                    "decision": "learn",
                    "wrong": "Claude Core",
                    "correct": "Claude Code",
                    "confidence": 0.99,
                    "reason": "稳定的产品名误识别",
                },
                {
                    "edit_id": 1,
                    "decision": "ignore",
                    "wrong": "其实还可以",
                    "correct": "挺好用的",
                    "confidence": 0.99,
                    "reason": "一次性表达改写",
                },
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            glossary = root / "glossary.json"
            report = root / "manual-edit-review.json"
            with patch.object(
                LEARN,
                "call_qwen_multimodal_json",
                return_value=(model_result, {"total_tokens": 42}),
            ):
                result = LEARN.learn_manual_edits(
                    before,
                    after,
                    report_path=report,
                    glossary_path=glossary,
                )

            self.assertEqual(
                json.loads(glossary.read_text(encoding="utf-8")),
                [{"wrong": "Claude Core", "correct": "Claude Code"}],
            )
            self.assertEqual(len(result["learned"]), 1)
            self.assertEqual(len(result["ignored"]), 1)
            self.assertEqual(json.loads(report.read_text())["status"], "ok")

    def test_single_latin_word_is_expanded_with_product_context(self):
        edit = {
            "before": "今天测试 Claude Core 这个功能",
            "after": "今天测试 Claude Code 这个功能",
        }

        self.assertEqual(
            LEARN._contextualize_ascii_mapping(edit, "Core", "Code"),
            ("Claude Core", "Claude Code"),
        )

    def test_single_latin_word_without_context_is_rejected(self):
        edit = {"before": "今天测试 Core", "after": "今天测试 Code"}

        self.assertIsNone(
            LEARN._contextualize_ascii_mapping(edit, "Core", "Code")
        )

    def test_model_failure_is_reported_without_writing_glossary(self):
        before = {"segments": [segment(0, "白练平台")]}
        after = {"segments": [segment(0, "百炼平台")]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            glossary = root / "glossary.json"
            report = root / "report.json"
            with patch.object(
                LEARN,
                "call_qwen_multimodal_json",
                side_effect=RuntimeError("temporary model error"),
            ):
                result = LEARN.learn_manual_edits(
                    before,
                    after,
                    report_path=report,
                    glossary_path=glossary,
                )

            self.assertEqual(result["status"], "error")
            self.assertFalse(glossary.exists())
            self.assertEqual(json.loads(report.read_text())["status"], "error")

    def test_existing_conflict_is_never_overwritten(self):
        before = {"segments": [segment(0, "白练平台")]}
        after = {"segments": [segment(0, "百炼平台")]}
        model_result = {
            "items": [{
                "edit_id": 0,
                "decision": "learn",
                "wrong": "白练",
                "correct": "百炼",
                "confidence": 0.99,
                "reason": "固定同音误识别",
            }]
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            glossary = root / "glossary.json"
            glossary.write_text(
                json.dumps([{"wrong": "白练", "correct": "白链"}], ensure_ascii=False),
                encoding="utf-8",
            )
            with patch.object(
                LEARN,
                "call_qwen_multimodal_json",
                return_value=(model_result, None),
            ):
                result = LEARN.learn_manual_edits(
                    before,
                    after,
                    report_path=root / "report.json",
                    glossary_path=glossary,
                )

            self.assertEqual(len(result["conflicts"]), 1)
            self.assertEqual(
                json.loads(glossary.read_text(encoding="utf-8"))[0]["correct"],
                "白链",
            )


if __name__ == "__main__":
    unittest.main()
