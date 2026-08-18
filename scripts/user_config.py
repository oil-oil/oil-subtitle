#!/usr/bin/env python3
"""Resolve oil-subtitle settings with backward-compatible legacy fallbacks."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any


PREFERRED_CONFIG = Path.home() / ".config" / "oil-subtitle" / "config.json"
PREFERRED_API_KEY_FILE = (
    Path.home() / ".config" / "oil-subtitle" / "dashscope_api_key"
)
PREFERRED_GLOSSARY = Path.home() / ".config" / "oil-subtitle" / "glossary.json"
LEGACY_BAILIAN_CONFIG = Path.home() / ".bailian" / "config.json"
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


def resolve_progress_enabled(override: bool | None = None) -> bool:
    """Resolve the chapter progress switch; enabled is the safe default."""
    if override is not None:
        return bool(override)

    configured_env = env_value(
        "OIL_SUBTITLE_PROGRESS_ENABLED",
        "SCREEN_STUDIO_EDITOR_PROGRESS_ENABLED",
    )
    config = load_user_config()
    subtitle_config = config.get("subtitles") or {}
    if not isinstance(subtitle_config, dict):
        raise RuntimeError("subtitles must be a JSON object in the oil-subtitle config")
    value = (
        configured_env
        if configured_env
        else subtitle_config.get("progress_enabled", True)
    )
    if isinstance(value, bool):
        return value
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError("subtitles.progress_enabled must be true or false")


def optional_user_path(
    config: dict[str, Any],
    key: str,
    env_name: str,
    legacy_env_name: str | None = None,
) -> Path | None:
    value = env_value(env_name, legacy_env_name) or str(config.get(key) or "").strip()
    return Path(value).expanduser() if value else None


def resolve_glossary_path(override: str | Path | None = None) -> Path:
    """Resolve the shared personal glossary, with a usable default path."""
    if override:
        return Path(override).expanduser()
    config = load_user_config()
    configured = env_value(
        "OIL_SUBTITLE_GLOSSARY", "SCREEN_STUDIO_EDITOR_GLOSSARY"
    ) or str(config.get("glossary") or "").strip()
    return Path(configured).expanduser() if configured else PREFERRED_GLOSSARY


def dashscope_api_key_file() -> Path:
    configured = env_value("OIL_SUBTITLE_API_KEY_FILE")
    return Path(configured).expanduser() if configured else PREFERRED_API_KEY_FILE


def _read_secret(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def legacy_bailian_api_key() -> str:
    try:
        payload = json.loads(LEGACY_BAILIAN_CONFIG.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return ""
    return str(payload.get("api_key") or "").strip() if isinstance(payload, dict) else ""


def load_dashscope_api_key(*, required: bool = True) -> str:
    key = env_value("DASHSCOPE_API_KEY")
    if not key:
        key = _read_secret(dashscope_api_key_file())
    if not key:
        key = legacy_bailian_api_key()
    if not key and required:
        raise RuntimeError(
            "DashScope API key is not configured. Run "
            "`.venv/bin/python3 scripts/configure_api_key.py`."
        )
    return key


def save_dashscope_api_key(key: str, path: Path | None = None) -> Path:
    key = str(key or "").strip()
    if not key:
        raise ValueError("DashScope API key must not be empty")
    target = (path or dashscope_api_key_file()).expanduser()
    target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    target.parent.chmod(0o700)
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(key + "\n", encoding="utf-8")
    temporary.chmod(0o600)
    temporary.replace(target)
    target.chmod(0o600)
    return target
