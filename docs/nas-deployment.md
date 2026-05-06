# NAS 部署指南

将 AI Lab 部署到群晖/威联通等 NAS 设备，与 Trading Agent 服务一起运行。

## 前提条件

- NAS 已安装 Docker 和 Docker Compose
- NAS 已启用 SSH 访问
- GitHub 仓库已配置 Secrets（用于自动部署）

## 快速部署

### 1. 配置 GitHub Secrets

在仓库 Settings > Secrets and variables > Actions 中添加：

| Secret | 说明 | 示例 |
|--------|------|------|
| `NAS_HOST` | NAS IP 地址 | `192.168.3.73` |
| `NAS_USER` | SSH 用户名 | `root` |
| `NAS_SSH_KEY` | SSH 私钥 | `-----BEGIN OPENSSH PRIVATE KEY-----...` |
| `NAS_PATH` | （可选）部署路径 | `/volume1/docker/ai-lab` |

生成 SSH 密钥：
```bash
ssh-keygen -t ed25519 -C "github-actions" -f ~/.ssh/nas_deploy
# 将 ~/.ssh/nas_deploy.pub 添加到 NAS 的 authorized_keys
cat ~/.ssh/nas_deploy | pbcopy  # 复制私钥到 GitHub Secret
```

### 2. 触发部署

#### 自动部署（推荐）
推送代码到 `main` 分支自动触发：
```bash
git push origin main
```

#### 手动部署
进入 GitHub 仓库 > Actions > Deploy to NAS > Run workflow

### 3. 验证部署

```bash
# 检查服务状态
curl http://192.168.3.73:8002/health

# 查看日志
ssh root@192.168.3.73 "docker logs ai-lab-webhook"
```

## 访问地址

部署完成后，可以通过以下地址访问：

| 功能 | URL |
|------|-----|
| 交易记录管理 | http://192.168.3.73:8002/ui/ |
| 策略选股 | http://192.168.3.73:8002/ui/strategy.html |
| 健康检查 | http://192.168.3.73:8002/health |
| API 文档 | http://192.168.3.73:8002/docs |

## 本地手动部署

如果不想用 GitHub Actions，可以本地部署：

```bash
# 1. 复制部署脚本到本地
chmod +x scripts/deploy-to-nas.sh

# 2. 运行部署
./scripts/deploy-to-nas.sh 192.168.3.73 root
```

## 配置 .env 文件

NAS 上首次部署后，需要编辑 `.env` 文件：

```bash
ssh root@192.168.3.73
vi /volume1/docker/ai-lab/.env
```

最小配置：
```bash
# LLM API（Azure OpenAI）
OPENAI_API_KEY=your-key-here
OPENAI_BASE_URL=https://ai-yhuang96usai172233441975.openai.azure.com/openai/v1
OPENAI_API_VERSION=2025-03-01-preview

# 数据库路径（容器内）
DB_PATH=/app/data/central_brain.db

# 关闭自动社媒发布（NAS 上通常不需要）
WEBHOOK_AUTO_SMA_DISPATCH=false
```

## 与 Trading Agent 联动

确保 NAS 上的 Trading Agent 服务正常运行：

```bash
curl http://192.168.3.73:8010/stats
```

策略选股时会自动调用该服务进行深度分析。

## 常见问题

### 1. 端口冲突

如果 8002 端口被占用，修改 `docker-compose.nas.yml`：
```yaml
ports:
  - "8003:8002"  # 主机8003映射到容器8002
```

### 2. 镜像拉取失败

在 NAS 上手动登录 GHCR：
```bash
docker login ghcr.io -u YOUR_GITHUB_USERNAME
# 输入 Personal Access Token 作为密码
```

### 3. 数据持久化

所有数据保存在 NAS 的 `/volume1/docker/ai-lab/data/` 目录，容器重启不会丢失。

### 4. 更新服务

```bash
# 手动更新
ssh root@192.168.3.73 "cd /volume1/docker/ai-lab && docker-compose -f docker-compose.nas.yml pull && docker-compose -f docker-compose.nas.yml up -d"
```

## 服务架构

```
┌─────────────────────────────────────────────┐
│                    NAS                       │
│  ┌─────────────────────────────────────┐   │
│  │   Trading Agent (已部署)            │   │
│  │   http://192.168.3.73:8010          │   │
│  └─────────────────────────────────────┘   │
│                                              │
│  ┌─────────────────────────────────────┐   │
│  │   AI Lab Webhook + 策略选股 (新部署) │   │
│  │   http://192.168.3.73:8002          │   │
│  │                                     │   │
│  │   - /ui/         交易记录管理        │   │
│  │   - /ui/strategy.html  策略选股      │   │
│  │   - /api/strategy/*  策略API         │   │
│  └─────────────────────────────────────┘   │
└─────────────────────────────────────────────┘
```
