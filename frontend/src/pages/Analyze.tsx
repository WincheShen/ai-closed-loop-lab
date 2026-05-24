import { useState, useEffect, useRef, useCallback } from 'react'
import { cn } from '@/lib/utils'
import {
  Search,
  Loader2,
  TrendingUp,
  TrendingDown,
  Minus,
  Sword,
  AlertTriangle,
  BarChart3,
  Activity,
  RefreshCw,
  History,
} from 'lucide-react'

// ── 类型定义 ──
type AnalysisStatus = 'pending' | 'running' | 'done' | 'error'
type Decision = 'BUY' | 'HOLD' | 'SELL'

interface AnalysisTask {
  task_id: string
  status: AnalysisStatus
  elapsed: number
  result?: StockReport
}

interface StockReport {
  symbol: string
  name: string
  report: {
    final_decision: Decision
    confidence: number
    current_price: number
    summary: string
    technical: {
      trend: string
      key_levels: { support?: number; resistance?: number }
      summary?: string
    }
    fundamental: {
      industry?: string
      pe_ttm?: number
      pb?: number
      roe?: number
      market_cap_yi?: number
      summary?: string
    }
    bull_case: string
    bear_case: string
    risk_warning?: string
    valid_until: string
  }
  metadata: {
    evaluated_at: string
    depth: string
    cache_hit: boolean
    elapsed_seconds: number
  }
}

interface HistoryItem {
  symbol: string
  name: string
  decision: Decision
  price: number
  time: string
}

const STATUS_LABEL: Record<AnalysisStatus, string> = {
  pending: '⏳ 排队中...',
  running: '🔄 AI 多智能体分析中',
  done: '✅ 分析完成',
  error: '❌ 分析失败',
}

// ── Toast Hook ──
function useToast() {
  const [toasts, setToasts] = useState<{ id: number; msg: string; type: 'success' | 'error' }[]>([])
  const show = useCallback((msg: string, type: 'success' | 'error') => {
    const id = Date.now()
    setToasts((prev) => [...prev, { id, msg, type }])
    setTimeout(() => setToasts((prev) => prev.filter((t) => t.id !== id)), 3000)
  }, [])
  return { toasts, show }
}

// ── 决策徽章 ──
function DecisionBadge({ decision, confidence }: { decision: Decision; confidence: number }) {
  const config = {
    BUY: { bg: 'bg-bullish/20', text: 'text-bullish', border: 'border-bullish/30', icon: TrendingUp },
    HOLD: { bg: 'bg-warning/20', text: 'text-warning', border: 'border-warning/30', icon: Minus },
    SELL: { bg: 'bg-status-error/20', text: 'text-status-error', border: 'border-status-error/30', icon: TrendingDown },
  }
  const { bg, text, border, icon: Icon } = config[decision]
  const pct = (confidence * 100).toFixed(0)

  return (
    <div className="flex items-center gap-3">
      <span className={cn('px-4 py-2 rounded-full text-sm font-bold border', bg, text, border)}>
        <Icon className="w-4 h-4 inline mr-1" />
        {decision}
      </span>
      <div className="flex items-center gap-2">
        <div className="w-24 h-2 bg-panel-border rounded-full overflow-hidden">
          <div
            className={cn('h-full rounded-full transition-all duration-500', text.replace('text-', 'bg-'))}
            style={{ width: `${pct}%` }}
          />
        </div>
        <span className="text-xs text-muted-foreground">置信度 {pct}%</span>
      </div>
    </div>
  )
}

