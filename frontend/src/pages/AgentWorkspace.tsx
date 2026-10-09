import { useState, useRef, useEffect, useMemo } from 'react'
import { cn, formatPercent } from '@/lib/utils'
import {
  Play,
  RotateCcw,
  Bot,
  BrainCircuit,
  FileText,
  CheckCircle2,
  XCircle,
  Clock,
  Terminal,
  Sparkles,
  GitBranch,
  ChevronDown,
  Activity,
  ShieldCheck,
  Radar,
  Zap,
  CalendarClock,
  Filter,
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
  eventType: string
  message: string
  details?: any
  sessionId?: string
}

interface EventRecord {
  id: string
  agent: string
  event_type: string
  created_at: string
  session_id: string
  payload: any
}

interface WorkflowRun {
  session_id: string
  job: string | null
  agents: string[]
  started_at: string
  ended_at: string
  event_count: number
  status: 'ok' | 'failed'
  duration_s: number | null
  summary: Record<string, any>
  steps?: EventRecord[]
}

// ── Job / Agent 中文名映射 ──
const JOB_LABELS: Record<string, { label: string; icon: React.ElementType }> = {
  hard_scan: { label: '硬扫描 (止损/条件单)', icon: ShieldCheck },
  intraday_review: { label: '盘中持仓复审', icon: Radar },
  market_brain_only: { label: 'MarketBrain Regime 判定', icon: BrainCircuit },
  regime_drift_check: { label: 'Regime 漂移检测', icon: Activity },
  closing_analysis: { label: '收盘分析', icon: FileText },
  daily_pipeline: { label: '每日完整管线 (LLM)', icon: GitBranch },
  health_check: { label: '健康检查', icon: CheckCircle2 },
  weekly_feedback: { label: '周复盘', icon: RotateCcw },
  morning_pulse: { label: '早盘脉冲止盈/止损', icon: Zap },
  force_close_ultra_short: { label: '隔夜强制清仓', icon: Zap },
  tail_hunt: { label: '尾盘狙击建仓', icon: Zap },
  short_term_watchlist_audit: { label: '短线自选池维护', icon: Radar },
  watchlist_check: { label: '自选股池检查', icon: Radar },
  sync_sma_status: { label: 'SMA 状态同步', icon: FileText },
  backfill_attributions: { label: '归因补跑', icon: FileText },
  stale_position_check: { label: '超期持仓复审', icon: Clock },
}

const AGENT_LABELS: Record<string, string> = {
  scheduler: '调度器',
  market_brain: 'MarketBrain',
  explorer: 'Explorer',
  strategist: 'Strategist',
  risk_governor: '风控官',
  executioner: '执行器',
  reviewer: '复审员',
  hard_scan: '硬扫描',
  tail_hunt: '尾盘狙击',
  influencer: '内容官',
  event_bus: '事件总线',
}

