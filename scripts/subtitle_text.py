#!/usr/bin/env python3
"""Shared display-text normalization for subtitle preparation and burning."""

from __future__ import annotations

import re


def add_cjk_spacing(text: str) -> str:
    """Add one space at CJK/Latin-or-number boundaries, idempotently."""
    text = re.sub(r"([\u4e00-\u9fff\u3400-\u4dbf])([A-Za-z0-9])", r"\1 \2", text)
    text = re.sub(r"([A-Za-z0-9])([\u4e00-\u9fff\u3400-\u4dbf])", r"\1 \2", text)
    return re.sub(r"[ \t]+", " ", text)
