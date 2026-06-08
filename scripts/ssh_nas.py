#!/usr/bin/env python3
"""通过 Keychain 获取 NAS 凭据并执行远程命令。"""

import argparse
import subprocess
import sys


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
        # 如果精确匹配失败，尝试模糊匹配（处理 Keychain 截断问题）
        try:
            result = subprocess.run(
                ["security", "find-generic-password", "-s", service[:20], "-a", account, "-w"],
                capture_output=True,
                text=True,
                check=True,
            )
            return result.stdout.strip()
        except subprocess.CalledProcessError:
            print(f"❌ 从 Keychain 读取失败: {e.stderr.strip()}", file=sys.stderr)
            sys.exit(1)


def ssh_nas(
    host: str,
    username: str,
    password: str,
    command: str | None = None,
    port: int = 22,
    interactive: bool = False,
) -> None:
    """SSH 连接到 NAS 并执行命令。"""
    # 使用 sshpass 自动输入密码
    ssh_cmd = [
        "sshpass",
        "-p",
        password,
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        "-p",
        str(port),
        f"{username}@{host}",
    ]

    if command:
        ssh_cmd.append(command)
        mode = "执行命令"
    else:
        mode = "交互式登录"

    print(f"🔐 正在连接 NAS: {username}@{host}:{port} ({mode})")

    try:
        if interactive:
            subprocess.run(ssh_cmd)
        else:
            result = subprocess.run(ssh_cmd, capture_output=True, text=True, check=True)
            print(result.stdout)
            if result.stderr:
                print(f"⚠️ stderr: {result.stderr}", file=sys.stderr)
    except subprocess.CalledProcessError as e:
        print(f"❌ SSH 执行失败: {e.stderr}", file=sys.stderr)
        sys.exit(1)
    except FileNotFoundError:
        print("❌ 需要安装 sshpass: brew install sshpass", file=sys.stderr)
        sys.exit(1)


def main():
    parser = argparse.ArgumentParser(description="通过 Keychain 凭据连接 NAS")
    parser.add_argument("--host", default="192.168.3.73", help="NAS 主机地址")
    parser.add_argument("--port", type=int, default=22, help="SSH 端口")
    parser.add_argument("--command", "-c", help="要执行的命令（不指定则进入交互式）")
    parser.add_argument("--interactive", "-i", action="store_true", help="交互式登录")

    args = parser.parse_args()

    # 从 Keychain 读取凭据（使用实际存储的 service 名称）
    username = get_keychain_password("ai-lab-nas-usernam", "nas_username")
    password = get_keychain_password("ai-lab-nas-password", "nas_password")

    # 执行 SSH
    ssh_nas(
        host=args.host,
        username=username,
        password=password,
        command=args.command,
        port=args.port,
        interactive=args.interactive or args.command is None,
    )


if __name__ == "__main__":
    main()
