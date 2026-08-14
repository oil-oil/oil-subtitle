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


class SubtitleModeTests(unittest.TestCase):
    def test_chinese_mode_prepares_text_without_translation_or_api_key(self):
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
                {
                    "SCREEN_STUDIO_EDITOR_CONFIG": str(root / "missing.json"),
                    "SCREEN_STUDIO_EDITOR_SUBTITLE_MODE": "",
                    "ZENMUX_API_KEY": "",
                },
                clear=False,
            ), patch.object(sys, "argv", argv):
                PREPARE.main()

            payload = json.loads(output.read_text(encoding="utf-8"))
            chapter_payload = json.loads(chapters.read_text(encoding="utf-8"))

        self.assertEqual(payload["subtitle_mode"], "zh")
        self.assertEqual(payload["segments"][0]["text"], "你好世界")
        self.assertNotIn("en", payload["segments"][0])
        self.assertFalse(chapter_payload["enabled"])
        self.assertEqual(chapter_payload["min_progress_duration"], 180.0)

    def test_missing_config_defaults_to_chinese(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            with patch.dict(
                os.environ,
                {"SCREEN_STUDIO_EDITOR_CONFIG": str(missing)},
                clear=False,
            ):
                os.environ.pop("SCREEN_STUDIO_EDITOR_SUBTITLE_MODE", None)
                self.assertEqual(PREPARE.resolve_subtitle_mode("auto"), "zh")

    def test_config_can_enable_bilingual_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(
                json.dumps({"subtitles": {"mode": "bilingual"}}),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"SCREEN_STUDIO_EDITOR_CONFIG": str(config)},
                clear=False,
            ):
                os.environ.pop("SCREEN_STUDIO_EDITOR_SUBTITLE_MODE", None)
                self.assertEqual(PREPARE.resolve_subtitle_mode("auto"), "bilingual")

    def test_explicit_mode_overrides_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(
                json.dumps({"subtitles": {"mode": "zh"}}),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"SCREEN_STUDIO_EDITOR_CONFIG": str(config)},
                clear=False,
            ):
                self.assertEqual(
                    PREPARE.resolve_subtitle_mode("bilingual"), "bilingual"
                )

    def test_progress_threshold_defaults_to_three_minutes(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "missing.json"
            with patch.dict(
                os.environ,
                {"SCREEN_STUDIO_EDITOR_CONFIG": str(missing)},
                clear=False,
            ):
                os.environ.pop("SCREEN_STUDIO_EDITOR_PROGRESS_MIN_DURATION", None)
                self.assertEqual(PREPARE.resolve_progress_min_duration(None), 180.0)

    def test_progress_threshold_can_be_configured_and_explicitly_overridden(self):
        with tempfile.TemporaryDirectory() as tmp:
            config = Path(tmp) / "config.json"
            config.write_text(
                json.dumps(
                    {"subtitles": {"progress_min_duration_seconds": 240}}
                ),
                encoding="utf-8",
            )
            with patch.dict(
                os.environ,
                {"SCREEN_STUDIO_EDITOR_CONFIG": str(config)},
                clear=False,
            ):
                os.environ.pop("SCREEN_STUDIO_EDITOR_PROGRESS_MIN_DURATION", None)
                self.assertEqual(PREPARE.resolve_progress_min_duration(None), 240.0)
                self.assertEqual(PREPARE.resolve_progress_min_duration(180), 180.0)

    def test_shorter_video_chapter_prompt_uses_fewer_broad_sections(self):
        segments = [{"start": 0.0, "text": "测试"}]
        self.assertIn(
            "Use 2 to 4 chapters total",
            PREPARE.chapter_prompt(segments, 181.0, 6),
        )
        self.assertIn(
            "Use 4 to 6 chapters total",
            PREPARE.chapter_prompt(segments, 301.0, 6),
        )


if __name__ == "__main__":
    unittest.main()
