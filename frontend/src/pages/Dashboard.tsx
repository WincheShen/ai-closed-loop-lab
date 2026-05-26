import { useEffect, useState } from 'react'
import { cn, formatPercent } from '@/lib/utils'
import {
  TrendingUp,
  TrendingDown,
  Activity,
  Bot,
  Play,
  Globe,
  BarChart3,
} from 'lucide-react'

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
  icon?: any
}

function IndexCard({ index }: { index: MarketIndex }) {
  const isBullish = index.change >= 0

  return (
    <div className="data-card flex items-center justify-between">
      <div className="flex items-center gap-3">
        <div
          className={cn(
            'w-10 h-10 rounded-lg flex items-center justify-center',
            isBullish ? 'bg-bullish/10' : 'bg-bearish/10'
          )}
        >
          {isBullish ? (
            <TrendingUp className="w-5 h-5 text-bullish" />
          ) : (
            <TrendingDown className="w-5 h-5 text-bearish" />
          )}
        </div>
        <div>
          <div className="text-sm font-medium text-foreground">{index.name}</div>
          <div className="text-[10px] text-muted-foreground font-mono">
            {index.symbol}
          </div>
        </div>
      </div>
      <div className="text-right">
        <div className="text-lg font-mono font-semibold text-foreground">
          {index.price.toLocaleString('zh-CN', { minimumFractionDigits: 2 })}
        </div>
        <div
          className={cn(
            'text-xs font-mono font-medium',
            isBullish ? 'text-bullish' : 'text-bearish'
          )}
        >
          {isBullish ? '+' : ''}
          {index.change.toFixed(2)} ({formatPercent(index.changePercent)})
        </div>
      </div>
    </div>
  )
}

function LatencySparkline({
  data,
  color,
}: {
  data: number[]
  color: string
}) {
  const max = Math.max(...data)
  const min = Math.min(...data)
  const range = max - min || 1
  const width = 120
  const height = 40

  const points = data
    .map((v, i) => {
      const x = (i / (data.length - 1)) * width
      const y = height - ((v - min) / range) * height
      return `${x},${y}`
    })
    .join(' ')

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
        return <circle key={i} cx={x} cy={y} r={2} fill={color} className="opacity-60" />
      })}
    </svg>
  )
}

function AgentCardComponent({ agent }: { agent: AgentCard }) {
  const Icon = agent.type === 'MarketBrain'
    ? BarChart3
    : agent.type === 'RiskGovernor'
    ? Bot
    : agent.type === 'DataCollector'
    ? Globe
    : Activity

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
            <div className="text-[10px] text-muted-foreground font-mono">
              {agent.type}
            </div>
          </div>
        </div>
        <div className="flex items-center gap-1.5">
          <span className={cn('status-dot', status.dot, status.animate)} />
          <span className="text-[11px] text-muted-foreground">
            {status.label}
          </span>
        </div>
      </div>

      <p className="text-xs text-muted-foreground mb-3">{agent.description}</p>

      <div className="flex items-center justify-between pt-3 border-t border-panel-border">
        <div className="flex items-center gap-1.5 text-[11px] text-muted-foreground">
          <Play className="w-3 h-3" />
          <span>上次运行: {agent.lastRun}</span>
        </div>
        <span className="text-[11px] font-mono text-accent">
          {agent.throughput}
        </span>
      </div>
    </div>
  )
}

export default function Dashboard() {
  const [scriptRuns, setScriptRuns] = useState(0)
  const [marketIndices, setMarketIndices] = useState<MarketIndex[]>([])
  const [agents, setAgents] = useState<AgentCard[]>([])
  const [apiStats, setApiStats] = useState<any>({})

  useEffect(() => {
    const fetchDashboard = async () => {
      try {
        const resp = await fetch('/api/dashboard')
        if (!resp.ok) return
        const data = await resp.json()

        if (data.indices) setMarketIndices(data.indices)
        if (data.agents) setAgents(data.agents)
        if (data.apiStats) setApiStats(data.apiStats)
        if (data.scriptRuns) setScriptRuns(data.scriptRuns)
      } catch (err) {
        console.error('Failed to fetch dashboard data', err)
      }
    }

    fetchDashboard()
    const timer = setInterval(fetchDashboard, 30000)
    return () => clearInterval(timer)
  }, [])

  return (
    <div className="space-y-6">
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
          <span className="text-sm font-mono font-bold text-accent">
            {scriptRuns}
          </span>
          <span className="text-[10px] text-muted-foreground">次</span>
        </div>
      </div>

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

      <div className="grid grid-cols-1 lg:grid-cols-3 gap-6">
        <section className="lg:col-span-2">
          <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider">
            API 监控看板
          </h2>

          <div className="data-card space-y-4">
            {Object.entries(apiStats).map(([name, stats]: any) => {
              const avgLatency =
                stats.latency.reduce((a: number, b: number) => a + b, 0) /
                stats.latency.length

              const degraded = avgLatency > 1000

              return (
                <div
                  key={name}
                  className="flex items-center gap-4 p-3 rounded-lg bg-panel-hover border border-panel-border"
                >
                  <div className="w-10 h-10 rounded-lg bg-accent/10 flex items-center justify-center shrink-0">
                    <Bot className="w-5 h-5 text-accent" />
                  </div>

                  <div className="flex-1">
                    <div className="flex items-center gap-2 mb-1">
                      <span className="text-sm font-medium text-foreground">
                        {name}
                      </span>
                      <span
                        className={cn(
                          'text-[10px] px-1.5 py-0.5 rounded font-mono',
                          degraded
                            ? 'bg-warning/20 text-warning'
                            : 'bg-status-running/20 text-status-running'
                        )}
                      >
                        {degraded ? '延迟高' : '正常'}
                      </span>
                    </div>

                    <div className="text-xs text-muted-foreground">
                      调用: {stats.calls} · 平均延迟 {avgLatency.toFixed(0)}ms
                    </div>
                  </div>

                  <LatencySparkline
                    data={stats.latency}
                    color={degraded ? '#ffab00' : '#00f0ff'}
                  />
                </div>
              )
            })}
          </div>
        </section>

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
