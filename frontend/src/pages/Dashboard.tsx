import { useEffect, useState } from 'react'
import { cn, formatNumber, formatPercent } from '@/lib/utils'
import {
  TrendingUp,
  TrendingDown,
  Activity,
  Bot,
  Play,
  Globe,
  Coins,
  BarChart3,
} from 'lucide-react'

// ── 类型定义 ──
interface MarketIndex {
  name: string
  symbol: string
  price: number
  change: number
  changePercent: number
}

interface AgentCard {
  id: string
  name: string
  type: string
  status: 'running' | 'idle' | 'error'
  lastRun: string
  throughput: string
  description: string
  icon: React.ElementType
}

// ── 模拟数据 ──
const marketIndices: MarketIndex[] = [
  { name: '上证指数', symbol: 'SH', price: 3052.37, change: 12.45, changePercent: 0.41 },
  { name: '深证成指', symbol: 'SZ', price: 9384.52, change: -28.13, changePercent: -0.30 },
  { name: '纳斯达克', symbol: 'NDX', price: 16834.92, change: 142.58, changePercent: 0.85 },
  { name: '黄金现货', symbol: 'XAU', price: 2348.60, change: 8.20, changePercent: 0.35 },
]

const apiStats = {
  gemini: { calls: 1248, latency: [820, 950, 1100, 890, 1240, 980, 1050] },
  claude: { calls: 856, latency: [720, 680, 890, 950, 890, 760, 890] },
}

const agents: AgentCard[] = [
  {
    id: 'agent-1',
    name: '数据抓取 Agent',
    type: 'DataCollector',
    status: 'running',
    lastRun: '2分钟前',
    throughput: '5511 只/批',
    description: 'AKShare + Sina 双源行情抓取',
    icon: Globe,
  },
  {
    id: 'agent-2',
    name: '研报分析 Agent',
    type: 'MarketBrain',
    status: 'running',
    lastRun: '5分钟前',
    throughput: '8 只/批',
    description: 'LLM 驱动的 Regime 判定与板块推断',
    icon: BarChart3,
  },
  {
    id: 'agent-3',
    name: '文案排版 Agent',
    type: 'ContentWriter',
    status: 'idle',
    lastRun: '1小时前',
    throughput: '3 篇/日',
    description: '小红书风格金融内容生成',
    icon: Activity,
  },
  {
    id: 'agent-4',
    name: '风控监察 Agent',
    type: 'RiskGovernor',
    status: 'running',
    lastRun: '刚刚',
    throughput: '实时监控',
    description: '持仓集中度与止损线监控',
    icon: Bot,
  },
]

