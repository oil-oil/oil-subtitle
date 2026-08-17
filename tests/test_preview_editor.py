import importlib.util
import re
import sys
import unittest
from pathlib import Path


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


if __name__ == "__main__":
    unittest.main()
