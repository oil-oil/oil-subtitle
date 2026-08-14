import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import burn_bilingual_subtitles as BURN_BILINGUAL  # noqa: E402


class BilingualProgressBarTests(unittest.TestCase):
    def test_progress_requires_more_than_three_minutes(self):
        payload = {
            "enabled": True,
            "min_progress_duration": 180.0,
            "chapters": [
                {"title": "开场", "start": 0.0, "end": 90.0},
                {"title": "正文", "start": 90.0, "end": 181.0},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chapters.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            self.assertEqual(BURN_BILINGUAL.load_chapters(path, 180.0), [])
            chapters = BURN_BILINGUAL.load_chapters(path, 181.0)

        self.assertEqual([item["title"] for item in chapters], ["开场", "正文"])
        self.assertEqual(chapters[-1]["end"], 181.0)


if __name__ == "__main__":
    unittest.main()