// ── 从事件生成可读消息 ──
function describeEvent(eventType: string, payload: any): { message: string; level: LogEntry['level'] } {
  const p = payload || {}
  let level: LogEntry['level'] = 'info'
  let message = eventType

  if (eventType.includes('error') || eventType.includes('fail')) level = 'error'
  else if (eventType.includes('complete') || eventType.includes('generated') || eventType === 'job_finished') level = 'success'
  else if (eventType.includes('warning') || eventType.includes('drift')) level = 'warning'

  switch (eventType) {
    case 'job_started':
      message = `任务启动: ${JOB_LABELS[p.job]?.label || p.job}`
      break
    case 'job_finished': {
      const extras = Object.entries(p)
        .filter(([k]) => !['job', 'duration_s'].includes(k))
        .map(([k, v]) => `${k}=${v}`)
        .slice(0, 4)
        .join(' ')
      message = `任务完成: ${JOB_LABELS[p.job]?.label || p.job} | ${p.duration_s}s ${extras}`
      break
    }
    case 'job_failed':
      message = `任务失败: ${JOB_LABELS[p.job]?.label || p.job} | ${p.error || ''}`
      level = 'error'
      break
    case 'regime_generated':
      message = `Regime 判定: ${p.regime} | posture=${p.posture} | 仓位上限=${formatPercent(p.max_position_pct || 0)} | 数据=${p.data_quality}${p.is_mock ? ' (MOCK!)' : ''}`
      if (p.is_mock) level = 'warning'
      break
    case 'scan_complete':
      message = `全市场扫描完成: 池 ${p.universe_size ?? '?'} 只 → 候选 ${p.candidate_count} 只 | 热点: ${(p.top_sectors || []).slice(0, 3).join(', ')}`
      break
    case 'signals_generated': {
      const syms = (p.signals || []).map((s: any) => `${s.symbol}(${s.strategy || ''})`).join(', ')
      message = `LLM 深度分析: 分析 ${p.analyzed} 只 → 买入信号 ${p.buy_count} 条${syms ? ` | ${syms}` : ''}`
      break
    }
    case 'risk_review_complete':
      message = `风控裁决: 输入 ${p.signal_count} 条 → ✓${p.approve} △${p.reduce} ✗${p.reject} | regime=${p.regime} posture=${p.posture}`
      if ((p.reject || 0) > 0) level = 'warning'
      break
    case 'hard_scan_complete': {
      const d = (p.details || []).map((x: any) => `${x.symbol} ${x.action}`).join(', ')
      message = `硬扫描[${p.persona_id || 'all'}]: 持仓 ${p.open_positions} | 硬退出 ${p.hard_exits} | 条件单触发 ${p.pending_triggered}${d ? ` | ${d}` : ''}`
      if ((p.hard_exits || 0) > 0) level = 'warning'
      break
    }
    case 'intraday_review_complete': {
      const acts = (p.reviews || []).filter((r: any) => r.action !== 'HOLD')
      message = `盘中复审[${p.persona_id || 'all'}]: ${p.total_positions} 只持仓 → ${p.actions_taken} 个动作${acts.length ? ` | ${acts.map((r: any) => `${r.symbol}:${r.action}`).join(', ')}` : ''}`
      break
    }
    case 'tail_hunt_complete':
      message = p.executed
        ? `尾盘狙击成交: ${p.symbol} ${p.name || ''} @${p.fill_price} ×${p.quantity} | 形态=${p.setup}`
        : `尾盘狙击未建仓: ${p.reason} | 池=${p.pool_size ?? 0} 只`
      if (p.executed) level = 'success'
      break
    case 'morning_pulse_complete':
      message = `早盘脉冲: 检查 ${p.checked} 只持仓 → ${(p.actions || []).length} 个动作`
      if ((p.actions || []).length > 0) level = 'warning'
      break
    case 'force_close_complete':
      message = `隔夜强制清仓: 平仓 ${p.closed} 只${(p.items || []).length ? ` | ${p.items.map((i: any) => i.symbol).join(', ')}` : ''}`
      if ((p.closed || 0) > 0) level = 'warning'
      break
    case 'order_fill':
      message = `成交: ${p.symbol} ${p.side || ''} @${p.avg_price ?? p.price} ×${p.quantity}`
      level = 'success'
      break
    case 'node_failed':
      message = `节点失败: ${p.error || 'unknown'}`
      level = 'error'
      break
    case 'closing_analysis_generated':
      message = `收盘分析已生成${p.dispatched ? '并发帖' : ''}`
      break
    case 'scheduler_started':
      message = `调度器启动，注册 ${p.job_count} 个任务`
      break
    case 'stale_position_detected':
      message = `发现超期持仓: ${p.symbol || ''} ${p.reason || ''}`
      level = 'warning'
      break
    case 'regime_drift_detected':
      message = `Regime 漂移: ${p.from_regime} → ${p.to_regime} (severity=${p.severity})`
      level = 'warning'
      break
    default:
      if (p.reason) message = `${eventType}: ${p.reason}`
      else if (p.symbol) message = `${eventType}: ${p.symbol}`
  }
  return { message, level }
}

