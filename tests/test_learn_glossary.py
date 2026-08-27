import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import learn_glossary as LEARN  # noqa: E402


def segment(start, text):
    return {"start": start, "end": start + 1, "text": text}


class ManualGlossaryReviewTests(unittest.TestCase):
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
        self.assertFalse(edits[2]["eligible"])

    def test_eligible_edit_is_pending_without_writing_glossary(self):
        before = {"segments": [segment(0, "我们来测试 Claude Core")]}
        after = {"segments": [segment(0, "我们来测试 Claude Code")]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            glossary = root / "glossary.json"
            report_path = root / "manual-edit-review.json"

            result = LEARN.learn_manual_edits(
                before,
                after,
                report_path=report_path,
                glossary_path=glossary,
            )

            self.assertEqual(result["mode"], "agent-review")
            self.assertEqual(len(result["pending"]), 1)
            self.assertEqual(result["learned"], [])
            self.assertFalse(glossary.exists())
            self.assertEqual(
                json.loads(report_path.read_text(encoding="utf-8"))["status"],
                "ok",
            )

    def test_existing_glossary_is_never_modified(self):
        before = {"segments": [segment(0, "白练平台")]}
        after = {"segments": [segment(0, "百炼平台")]}
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            glossary = root / "glossary.json"
            original = [{"wrong": "白练", "correct": "白链"}]
            glossary.write_text(
                json.dumps(original, ensure_ascii=False),
                encoding="utf-8",
            )

            result = LEARN.learn_manual_edits(
                before,
                after,
                report_path=root / "report.json",
                glossary_path=glossary,
            )

            self.assertEqual(len(result["pending"]), 1)
            self.assertEqual(
                json.loads(glossary.read_text(encoding="utf-8")), original
            )

    def test_review_script_has_no_model_dependency(self):
        source = (SCRIPT_DIR / "learn_glossary.py").read_text(encoding="utf-8")
        self.assertNotIn("call_qwen", source)
        self.assertNotIn("confidence_threshold", source)


if __name__ == "__main__":
    unittest.main()
