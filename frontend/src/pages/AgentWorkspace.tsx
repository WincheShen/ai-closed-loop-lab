import { useState, useRef, useEffect } from 'react'
import { cn } from '@/lib/utils'
import {
  Play,
  RotateCcw,
  Bot,
  BrainCircuit,
  FileText,
  Send,
  Circle,
  CheckCircle2,
  XCircle,
  Clock,
  Terminal,
  Sparkles,
  GitBranch,
  ChevronDown,
} from 'lucide-react'

// ── 类型定义 ──
type NodeStatus = 'pending' | 'running' | 'completed' | 'error'

interface WorkflowNode {
  id: string
  label: string
  description: string
  status: NodeStatus
  icon: React.ElementType
  duration?: string
  output?: string
}

interface LogEntry {
  timestamp: string
  level: 'info' | 'success' | 'warning' | 'error'
  agent: string
  message: string
  details?: any // 保留完整的 payload 用于详情展示
}

interface EventRecord {
  id: number
  event_type: string
  created_at: string
  payload: any
}

// ── 工作流节点定义 ──
const workflowNodeDefinitions: Omit<WorkflowNode, 'status' | 'duration' | 'output'>[] = [
  {
    id: 'fetch',
    label: '获取金融数据',
    description: 'AKShare + Sina 双源行情抓取',
    icon: BrainCircuit,
  },
  {
    id: 'regime',
    label: '市场 regime 判定',
    description: 'LLM 综合判断 bull/neutral/bear',
    icon: Sparkles,
  },
  {
    id: 'hotsector',
    label: '热点板块推断',
    description: 'emappdata 热度榜 + 关键词聚类',
    icon: GitBranch,
  },
  {
    id: 'scan',
    label: '全市场扫描',
    description: '规则引擎筛选候选票',
    icon: Bot,
  },
  {
    id: 'strategy',
    label: '策略决策',
    description: 'Strategist LLM 深度分析',
    icon: BrainCircuit,
  },
  {
    id: 'risk',
    label: '风控审查',
    description: 'RiskGovernor 仓位/止损检查',
    icon: CheckCircle2,
  },
  {
    id: 'execute',
    label: '执行交易',
    description: 'Executor 模拟盘下单',
    icon: Play,
  },
  {
    id: 'content',
    label: '生成内容',
    description: '小红书风格金融笔记',
    icon: FileText,
  },
]

// ── 从事件映射到节点状态 ──
function mapEventsToNodes(events: EventRecord[]): WorkflowNode[] {
  const nodeStatusMap: Record<string, NodeStatus> = {}
  const nodeOutputMap: Record<string, string> = {}

  // 默认所有节点为 pending
  workflowNodeDefinitions.forEach(node => {
    nodeStatusMap[node.id] = 'pending'
  })

  // 根据事件更新节点状态
  events.forEach(event => {
    const eventType = event.event_type
    const payload = event.payload

    // MarketBrain 完成
    if (eventType.includes('market_brain') || eventType.includes('regime')) {
      nodeStatusMap['regime'] = 'completed'
      nodeStatusMap['fetch'] = 'completed'
      if (payload?.regime) {
        nodeOutputMap['regime'] = `regime=${payload.regime}, posture=${payload.posture || 'unknown'}`
      }
    }

    // Explorer 完成
    if (eventType.includes('explorer') || eventType.includes('scan')) {
      nodeStatusMap['scan'] = 'completed'
      nodeStatusMap['hotsector'] = 'completed'
      if (payload?.candidates_count !== undefined) {
        nodeOutputMap['scan'] = `候选票 ${payload.candidates_count} 只`
      }
    }

    // Strategist 完成
    if (eventType.includes('strategist') || eventType.includes('signal')) {
      nodeStatusMap['strategy'] = 'completed'
      if (payload?.signals_count !== undefined) {
        nodeOutputMap['strategy'] = `生成信号 ${payload.signals_count} 条`
      }
    }

    // RiskGovernor 完成
    if (eventType.includes('risk') || eventType.includes('governor')) {
      nodeStatusMap['risk'] = 'completed'
      if (payload?.decision) {
        nodeOutputMap['risk'] = `裁决: ${payload.decision}`
      }
    }

    // Executioner 完成
    if (eventType.includes('execute') || eventType.includes('order') || eventType.includes('fill')) {
      nodeStatusMap['execute'] = 'completed'
      if (payload?.filled_count !== undefined) {
        nodeOutputMap['execute'] = `成交 ${payload.filled_count} 笔`
      }
    }

    // Influencer 完成
    if (eventType.includes('influencer') || eventType.includes('content') || eventType.includes('publish')) {
      nodeStatusMap['content'] = 'completed'
      if (payload?.post_url) {
        nodeOutputMap['content'] = `发布成功: ${payload.post_url}`
      }
    }
  })

  // 检查是否有正在运行的节点（最近的事件）
  if (events.length > 0) {
    const latestEvent = events[0]
    const latestType = latestEvent.event_type

    if (latestType.includes('market_brain') && nodeStatusMap['regime'] !== 'completed') {
      nodeStatusMap['regime'] = 'running'
      nodeStatusMap['fetch'] = 'running'
    } else if (latestType.includes('explorer') && nodeStatusMap['scan'] !== 'completed') {
      nodeStatusMap['scan'] = 'running'
      nodeStatusMap['hotsector'] = 'running'
    } else if (latestType.includes('strategist') && nodeStatusMap['strategy'] !== 'completed') {
      nodeStatusMap['strategy'] = 'running'
    } else if (latestType.includes('risk') && nodeStatusMap['risk'] !== 'completed') {
      nodeStatusMap['risk'] = 'running'
    } else if (latestType.includes('execute') && nodeStatusMap['execute'] !== 'completed') {
      nodeStatusMap['execute'] = 'running'
    } else if (latestType.includes('influencer') && nodeStatusMap['content'] !== 'completed') {
      nodeStatusMap['content'] = 'running'
    }
  }

  return workflowNodeDefinitions.map(node => ({
    ...node,
    status: nodeStatusMap[node.id] || 'pending',
    output: nodeOutputMap[node.id],
  }))
}

