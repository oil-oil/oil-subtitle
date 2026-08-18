import argparse
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import prepare_subtitles as PREPARE  # noqa: E402


class SubtitlePreparationTests(unittest.TestCase):
    def test_preview_text_adds_cjk_latin_spacing_by_default(self):
        self.assertEqual(
            PREPARE.display_text("这是Claude Code和GPT5实战"),
            "这是 Claude Code 和 GPT5 实战",
        )
        self.assertEqual(PREPARE.display_text("一些 AI 实战"), "一些 AI 实战")

    def test_short_chinese_video_needs_no_chapter_model_call(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            transcript = root / "transcript.json"
            output = root / "subtitle-transcript.json"
            chapters = root / "subtitle-chapters.json"
            transcript.write_text(
                json.dumps(
                    [{"start": 0.0, "end": 2.0, "text": "你好，世界！"}],
                    ensure_ascii=False,
                ),
                encoding="utf-8",
            )
            argv = [
                "prepare_subtitles.py",
                "--transcript",
                str(transcript),
                "--output",
                str(output),
                "--chapters-output",
                str(chapters),
                "--work-dir",
                str(root / "cache"),
            ]
            with patch.dict(
                os.environ,
                {"OIL_SUBTITLE_CONFIG": str(root / "missing.json")},
                clear=False,
            ), patch.object(sys, "argv", argv), patch.object(
                PREPARE, "model_json", side_effect=AssertionError("unexpected call")
            ):
                PREPARE.main()

            payload = json.loads(output.read_text(encoding="utf-8"))
            chapter_payload = json.loads(chapters.read_text(encoding="utf-8"))

        self.assertEqual(payload["subtitle_mode"], "zh")
        self.assertEqual(payload["segments"][0]["text"], "你好世界")
        self.assertFalse(chapter_payload["enabled"])

    def test_progress_threshold_defaults_to_three_minutes(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            with patch.dict(
                os.environ, {"OIL_SUBTITLE_CONFIG": str(missing)}, clear=False
            ):
                self.assertEqual(PREPARE.resolve_progress_min_duration(None), 180.0)

    def test_progress_is_enabled_by_default_and_can_be_disabled(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            with patch.dict(
                os.environ, {"OIL_SUBTITLE_CONFIG": str(missing)}, clear=False
            ):
                self.assertTrue(PREPARE.resolve_progress_enabled(None))
                self.assertFalse(PREPARE.resolve_progress_enabled(False))

    def test_progress_can_be_persistently_disabled_in_user_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(
                json.dumps({"subtitles": {"progress_enabled": False}}),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {
                    "OIL_SUBTITLE_CONFIG": str(config),
                    "OIL_SUBTITLE_PROGRESS_ENABLED": "",
                },
                clear=False,
            ):
                self.assertFalse(PREPARE.resolve_progress_enabled(None))
                self.assertTrue(PREPARE.resolve_progress_enabled(True))

    def test_progress_threshold_can_be_configured_and_overridden(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(
                json.dumps(
                    {"subtitles": {"progress_min_duration_seconds": 240}}
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ, {"OIL_SUBTITLE_CONFIG": str(config)}, clear=False
            ):
                self.assertEqual(PREPARE.resolve_progress_min_duration(None), 240.0)
                self.assertEqual(PREPARE.resolve_progress_min_duration(180), 180.0)

    def test_chapter_prompt_uses_fewer_sections_for_shorter_video(self):
        segments = [{"start": 0.0, "text": "测试"}]
        self.assertIn(
            "Use 2 to 4 chapters total",
            PREPARE.chapter_prompt(segments, 181.0, 6),
        )
        self.assertIn(
            "Use 4 to 6 chapters total",
            PREPARE.chapter_prompt(segments, 301.0, 6),
        )

    def test_model_json_calls_dashscope_sdk_wrapper(self):
        response = {"chapters": [{"start_id": 0, "title": "内容开场"}]}
        with patch.object(
            PREPARE, "call_qwen_json", return_value=(response, None)
        ) as call:
            payload, usage = PREPARE.model_json(
                prompt="字幕",
                model="qwen-plus",
                timeout=30,
                max_tokens=500,
            )

        kwargs = call.call_args.kwargs
        self.assertEqual(kwargs["model"], "qwen-plus")
        self.assertEqual(kwargs["timeout"], 30)
        self.assertEqual(payload["chapters"][0]["start_id"], 0)
        self.assertIsNone(usage)

    def test_plan_chapters_uses_subtitle_segment_ids(self):
        segments = [
            {"start": 0.0, "end": 89.0, "text": "开场"},
            {"start": 90.0, "end": 179.0, "text": "第一部分"},
            {"start": 180.0, "end": 269.0, "text": "第二部分"},
            {"start": 270.0, "end": 360.0, "text": "结尾"},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            args = argparse.Namespace(
                progress_enabled=True,
                min_progress_duration=180.0,
                model="qwen-plus",
                max_chapters=6,
                min_chapter_duration=75.0,
                work_dir=Path(tmp),
                resume=False,
                timeout=30,
            )
            response = {
                "chapters": [
                    {"start_id": 0, "title": "内容开场"},
                    {"start_id": 2, "title": "核心方法"},
                ]
            }
            with patch.object(
                PREPARE, "model_json", return_value=(response, None)
            ):
                chapters, usage = PREPARE.plan_chapters(
                    segments, 360.0, args=args
                )

        self.assertIsNone(usage)
        self.assertEqual([item["start"] for item in chapters], [0.0, 180.0])
        self.assertEqual([item["title"] for item in chapters], ["内容开场", "核心方法"])


if __name__ == "__main__":
    unittest.main()