// ── 从运行 steps 提取 pipeline 节点状态 ──
const PIPELINE_NODE_DEFS: Omit<WorkflowNode, 'status' | 'duration' | 'output'>[] = [
  { id: 'fetch', label: '获取金融数据', description: 'AKShare/Sina 行情快照', icon: BrainCircuit },
  { id: 'regime', label: '市场 Regime 判定', description: 'LLM 判定 bull/neutral/bear', icon: Sparkles },
  { id: 'scan', label: '全市场扫描', description: '规则引擎粗筛候选票', icon: Radar },
  { id: 'strategy', label: '策略决策', description: 'Strategist LLM 逐票分析', icon: Bot },
  { id: 'risk', label: '风控审查', description: 'RiskGovernor 仓位/止损裁决', icon: ShieldCheck },
  { id: 'execute', label: '执行交易', description: 'ExecutionEngine 下单撮合', icon: Play },
  { id: 'content', label: '内容生成', description: '复盘笔记', icon: FileText },
]

function stepsToPipelineNodes(steps: EventRecord[]): WorkflowNode[] {
  const status: Record<string, NodeStatus> = {}
  const output: Record<string, string> = {}
  PIPELINE_NODE_DEFS.forEach(n => { status[n.id] = 'pending' })

  for (const ev of steps) {
    const t = ev.event_type
    const p = ev.payload || {}
    if (t === 'regime_generated') {
      status['fetch'] = 'completed'
      status['regime'] = 'completed'
      output['regime'] = `${p.regime} / ${p.posture}`
    } else if (t === 'scan_complete') {
      status['scan'] = 'completed'
      output['scan'] = `${p.universe_size ?? '?'} → ${p.candidate_count} 只`
    } else if (t === 'signals_generated') {
      status['strategy'] = 'completed'
      output['strategy'] = `${p.analyzed} 只分析 → ${p.buy_count} 信号`
    } else if (t === 'risk_review_complete') {
      status['risk'] = 'completed'
      output['risk'] = `✓${p.approve} △${p.reduce} ✗${p.reject}`
    } else if (t === 'order_fill') {
      status['execute'] = 'completed'
      output['execute'] = `${p.symbol} ${p.side || ''} @${p.avg_price ?? p.price}`
    } else if (t === 'closing_analysis_generated' || t.includes('influencer')) {
      status['content'] = 'completed'
    } else if (t === 'node_failed') {
      // 标记所有 pending 中最早的一个为 error 不易判定，统一打在日志里
    }
  }

  return PIPELINE_NODE_DEFS.map(n => ({ ...n, status: status[n.id], output: output[n.id] }))
}

