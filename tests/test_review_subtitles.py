import importlib.util
import json
import sys
import tempfile
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
    def test_focus_list_includes_versions_commands_and_ascii_terms(self):
        candidates = REVIEW.heuristic_review_candidates(
            [
                {"start": 1, "end": 2, "text": "Grok 4.6 今天发布"},
                {"start": 3, "end": 4, "text": "运行 --resume 打开 app.py"},
                {"start": 5, "end": 6, "text": "modelreport 和 sq 需要确认"},
            ]
        )

        originals = {item["original"] for item in candidates}
        self.assertIn("Grok 4.6", originals)
        self.assertIn("--resume", originals)
        self.assertIn("app.py", originals)
        self.assertIn("modelreport", originals)
        self.assertIn("sq", originals)
        self.assertTrue(
            all(item["status"] == "needs-agent-review" for item in candidates)
        )

    def test_prepare_review_never_changes_subtitle_text(self):
        payload = {
            "segments": [
                {"start": 0, "end": 1, "text": "Sol 不应自动改成 Sonnet"},
                {"start": 1, "end": 2, "text": "Codex 不应自动改成 Claude"},
                {"start": 2, "end": 3, "text": "COLLX 等待 Agent 判断"},
                {"start": 3, "end": 4, "text": "modelreport 不应被背景 pail-ui 覆盖"},
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            video = root / "demo.mp4"
            source = root / "transcript.json"
            output = root / "reviewed.json"
            report_path = root / "report.json"
            video.touch()
            source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

            report = REVIEW.prepare_agent_review(
                video=video,
                transcript=source,
                output=output,
                report_path=report_path,
                frames_dir=root / "frames",
            )

            reviewed = json.loads(output.read_text(encoding="utf-8"))
            self.assertEqual(
                [item["text"] for item in reviewed["segments"]],
                [item["text"] for item in payload["segments"]],
            )
            self.assertEqual(report["mode"], "agent-only")
            self.assertEqual(report["summary"]["automatic_text_changes"], 0)
            self.assertEqual(report["applied_or_verified"], [])

    def test_review_script_has_no_qwen_correction_dependency(self):
        source = (SCRIPT_DIR / "review_subtitles.py").read_text(encoding="utf-8")
        self.assertNotIn("call_qwen", source)
        self.assertNotIn("vision_suggested", source)


if __name__ == "__main__":
    unittest.main()
