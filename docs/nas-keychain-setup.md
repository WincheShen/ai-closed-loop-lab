# NAS 凭据管理

通过 macOS Keychain 安全存储 NAS 凭据，避免在脚本中硬编码密码。

## 步骤 1: 添加凭据到 Keychain

### 方法 A: 命令行

```bash
# 添加用户名
security add-generic-password -a "nas_username" -s "ai-lab-nas-username" -w "your_username"

# 添加密码
security add-generic-password -a "nas_password" -s "ai-lab-nas-password" -w "your_password"
```

### 方法 B: 图形界面

1. 打开 "钥匙串访问" (Keychain Access)
2. 点击 "+" 添加新项目
3. 填写：
   - 名称: `ai-lab-nas-username` / `ai-lab-nas-password`
   - 账户: `nas_username` / `nas_password`
   - 密码: 填入实际值

## 步骤 2: 使用脚本

### 读取凭据

```bash
python scripts/get_nas_creds.py
# 输出: username password
```

### SSH 连接 NAS

```bash
# 交互式登录
python scripts/ssh_nas.py --interactive

# 执行命令
python scripts/ssh_nas.py -c "docker-compose -f docker/docker-compose.nas.yml logs -f web"

# 指定主机和端口
python scripts/ssh_nas.py --host 192.168.3.73 --port 22 -c "ls -la"
```

## 步骤 3: 让 Devin 使用

Devin 可以通过 `ssh_nas.py` 脚本自动获取凭据并执行 NAS 上的命令，无需手动输入密码。

示例：

```bash
# 拉取最新镜像
python scripts/ssh_nas.py -c "docker pull ghcr.io/wincheshen/ai-closed-loop-lab/ai-lab:latest"

# 重启服务
python scripts/ssh_nas.py -c "cd /volume1/docker/ai-lab && docker-compose -f docker-compose.nas.yml up -d web"

# 查看日志
python scripts/ssh_nas.py -c "cd /volume1/docker/ai-lab && docker-compose -f docker-compose.nas.yml logs -f web"
```

## 安全说明

- 凭据存储在 macOS Keychain 中，受系统保护
- 脚本仅读取 Keychain，不保存凭据到文件
- 建议使用 SSH 公钥认证替代密码（更安全）
