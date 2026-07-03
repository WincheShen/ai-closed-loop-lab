#!/usr/bin/env python3
"""部署 Docker 镜像到 NAS（通过 HTTP 传输）。"""

import subprocess
import sys
import time
import os
from pathlib import Path

# 配置
IMAGE_NAME = "ai-lab"
IMAGE_TAG = "latest-amd64"
NAS_HOST = "192.168.3.73"
NAS_USER = "kingSY_9"
NAS_PASSWORD = "Shen.Yun.88"
NAS_PATH = "/volume1/docker/ai-lab/"
HTTP_PORT = 8888


def build_image():
    """本地构建 Docker 镜像。"""
    print("🔨 步骤 1/5: 检查本地 Docker 镜像...")

    # 检查镜像是否已存在
    check_cmd = ["docker", "images", "-q", f"{IMAGE_NAME}:{IMAGE_TAG}"]
    result = subprocess.run(check_cmd, capture_output=True, text=True)

    if result.stdout.strip():
        print(f"✅ 镜像已存在: {IMAGE_NAME}:{IMAGE_TAG}")
        return

    print(f"🔨 镜像不存在，开始构建...")
    cmd = [
        "docker", "build",
        "-f", "docker/Dockerfile",
        "-t", f"{IMAGE_NAME}:{IMAGE_TAG}",
        "."
    ]
    try:
        subprocess.run(cmd, check=True)
        print(f"✅ 镜像构建完成: {IMAGE_NAME}:{IMAGE_TAG}")
    except subprocess.CalledProcessError as e:
        print(f"❌ 镜像构建失败: {e}", file=sys.stderr)
        sys.exit(1)


def save_image():
    """保存镜像为 tar 文件。"""
    print("📦 步骤 2/5: 保存镜像为 tar 文件...")
    tar_file = f"{IMAGE_NAME}.tar"
    cmd = ["docker", "save", "-o", tar_file, f"{IMAGE_NAME}:{IMAGE_TAG}"]
    try:
        subprocess.run(cmd, check=True)
        size_mb = os.path.getsize(tar_file) / (1024 * 1024)
        print(f"✅ 镜像保存完成: {tar_file} ({size_mb:.1f} MB)")
        return tar_file
    except subprocess.CalledProcessError as e:
        print(f"❌ 镜像保存失败: {e}", file=sys.stderr)
        sys.exit(1)


