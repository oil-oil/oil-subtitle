#!/usr/bin/env python3
"""Resolve oil-subtitle settings with backward-compatible legacy fallbacks."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


PREFERRED_CONFIG = Path.home() / ".config" / "oil-subtitle" / "config.json"
LEGACY_CONFIG = (
    Path.home() / ".config" / "screen-studio-editor" / "config.json"
)


def config_path() -> Path:
    explicit = (
        os.environ.get("OIL_SUBTITLE_CONFIG")
        or os.environ.get("SCREEN_STUDIO_EDITOR_CONFIG")
    )
    if explicit:
        return Path(explicit).expanduser()
    if PREFERRED_CONFIG.exists():
        return PREFERRED_CONFIG
    return LEGACY_CONFIG


def load_user_config() -> dict[str, Any]:
    path = config_path()
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Invalid oil-subtitle config: {path}: {exc}") from exc
    if not isinstance(payload, dict):
        raise RuntimeError(f"oil-subtitle config must be a JSON object: {path}")
    return payload


def env_value(name: str, legacy_name: str | None = None) -> str:
    value = os.environ.get(name)
    if value is None and legacy_name:
        value = os.environ.get(legacy_name)
    return str(value or "").strip()


def optional_user_path(
    config: dict[str, Any],
    key: str,
    env_name: str,
    legacy_env_name: str | None = None,
) -> Path | None:
    value = env_value(env_name, legacy_env_name) or str(config.get(key) or "").strip()
    return Path(value).expanduser() if value else None
