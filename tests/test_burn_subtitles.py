import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "burn_subtitles.py"
sys.path.insert(0, str(SCRIPT_PATH.parent))
SPEC = importlib.util.spec_from_file_location("burn_subtitles", SCRIPT_PATH)
BURN_SUBTITLES = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(BURN_SUBTITLES)


class ReviewedSrtTests(unittest.TestCase):
    def test_reviewed_srt_text_is_immutable(self):
        source = """1
00:00:00,000 --> 00:00:01,000
Superpowers gstack grill-me
3:4 和 4:3

2
00:00:01,100 --> 00:00:02,000
oil-html Vibe Coding，保留标点
"""
        BURN_SUBTITLES.set_display_replacements([
            {"wrong": "grill-me", "correct": "grillme"},
            {"wrong": "oil-html", "correct": "oil-HTML"},
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reviewed.srt"
            path.write_text(source, encoding="utf-8")
            lines = BURN_SUBTITLES.read_srt_lines(path)

        self.assertEqual(
            lines[0]["text"],
            "Superpowers gstack grill-me\n3:4 和 4:3",
        )
        self.assertEqual(lines[1]["text"], "oil-html Vibe Coding，保留标点")
        self.assertEqual(lines[0]["start"], 0.0)
        self.assertEqual(lines[0]["end"], 1.0)

    def test_reviewed_srt_rejects_overlap(self):
        source = """1
00:00:00,000 --> 00:00:02,000
第一条

2
00:00:01,900 --> 00:00:03,000
第二条
"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "overlap.srt"
            path.write_text(source, encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "Overlapping"):
                BURN_SUBTITLES.read_srt_lines(path)

    def test_ass_generation_preserves_reviewed_text(self):
        lines = [{
            "start": 0.0,
            "end": 1.0,
            "text": "grill-me\n3:4 和 4:3 oil-html Vibe Coding",
        }]
        BURN_SUBTITLES.set_display_replacements([
            {"wrong": "grill-me", "correct": "grillme"},
        ])
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "reviewed.ass"
            BURN_SUBTITLES.generate_ass(
                lines,
                path,
                video_width=1920,
                video_height=1080,
                max_chars=25,
                preserve_text=True,
            )
            content = path.read_text(encoding="utf-8")

        self.assertIn("grill-me\\N3:4 和 4:3 oil-html Vibe Coding", content)
        self.assertNotIn("grillme\\N", content)


class ProgressBarTests(unittest.TestCase):
    def test_chapter_titles_stay_fixed_in_their_own_intervals(self):
        payload = {
            "enabled": True,
            "min_progress_duration": 180.0,
            "chapters": [
                {"title": "开场", "start": 0.0, "end": 90.0},
                {"title": "正文", "start": 90.0, "end": 181.0},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            chapter_path = root / "chapters.json"
            ass_path = root / "subtitles.ass"
            chapter_path.write_text(json.dumps(payload), encoding="utf-8")
            chapters = BURN_SUBTITLES.load_progress_chapters(chapter_path, 181.0)
            BURN_SUBTITLES.generate_ass(
                [{"start": 0.0, "end": 1.0, "text": "测试字幕"}],
                ass_path,
                video_width=1920,
                video_height=1080,
                chapters=chapters,
                duration=181.0,
            )
            content = ass_path.read_text(encoding="utf-8")

        labels = [line for line in content.splitlines() if "ProgressLabel" in line and line.startswith("Dialogue")]
        self.assertEqual(len(labels), 2)
        self.assertTrue(all("0:00:00.00,0:03:01.00" in line for line in labels))
        self.assertIn("开场", labels[0])
        self.assertIn("正文", labels[1])

    def test_progress_bar_is_a_transparent_overlay_inside_the_video(self):
        chapters = [
            {"title": "开场", "start": 0.0, "end": 90.0},
            {"title": "正文", "start": 90.0, "end": 181.0},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            ass_path = Path(tmp) / "subtitles.ass"
            BURN_SUBTITLES.generate_ass(
                [{"start": 0.0, "end": 1.0, "text": "测试字幕"}],
                ass_path,
                video_width=1920,
                video_height=1080,
                chapters=chapters,
                duration=181.0,
            )
            content = ass_path.read_text(encoding="utf-8")

        self.assertIn("PlayResY: 1080", content)
        self.assertNotIn("ProgressBand", content)
        self.assertNotIn("ProgressShadow", content)
        self.assertIn("Style: ProgressLabel,PingFang SC,22", content)
        self.assertIn(r"\1c&HFFFFFF&", content)

    def test_three_minute_video_does_not_show_progress(self):
        payload = {
            "enabled": True,
            "min_progress_duration": 180.0,
            "chapters": [
                {"title": "上半段", "start": 0.0, "end": 90.0},
                {"title": "下半段", "start": 90.0, "end": 180.0},
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "chapters.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            chapters = BURN_SUBTITLES.load_progress_chapters(path, 180.0)

        self.assertEqual(chapters, [])

    def test_progress_can_be_disabled_even_when_chapters_exist(self):
        chapters = BURN_SUBTITLES.load_progress_chapters(
            Path("/does/not/need/to/exist.json"),
            360.0,
            enabled=False,
        )

        self.assertEqual(chapters, [])


class BeautyFilterTests(unittest.TestCase):
    def test_persistent_face_can_be_detected_away_from_top_right(self):
        detections = [
            {
                "sample": sample,
                "x": 0.10 + (sample % 3 - 1) * 0.003,
                "y": 0.70,
                "width": 0.10,
                "height": 0.15,
                "confidence": 0.99,
            }
            for sample in range(18)
        ]
        detections.extend([
            {
                "sample": sample,
                "x": 0.40 + sample * 0.02,
                "y": 0.20,
                "width": 0.08,
                "height": 0.12,
                "confidence": 0.95,
            }
            for sample in range(5)
        ])

        region = BURN_SUBTITLES.derive_camera_region(
            detections, 1920, 1080, sample_count=18
        )

        self.assertIsNotNone(region)
        x, y, width, height = region
        self.assertLess(x, 400)
        self.assertGreater(y, 500)
        self.assertEqual(width, height)
        self.assertEqual((x % 2, y % 2, width % 2), (0, 0, 0))

    def test_region_is_not_guessed_when_face_is_not_persistent(self):
        detections = [{
            "sample": 0,
            "x": 0.7,
            "y": 0.1,
            "width": 0.1,
            "height": 0.1,
            "confidence": 0.99,
        }]
        self.assertIsNone(
            BURN_SUBTITLES.derive_camera_region(
                detections, 1920, 1080, sample_count=18
            )
        )

    def test_default_graph_blends_ten_percent_smoothing_and_brightening(self):
        graph, output = BURN_SUBTITLES.build_beauty_filter_graph(
            "ass='/tmp/subtitles.ass'",
            2880,
            2160,
            (2104, 42, 734, 734),
            0.10,
            0.10,
        )

        self.assertEqual(output, "[video_out]")
        self.assertIn("crop=734:734:2104:42", graph)
        self.assertIn("bilateral=sigmaS=2.5:sigmaR=0.04:planes=1", graph)
        self.assertIn(
            "[beauty_smooth][beauty_original]"
            "blend=all_mode=normal:all_opacity=0.1",
            graph,
        )
        self.assertIn("eq=brightness=0.08:gamma=1.04", graph)
        self.assertIn(
            "[brightness_lifted][brightness_base]"
            "blend=all_mode=normal:all_opacity=0.1",
            graph,
        )
        self.assertTrue(graph.endswith("ass='/tmp/subtitles.ass'[video_out]"))

    def test_graph_keeps_crop_and_scale_after_beauty(self):
        graph, _ = BURN_SUBTITLES.build_beauty_filter_graph(
            "ass='/tmp/subtitles.ass'",
            1920,
            1080,
            (1300, 20, 500, 500),
            0.10,
            0.10,
            filter_prefix=["crop=1080:1080:420:0"],
            scale_to=(720, 720),
        )

        self.assertIn(
            "[beautified]crop=1080:1080:420:0,scale=720:720,"
            "ass='/tmp/subtitles.ass'[video_out]",
            graph,
        )

    def test_progress_graph_uses_one_continuous_rgba_gradient(self):
        graph, output = BURN_SUBTITLES.build_progress_filter_graph(
            "ass='/tmp/subtitles.ass'",
            82,
        )

        self.assertEqual(output, "[video_out]")
        self.assertIn("crop=iw:82:0:ih-82,format=rgba", graph)
        self.assertIn(
            "geq=r='47':g='47':b='49':a='255*0.72*Y/(H-1)'",
            graph,
        )
        self.assertIn(
            "overlay=0:main_h-overlay_h:format=auto[with_progress]",
            graph,
        )
        self.assertTrue(graph.endswith("ass='/tmp/subtitles.ass'[video_out]"))

    def test_beauty_graph_adds_gradient_after_crop_and_scale(self):
        graph, _ = BURN_SUBTITLES.build_beauty_filter_graph(
            "ass='/tmp/subtitles.ass'",
            1920,
            1080,
            (1300, 20, 500, 500),
            filter_prefix=["crop=1080:1080:420:0"],
            scale_to=(720, 720),
            progress_overlay_height=54,
        )

        self.assertIn(
            "[beautified]crop=1080:1080:420:0,scale=720:720[prepared]",
            graph,
        )
        self.assertIn("crop=iw:54:0:ih-54,format=rgba", graph)
        self.assertTrue(graph.endswith("ass='/tmp/subtitles.ass'[video_out]"))

    def test_graph_rejects_invalid_strength(self):
        with self.assertRaisesRegex(ValueError, "At least one"):
            BURN_SUBTITLES.build_beauty_filter_graph(
                "ass='/tmp/subtitles.ass'",
                1920,
                1080,
                (1300, 20, 500, 500),
                0,
                0,
            )


if __name__ == "__main__":
    unittest.main()
