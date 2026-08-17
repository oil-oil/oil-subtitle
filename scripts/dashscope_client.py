#!/usr/bin/env python3
"""Shared DashScope SDK calls for text, vision, and file transcription."""

from __future__ import annotations

import json
import os
import re
from http import HTTPStatus
from pathlib import Path
from typing import Any
from urllib.request import urlopen

import dashscope
from dashscope import Generation, MultiModalConversation
from dashscope.audio.asr import Transcription
from dashscope.utils.oss_utils import OssUtils

from user_config import load_dashscope_api_key, load_user_config


def _configure() -> str:
    api_key = load_dashscope_api_key()
    config = load_user_config()
    base_url = (
        os.environ.get("OIL_SUBTITLE_DASHSCOPE_BASE_URL")
        or str(config.get("dashscope_base_http_api_url") or "").strip()
    )
    if base_url:
        dashscope.base_http_api_url = base_url.rstrip("/")
    dashscope.api_key = api_key
    return api_key


def _response_error(response: Any, action: str) -> RuntimeError:
    code = getattr(response, "code", None) or "unknown"
    message = getattr(response, "message", None) or "unknown error"
    request_id = getattr(response, "request_id", None)
    suffix = f" (request_id={request_id})" if request_id else ""
    return RuntimeError(f"DashScope {action} failed: {code}: {message}{suffix}")


def call_qwen_text(
    *,
    prompt: str,
    system: str,
    model: str = "qwen-plus",
    max_tokens: int = 8192,
    temperature: float = 0.1,
    timeout: int | None = None,
    response_format: dict[str, Any] | None = None,
) -> tuple[str, dict[str, Any] | None]:
    api_key = _configure()
    response = Generation.call(
        model=model,
        api_key=api_key,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": prompt},
        ],
        result_format="message",
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
        response_format=response_format,
    )
    if response.status_code != HTTPStatus.OK:
        raise _response_error(response, "text generation")
    choices = response.output.get("choices") or []
    if not choices:
        raise RuntimeError("DashScope text generation returned no choices")
    content = (choices[0].get("message") or {}).get("content")
    if not isinstance(content, str) or not content.strip():
        raise RuntimeError("DashScope text generation returned no text")
    usage = dict(response.usage) if getattr(response, "usage", None) else None
    return content.strip(), usage


def extract_json_from_text(text: str) -> dict[str, Any]:
    text = text.strip()
    fenced = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, flags=re.S)
    if fenced:
        text = fenced.group(1)
    start = text.find("{")
    if start >= 0:
        text = text[start:]
    value, _end = json.JSONDecoder().raw_decode(text)
    if not isinstance(value, dict):
        raise ValueError("Model response is not a JSON object")
    return value


