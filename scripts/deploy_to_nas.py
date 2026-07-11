#!/usr/bin/env python3
"""同步代码到 NAS 并重新构建镜像。"""

import subprocess
import sys
import time


def get_nas_creds():
    """从 Keychain 读取 NAS 凭据。"""
    result = subprocess.run(
        ["python3", "scripts/get_nas_creds.py"],
        capture_output=True,
        text=True,
        check=True,
    )
    username, password = result.stdout.strip().split()
    return username, password


def sync_to_nas(username: str, password: str, host: str = "192.168.3.73"):
    """使用 rsync 同步代码到 NAS。"""
    print(f"📦 同步代码到 NAS: {username}@{host}...")

    # rsync 命令
    rsync_cmd = [
        "rsync",
        "-avz",
        "--delete",
        "--exclude", ".git",
        "--exclude", "__pycache__",
        "--exclude", "*.pyc",
        "--exclude", "node_modules",
        "--exclude", "frontend/dist",
        "--exclude", "data",
        "--exclude", "logs",
        "--exclude", "reports",
        "--exclude", ".venv",
        "--exclude", "venv",
        ".",
        f"{username}@{host}:/volume1/docker/ai-lab/",
    ]

    try:
        # 使用 sshpass 传递密码
        result = subprocess.run(
            ["sshpass", "-p", password] + rsync_cmd,
            check=True,
        )
        print("✅ 代码同步完成")
    except subprocess.CalledProcessError as e:
        print(f"❌ 同步失败: {e}", file=sys.stderr)
        sys.exit(1)


def rebuild_on_nas(username: str, password: str, host: str = "192.168.3.73"):
    """在 NAS 上重新构建镜像。"""
    print(f"🔨 在 NAS 上重新构建镜像...")

    ssh_cmd = [
        "sshpass",
        "-p",
        password,
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        f"{username}@{host}",
        "bash -c 'cd /volume1/docker/ai-lab && sudo /usr/local/bin/docker-compose build web'",
    ]

    try:
        subprocess.run(ssh_cmd, check=True)
        print("✅ 镜像构建完成")
    except subprocess.CalledProcessError as e:
        print(f"❌ 构建失败: {e}", file=sys.stderr)
        sys.exit(1)


def restart_service(username: str, password: str, host: str = "192.168.3.73"):
    """重启服务。"""
    print(f"🔄 重启服务...")

    ssh_cmd = [
        "sshpass",
        "-p",
        password,
        "ssh",
        "-o",
        "StrictHostKeyChecking=no",
        f"{username}@{host}",
        "bash -c 'cd /volume1/docker/ai-lab && sudo /usr/local/bin/docker-compose up -d web'",
    ]

    try:
        subprocess.run(ssh_cmd, check=True)
        print("✅ 服务重启完成")
    except subprocess.CalledProcessError as e:
        print(f"❌ 重启失败: {e}", file=sys.stderr)
        sys.exit(1)


def main():
    username, password = get_nas_creds()

    print("🚀 开始部署到 NAS...")
    print("1/3: 同步代码")
    sync_to_nas(username, password)

    print("2/3: 重新构建镜像")
    rebuild_on_nas(username, password)

    print("3/3: 重启服务")
    restart_service(username, password)

    print("\n✨ 部署完成！")
    print("📊 访问: http://192.168.3.73:8002")


if __name__ == "__main__":
    main()
