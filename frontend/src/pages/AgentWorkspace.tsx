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
}

// ── 模拟数据 ──
const workflowNodes: WorkflowNode[] = [
  {
    id: 'fetch',
    label: '获取金融数据',
    description: 'AKShare + Sina 双源行情抓取',
    status: 'completed',
    icon: BrainCircuit,
    duration: '12.5s',
    output: '5511 只股票快照',
  },
  {
    id: 'regime',
    label: '市场 regime 判定',
    description: 'LLM 综合判断 bull/neutral/bear',
    status: 'completed',
    icon: Sparkles,
    duration: '8.3s',
    output: 'regime=bull, posture=selective_attack',
  },
  {
    id: 'hotsector',
    label: '热点板块推断',
    description: 'emappdata 热度榜 + 关键词聚类',
    status: 'running',
    icon: GitBranch,
    duration: '3.2s',
  },
  {
    id: 'scan',
    label: '全市场扫描',
    description: '规则引擎筛选候选票',
    status: 'pending',
    icon: Bot,
  },
  {
    id: 'strategy',
    label: '策略决策',
    description: 'Strategist LLM 深度分析',
    status: 'pending',
    icon: BrainCircuit,
  },
  {
    id: 'risk',
    label: '风控审查',
    description: 'RiskGovernor 仓位/止损检查',
    status: 'pending',
    icon: CheckCircle2,
  },
  {
    id: 'execute',
    label: '执行交易',
    description: 'Executor 模拟盘下单',
    status: 'pending',
    icon: Play,
  },
  {
    id: 'content',
    label: '生成内容',
    description: '小红书风格金融笔记',
    status: 'pending',
    icon: FileText,
  },
]

const mockLogs: LogEntry[] = [
  { timestamp: '09:35:12', level: 'info', agent: 'MarketBrain', message: '开始生成市场 regime 快照...' },
  { timestamp: '09:35:24', level: 'success', agent: 'MarketBrain', message: 'Regime 判定完成: bull | posture=selective_attack | 上限 70%' },
  { timestamp: '09:35:25', level: 'info', agent: 'AkshareClient', message: 'akshare 拉取失败，降级到 Sina API' },
  { timestamp: '09:35:45', level: 'success', agent: 'AkshareClient', message: 'Sina 行情快照: 5511 只股票（无板块数据）' },
  { timestamp: '09:36:01', level: 'info', agent: 'EmappdataDetector', message: '开始从 emappdata 推断热点板块...' },
  { timestamp: '09:36:03', level: 'success', agent: 'EmappdataDetector', message: '推断成功: 人工智能、有色金属、白酒、电力、医疗器械' },
  { timestamp: '09:36:15', level: 'info', agent: 'Explorer', message: '复用 MarketBrain 快照: 5511 只股票, 0 个板块' },
  { timestamp: '09:36:15', level: 'info', agent: 'Explorer', message: '复用传入热点板块: 人工智能、有色金属、白酒、电力、医疗器械' },
  { timestamp: '09:36:18', level: 'success', agent: 'Explorer', message: '扫描完成 — 候选票 30 只' },
  { timestamp: '09:36:20', level: 'info', agent: 'Strategist', message: '开始 LLM 深度分析 — Top 8 候选' },
  { timestamp: '09:36:35', level: 'warning', agent: 'Strategist', message: '[600519 贵州茅台] PASS — 技术面与进攻策略不匹配' },
  { timestamp: '09:36:52', level: 'warning', agent: 'Strategist', message: '[000725 京东方A] PASS — 虽然处于热点板块，但量能不足' },
  { timestamp: '09:37:08', level: 'success', agent: 'Strategist', message: 'LLM 分析完成 — 分析 8 只, 买入信号 2 条' },
  { timestamp: '09:37:10', level: 'info', agent: 'RiskGovernor', message: '审查 2 条交易信号...' },
  { timestamp: '09:37:12', level: 'success', agent: 'RiskGovernor', message: '全部通过: approve=2 reduce=0 reject=0' },
  { timestamp: '09:37:15', level: 'success', agent: 'Executor', message: '模拟盘下单完成: 成交 2 笔' },
]

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
  const levelColors = {
    info: 'text-accent',
    success: 'text-bullish',
    warning: 'text-warning',
    error: 'text-bearish',
  }

  return (
    <div className="flex items-start gap-3 py-1 font-mono text-xs">
      <span className="text-muted-foreground shrink-0">{entry.timestamp}</span>
      <span className={cn('shrink-0 w-20', levelColors[entry.level])}>
        [{entry.level.toUpperCase()}]
      </span>
      <span className="text-accent/70 shrink-0 w-28">{entry.agent}</span>
      <span className="text-foreground">{entry.message}</span>
    </div>
  )
}

// ── 主页面 ──
export default function AgentWorkspace() {
  const [nodes] = useState(workflowNodes)
  const [logs, setLogs] = useState(mockLogs)
  const logRef = useRef<HTMLDivElement>(null)

  // 自动滚动日志到底部
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [logs])

  // 轮询事件流并更新日志
  useEffect(() => {
    const fetchEvents = async () => {
      try {
        const resp = await fetch('/events/recent?limit=20')
        if (resp.ok) {
          const data = await resp.json()
          // 将事件转换为日志格式
          const newLogs: LogEntry[] = data.map((event: any) => ({
            timestamp: new Date(event.created_at).toLocaleTimeString('zh-CN', { hour12: false }),
            level: event.event_type.includes('error') ? 'error' : event.event_type.includes('success') ? 'success' : 'info',
            agent: event.event_type.split('.')[0] || 'System',
            message: event.event_type,
          }))
          if (newLogs.length > 0) {
            setLogs(newLogs)
          }
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
          <button className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent/10 text-accent text-sm font-medium hover:bg-accent/20 transition-colors border border-accent/20">
            <RotateCcw className="w-4 h-4" />
            重新执行
          </button>
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
