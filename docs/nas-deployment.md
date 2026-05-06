# NAS 部署指南

将 AI Lab Web 服务部署到群晖 NAS，与已有的 Trading Agent 服务共存。

## 现有服务

| 服务 | 容器 | 端口 | 状态 |
|------|------|------|------|
| Trading Agent | trading-agent | 8010 | ✅ 已部署 http://192.168.3.73:8010/stats |
| AI Lab Web | ai-lab-webhook | 8002 | 📦 本文档部署 |

两个服务共用同一个 Docker 镜像 `ghcr.io/wincheshen/ai-closed-loop-lab/trading-agent:latest`，
通过不同的启动命令运行不同功能。

## 前提条件

- NAS 已安装 Docker 和 Docker Compose
- NAS 已启用 SSH 访问（用户 `kingsy_9`）
- NAS 上 Docker 命令需要 `sudo`

## 手动部署（推荐）

### 1. SSH 到 NAS

```bash
ssh kingsy_9@192.168.3.73
```

### 2. 创建项目目录

```bash
sudo mkdir -p /volume1/docker/ai-lab-web/{data,logs}
cd /volume1/docker/ai-lab-web
```

### 3. 登录 GHCR（首次需要）

镜像托管在 GitHub Container Registry（私有），需要先认证：

```bash
# 用 GitHub 用户名 + Personal Access Token 登录
sudo docker login ghcr.io -u WincheShen
# 密码输入 GitHub PAT（需有 read:packages 权限）
```

### 4. 下载配置文件

```bash
# 从 GitHub 下载 docker-compose 配置
sudo curl -fsSL -o docker-compose.nas.yml \
  https://raw.githubusercontent.com/WincheShen/ai-closed-loop-lab/main/docker/docker-compose.nas.yml
```

### 5. 创建 .env 文件

```bash
sudo tee .env > /dev/null << 'EOF'
# LLM API（Azure OpenAI）
OPENAI_API_KEY=your-key-here
OPENAI_BASE_URL=https://ai-yhuang96usai172233441975.openai.azure.com/openai/v1
OPENAI_API_VERSION=2025-03-01-preview

# Trading Agent 服务地址（同 NAS 上已部署）
TRADING_AGENT_URL=http://192.168.3.73:8010

# 数据库路径（容器内）
DB_PATH=/app/data/central_brain.db

# 关闭自动社媒发布
WEBHOOK_AUTO_SMA_DISPATCH=false
EOF
```

⚠️ **必须将 `OPENAI_API_KEY` 替换为真实值。**

### 6. 拉取镜像并启动

```bash
sudo docker pull ghcr.io/wincheshen/ai-closed-loop-lab/trading-agent:latest
sudo docker-compose -f docker-compose.nas.yml up -d webhook
```

### 7. 验证

```bash
# 检查容器运行状态
sudo docker ps | grep ai-lab

# 健康检查
curl http://localhost:8002/health

# 查看日志
sudo docker logs ai-lab-webhook -f
```

## 访问地址

| 功能 | URL |
|------|-----|
| 首页（重定向到管理页面）| http://192.168.3.73:8002/ |
| 交易记录管理 | http://192.168.3.73:8002/ui/ |
| 策略选股 | http://192.168.3.73:8002/ui/strategy.html |
| 健康检查 | http://192.168.3.73:8002/health |
| API 文档 | http://192.168.3.73:8002/docs |
| Trading Agent（已有）| http://192.168.3.73:8010/stats |

## 更新服务

```bash
ssh kingsy_9@192.168.3.73
cd /volume1/docker/ai-lab-web
sudo docker pull ghcr.io/wincheshen/ai-closed-loop-lab/trading-agent:latest
sudo docker-compose -f docker-compose.nas.yml up -d webhook
```

## GitHub Actions 自动部署（可选）

如果配置了 NAS 的 SSH 密钥访问，可以通过 GitHub Actions 自动部署。

在仓库 Settings > Secrets and variables > Actions 中添加：

| Secret | 说明 | 示例 |
|--------|------|------|
| `NAS_HOST` | NAS IP 地址 | `192.168.3.73` |
| `NAS_USER` | SSH 用户名 | `kingsy_9` |
| `NAS_SSH_KEY` | SSH 私钥 | `-----BEGIN OPENSSH PRIVATE KEY-----...` |

推送代码到 `main` 分支自动触发，或在 Actions 页面手动运行。

## 常见问题

### 1. Docker 权限不足

```
permission denied while trying to connect to the Docker daemon socket
```

解决：所有 `docker` 命令前加 `sudo`。或一劳永逸：

```bash
sudo synogroup --member docker $(whoami)
# 退出重新登录后生效
```

### 2. 镜像拉取失败 (401 Unauthorized)

GHCR 私有镜像需要登录：

```bash
sudo docker login ghcr.io -u WincheShen
# 输入 GitHub Personal Access Token（需 read:packages 权限）
```

### 3. 端口冲突

8002 被占用时，修改 `docker-compose.nas.yml`：

```yaml
ports:
  - "8003:8002"  # 主机 8003 映射到容器 8002
```

### 4. Bind mount 失败

```
Bind mount failed: '/volume1/docker/ai-lab-web/logs' does not exist
```

确保挂载目录已创建：

```bash
sudo mkdir -p /volume1/docker/ai-lab-web/{data,logs}
```

### 5. 数据持久化

所有数据保存在 NAS 的 `/volume1/docker/ai-lab-web/data/` 目录，容器重启不会丢失。

## NAS 服务架构

```
NAS (192.168.3.73)
├── Trading Agent (已部署)         :8010
│   ├── /stats          服务状态
│   └── /analyze        深度分析 API
│
└── AI Lab Web (本次部署)          :8002
    ├── /ui/             交易记录管理页面
    ├── /ui/strategy.html 策略选股页面
    ├── /api/strategy/*   策略 API（编译/执行/保存）
    ├── /webhook/trade    交易记录接收
    └── /health           健康检查

共用镜像: ghcr.io/wincheshen/ai-closed-loop-lab/trading-agent:latest
数据目录: /volume1/docker/ai-lab-web/data/
```
