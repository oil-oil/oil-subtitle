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
    load_user_config,
    load_dashscope_api_key,
)


def main() -> int:
    parser = argparse.ArgumentParser(description="Configure the DashScope API key")
    parser.add_argument(
        "--migrate-existing",
        action="store_true",
        help="明确迁移旧 API Key 文件或 Bailian 配置；不删除旧文件",
    )
    args = parser.parse_args()

    if load_user_config().get('credential_ref') and load_dashscope_api_key(required=False):
        print('DashScope API key is configured in the system credential store.')
        return 0

    target = dashscope_api_key_file()
    if not args.migrate_existing and target.exists() and target.read_text(encoding="utf-8").strip():
        target.chmod(0o600)
        print(f"DashScope API key is already configured: {target}")
        return 0

    if args.migrate_existing:
        legacy_key = target.read_text(encoding="utf-8").strip() if target.exists() else legacy_bailian_api_key()
        if legacy_key:
            saved = save_dashscope_api_key(legacy_key, target)
            print(f"旧凭据已迁移到系统凭据库；普通配置引用位置（旧文件保留）： {saved}")
            return 0

    if os.environ.get("DASHSCOPE_API_KEY", "").strip():
        print("DASHSCOPE_API_KEY is configured in the current environment.")
        return 0

    if not sys.stdin.isatty():
        print(
            "DashScope API key is not configured. Run:\n"
            f"  .venv/bin/python3 scripts/configure_api_key.py\n"
            "The key will be stored in the system credential store."
        )
        return 2


    key = getpass.getpass("DashScope API Key: ").strip()
    if not key:
        print("API key was not saved: empty input.", file=sys.stderr)
        return 1
    saved = save_dashscope_api_key(key, target)
    print(f"DashScope API key saved: {saved}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