// ── 主页面 ──
export default function Analyze() {
  const [symbol, setSymbol] = useState('')
  const [depth, setDepth] = useState('deep')
  const [task, setTask] = useState<AnalysisTask | null>(null)
  const [report, setReport] = useState<StockReport | null>(null)
  const [analyzing, setAnalyzing] = useState(false)
  const [history, setHistory] = useState<HistoryItem[]>([])
  const pollTimer = useRef<ReturnType<typeof setInterval> | null>(null)
  const { toasts, show } = useToast()

  // 从 localStorage 加载历史
  useEffect(() => {
    const saved = localStorage.getItem('analysis_history')
    if (saved) {
      try {
        setHistory(JSON.parse(saved))
      } catch { /* ignore */ }
    }
  }, [])

  // 保存历史
  const saveHistory = useCallback((item: HistoryItem) => {
    setHistory((prev) => {
      const filtered = prev.filter((h) => h.symbol !== item.symbol)
      const next = [item, ...filtered].slice(0, 20)
      localStorage.setItem('analysis_history', JSON.stringify(next))
      return next
    })
  }, [])

  // 开始分析
  const doAnalyze = async (forceRefresh = false) => {
    if (!symbol.trim()) { show('请输入股票代码', 'error'); return }

    setAnalyzing(true)
    setReport(null)
    setTask({ task_id: '', status: 'pending', elapsed: 0 })

    try {
      const resp = await fetch('/api/stock/analyze', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ symbol: symbol.trim(), depth, force_refresh: forceRefresh }),
      })
      if (!resp.ok) {
        const err = await resp.json().catch(() => ({}))
        throw new Error(err.detail || `HTTP ${resp.status}`)
      }
      const { task_id } = await resp.json()
      startPolling(task_id)
    } catch (e: any) {
      show('提交失败: ' + e.message, 'error')
      setAnalyzing(false)
      setTask(null)
    }
  }

  // 轮询任务状态
  const startPolling = (taskId: string) => {
    if (pollTimer.current) clearInterval(pollTimer.current)

    const poll = async () => {
      try {
        const resp = await fetch(`/api/stock/task/${taskId}`)
        if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
        const data = await resp.json()

        setTask({ task_id: taskId, status: data.status, elapsed: data.elapsed })

        if (data.status === 'done') {
          if (pollTimer.current) clearInterval(pollTimer.current)
          setAnalyzing(false)
          setReport(data.result)
          saveHistory({
            symbol: data.result.symbol,
            name: data.result.name,
            decision: data.result.report.final_decision,
            price: data.result.report.current_price,
            time: new Date().toISOString(),
          })
          show(`${data.result.name}(${data.result.symbol}) 分析完成`, 'success')
        } else if (data.status === 'error') {
          if (pollTimer.current) clearInterval(pollTimer.current)
          setAnalyzing(false)
          setTask(null)
          show('分析失败: ' + data.error, 'error')
        }
      } catch {
        // 网络抖动不中断轮询
      }
    }

    pollTimer.current = setInterval(poll, 3000)
    poll()
  }

  // 清理
  useEffect(() => {
    return () => { if (pollTimer.current) clearInterval(pollTimer.current) }
  }, [])

  const truncate = (text: string, max: number) => {
    if (!text) return ''
    return text.length > max ? text.substring(0, max) + '...' : text
  }

  return (
    <div className="space-y-6 relative">
      {/* Toast */}
      <div className="fixed top-20 right-6 z-50 space-y-2">
        {toasts.map((t) => (
          <div key={t.id} className={cn(
            'px-4 py-3 rounded-lg text-sm font-medium shadow-lg animate-fade-in',
            t.type === 'success' ? 'bg-bullish/90 text-white' : 'bg-status-error/90 text-white'
          )}>{t.msg}</div>
        ))}
      </div>

      {/* 页面标题 */}
      <div>
        <h1 className="text-2xl font-bold text-foreground">个股深度分析</h1>
        <p className="text-sm text-muted-foreground mt-1">
          输入股票代码 → TradingAgents 多智能体辩论 → 生成投资建议
        </p>
      </div>

      {/* 输入区 */}
      <div className="data-card">
        <h2 className="text-sm font-semibold text-foreground mb-4 flex items-center gap-2">
          <Search className="w-4 h-4 text-accent" />
          分析请求
        </h2>
        <div className="flex items-end gap-3 flex-wrap">
          <div className="flex-1 min-w-[200px]">
            <label className="block text-sm text-muted-foreground mb-2">股票代码</label>
            <input
              type="text"
              value={symbol}
              onChange={(e) => setSymbol(e.target.value)}
              onKeyDown={(e) => e.key === 'Enter' && doAnalyze()}
              placeholder="如 600519、000001、688981"
              className="w-full bg-panel-hover border border-panel-border rounded-lg px-3 py-2 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-accent/50"
            />
          </div>
          <div className="w-40">
            <label className="block text-sm text-muted-foreground mb-2">分析深度</label>
            <select
              value={depth}
              onChange={(e) => setDepth(e.target.value)}
              className="w-full bg-panel-hover border border-panel-border rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none focus:border-accent/50"
            >
              <option value="deep">深度分析</option>
              <option value="quick">快速分析</option>
            </select>
          </div>
          <div className="flex gap-2">
            <button
              onClick={() => doAnalyze()}
              disabled={analyzing}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent/10 text-accent text-sm font-medium hover:bg-accent/20 transition-colors border border-accent/20 disabled:opacity-50"
            >
              {analyzing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Activity className="w-4 h-4" />}
              分析
            </button>
            <button
              onClick={() => doAnalyze(true)}
              disabled={analyzing}
              className="flex items-center gap-2 px-4 py-2 rounded-lg bg-panel-hover text-muted-foreground text-sm hover:text-foreground transition-colors border border-panel-border disabled:opacity-50"
            >
              <RefreshCw className="w-4 h-4" />
              强制刷新
            </button>
          </div>
        </div>
      </div>

      {/* 报告展示区 */}
      <div>
        {/* 空状态 */}
        {!analyzing && !report && !task && (
          <div className="data-card text-center py-16">
            <BarChart3 className="w-16 h-16 mx-auto text-muted-foreground opacity-20 mb-4" />
            <p className="text-muted-foreground mb-2">输入股票代码并点击"分析"，AI 将生成深度投资报告</p>
            <p className="text-xs text-muted-foreground opacity-60">
              基于 TradingAgents 多智能体系统：研究员调研 → 多空辩论 → 风控评估 → 交易决策
            </p>
          </div>
        )}

        {/* 加载状态 */}
        {analyzing && task && (
          <div className="data-card text-center py-12">
            <Loader2 className="w-8 h-8 animate-spin text-accent mx-auto mb-4" />
            <p className="text-foreground font-medium mb-2">{STATUS_LABEL[task.status]}</p>
            {task.elapsed > 0 && (
              <p className="text-xs text-muted-foreground">已等待 {task.elapsed}s（深度分析通常需要 30-60 秒）</p>
            )}
          </div>
        )}

        {/* 报告内容 */}
        {report && (
          <div className="space-y-4">
            {/* 报告头部 */}
            <div className="data-card">
              <div className="flex items-start justify-between flex-wrap gap-4 mb-4">
                <div>
                  <div className="flex items-baseline gap-3">
                    <h2 className="text-2xl font-bold text-foreground">{report.name}</h2>
                    <span className="text-sm text-muted-foreground font-mono">{report.symbol}</span>
                    <span className="text-2xl font-bold text-foreground font-mono">
                      ¥{report.report.current_price.toFixed(2)}
                    </span>
                  </div>
                  <div className="mt-3">
                    <DecisionBadge
                      decision={report.report.final_decision}
                      confidence={report.report.confidence}
                    />
                  </div>
                </div>
              </div>

              {/* 摘要 */}
              <div className="p-4 rounded-lg bg-accent/5 border border-accent/20 text-sm text-accent/80 leading-relaxed">
                {report.report.summary}
              </div>

              {/* 技术面 + 基本面 */}
              <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mt-4">
                {/* 技术面 */}
                <div>
                  <h3 className="text-sm font-semibold text-foreground mb-3 flex items-center gap-2">
                    <BarChart3 className="w-4 h-4 text-accent" />
                    技术面分析
                  </h3>
                  <div className="mb-2 text-sm">
                    <span className="text-muted-foreground">趋势：</span>
                    <strong className="text-foreground">{report.report.technical.trend}</strong>
                  </div>
                  <div className="flex gap-3 mb-3">
                    <span className="px-3 py-1.5 rounded-lg bg-bullish/10 text-bullish text-xs border border-bullish/20">
                      支撑 ¥{report.report.technical.key_levels.support ?? '-'}
                    </span>
                    <span className="px-3 py-1.5 rounded-lg bg-status-error/10 text-status-error text-xs border border-status-error/20">
                      阻力 ¥{report.report.technical.key_levels.resistance ?? '-'}
                    </span>
                  </div>
                  {report.report.technical.summary && (
                    <p className="text-xs text-muted-foreground leading-relaxed">
                      {truncate(report.report.technical.summary, 500)}
                    </p>
                  )}
                </div>

                {/* 基本面 */}
                <div>
                  <h3 className="text-sm font-semibold text-foreground mb-3 flex items-center gap-2">
                    <Activity className="w-4 h-4 text-accent" />
                    基本面分析
                  </h3>
                  <div className="grid grid-cols-3 gap-2 mb-3">
                    {[
                      { label: '行业', value: report.report.fundamental.industry ?? '-', small: true },
                      { label: 'PE(TTM)', value: report.report.fundamental.pe_ttm ? report.report.fundamental.pe_ttm.toFixed(1) : '-' },
                      { label: 'PB', value: report.report.fundamental.pb ? report.report.fundamental.pb.toFixed(2) : '-' },
                      { label: 'ROE', value: report.report.fundamental.roe ? report.report.fundamental.roe.toFixed(1) + '%' : '-' },
                      { label: '市值(亿)', value: report.report.fundamental.market_cap_yi ? report.report.fundamental.market_cap_yi.toFixed(0) : '-' },
                    ].map((m) => (
                      <div key={m.label} className="text-center p-2 rounded-lg bg-panel-hover border border-panel-border">
                        <div className="text-[10px] text-muted-foreground">{m.label}</div>
                        <div className={cn('text-sm font-semibold text-foreground mt-0.5', m.small && 'text-[11px]')}>{m.value}</div>
                      </div>
                    ))}
                  </div>
                  {report.report.fundamental.summary && (
                    <p className="text-xs text-muted-foreground leading-relaxed">
                      {truncate(report.report.fundamental.summary, 500)}
                    </p>
                  )}
                </div>
              </div>

              {/* 多空辩论 */}
              <div className="mt-4">
                <h3 className="text-sm font-semibold text-foreground mb-3 flex items-center gap-2">
                  <Sword className="w-4 h-4 text-accent" />
                  多空辩论
                </h3>
                <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
                  <div className="p-4 rounded-lg bg-bullish/5 border-l-4 border-bullish">
                    <div className="text-xs font-semibold text-bullish mb-2 flex items-center gap-1">
                      <TrendingUp className="w-3.5 h-3.5" />
                      多方观点
                    </div>
                    <p className="text-xs text-foreground/80 leading-relaxed">{report.report.bull_case}</p>
                  </div>
                  <div className="p-4 rounded-lg bg-status-error/5 border-l-4 border-status-error">
                    <div className="text-xs font-semibold text-status-error mb-2 flex items-center gap-1">
                      <TrendingDown className="w-3.5 h-3.5" />
                      空方观点
                    </div>
                    <p className="text-xs text-foreground/80 leading-relaxed">{report.report.bear_case}</p>
                  </div>
                </div>
              </div>

              {/* 风险提示 */}
              {report.report.risk_warning && (
                <div className="mt-4 p-3 rounded-lg bg-warning/5 border border-warning/20 flex items-start gap-2">
                  <AlertTriangle className="w-4 h-4 text-warning shrink-0 mt-0.5" />
                  <p className="text-xs text-warning/80">{report.report.risk_warning}</p>
                </div>
              )}

              {/* 元信息 */}
              <div className="flex flex-wrap gap-4 mt-4 pt-4 border-t border-panel-border text-[11px] text-muted-foreground">
                <span>分析时间: {new Date(report.metadata.evaluated_at).toLocaleString('zh-CN')}</span>
                <span>深度: {report.metadata.depth}</span>
                <span>{report.metadata.cache_hit ? '📦 缓存命中' : '🔄 实时分析'}</span>
                <span>耗时: {report.metadata.elapsed_seconds.toFixed(1)}s</span>
                <span>有效期至: {report.report.valid_until}</span>
              </div>
            </div>
          </div>
        )}
      </div>

      {/* 历史记录 */}
      <div className="data-card">
        <h2 className="text-sm font-semibold text-foreground mb-4 flex items-center gap-2">
          <History className="w-4 h-4 text-accent" />
          历史分析记录
        </h2>
        {history.length === 0 ? (
          <p className="text-sm text-muted-foreground text-center py-4">分析过的股票将显示在这里</p>
        ) : (
          <div className="space-y-2">
            {history.map((h) => {
              const decConfig = {
                BUY: { bg: 'bg-bullish/10', text: 'text-bullish', border: 'border-bullish/20' },
                HOLD: { bg: 'bg-warning/10', text: 'text-warning', border: 'border-warning/20' },
                SELL: { bg: 'bg-status-error/10', text: 'text-status-error', border: 'border-status-error/20' },
              }
              const dec = decConfig[h.decision]
              const time = new Date(h.time).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })

              return (
                <div
                  key={h.symbol}
                  onClick={() => { setSymbol(h.symbol); doAnalyze() }}
                  className="flex items-center justify-between p-3 rounded-lg bg-panel-hover border border-panel-border hover:border-accent/30 transition-all cursor-pointer"
                >
                  <div className="flex items-center gap-3">
                    <strong className="text-sm text-foreground">{h.name}</strong>
                    <span className="text-xs text-muted-foreground font-mono">{h.symbol}</span>
                    <span className={cn('text-[10px] px-2 py-0.5 rounded border', dec.bg, dec.text, dec.border)}>
                      {h.decision}
                    </span>
                    <span className="text-sm text-foreground font-mono">¥{h.price.toFixed(2)}</span>
                  </div>
                  <span className="text-xs text-muted-foreground">{time}</span>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
