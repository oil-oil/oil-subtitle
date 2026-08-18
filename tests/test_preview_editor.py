import importlib.util
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "preview_editor.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("preview_editor", SCRIPT_PATH)
PREVIEW_EDITOR = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PREVIEW_EDITOR)


class ProgressLayoutTests(unittest.TestCase):
    def test_progress_is_inside_the_video_overlay(self):
        self.assertIn(
            '<div class="current-subtitle" id="curSub"></div>\n'
            '      <div class="content-progress" id="contentProgress">',
            PREVIEW_EDITOR.HTML_TEMPLATE,
        )

    def test_progress_uses_a_transparent_bottom_gradient(self):
        match = re.search(
            r"\.content-progress\s*\{(?P<rules>.*?)\}",
            PREVIEW_EDITOR.HTML_TEMPLATE,
            re.DOTALL,
        )
        self.assertIsNotNone(match)
        rules = match.group("rules")
        self.assertIn("position: absolute", rules)
        self.assertIn("bottom: 0", rules)
        self.assertIn("linear-gradient", rules)
        self.assertIn("rgba(47,47,49,0)", rules)


class ManualGlossaryHookTests(unittest.TestCase):
    def test_saving_source_subtitles_runs_glossary_learning(self):
        with tempfile.TemporaryDirectory() as tmp:
            transcript = Path(tmp) / "subtitle-transcript.json"
            transcript.write_text(
                json.dumps({"segments": [{"start": 0, "end": 1, "text": "白练"}]}),
                encoding="utf-8",
            )
            old_transcript = PREVIEW_EDITOR.TRANSCRIPT_PATH
            old_manifest = PREVIEW_EDITOR.MANIFEST
            PREVIEW_EDITOR.TRANSCRIPT_PATH = str(transcript)
            PREVIEW_EDITOR.MANIFEST = None
            try:
                with patch.object(
                    PREVIEW_EDITOR,
                    "learn_manual_edits",
                    return_value={
                        "status": "ok",
                        "learned": [{"wrong": "白练", "correct": "百炼"}],
                        "ignored": [],
                        "conflicts": [],
                    },
                ) as learn:
                    response = PREVIEW_EDITOR.app.test_client().post(
                        "/api/transcript",
                        json={
                            "lang": "src",
                            "segments": [{"start": 0, "end": 1, "text": "百炼"}],
                        },
                    )
            finally:
                PREVIEW_EDITOR.TRANSCRIPT_PATH = old_transcript
                PREVIEW_EDITOR.MANIFEST = old_manifest

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.get_json()["glossary_learning"]["learned_count"], 1)
        learn.assert_called_once()


if __name__ == "__main__":
    unittest.main()
