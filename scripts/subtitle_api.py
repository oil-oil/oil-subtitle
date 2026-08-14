#!/usr/bin/env python3
"""Minimal OpenAI-compatible JSON client used by subtitle preparation."""

from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_API_BASE = "https://zenmux.ai/api/v1"
DEFAULT_API_KEY_FILE = Path.home() / ".zenmux_api_key"
RETRYABLE_HTTP_CODES = {408, 429, 500, 502, 503, 504}


def _retry_delay(attempt: int) -> float:
    return min(2 ** attempt, 8) + attempt * 0.25


def _read_json(
    request: urllib.request.Request,
    timeout: int,
    *,
    attempts: int = 3,
) -> dict[str, Any]:
    last_error: BaseException | None = None
    for attempt in range(attempts):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            quota_exhausted = exc.code == 429 and any(
                marker in body for marker in ("insufficient_quota", "token-limit")
            )
            if (
                quota_exhausted
                or exc.code not in RETRYABLE_HTTP_CODES
                or attempt == attempts - 1
            ):
                raise RuntimeError(
                    f"HTTP {exc.code} from {request.full_url}: {body}"
                ) from exc
            last_error = exc
        except (urllib.error.URLError, TimeoutError, ConnectionError) as exc:
            if attempt == attempts - 1:
                raise RuntimeError(
                    f"Network error from {request.full_url}: {exc}"
                ) from exc
            last_error = exc
        time.sleep(_retry_delay(attempt))
    raise RuntimeError(f"Request failed after retries: {last_error}")


def post_json(
    url: str,
    payload: dict[str, Any],
    api_key: str,
    timeout: int,
) -> dict[str, Any]:
    request = urllib.request.Request(
        url,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        },
        method="POST",
    )
    return _read_json(request, timeout)


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
        raise ValueError("Structured model response is not a JSON object.")
    return value
