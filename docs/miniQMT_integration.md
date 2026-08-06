# miniQMT 实盘接入指南

## 架构概览

```
NAS (Linux/Docker)                     Windows 机器
┌──────────────────────┐               ┌────────────────────────┐
│ ai-lab-scheduler     │               │ miniQMT Bridge Server  │
│   └─ executor.py     │  HTTP/REST    │   └─ xtquant SDK       │
│       └─ adapter ────┼──────────────>│       └─ miniQMT 客户端 │
│           (safety)   │  :9090        │           (东方财富)    │
└──────────────────────┘               └────────────────────────┘
```

## 三种交易模式

| 模式 | 说明 | 配置 |
|------|------|------|
| `mock` | 仅模拟记录到数据库（默认） | `TRADING_MODE=mock` |
| `shadow` | **双轨执行**：mock + 实盘同时运行 | `TRADING_MODE=shadow` |
| `live` | 纯实盘，不再 mock | `TRADING_MODE=live` |

**推荐路径**: `mock` → `shadow` (1万元验证) → `live` (放大资金)

## 部署步骤

### 1. Windows 端：Bridge Server

**前提**：已安装 miniQMT 客户端（东方财富/国金 QMT 量化终端）并登录。

```powershell
# 安装依赖
pip install fastapi uvicorn xtquant

# 配置环境变量
set XTQUANT_ACCOUNT_ID=你的资金账号
set MINI_QMT_PATH=D:\国金QMT交易端\userdata_mini
set BRIDGE_TOKEN=一个安全的随机字符串
set BRIDGE_PORT=9090

# 启动 Bridge
python scripts/miniQMT_bridge.py
```

验证：浏览器访问 `http://localhost:9090/health`

### 2. NAS 端：配置环境变量

在 NAS 的 `.env` 文件中添加：

```bash
# 切换到 shadow 模式（先不要直接用 live）
TRADING_MODE=shadow

# Bridge 连接信息
XTQUANT_BRIDGE_URL=http://192.168.3.100:9090  # Windows 机器局域网 IP
XTQUANT_BRIDGE_TOKEN=和 Bridge 端一致的 token
XTQUANT_ACCOUNT_ID=你的资金账号

# 安全护栏（按需调整）
MAX_SINGLE_ORDER_AMOUNT=10000   # 单笔最大 1万
MAX_DAILY_BUY_AMOUNT=30000      # 日买入最大 3万
MAX_TOTAL_POSITION_VALUE=50000  # 持仓总市值最大 5万
MAX_DAILY_ORDERS=10             # 日最多下10单
MAX_DAILY_LOSS=1000             # 日亏损超1000元熔断
```

### 3. 重启 NAS 容器

```bash
cd /volume1/docker/ai-lab
sudo docker-compose --profile scheduler down
sudo docker-compose --profile scheduler up -d
```

## 安全护栏

`SafeBrokerAdapter` 在每笔真实下单前执行以下检查：

1. **Kill Switch** — 紧急停止（设置 `kill_switch=True`）
2. **交易时间** — 仅 9:30-11:30, 13:00-14:57 允许下单
3. **黑白名单** — 可限制仅允许特定股票
4. **单笔金额** — 默认单笔不超过 1万元
5. **单笔股数** — 默认不超过 5000 股
6. **日下单次数** — 默认每日不超过 10 笔
7. **日买入金额** — 默认每日买入不超过 3万
8. **持仓总市值** — 默认不超过 5万
9. **单股持仓** — 默认单只不超过 1.5万
10. **日亏损熔断** — 当日亏损超限自动停止买入

## Shadow 模式说明

Shadow 模式是验证阶段的关键：

- **mock 照常运行**：数据库状态、仓位计算、绩效跟踪不受影响
- **实盘同步下单**：相同信号发送到券商，真实执行
- **对比记录**：日志记录 mock vs live 的差异
- **实盘异常不影响 mock**：如果券商拒绝/超时，mock 记录不受影响

在 shadow 模式稳定运行 2-4 周后，确认：
- 实盘成交价与 mock 价差在 1% 以内
- 安全护栏未被意外触发
- 券商连接稳定

即可切换到 `TRADING_MODE=live`。

## 文件结构

```
src/agents/executioner/
├── executor.py           # 主执行引擎（已支持 mock/shadow/live）
├── broker_adapter.py     # 抽象接口 + SafeBrokerAdapter 安全层
└── xtquant_client.py     # HTTP 客户端（NAS 端）

scripts/
└── miniQMT_bridge.py     # Bridge Server（Windows 端）
```

## 故障处理

| 问题 | 表现 | 处理 |
|------|------|------|
| Bridge 断连 | `[SHADOW] Broker 未连接` | 检查 Windows 机器网络/miniQMT 是否运行 |
| 安全护栏拒绝 | `🚫 安全护栏拒绝` | 检查是否超过配置限制 |
| miniQMT 未登录 | health 返回 `xt_connected: false` | 重新打开 miniQMT 并登录 |
| 下单失败 | `order_stock returned -1` | 检查账户资金/股票代码/涨跌停 |
