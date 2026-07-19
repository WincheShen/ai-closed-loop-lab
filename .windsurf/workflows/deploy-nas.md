---
description: 部署 AI 闭环实验室到群晖 NAS（Docker 镜像方式）
tags: [deploy, nas, docker, production]
---

# 部署到群晖 NAS

> 将本地构建的 Docker 镜像部署到群晖 NAS，通过 HTTP 传输镜像文件并在 NAS 上加载重启服务。

## 前置条件

- NAS 已安装 Docker 套件（位于 `/usr/local/bin/docker`）
- NAS SSH 已启用，用户有 sudo 权限
- 本地与 NAS 在同一内网
- 本机 IP 已在 `scripts/deploy_image_to_nas.py` 中配置（当前：`192.168.3.158`）

## 快速部署

```bash
cd /Users/neo/Projects/ai-closed-loop-lab
python3 scripts/deploy_image_to_nas.py
```

脚本会自动执行以下步骤：
1. 🔨 检查本地 Docker 镜像（`ai-lab:latest-amd64`）
2. 📦 保存镜像为 tar 文件
3. 🌐 启动 HTTP 文件服务（端口 8888）
4. 📥 NAS 下载镜像文件
5. 🔄 NAS 加载镜像并重启服务
6. 🧹 清理临时文件

## 手动部署（如脚本失败）

### 1. 本地构建镜像

```bash
cd /Users/neo/Projects/ai-closed-loop-lab
docker build -f docker/Dockerfile -t ai-lab:latest-amd64 .
```

### 2. 保存镜像

```bash
docker save -o ai-lab.tar ai-lab:latest-amd64
```

### 3. 传输到 NAS

```bash
# 方式 1: HTTP 传输（推荐）
python3 -m http.server 8888 --bind 0.0.0.0
# 在 NAS 上执行：
curl -O http://192.168.3.158:8888/ai-lab.tar

# 方式 2: SCP 传输
scp ai-lab.tar kingSY_9@192.168.3.73:/volume1/docker/ai-lab/
```

### 4. NAS 加载镜像

```bash
ssh kingSY_9@192.168.3.73
cd /volume1/docker/ai-lab
sudo /usr/local/bin/docker load -i ai-lab.tar
rm ai-lab.tar
```

### 5. 重启服务

```bash
sudo /usr/local/bin/docker-compose up -d
```

## 验证部署

```bash
# 检查服务状态
ssh kingSY_9@192.168.3.73 "cd /volume1/docker/ai-lab && sudo /usr/local/bin/docker-compose ps"

# 访问 Web UI
open http://192.168.3.73:8002

# 查看日志
ssh kingSY_9@192.168.3.73 "cd /volume1/docker/ai-lab && sudo /usr/local/bin/docker-compose logs -f web"
```

## 配置说明

部署脚本配置（`scripts/deploy_image_to_nas.py`）：
- `IMAGE_NAME`: ai-lab
- `IMAGE_TAG`: latest-amd64
- `NAS_HOST`: 192.168.3.73
- `NAS_USER`: kingSY_9
- `NAS_PATH`: /volume1/docker/ai-lab/
- `HTTP_PORT`: 8888

如需修改配置，编辑脚本中的常量定义。

**重要**：部署脚本会保护 NAS 上的配置文件：
- `docker-compose.yml` - 不会被覆盖
- `.env` - 环境变量不会被覆盖
- `data/` - 数据目录不会被覆盖
- `logs/` - 日志目录不会被覆盖

如需更新配置文件，请手动在 NAS 上编辑。

## 故障排查

### 问题：NAS 无法访问本机 HTTP 服务

**解决**：检查本机 IP 是否正确（`ifconfig`），更新脚本中的 IP 地址。

### 问题：LLM 配置错误 (404 Resource Not Found)

**原因**：`.env` 文件中 Azure OpenAI 配置冲突
- 错误：同时配置了 `OPENAI_BASE_URL` 和 `AZURE_OPENAI_ENDPOINT`
- 正确：使用 `DEFAULT_LLM_PROVIDER=azure` 时，只配置 `AZURE_OPENAI_ENDPOINT`

**解决**：修正 NAS 上的 `.env` 文件
```bash
ssh kingSY_9@192.168.3.73
vi /volume1/docker/ai-lab/.env
# 确保配置如下：
DEFAULT_LLM_PROVIDER=azure
AZURE_OPENAI_ENDPOINT=https://ai-yhuang96usai172233441975.openai.azure.com/openai/v1
AZURE_OPENAI_API_KEY=your_key
AZURE_OPENAI_API_VERSION=2025-03-01-preview
# 注释掉或删除 OPENAI_BASE_URL
# OPENAI_BASE_URL=...
```

### 问题：Docker 命令未找到

**解决**：NAS 上 Docker 路径为 `/usr/local/bin/docker`，脚本已使用完整路径。

### 问题：sudo 权限问题

**解决**：确保 NAS 用户在 sudoers 中，或使用 root 用户。

### 问题：rsync 失败

**解决**：脚本会自动降级到 Docker 镜像方式，无需手动干预。

## 回滚

如需回滚到上一版本：

```bash
ssh kingSY_9@192.168.3.73
cd /volume1/docker/ai-lab
sudo /usr/local/bin/docker images
# 找到旧镜像 ID
sudo /usr/local/bin/docker tag <old-image-id> ai-lab:latest-amd64
sudo /usr/local/bin/docker-compose up -d
```