// ── 从事件映射到日志 ──
function mapEventsToLogs(events: EventRecord[]): LogEntry[] {
  return events.map(event => {
    const eventType = event.event_type
    const agent = eventType.split('.')[0] || 'System'
    const payload = event.payload

    let level: LogEntry['level'] = 'info'
    let message = eventType

    if (eventType.includes('error') || eventType.includes('fail')) {
      level = 'error'
    } else if (eventType.includes('success') || eventType.includes('complete')) {
      level = 'success'
    } else if (eventType.includes('warning') || eventType.includes('pass')) {
      level = 'warning'
    }

    // 从 payload 提取更有意义的消息
    if (payload) {
      if (payload.regime) {
        message = `Regime 判定完成: ${payload.regime} | posture=${payload.posture || 'unknown'} | 上限=${(payload.max_total_position_pct || 0) * 100}%`
      } else if (payload.candidates_count !== undefined) {
        message = `扫描完成 — 候选票 ${payload.candidates_count} 只 | 热点板块: ${(payload.hot_sectors || []).join(', ')}`
      } else if (payload.signals_count !== undefined) {
        message = `LLM 分析完成 — 生成信号 ${payload.signals_count} 条 | Top 候选: ${(payload.top_symbols || []).slice(0, 3).join(', ')}`
      } else if (payload.decision) {
        message = `风控裁决: ${payload.decision} | 原始仓位: ${(payload.original_position_pct || 0) * 100}% | 批准仓位: ${(payload.approved_position_pct || 0) * 100}%`
      } else if (payload.filled_count !== undefined) {
        message = `模拟盘下单完成: 成交 ${payload.filled_count} 笔 | 总金额: ${payload.total_amount || 'N/A'}`
      } else if (payload.post_url) {
        message = `发布成功: ${payload.post_url} | 平台: ${payload.platform || 'unknown'}`
      } else if (payload.error) {
        message = `错误: ${payload.error}`
        level = 'error'
      } else if (payload.symbol && payload.action) {
        message = `交易信号: ${payload.symbol} ${payload.action} @ ${payload.entry_price} | 目标: ${payload.target_price} | 止损: ${payload.stop_loss}`
      }
    }

    return {
      timestamp: new Date(event.created_at).toLocaleTimeString('zh-CN', { hour12: false }),
      level,
      agent: agent.charAt(0).toUpperCase() + agent.slice(1),
      message,
      details: payload, // 保留完整的 payload 用于详情展示
    }
  })
}