def start_http_server(tar_file: str):
    """启动 Python HTTP 文件服务。"""
    print(f"🌐 步骤 3/5: 启动 HTTP 文件服务 (端口 {HTTP_PORT})...")

    # 检查端口是否被占用
    try:
        subprocess.run(
            ["lsof", "-ti", f":{HTTP_PORT}"],
            check=True,
            capture_output=True,
        )
        print(f"⚠️  端口 {HTTP_PORT} 已被占用，尝试关闭...")
        subprocess.run(["kill", "-9", subprocess.run(
            ["lsof", "-ti", f":{HTTP_PORT}"],
            capture_output=True,
            text=True,
        ).stdout.strip()], check=False)
        time.sleep(1)
    except subprocess.CalledProcessError:
        pass  # 端口未被占用

    # 启动 HTTP 服务器
    server_cmd = [
        "python3", "-m", "http.server", str(HTTP_PORT),
        "--bind", "0.0.0.0", "--directory", "."
    ]
    process = subprocess.Popen(
        server_cmd,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    time.sleep(2)

    # 检查服务是否启动成功
    try:
        subprocess.run(
            ["lsof", "-ti", f":{HTTP_PORT}"],
            check=True,
            capture_output=True,
        )
        print(f"✅ HTTP 服务已启动: http://0.0.0.0:{HTTP_PORT}/{tar_file}")
        return process
    except subprocess.CalledProcessError:
        print(f"❌ HTTP 服务启动失败", file=sys.stderr)
        process.kill()
        sys.exit(1)


def download_to_nas(tar_file: str):
    """在 NAS 上下载镜像文件。"""
    print(f"📥 步骤 4/5: 在 NAS 上下载镜像文件...")

    url = f"http://192.168.3.149:{HTTP_PORT}/{tar_file}"  # 本机 IP
    # 如果 NAS 在外网，需要使用公网 IP 或内网穿透

    ssh_cmd = [
        "sshpass", "-p", NAS_PASSWORD,
        "ssh", "-o", "StrictHostKeyChecking=no",
        f"{NAS_USER}@{NAS_HOST}",
        f"bash -c 'cd {NAS_PATH} && curl -O {url}'"
    ]

    try:
        subprocess.run(ssh_cmd, check=True)
        print(f"✅ NAS 下载完成: {NAS_PATH}{tar_file}")
    except subprocess.CalledProcessError as e:
        print(f"❌ NAS 下载失败: {e}", file=sys.stderr)
        print(f"💡 提示: 请确保 NAS 能访问本机 IP: http://192.168.3.1:{HTTP_PORT}")
        sys.exit(1)


def load_and_restart(tar_file: str) -> bool:
    """在 NAS 上加载镜像并重启服务。

    Returns:
        True if used rsync (docker not found), False if used docker
    """
    print(f"🔄 步骤 5/5: 在 NAS 上加载镜像并重启服务...")

    # 先检查 docker 是否存在
    check_cmd = [
        "sshpass", "-p", NAS_PASSWORD,
        "ssh", "-o", "StrictHostKeyChecking=no",
        f"{NAS_USER}@{NAS_HOST}",
        "which docker"
    ]

    try:
        subprocess.run(check_cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError:
        print(f"❌ NAS 上未找到 docker 命令")
        print(f"💡 提示: NAS 未安装 Docker，改用 rsync 方式部署代码")
        print(f"📦 切换到 rsync 部署方式...")
        sync_to_nas()
        rebuild_on_nas()
        return True  # Used rsync

    # 加载镜像（使用 sudo docker，通过 SSH 传递密码）
    load_cmd = [
        "sshpass", "-p", NAS_PASSWORD,
        "ssh", "-o", "StrictHostKeyChecking=no",
        f"{NAS_USER}@{NAS_HOST}",
        f"sudo -S docker load -i {NAS_PATH}{tar_file}"
    ]

    # 通过 stdin 传递密码
    try:
        process = subprocess.Popen(
            load_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate(input=NAS_PASSWORD + "\n")
        if process.returncode != 0:
            print(f"❌ 镜像加载失败: {stderr}", file=sys.stderr)
            sys.exit(1)
        print(f"✅ 镜像加载完成")
    except Exception as e:
        print(f"❌ 镜像加载失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 重启服务
    restart_cmd = [
        "sshpass", "-p", NAS_PASSWORD,
        "ssh", "-o", "StrictHostKeyChecking=no",
        f"{NAS_USER}@{NAS_HOST}",
        f"cd {NAS_PATH} && sudo -S /usr/local/bin/docker-compose up -d"
    ]

    try:
        process = subprocess.Popen(
            restart_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate(input=NAS_PASSWORD + "\n")
        if process.returncode != 0:
            print(f"❌ 服务重启失败: {stderr}", file=sys.stderr)
            sys.exit(1)
        print(f"✅ 服务重启完成")
    except Exception as e:
        print(f"❌ 服务重启失败: {e}", file=sys.stderr)
        sys.exit(1)

    return False  # Used docker


def sync_to_nas():
    """使用 rsync 同步代码到 NAS。"""
    print(f"📦 同步代码到 NAS: {NAS_USER}@{NAS_HOST}...")

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
        f"{NAS_USER}@{NAS_HOST}:{NAS_PATH}",
    ]

    # 使用 sshpass 传递密码
    try:
        result = subprocess.run(
            ["sshpass", "-p", NAS_PASSWORD] + rsync_cmd,
            check=True
        )
        print("✅ 代码同步完成")
    except subprocess.CalledProcessError as e:
        print(f"❌ 同步失败: {e}", file=sys.stderr)
        sys.exit(1)


def rebuild_on_nas():
    """在 NAS 上重新构建镜像。"""
    print(f"🔨 在 NAS 上重新构建镜像...")

    ssh_cmd = [
        "sshpass", "-p", NAS_PASSWORD,
        "ssh", "-o", "StrictHostKeyChecking=no",
        f"{NAS_USER}@{NAS_HOST}",
        f"cd {NAS_PATH} && sudo -S /usr/local/bin/docker-compose build"
    ]

    try:
        process = subprocess.Popen(
            ssh_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate(input=NAS_PASSWORD + "\n")
        if process.returncode != 0:
            print(f"❌ 构建失败: {stderr}", file=sys.stderr)
            sys.exit(1)
        print("✅ 镜像构建完成")
    except Exception as e:
        print(f"❌ 构建失败: {e}", file=sys.stderr)
        sys.exit(1)

    # 重启服务
    restart_cmd = [
        "sshpass", "-p", NAS_PASSWORD,
        "ssh", "-o", "StrictHostKeyChecking=no",
        f"{NAS_USER}@{NAS_HOST}",
        f"cd {NAS_PATH} && sudo -S /usr/local/bin/docker-compose up -d"
    ]

    try:
        process = subprocess.Popen(
            restart_cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True
        )
        stdout, stderr = process.communicate(input=NAS_PASSWORD + "\n")
        if process.returncode != 0:
            print(f"❌ 重启失败: {stderr}", file=sys.stderr)
            sys.exit(1)
        print("✅ 服务重启完成")
    except Exception as e:
        print(f"❌ 重启失败: {e}", file=sys.stderr)
        sys.exit(1)


def cleanup(tar_file: str, http_process, delete_tar: bool = True):
    """清理临时文件和服务。"""
    print("🧹 清理临时文件...")
    http_process.kill()
    if delete_tar and os.path.exists(tar_file):
        os.remove(tar_file)
        print(f"✅ 已删除本地: {tar_file}")

    # 删除 NAS 上的 tar 文件（如果存在）
    if delete_tar:
        cleanup_cmd = [
            "sshpass", "-p", NAS_PASSWORD,
            "ssh", "-o", "StrictHostKeyChecking=no",
            f"{NAS_USER}@{NAS_HOST}",
            f"bash -c 'cd {NAS_PATH} && rm -f {tar_file}'"
        ]
        try:
            subprocess.run(cleanup_cmd, check=True)
            print(f"✅ 已删除 NAS: {NAS_PATH}{tar_file}")
        except subprocess.CalledProcessError:
            pass  # 可能已经不存在


def main():
    print("🚀 开始部署到 NAS（Docker 镜像方式）")
    print("=" * 60)

    # 1. 构建镜像
    build_image()

    # 2. 保存镜像
    tar_file = save_image()

    # 3. 启动 HTTP 服务
    http_process = start_http_server(tar_file)

    used_rsync = False
    try:
        # 4. 下载到 NAS
        download_to_nas(tar_file)

        # 5. 加载并重启（内部会检测 docker 是否存在，不存在则用 rsync）
        used_rsync = load_and_restart(tar_file)

        print("\n✨ 部署完成！")
        print(f"📊 访问: http://{NAS_HOST}:8002")
    finally:
        # 清理
        cleanup(tar_file, http_process, delete_tar=not used_rsync)


if __name__ == "__main__":
    main()