// ── 子组件：指数卡片 ──
function IndexCard({ index }: { index: MarketIndex }) {
  const isBullish = index.change >= 0

  return (
    <div className="data-card flex items-center justify-between">
      <div className="flex items-center gap-3">
        <div className={cn(
          'w-10 h-10 rounded-lg flex items-center justify-center',
          isBullish ? 'bg-bullish/10' : 'bg-bearish/10'
        )}>
          {isBullish ? (
            <TrendingUp className="w-5 h-5 text-bullish" />
          ) : (
            <TrendingDown className="w-5 h-5 text-bearish" />
          )}
        </div>
        <div>
          <div className="text-sm font-medium text-foreground">{index.name}</div>
          <div className="text-[10px] text-muted-foreground font-mono">{index.symbol}</div>
        </div>
      </div>
      <div className="text-right">
        <div className="text-lg font-mono font-semibold text-foreground">
          {index.price.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
        </div>
        <div className={cn(
          'text-xs font-mono font-medium',
          isBullish ? 'text-bullish' : 'text-bearish'
        )}>
          {isBullish ? '+' : ''}{index.change.toFixed(2)} ({formatPercent(index.changePercent)})
        </div>
      </div>
    </div>
  )
}

// ── 子组件：API 延迟折线图（SVG 简化版） ──
function LatencySparkline({ data, color }: { data: number[]; color: string }) {
  const max = Math.max(...data)
  const min = Math.min(...data)
  const range = max - min || 1
  const width = 120
  const height = 40
  const points = data.map((v, i) => {
    const x = (i / (data.length - 1)) * width
    const y = height - ((v - min) / range) * height
    return `${x},${y}`
  }).join(' ')

  return (
    <svg width={width} height={height} className="overflow-visible">
      <polyline
        fill="none"
        stroke={color}
        strokeWidth={1.5}
        points={points}
        className="opacity-80"
      />
      {data.map((v, i) => {
        const x = (i / (data.length - 1)) * width
        const y = height - ((v - min) / range) * height
        return (
          <circle
            key={i}
            cx={x}
            cy={y}
            r={2}
            fill={color}
            className="opacity-60"
          />
        )
      })}
    </svg>
  )
}

// ── 子组件：Agent 状态卡片 ──
function AgentCardComponent({ agent }: { agent: AgentCard }) {
  const Icon = agent.icon
  const statusConfig = {
    running: { dot: 'bg-status-running', label: '运行中', animate: 'animate-pulse' },
    idle: { dot: 'bg-status-idle', label: '空闲', animate: '' },
    error: { dot: 'bg-status-error', label: '报错', animate: '' },
  }
  const status = statusConfig[agent.status]

  return (
    <div className="data-card group">
      <div className="flex items-start justify-between mb-3">
        <div className="flex items-center gap-3">
          <div className="w-9 h-9 rounded-lg bg-accent/10 flex items-center justify-center">
            <Icon className="w-5 h-5 text-accent" />
          </div>
          <div>
            <div className="text-sm font-medium text-foreground">{agent.name}</div>
            <div className="text-[10px] text-muted-foreground font-mono">{agent.type}</div>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={cn('status-dot', status.dot, status.animate)} />
          <span className="text-[11px] text-muted-foreground">{status.label}</span>
        </div>
      </div>

      <p className="text-xs text-muted-foreground mb-3">{agent.description}</p>

      <div className="flex items-center justify-between pt-3 border-t border-panel-border">
        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <Play className="w-3 h-3" />
          <span>上次运行: {agent.lastRun}</span>
        </div>
        <span className="text-[11px] font-mono text-accent">{agent.throughput}</span>
      </div>
    </div>
  )
}

// ── 主页面 ──
export default function Dashboard() {
  const [scriptRuns, setScriptRuns] = useState(47)

  // 模拟脚本计数器自动增长
  useEffect(() => {
    const timer = setInterval(() => {
      setScriptRuns((prev) => prev + Math.floor(Math.random() * 3))
    }, 8000)
    return () => clearInterval(timer)
  }, [])

  return (
    <div className="space-y-6">
      {/* 页面标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">核心控制台</h1>
          <p className="text-sm text-muted-foreground mt-1">
            实时监控 · 量化数据 · Agent 状态池
          </p>
        </div>
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-panel-hover border border-panel-border">
          <Activity className="w-4 h-4 text-accent" />
          <span className="text-xs text-muted-foreground">今日脚本运行:</span>
          <span className="text-sm font-mono font-bold text-accent">{scriptRuns}</span>
          <span className="text-[10px] text-muted-foreground">次</span>
        </div>
      </div>

      {/* ── 第一行：大盘指数卡片 ── */}
      <section>
        <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider">
          今日大盘核心指数
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-4">
          {marketIndices.map((idx) => (
            <IndexCard key={idx.symbol} index={idx} />
          ))}
        </div>
      </section>

      {/* ── 第二行：API 监控 + Agent 状态池 ── */}
      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        {/* API 监控看板 */}
        <section className="lg:col-span-2">
          <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider">
            API 监控看板
          </h2>
          <div className="data-card space-y-4">
            {Object.entries(apiStats).map(([name, stats]) => {
              const isGemini = name === 'gemini'
              const avgLatency = stats.latency.reduce((a, b) => a + b, 0) / stats.latency.length
              const isDegraded = avgLatency > 1000

              return (
                <div
                  key={name}
                  className="flex items-center gap-4 p-3 rounded-lg bg-panel-hover border border-panel-border"
                >
                  <div className="w-10 h-10 rounded-lg bg-accent/10 flex items-center justify-center shrink-0">
                    {isGemini ? (
                      <Globe className="w-5 h-5 text-accent" />
                    ) : (
                      <Bot className="w-5 h-5 text-accent" />
                    )}
                  </div>

                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-sm font-medium text-foreground">
                        {isGemini ? 'Gemini 1.5 Pro' : 'Claude 3.5 Sonnet'}
                      </span>
                      <span className={cn(
                        'text-[10px] px-1.5 py-0.5 rounded font-mono',
                        isDegraded ? 'bg-warning/20 text-warning' : 'bg-status-running/20 text-status-running'
                      )}>
                        {isDegraded ? '延迟高' : '正常'}
                      </span>
                    </div>
                    <div className="flex items-center gap-4 text-xs text-muted-foreground">
                      <span>调用: <span className="font-mono text-foreground">{stats.calls}</span></span>
                      <span>平均延迟: <span className={cn(
                        'font-mono',
                        isDegraded ? 'text-warning' : 'text-foreground'
                      )}>{avgLatency.toFixed(0)}ms</span></span>
                    </div>
                  </div>

                  <LatencySparkline
                    data={stats.latency}
                    color={isDegraded ? '#ffab00' : '#00f0ff'}
                  />
                </div>
              )
            })}

            {/* 并发延迟实时指示 */}
            <div className="flex items-center gap-4 pt-2 border-t border-panel-border">
              <div className="flex-1">
                <div className="text-[11px] text-muted-foreground mb-1">当前并发延迟分布</div>
                <div className="flex items-center gap-1 h-6">
                  {[40, 65, 30, 80, 55, 90, 45, 70, 35, 85, 50, 75].map((h, i) => (
                    <div
                      key={i}
                      className="flex-1 rounded-sm bg-accent/30"
                      style={{ height: `${h}%` }}
                    />
                  ))}
                </div>
              </div>
            </div>
          </div>
        </section>

        {/* Agent 状态池 */}
        <section>
          <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider">
            Agent 状态池
          </h2>
          <div className="space-y-3">
            {agents.map((agent) => (
              <AgentCardComponent key={agent.id} agent={agent} />
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}
