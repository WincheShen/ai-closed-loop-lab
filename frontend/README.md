# Social Media AI Lab — Frontend

FinTech + AI 赛博风格控制台前端。

## 技术栈

- **React 18** + TypeScript
- **Vite** (构建工具)
- **Tailwind CSS** (原子化样式)
- **React Router** (路由)
- **Lucide React** (图标)
- **Recharts** (图表，已预留)

## 快速开始

```bash
cd frontend
npm install
npm run dev
```

访问 http://localhost:5173

## 项目结构

```
frontend/
├── public/
│   └── favicon.svg
├── src/
│   ├── components/
│   │   └── layout/
│   │       ├── Layout.tsx      # 全局布局骨架
│   │       ├── Sidebar.tsx     # 左侧导航侧边栏
│   │       └── TopBar.tsx      # 顶部 API 状态栏
│   ├── pages/
│   │       ├── Dashboard.tsx       # 模块1: 核心控制台
│   │       ├── QuantMonitor.tsx    # 模块2: 金融数据中台
│   │       ├── AgentWorkspace.tsx  # 模块3: 多智能体工作流
│   │       └── ContentPipeline.tsx # 模块4: 社交媒体管线
│   ├── lib/
│   │       └── utils.ts        # 工具函数 (cn, formatNumber, formatPercent...)
│   ├── App.tsx                 # 路由配置
│   ├── main.tsx                # 入口
│   └── index.css               # 全局样式 + Tailwind 指令
├── index.html
├── package.json
├── tailwind.config.js          # 自定义 FinTech 暗黑主题
├── tsconfig.json
└── vite.config.ts
```

## 设计规范

### 颜色系统

| 用途 | 颜色值 | Tailwind 类 |
|------|--------|-------------|
| 背景 | `#0a0a0f` | `bg-background` |
| 面板 | `#12121a` | `bg-panel` |
| 边框 | `#1e1e2e` | `border-panel-border` |
| 强调色 (AI) | `#00f0ff` | `text-accent` |
| 涨 (Bullish) | `#00c853` | `text-bullish` |
| 跌 (Bearish) | `#ff1744` | `text-bearish` |
| 警告 | `#ffab00` | `text-warning` |

### 组件规范

- **数据卡片**: 使用 `.data-card` 类
- **状态指示灯**: 使用 `.status-dot` 配合对应状态类
- **终端文本**: 使用 `.terminal-text` 类
- **金融涨跌**: 使用 `.text-bullish` / `.text-bearish`

## 模块概览

### 模块1: Dashboard (核心控制台)
- 大盘指数卡片 (上证/深证/纳斯达克/黄金)
- API 监控看板 (Gemini/Claude 延迟 + Sparkline)
- Agent 状态池 (卡片列表 + 动态指示灯)

### 模块2: QuantMonitor (金融数据中台)
- AKShare 数据表格 (可排序/筛选)
- K 线占位图 + MACD/KDJ 缩略图
- 关注列表侧边栏 (红涨绿跌)

### 模块3: AgentWorkspace (多智能体工作流)
- LangGraph 节点流程可视化
- 终端风格日志控制台 (自动滚动)
- Agent 交互记录实时打印

### 模块4: ContentPipeline (社交媒体管线)
- AI 分析报告编辑区
- 小红书手机预览框 (iPhone 风格)
- 发布队列管理 (待发布/已发布)

## 后续优化方向

1. **接入 Recharts**: 替换占位 SVG 为真实图表
2. **WebSocket 实时数据**: 大盘指数、Agent 状态实时推送
3. **与后端 API 对接**: 替换 mock 数据为真实接口
4. **响应式优化**: 移动端适配
5. **暗/亮主题切换**: 当前仅支持暗黑模式