// ── 子组件：工作流节点 ──
function WorkflowNodeComponent({
  node,
  isLast,
}: {
  node: WorkflowNode
  isLast: boolean
}) {
  const Icon = node.icon
  const statusConfig = {
    pending: { color: 'text-muted-foreground', bg: 'bg-panel-border', border: 'border-panel-border' },
    running: { color: 'text-accent', bg: 'bg-accent/20', border: 'border-accent animate-pulse' },
    completed: { color: 'text-bullish', bg: 'bg-bullish/20', border: 'border-bullish/50' },
    error: { color: 'text-bearish', bg: 'bg-bearish/20', border: 'border-bearish/50' },
  }
  const status = statusConfig[node.status]

  return (
    <div className="relative flex items-start gap-4">
      {/* 连接线 */}
      {!isLast && (
        <div className="absolute left-5 top-10 w-px h-12 bg-panel-border" />
      )}

      {/* 节点图标 */}
      <div
        className={cn(
          'relative z-10 w-10 h-10 rounded-xl flex items-center justify-center shrink-0 border-2 transition-all',
          status.bg,
          status.border
        )}
      >
        <Icon className={cn('w-5 h-5', status.color)} />
        {node.status === 'running' && (
          <div className="absolute -inset-1 rounded-xl border-2 border-accent/30 animate-ping opacity-50" />
        )}
      </div>

      {/* 节点内容 */}
      <div className="flex-1 pb-8">
        <div className="flex items-center gap-2 mb-1">
          <span className="text-sm font-medium text-foreground">{node.label}</span>
          {node.status === 'running' && (
            <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/20 text-accent font-mono animate-pulse">
              RUNNING
            </span>
          )}
          {node.status === 'completed' && (
            <CheckCircle2 className="w-4 h-4 text-bullish" />
          )}
          {node.status === 'error' && (
            <XCircle className="w-4 h-4 text-bearish" />
          )}
        </div>
        <p className="text-xs text-muted-foreground mb-1.5">{node.description}</p>
        {node.duration && (
          <div className="flex items-center gap-1 text-[11px] text-muted-foreground">
            <Clock className="w-3 h-3" />
            <span className="font-mono">{node.duration}</span>
          </div>
        )}
        {node.output && (
          <div className="mt-2 p-2 rounded bg-panel-hover border border-panel-border">
            <span className="text-[11px] font-mono text-accent">→ {node.output}</span>
          </div>
        )}
      </div>
    </div>
  )
}

// ── 子组件：日志行 ──
function LogLine({ entry }: { entry: LogEntry }) {
  const [expanded, setExpanded] = useState(false)
  const levelColors = {
    info: 'text-accent',
    success: 'text-bullish',
    warning: 'text-warning',
    error: 'text-bearish',
  }

  return (
    <div className="border-b border-panel-border/50">
      <div
        className="flex items-start gap-3 py-2 font-mono text-xs cursor-pointer hover:bg-panel-hover/50 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <span className="text-muted-foreground shrink-0">{entry.timestamp}</span>
        <span className={cn('shrink-0 w-20', levelColors[entry.level])}>
          [{entry.level.toUpperCase()}]
        </span>
        <span className="text-accent/70 shrink-0 w-28">{entry.agent}</span>
        <span className="text-foreground flex-1">{entry.message}</span>
        {entry.details && (
          <ChevronDown className={`w-4 h-4 text-muted-foreground transition-transform ${expanded ? 'rotate-180' : ''}`} />
        )}
      </div>
      {expanded && entry.details && (
        <div className="px-2 pb-2 pl-16">
          <div className="p-2 rounded bg-panel-hover border border-panel-border">
            <pre className="text-[10px] text-muted-foreground whitespace-pre-wrap break-all">
              {JSON.stringify(entry.details, null, 2)}
            </pre>
          </div>
        </div>
      )}
    </div>
  )
}

