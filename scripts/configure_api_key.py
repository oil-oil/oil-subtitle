#!/usr/bin/env python3
"""Configure the one DashScope API key used by oil-subtitle."""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from user_config import (
    PREFERRED_API_KEY_FILE,
    dashscope_api_key_file,
    legacy_bailian_api_key,
    save_dashscope_api_key,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure the DashScope API key")
    parser.add_argument(
        "--migrate-existing",
        action="store_true",
        help="Import an existing Bailian CLI credential when available",
    )
    args = parser.parse_args()

    target = dashscope_api_key_file()
    if target.exists() and target.read_text(encoding="utf-8").strip():
        target.chmod(0o600)
        print(f"DashScope API key is already configured: {target}")
        return 0

    if args.migrate_existing:
        legacy_key = legacy_bailian_api_key()
        if legacy_key:
            saved = save_dashscope_api_key(legacy_key, target)
            print(f"Migrated the existing Bailian credential to: {saved}")
            return 0

    if os.environ.get("DASHSCOPE_API_KEY", "").strip():
        print("DASHSCOPE_API_KEY is configured in the current environment.")
        return 0

    if not sys.stdin.isatty():
        print(
            "DashScope API key is not configured. Run:\n"
            f"  .venv/bin/python3 scripts/configure_api_key.py\n"
            f"The key will be stored at {PREFERRED_API_KEY_FILE} with mode 600."
        )
        return 0


    key = getpass.getpass("DashScope API Key: ").strip()
    if not key:
        print("API key was not saved: empty input.", file=sys.stderr)
        return 1
    saved = save_dashscope_api_key(key, target)
    print(f"DashScope API key saved: {saved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
