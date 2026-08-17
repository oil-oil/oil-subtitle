import importlib.util
import sys
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
SPEC = importlib.util.spec_from_file_location(
    "review_subtitles", SCRIPT_DIR / "review_subtitles.py"
)
REVIEW = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(REVIEW)


class ReviewSubtitleTests(unittest.TestCase):
    def test_versions_are_forced_to_visual_review(self):
        candidates = REVIEW.heuristic_visual_candidates(
            [
                {"start": 1, "end": 2, "text": "Grok 4.6 今天发布"},
                {"start": 3, "end": 4, "text": "像 GPT-5.4 一样"},
                {"start": 5, "end": 6, "text": "字幕错成 Grok 46"},
                {"start": 7, "end": 8, "text": "字幕错成 GPT-54"},
            ]
        )
        self.assertEqual(
            [item["original"] for item in candidates],
            ["Grok 4.6", "GPT-5.4", "Grok 46", "GPT-54"],
        )
        self.assertTrue(all(item["decision"] == "visual" for item in candidates))

    def test_commands_and_filenames_are_forced_to_visual_review(self):
        candidates = REVIEW.heuristic_visual_candidates(
            [{"start": 1, "end": 2, "text": "运行 --resume 打开 app.py"}]
        )
        self.assertEqual(
            {item["original"] for item in candidates}, {"--resume", "app.py"}
        )

    def test_visual_candidate_overrides_text_only_replacement(self):
        merged = REVIEW.merge_candidates(
            [
                {
                    "segment_id": 2,
                    "original": "Grok 4.6",
                    "suggested": "Grok 4.5",
                    "decision": "replace",
                    "confidence": 0.99,
                    "reason": "模型猜测",
                    "source": "text-model",
                },
                {
                    "segment_id": 2,
                    "original": "Grok 4.6",
                    "suggested": "",
                    "decision": "visual",
                    "confidence": 0,
                    "reason": "版本号必须看画面",
                    "source": "rule",
                },
            ]
        )
        self.assertEqual(merged[0]["decision"], "visual")

    def test_safe_replace_requires_one_exact_occurrence(self):
        self.assertEqual(
            REVIEW.safe_replace("使用 cloud call", "cloud call", "Claude Code"),
            ("使用 Claude Code", True),
        )
        self.assertEqual(
            REVIEW.safe_replace("Grok Grok", "Grok", "Grok 4.6"),
            ("Grok Grok", False),
        )


if __name__ == "__main__":
    unittest.main()