// ── 主页面 ──
export default function AgentWorkspace() {
  const [nodes, setNodes] = useState<WorkflowNode[]>([])
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [isTriggering, setIsTriggering] = useState(false)
  const [triggerMessage, setTriggerMessage] = useState<string | null>(null)
  const logRef = useRef<HTMLDivElement>(null)

  // 自动滚动日志到底部
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [logs])

  // 轮询事件流并更新状态
  useEffect(() => {
    const fetchEvents = async () => {
      try {
        const resp = await fetch('/api/events/recent?limit=20')
        if (resp.ok) {
          const data: EventRecord[] = await resp.json()

          // 映射到节点状态
          const mappedNodes = mapEventsToNodes(data)
          setNodes(mappedNodes)

          // 映射到日志
          const mappedLogs = mapEventsToLogs(data)
          setLogs(mappedLogs)
        }
      } catch (err) {
        console.error('Failed to fetch events:', err)
      }
    }
    fetchEvents()
    // 每 5 秒轮询一次
    const interval = setInterval(fetchEvents, 5000)
    return () => clearInterval(interval)
  }, [])

  const runningCount = nodes.filter((n) => n.status === 'running').length
  const completedCount = nodes.filter((n) => n.status === 'completed').length

  // 手动触发工作流
  const handleTriggerWorkflow = async () => {
    setIsTriggering(true)
    setTriggerMessage(null)
    try {
      const resp = await fetch('/api/workflow/trigger', {
        method: 'POST',
      })
      if (resp.ok) {
        const data = await resp.json()
        setTriggerMessage(data.message)
        // 3秒后清除消息
        setTimeout(() => setTriggerMessage(null), 3000)
      } else {
        setTriggerMessage('触发失败，请查看日志')
      }
    } catch (err) {
      setTriggerMessage('触发失败，请检查网络连接')
      console.error('Failed to trigger workflow:', err)
    } finally {
      setIsTriggering(false)
    }
  }

  return (
    <div className="space-y-6 h-full flex flex-col">
      {/* 页面标题 */}
      <div className="flex items-center justify-between shrink-0">
        <div>
          <h1 className="text-2xl font-bold text-foreground">多智能体工作流</h1>
          <p className="text-sm text-muted-foreground mt-1">
            LangGraph 执行链条 · Agent 协作日志
          </p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-panel-hover border border-panel-border">
            <div className="flex items-center gap-1.5">
              <Circle className="w-2.5 h-2.5 text-status-running fill-status-running" />
              <span className="text-xs text-muted-foreground">运行中: {runningCount}</span>
            </div>
            <div className="w-px h-4 bg-panel-border" />
            <div className="flex items-center gap-1.5">
              <CheckCircle2 className="w-2.5 h-2.5 text-bullish" />
              <span className="text-xs text-muted-foreground">已完成: {completedCount}/{nodes.length}</span>
            </div>
          </div>
          <button
            onClick={handleTriggerWorkflow}
            disabled={isTriggering}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent/10 text-accent text-sm font-medium hover:bg-accent/20 transition-colors border border-accent/20 disabled:opacity-50 disabled:cursor-not-allowed"
          >
            <RotateCcw className={`w-4 h-4 ${isTriggering ? 'animate-spin' : ''}`} />
            {isTriggering ? '启动中...' : '重新执行'}
          </button>
          {triggerMessage && (
            <span className="text-xs text-accent animate-pulse">{triggerMessage}</span>
          )}
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6 flex-1 min-h-0">
        {/* 左侧：工作流可视化 */}
        <section className="lg:col-span-2 overflow-y-auto">
          <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider shrink-0">
            执行链条
          </h2>
          <div className="data-card">
            {nodes.map((node, index) => (
              <WorkflowNodeComponent
                key={node.id}
                node={node}
                isLast={index === nodes.length - 1}
              />
            ))}
          </div>
        </section>

        {/* 右侧：日志控制台 */}
        <section className="lg:col-span-3 flex flex-col min-h-0">
          <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider shrink-0">
            Agent 交互日志
          </h2>
          <div className="data-card flex-1 flex flex-col p-0 overflow-hidden">
            {/* 日志头部 */}
            <div className="flex items-center gap-2 px-4 py-2 border-b border-panel-border bg-panel-hover shrink-0">
              <Terminal className="w-4 h-4 text-accent" />
              <span className="text-xs text-muted-foreground font-mono">/app/logs/agent-pipeline.log</span>
              <div className="flex-1" />
              <div className="flex items-center gap-1.5">
                <div className="w-2 h-2 rounded-full bg-status-running animate-pulse" />
                <span className="text-[10px] text-muted-foreground font-mono">LIVE</span>
              </div>
            </div>

            {/* 日志内容区 */}
            <div
              ref={logRef}
              className="flex-1 overflow-y-auto p-4 font-mono"
            >
              {logs.map((entry, index) => (
                <LogLine key={index} entry={entry} />
              ))}
              {/* 光标闪烁 */}
              <div className="flex items-center gap-2 mt-1">
                <span className="text-accent">$</span>
                <span className="w-2 h-4 bg-accent animate-pulse" />
              </div>
            </div>

            {/* 日志底部输入 */}
            <div className="flex items-center gap-2 px-4 py-2 border-t border-panel-border bg-panel-hover shrink-0">
              <span className="text-accent text-sm">$</span>
              <input
                type="text"
                placeholder="输入命令或查询 Agent 状态..."
                className="flex-1 bg-transparent text-sm text-foreground placeholder:text-muted-foreground focus:outline-none font-mono"
              />
              <button className="p-1.5 rounded hover:bg-panel-hover text-muted-foreground hover:text-accent transition-colors">
                <Send className="w-4 h-4" />
              </button>
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}
