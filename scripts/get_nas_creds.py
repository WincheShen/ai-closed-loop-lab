#!/usr/bin/env python3
"""从 macOS Keychain 读取 NAS 凭据的工具脚本。"""

import subprocess
import sys
from pathlib import Path


def get_keychain_password(service: str, account: str) -> str:
    """从 Keychain 读取密码。"""
    try:
        result = subprocess.run(
            ["security", "find-generic-password", "-s", service, "-a", account, "-w"],
            capture_output=True,
            text=True,
            check=True,
        )
        return result.stdout.strip()
    except subprocess.CalledProcessError as e:
        print(f"❌ 从 Keychain 读取失败: {e.stderr.strip()}", file=sys.stderr)
        sys.exit(1)


def main():
    """读取 NAS 凭据并打印（供其他脚本调用）。"""
    username = get_keychain_password("ai-lab-nas-username", "nas_username")
    password = get_keychain_password("ai-lab-nas-password", "nas_password")

    # 输出格式：用户名和密码用空格分隔
    print(f"{username} {password}")


if __name__ == "__main__":
    main()
