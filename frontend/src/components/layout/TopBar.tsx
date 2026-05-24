import { useState, useEffect } from 'react'
import { cn, formatLatency } from '@/lib/utils'
import {
  Activity,
  Cpu,
  Wifi,
  WifiOff,
  Zap,
  Clock,
  Database,
} from 'lucide-react'

interface ApiStatus {
  name: string
  provider: string
  latency: number
  tokensUsed: number
  tokensLimit: number
  status: 'connected' | 'degraded' | 'disconnected'
}

const apiStatuses: ApiStatus[] = [
  {
    name: 'Gemini 1.5 Pro',
    provider: 'Google',
    latency: 1240,
    tokensUsed: 284_000,
    tokensLimit: 1_000_000,
    status: 'connected',
  },
  {
    name: 'Claude 3.5 Sonnet',
    provider: 'Anthropic',
    latency: 890,
    tokensUsed: 156_000,
    tokensLimit: 200_000,
    status: 'connected',
  },
  {
    name: 'Azure GPT-5',
    provider: 'Microsoft',
    latency: 2100,
    tokensUsed: 420_000,
    tokensLimit: 500_000,
    status: 'degraded',
  },
]

function StatusBadge({ status }: { status: ApiStatus['status'] }) {
  const config = {
    connected: {
      icon: Wifi,
      className: 'bg-status-running/20 text-status-running',
      label: '正常',
    },
    degraded: {
      icon: Activity,
      className: 'bg-warning/20 text-warning',
      label: '延迟高',
    },
    disconnected: {
      icon: WifiOff,
      className: 'bg-status-error/20 text-status-error',
      label: '断开',
    },
  }

  const { icon: Icon, className, label } = config[status]

  return (
    <div className={cn('flex items-center gap-1.5 px-2 py-1 rounded text-[11px] font-mono', className)}>
      <Icon className="w-3 h-3" />
      <span>{label}</span>
    </div>
  )
}

export function TopBar() {
  const [currentTime, setCurrentTime] = useState(new Date())

  useEffect(() => {
    const timer = setInterval(() => {
      setCurrentTime(new Date())
    }, 1000)
    return () => clearInterval(timer)
  }, [])

  const totalTokens = apiStatuses.reduce((sum, api) => sum + api.tokensUsed, 0)
  const totalLimit = apiStatuses.reduce((sum, api) => sum + api.tokensLimit, 0)
  const tokenPercent = (totalTokens / totalLimit) * 100

  return (
    <header className="flex items-center justify-between px-6 h-14 bg-panel border-b border-panel-border shrink-0">
      {/* 左侧：当前路径标题 + 系统状态 */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 text-muted-foreground">
          <Cpu className="w-4 h-4" />
          <span className="text-xs font-mono">SYSTEM ONLINE</span>
        </div>
        <div className="h-4 w-px bg-panel-border" />
        <div className="flex items-center gap-2">
          <span className="status-dot running" />
          <span className="text-xs text-status-running">Pipeline Active</span>
        </div>
      </div>

      {/* 中间：API 监控 */}
      <div className="flex items-center gap-4">
        {apiStatuses.map((api) => (
          <div
            key={api.name}
            className="flex items-center gap-3 px-3 py-1.5 rounded-lg bg-panel-hover border border-panel-border"
          >
            <div className="flex flex-col">
              <span className="text-[11px] text-muted-foreground">{api.provider}</span>
              <span className="text-xs font-medium text-foreground">{api.name}</span>
            </div>
            <div className="flex flex-col items-end gap-0.5">
              <div className="flex items-center gap-1.5">
                <Clock className="w-3 h-3 text-muted-foreground" />
                <span
                  className={cn(
                    'text-[11px] font-mono',
                    api.latency > 1500 ? 'text-warning' : 'text-muted-foreground'
                  )}
                >
                  {formatLatency(api.latency)}
                </span>
              </div>
              <StatusBadge status={api.status} />
            </div>
          </div>
        ))}
      </div>

      {/* 右侧：Token 消耗统计 */}
      <div className="flex items-center gap-4">
        <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-panel-hover border border-panel-border">
          <Database className="w-4 h-4 text-accent" />
          <div className="flex flex-col">
            <span className="text-[10px] text-muted-foreground">今日 Token 消耗</span>
            <div className="flex items-center gap-2">
              <span className="text-xs font-mono font-medium text-foreground">
                {(totalTokens / 1_000).toFixed(0)}K
              </span>
              <span className="text-[10px] text-muted-foreground">
                / {(totalLimit / 1_000).toFixed(0)}K
              </span>
            </div>
          </div>
          {/* Token 进度条 */}
          <div className="w-16 h-1.5 bg-panel-border rounded-full overflow-hidden">
            <div
              className={cn(
                'h-full rounded-full transition-all',
                tokenPercent > 80 ? 'bg-status-error' : tokenPercent > 50 ? 'bg-warning' : 'bg-accent'
              )}
              style={{ width: `${Math.min(tokenPercent, 100)}%` }}
            />
          </div>
        </div>

        {/* 实时时间 */}
        <div className="flex items-center gap-2 text-muted-foreground">
          <Zap className="w-4 h-4 text-accent" />
          <span className="text-xs font-mono">
            {currentTime.toLocaleTimeString('zh-CN', { hour12: false })}
          </span>
        </div>
      </div>
    </header>
  )
}