// ── 子组件：工作流节点 ──
function WorkflowNodeComponent({ node, isLast }: { node: WorkflowNode; isLast: boolean }) {
  const Icon = node.icon
  const statusConfig = {
    pending: { color: 'text-muted-foreground', bg: 'bg-panel-border', border: 'border-panel-border' },
    running: { color: 'text-accent', bg: 'bg-accent/20', border: 'border-accent animate-pulse' },
    completed: { color: 'text-bullish', bg: 'bg-bullish/20', border: 'border-bullish/50' },
    error: { color: 'text-bearish', bg: 'bg-bearish/20', border: 'border-bearish/50' },
  }
  const s = statusConfig[node.status]

  return (
    <div className="relative flex items-start gap-3">
      {!isLast && <div className="absolute left-4 top-8 w-px h-8 bg-panel-border" />}
      <div className={cn('relative z-10 w-8 h-8 rounded-lg flex items-center justify-center shrink-0 border-2 transition-all', s.bg, s.border)}>
        <Icon className={cn('w-4 h-4', s.color)} />
      </div>
      <div className="flex-1 pb-5">
        <div className="flex items-center gap-2">
          <span className="text-xs font-medium text-foreground">{node.label}</span>
          {node.status === 'completed' && <CheckCircle2 className="w-3.5 h-3.5 text-bullish" />}
          {node.status === 'error' && <XCircle className="w-3.5 h-3.5 text-bearish" />}
        </div>
        {node.output && (
          <span className="text-[10px] font-mono text-accent">→ {node.output}</span>
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
        <span className={cn('shrink-0 w-16', levelColors[entry.level])}>
          [{entry.level.toUpperCase()}]
        </span>
        <span className="text-accent/70 shrink-0 w-24 truncate">{entry.agent}</span>
        <span className="text-foreground flex-1 break-all">{entry.message}</span>
        {entry.details && Object.keys(entry.details).length > 0 && (
          <ChevronDown className={cn('w-4 h-4 text-muted-foreground transition-transform shrink-0', expanded && 'rotate-180')} />
        )}
      </div>
      {expanded && entry.details && (
        <div className="px-2 pb-2 pl-14">
          <div className="p-2 rounded bg-panel-hover border border-panel-border">
            {entry.sessionId && (
              <div className="text-[10px] text-muted-foreground mb-1 font-mono">session: {entry.sessionId}</div>
            )}
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
  const [runs, setRuns] = useState<WorkflowRun[]>([])
  const [logs, setLogs] = useState<LogEntry[]>([])
  const [selectedDate, setSelectedDate] = useState(() => new Date().toISOString().slice(0, 10))
  const [agentFilter, setAgentFilter] = useState<string>('all')
  const [expandedAll, setExpandedAll] = useState(false)
  const logRef = useRef<HTMLDivElement>(null)

  const isToday = selectedDate === new Date().toISOString().slice(0, 10)

  // 轮询数据
  useEffect(() => {
    const fetchAll = async () => {
      try {
        const [runsResp, evResp] = await Promise.all([
          fetch(`/api/workflow/runs?date=${selectedDate}&limit=200`),
          fetch(`/api/events/recent?date=${selectedDate}&limit=300`),
        ])
        if (runsResp.ok) setRuns(await runsResp.json())
        if (evResp.ok) {
          const data: EventRecord[] = await evResp.json()
          const mapped = [...data].reverse().map(ev => {
            const { message, level } = describeEvent(ev.event_type, ev.payload)
            return {
              timestamp: ev.created_at.slice(11, 19),
              level,
              agent: AGENT_LABELS[ev.agent] || ev.agent,
              eventType: ev.event_type,
              message,
              details: ev.payload,
              sessionId: ev.session_id,
            } as LogEntry
          })
          setLogs(mapped)
        }
      } catch (err) {
        console.error('Failed to fetch workflow data:', err)
      }
    }
    fetchAll()
    if (!isToday) return
    const interval = setInterval(fetchAll, 5000)
    return () => clearInterval(interval)
  }, [selectedDate, isToday])

  const filteredLogs = useMemo(
    () => agentFilter === 'all' ? logs : logs.filter(l => l.agent === agentFilter),
    [logs, agentFilter],
  )

  // 新日志到达时滚到底部
  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight
    }
  }, [filteredLogs.length])

  const agentOptions = useMemo(
    () => ['all', ...new Set(logs.map(l => l.agent))],
    [logs],
  )

  const failedCount = runs.filter(r => r.status === 'failed').length
  const lastRun = runs[0]

  return (
    <div className="space-y-6 h-full flex flex-col">
      {/* 页面标题 */}
      <div className="flex items-center justify-between shrink-0 flex-wrap gap-3">
        <div>
          <h1 className="text-2xl font-bold text-foreground">运行监控</h1>
          <p className="text-sm text-muted-foreground mt-1">
            调度任务时间线 · 各 Agent 决策日志
          </p>
        </div>
        <div className="flex items-center gap-3">
          <input
            type="date"
            value={selectedDate}
            onChange={e => setSelectedDate(e.target.value)}
            className="px-3 py-1.5 rounded-lg bg-panel-hover border border-panel-border text-sm text-foreground"
          />
          <div className="flex items-center gap-2 px-3 py-1.5 rounded-lg bg-panel-hover border border-panel-border">
            <CalendarClock className="w-3.5 h-3.5 text-accent" />
            <span className="text-xs text-muted-foreground">运行: {runs.length}</span>
            <div className="w-px h-4 bg-panel-border" />
            <XCircle className="w-3.5 h-3.5 text-bearish" />
            <span className="text-xs text-muted-foreground">失败: {failedCount}</span>
            {isToday && (
              <>
                <div className="w-px h-4 bg-panel-border" />
                <div className="w-2 h-2 rounded-full bg-status-running animate-pulse" />
                <span className="text-[10px] text-muted-foreground font-mono">LIVE</span>
              </>
            )}
          </div>
        </div>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-5 gap-6 flex-1 min-h-0">
        {/* 左侧：今日运行时间线 */}
        <section className="lg:col-span-2 flex flex-col min-h-0">
          <div className="flex items-center justify-between mb-3 shrink-0">
            <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
              运行时间线
            </h2>
            <button
              onClick={() => setExpandedAll(!expandedAll)}
              className="text-[11px] text-accent hover:text-accent/80"
            >
              {expandedAll ? '收起全部' : '展开全部'}
            </button>
          </div>
          {lastRun && (
            <div className="mb-3 px-3 py-2 rounded-lg bg-accent/5 border border-accent/20 text-[11px] font-mono text-muted-foreground shrink-0">
              最近: {lastRun.started_at.slice(11, 19)} · {JOB_LABELS[lastRun.job || '']?.label || lastRun.job || lastRun.session_id}
            </div>
          )}
          <div className="flex-1 overflow-y-auto space-y-2 pr-1">
            {runs.length === 0 && (
              <div className="data-card text-sm text-muted-foreground">
                {isToday ? '今日尚无运行记录（非交易时段或调度器未启动）' : '该日期无运行记录'}
              </div>
            )}
            {runs.map(run => (
              <RunCard key={run.session_id} run={run} forceExpand={expandedAll} />
            ))}
          </div>
        </section>

        {/* 右侧：全量事件日志 */}
        <section className="lg:col-span-3 flex flex-col min-h-0">
          <div className="flex items-center justify-between mb-3 shrink-0">
            <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
              事件日志 ({filteredLogs.length})
            </h2>
            <div className="flex items-center gap-1.5">
              <Filter className="w-3.5 h-3.5 text-muted-foreground" />
              <select
                value={agentFilter}
                onChange={e => setAgentFilter(e.target.value)}
                className="bg-panel-hover border border-panel-border rounded px-2 py-1 text-xs text-foreground"
              >
                {agentOptions.map(a => (
                  <option key={a} value={a}>{a === 'all' ? '全部 Agent' : a}</option>
                ))}
              </select>
            </div>
          </div>
          <div className="data-card flex-1 flex flex-col p-0 overflow-hidden">
            <div className="flex items-center gap-2 px-4 py-2 border-b border-panel-border bg-panel-hover shrink-0">
              <Terminal className="w-4 h-4 text-accent" />
              <span className="text-xs text-muted-foreground font-mono">central_brain.events</span>
              <div className="flex-1" />
              {isToday && (
                <div className="flex items-center gap-1.5">
                  <div className="w-2 h-2 rounded-full bg-status-running animate-pulse" />
                  <span className="text-[10px] text-muted-foreground font-mono">5s 轮询</span>
                </div>
              )}
            </div>
            <div ref={logRef} className="flex-1 overflow-y-auto p-4 font-mono">
              {filteredLogs.length === 0 && (
                <div className="text-xs text-muted-foreground">无事件记录</div>
              )}
              {filteredLogs.map((entry, index) => (
                <LogLine key={index} entry={entry} />
              ))}
            </div>
          </div>
        </section>
      </div>
    </div>
  )
}

// ── 子组件：运行卡片（支持"展开全部"外部控制） ──
function RunCard({ run, forceExpand }: { run: WorkflowRun; forceExpand: boolean }) {
  const [expanded, setExpanded] = useState(false)
  useEffect(() => { setExpanded(forceExpand) }, [forceExpand])

  const meta = run.job ? JOB_LABELS[run.job] : null
  const Icon = meta?.icon || Bot
  const title = meta?.label || run.job || `Session ${run.session_id.slice(0, 24)}`
  const startTime = run.started_at.slice(11, 19)

  const isPipeline = run.session_id.startsWith('daily-') || run.job === 'daily_pipeline'
  const pipelineNodes = isPipeline ? stepsToPipelineNodes(run.steps || []) : null

  return (
    <div className="border border-panel-border rounded-lg overflow-hidden">
      <div
        className="flex items-center gap-3 px-3 py-2.5 cursor-pointer hover:bg-panel-hover/60 transition-colors"
        onClick={() => setExpanded(!expanded)}
      >
        <div className={cn(
          'w-8 h-8 rounded-lg flex items-center justify-center shrink-0 border',
          run.status === 'failed' ? 'bg-bearish/15 border-bearish/40' : 'bg-accent/10 border-accent/30',
        )}>
          <Icon className={cn('w-4 h-4', run.status === 'failed' ? 'text-bearish' : 'text-accent')} />
        </div>
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-medium text-foreground truncate">{title}</span>
            {run.status === 'failed'
              ? <XCircle className="w-3.5 h-3.5 text-bearish shrink-0" />
              : <CheckCircle2 className="w-3.5 h-3.5 text-bullish shrink-0" />}
          </div>
          <div className="flex items-center gap-2 text-[10px] text-muted-foreground font-mono mt-0.5">
            <span>{startTime}</span>
            {run.duration_s != null && <span>· {run.duration_s}s</span>}
            <span>· {run.event_count} 事件</span>
          </div>
        </div>
        {Object.keys(run.summary || {}).length > 0 && (
          <span className="text-[10px] font-mono text-accent/80 truncate max-w-[200px]">
            {Object.entries(run.summary).slice(0, 4).map(([k, v]) => `${k}=${v}`).join(' ')}
          </span>
        )}
        <ChevronDown className={cn('w-4 h-4 text-muted-foreground transition-transform shrink-0', expanded && 'rotate-180')} />
      </div>

      {expanded && (
        <div className="border-t border-panel-border bg-panel/60 px-3 py-2 space-y-1">
          {pipelineNodes && (
            <div className="py-2 border-b border-panel-border/50 mb-2">
              {pipelineNodes.map((n, i) => (
                <WorkflowNodeComponent key={n.id} node={n} isLast={i === pipelineNodes.length - 1} />
              ))}
            </div>
          )}
          {(run.steps || []).length === 0 && (
            <div className="text-[11px] text-muted-foreground py-1">无明细事件</div>
          )}
          {(run.steps || []).map((ev) => {
            const { message, level } = describeEvent(ev.event_type, ev.payload)
            const levelColor = { info: 'text-accent', success: 'text-bullish', warning: 'text-warning', error: 'text-bearish' }[level]
            return (
              <div key={ev.id} className="flex items-start gap-2 py-1 font-mono text-[11px]">
                <span className="text-muted-foreground shrink-0">{ev.created_at.slice(11, 19)}</span>
                <span className="text-accent/70 shrink-0 w-20 truncate">{AGENT_LABELS[ev.agent] || ev.agent}</span>
                <span className={cn('flex-1 break-all', levelColor)}>{message}</span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
