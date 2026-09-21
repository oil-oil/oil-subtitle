#!/usr/bin/env python3
"""启动或检查 oil-subtitle 随附的系统凭据页。"""

from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


SKILL_DIR = Path(__file__).resolve().parents[1]
PROFILE = SKILL_DIR / "scripts" / "credential-ui" / "src" / "profile.ts"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="启动或检查 DashScope API Key 的系统凭据页"
    )
    parser.add_argument(
        "--migrate-existing",
        action="store_true",
        help="已废弃：不会自动迁移旧的明文凭据",
    )
    parser.add_argument("--status", action="store_true", help="只查询配置状态")
    args = parser.parse_args()

    if args.migrate_existing:
        print("已停用自动迁移。请在随附配置页中手动保存到系统凭据库。")
    if not PROFILE.is_file():
        parser.error(f"找不到随附凭据组件：{PROFILE}")
    action = "status" if args.status else "setup"
    return subprocess.call(
        ["node", str(PROFILE), action, "default"],
        cwd=SKILL_DIR,
    )


if __name__ == "__main__":
    raise SystemExit(main())
