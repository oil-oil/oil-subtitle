import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))
import dashscope_client as CLIENT  # noqa: E402
import user_config as USER_CONFIG  # noqa: E402


class FakeDownload:
    def __init__(self, payload):
        self.payload = payload

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return False

    def read(self):
        return json.dumps(self.payload).encode("utf-8")


class DashScopeClientTests(unittest.TestCase):
    def test_qwen_text_uses_sdk_and_returns_usage(self):
        response = SimpleNamespace(
            status_code=200,
            output={
                "choices": [
                    {"message": {"content": "第一行\n第二行"}}
                ]
            },
            usage={"total_tokens": 20},
        )
        with patch.object(CLIENT, "_configure", return_value="secret"), patch.object(
            CLIENT.Generation, "call", return_value=response
        ) as call:
            text, usage = CLIENT.call_qwen_text(
                prompt="内容",
                system="规则",
                model="qwen-plus",
                timeout=30,
            )

        self.assertEqual(text, "第一行\n第二行")
        self.assertEqual(usage, {"total_tokens": 20})
        self.assertEqual(call.call_args.kwargs["api_key"], "secret")
        self.assertEqual(call.call_args.kwargs["timeout"], 30)

    def test_file_transcription_uploads_waits_and_downloads_json(self):
        payload = {
            "transcripts": [
                {"sentences": [{"text": "测试", "begin_time": 0, "end_time": 500}]}
            ]
        }
        task = SimpleNamespace(
            status_code=200,
            output={"task_id": "task-1"},
        )
        result = SimpleNamespace(
            status_code=200,
            output={
                "results": [
                    {
                        "subtask_status": "SUCCEEDED",
                        "transcription_url": "https://example.test/result.json",
                    }
                ]
            },
        )
        with tempfile.TemporaryDirectory() as tmp:
            audio = Path(tmp) / "audio.wav"
            audio.write_bytes(b"RIFFtest")
            with patch.object(CLIENT, "_configure", return_value="secret"), patch.object(
                CLIENT.OssUtils, "upload", return_value=("oss://audio.wav", {})
            ) as upload, patch.object(
                CLIENT.Transcription, "async_call", return_value=task
            ) as submit, patch.object(
                CLIENT.Transcription, "wait", return_value=result
            ) as wait, patch.object(
                CLIENT, "urlopen", return_value=FakeDownload(payload)
            ):
                actual = CLIENT.transcribe_audio_file(
                    audio,
                    language="zh",
                    vocabulary_id="vocab-1",
                )

        self.assertEqual(actual, payload)
        self.assertEqual(upload.call_args.kwargs["model"], "fun-asr")
        self.assertEqual(submit.call_args.kwargs["vocabulary_id"], "vocab-1")
        self.assertEqual(submit.call_args.kwargs["language_hints"], ["zh"])
        self.assertEqual(wait.call_args.kwargs["task"], "task-1")


class ApiKeyConfigTests(unittest.TestCase):
    def test_saved_key_uses_private_permissions_and_can_be_reloaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            target = Path(tmp) / "dashscope_api_key"
            with patch.dict(
                os.environ,
                {
                    "DASHSCOPE_API_KEY": "",
                    "OIL_SUBTITLE_API_KEY_FILE": str(target),
                },
                clear=False,
            ), patch.object(USER_CONFIG, "legacy_bailian_api_key", return_value=""):
                saved = USER_CONFIG.save_dashscope_api_key("secret-value")
                loaded = USER_CONFIG.load_dashscope_api_key()

            self.assertEqual(saved, target)
            self.assertEqual(loaded, "secret-value")
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_environment_key_has_highest_priority(self):
        with patch.dict(
            os.environ, {"DASHSCOPE_API_KEY": "environment-key"}, clear=False
        ):
            self.assertEqual(
                USER_CONFIG.load_dashscope_api_key(), "environment-key"
            )


if __name__ == "__main__":
    unittest.main()
