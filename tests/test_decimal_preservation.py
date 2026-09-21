"""字幕清理标点时必须保留评测分数与模型版本号。"""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bailian_transcribe import _strip_display_punctuation as asr_clean
from prepare_subtitles import display_text
from burn_subtitles import _strip_display_punctuation as burn_clean


class DecimalPreservationTests(unittest.TestCase):
    def test_numeric_content_survives_all_stages(self):
        text = "Qwen 3.5 模型，58.94 分提高到 64.87 分。9B 为 69.04."
        result = burn_clean(display_text(asr_clean(text)))
        self.assertEqual(result, "Qwen 3.5 模型 58.94 分提高到 64.87 分 9B 为 69.04")

    def test_sentence_period_is_removed(self):
        for clean in (asr_clean, display_text, burn_clean):
            self.assertEqual(clean("结束.谢谢。"), "结束谢谢")