def call_qwen_json(
    *,
    prompt: str,
    system: str,
    model: str = "qwen-plus",
    max_tokens: int = 2500,
    temperature: float = 0,
    timeout: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    retry_note = ""
    for attempt in range(2):
        text, usage = call_qwen_text(
            prompt=prompt + retry_note,
            system=system,
            model=model,
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            response_format={"type": "json_object"},
        )
        try:
            return extract_json_from_text(text), usage
        except (json.JSONDecodeError, ValueError):
            if attempt == 1:
                raise RuntimeError("DashScope text generation returned invalid JSON")
            retry_note = "\n上一次输出不是合法 JSON。这次只返回合法 JSON。"
    raise AssertionError("unreachable")


def _multimodal_text(response: Any) -> str:
    choices = response.output.get("choices") or []
    if not choices:
        raise RuntimeError("DashScope vision generation returned no choices")
    content = (choices[0].get("message") or {}).get("content")
    if isinstance(content, str) and content.strip():
        return content.strip()
    if isinstance(content, list):
        parts = [
            str(item.get("text") or "").strip()
            for item in content
            if isinstance(item, dict) and item.get("text")
        ]
        if parts:
            return "\n".join(parts)
    raise RuntimeError("DashScope vision generation returned no text")


def call_qwen_multimodal_json(
    *,
    prompt: str,
    images: list[Path] | None = None,
    model: str = "qwen3.7-flash",
    max_tokens: int = 1800,
    temperature: float = 0,
    timeout: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Ask a DashScope multimodal model to return structured JSON."""
    api_key = _configure()
    content = [
        {"image": Path(path).resolve().as_uri()} for path in (images or [])
    ]
    retry_note = ""
    last_usage: dict[str, Any] | None = None
    for attempt in range(2):
        response = MultiModalConversation.call(
            model=model,
            api_key=api_key,
            messages=[
                {
                    "role": "user",
                    "content": content
                    + [{"text": prompt + retry_note}],
                }
            ],
            result_format="message",
            max_tokens=max_tokens,
            temperature=temperature,
            timeout=timeout,
            enable_thinking=False,
            response_format={"type": "json_object"},
        )
        if response.status_code != HTTPStatus.OK:
            raise _response_error(response, "vision generation")
        text = _multimodal_text(response)
        last_usage = dict(response.usage) if getattr(response, "usage", None) else None
        try:
            return extract_json_from_text(text), last_usage
        except (json.JSONDecodeError, ValueError):
            if attempt == 1:
                raise RuntimeError("DashScope vision generation returned invalid JSON")
            retry_note = "\n上一次输出不是合法 JSON。这次只返回合法 JSON。"
    raise AssertionError("unreachable")


def call_qwen_vision_json(
    *,
    prompt: str,
    images: list[Path],
    model: str = "qwen3.7-flash",
    max_tokens: int = 1800,
    temperature: float = 0,
    timeout: int | None = None,
) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Ask a DashScope vision model to inspect local images and return JSON."""
    if not images:
        raise ValueError("At least one image is required")
    return call_qwen_multimodal_json(
        prompt=prompt,
        images=images,
        model=model,
        max_tokens=max_tokens,
        temperature=temperature,
        timeout=timeout,
    )


def transcribe_audio_file(
    audio_path: Path,
    *,
    model: str = "fun-asr",
    language: str | None = "zh",
    vocabulary_id: str | None = None,
    wait_timeout: int = 7200,
) -> dict[str, Any]:
    """Upload a local audio file, run asynchronous Fun-ASR, and fetch JSON."""
    api_key = _configure()
    audio_path = Path(audio_path).resolve()
    if not audio_path.is_file():
        raise FileNotFoundError(f"Audio file not found: {audio_path}")

    oss_url, _certificate = OssUtils.upload(
        model=model,
        file_path=str(audio_path),
        api_key=api_key,
    )
    parameters: dict[str, Any] = {
        "headers": {"X-DashScope-OssResourceResolve": "enable"},
        "channel_id": [0],
    }
    if language:
        parameters["language_hints"] = [language]
    if vocabulary_id:
        parameters["vocabulary_id"] = vocabulary_id

    task = Transcription.async_call(
        model=model,
        file_urls=[oss_url],
        api_key=api_key,
        **parameters,
    )
    if task.status_code != HTTPStatus.OK or not task.output.get("task_id"):
        raise _response_error(task, "transcription submission")

    result = Transcription.wait(
        task=task.output["task_id"],
        api_key=api_key,
        wait_timeout=wait_timeout,
    )
    if result.status_code != HTTPStatus.OK:
        raise _response_error(result, "transcription")
    items = result.output.get("results") or []
    if not items:
        raise RuntimeError("DashScope transcription returned no result")
    item = items[0]
    if item.get("subtask_status") != "SUCCEEDED":
        detail = item.get("message") or item.get("code") or item
        raise RuntimeError(f"DashScope transcription subtask failed: {detail}")
    transcription_url = item.get("transcription_url")
    if not transcription_url:
        raise RuntimeError("DashScope transcription returned no result URL")
    with urlopen(transcription_url, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    if not isinstance(payload, dict):
        raise RuntimeError("DashScope transcription result is not a JSON object")
    return payload
