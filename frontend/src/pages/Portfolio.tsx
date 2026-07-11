import { useState, useEffect, useCallback } from 'react'
import { cn } from '@/lib/utils'
import {
  TrendingUp,
  Briefcase,
  BarChart3,
  Eye,
  ArrowUpRight,
  ArrowDownRight,
  RefreshCw,
  Trophy,
  Clock,
  CheckCircle2,
  XCircle,
} from 'lucide-react'
import {
  ResponsiveContainer,
  AreaChart,
  Area,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from 'recharts'

// ── 类型 ──
interface Position {
  position_id: string
  symbol: string
  name: string
  entry_price: number
  current_price: number
  current_qty: number
  entry_date: string
  status: string
  realized_pnl: number
  sector: string
  closed_at: string | null
}

interface Fill {
  fill_id: string
  symbol: string
  side: string
  avg_price: number
  quantity: number
  filled_at: string
}

interface WatchlistItem {
  watch_id: string
  symbol: string
  name: string
  sector: string
  status: string
  thesis: string
  entry_condition: string
  target_price: number | null
  stop_loss: number | null
  last_price: number | null
  last_change_pct: number | null
  days_watched: number
  added_at: string
}

interface PortfolioSummary {
  open_count: number
  closed_count: number
  total_market_value: number
  total_cost: number
  total_unrealized_pnl: number
  total_realized_pnl: number
  win_count: number
  loss_count: number
  win_rate: number
  pnl_curve: { date: string; cumulative_pnl: number }[]
}

// ── 辅助 ──
function pnlColor(v: number) {
  if (v > 0) return 'text-bullish'
  if (v < 0) return 'text-bearish'
  return 'text-muted-foreground'
}
function pnlBg(v: number) {
  if (v > 0) return 'bg-bullish/10'
  if (v < 0) return 'bg-bearish/10'
  return 'bg-panel-hover'
}
function fmtPnl(v: number) {
  const s = v >= 0 ? `+${v.toFixed(2)}` : v.toFixed(2)
  return s
}
function fmtPct(entry: number, current: number) {
  if (!entry || entry === 0) return '--'
  const pct = ((current - entry) / entry) * 100
  const sign = pct >= 0 ? '+' : ''
  return `${sign}${pct.toFixed(2)}%`
}

// 人格选项
const PERSONA_OPTIONS = [
  { id: 'all', name: '全部人格' },
  { id: 'short_term_hot_rotation_v1', name: '短线热点' },
  { id: 'duan_yongping_v1', name: '段永平' },
  { id: 'warren_buffett_v1', name: '巴菲特' },
]

// ── 主页面 ──
export default function Portfolio() {
  const [summary, setSummary] = useState<PortfolioSummary | null>(null)
  const [openPositions, setOpenPositions] = useState<Position[]>([])
  const [closedPositions, setClosedPositions] = useState<Position[]>([])
  const [fills, setFills] = useState<Fill[]>([])
  const [watchlist, setWatchlist] = useState<WatchlistItem[]>([])
  const [loading, setLoading] = useState(true)
  const [activeTab, setActiveTab] = useState<'open' | 'closed' | 'fills' | 'watchlist'>('open')
  const [selectedPersona, setSelectedPersona] = useState<string>('all')

  const loadAll = useCallback(async () => {
    setLoading(true)
    try {
      const personaParam = selectedPersona !== 'all' ? `&persona_id=${selectedPersona}` : ''
      const [sumRes, openRes, closedRes, fillsRes, watchRes] = await Promise.all([
        fetch(`/api/portfolio-summary?${personaParam ? `persona_id=${selectedPersona}` : ''}`),
        fetch(`/api/positions?status=open${personaParam}`),
        fetch(`/api/positions?status=closed${personaParam}`),
        fetch(`/api/fills?limit=50${personaParam}`),
        fetch('/api/watchlist?status=watching'),
      ])
      if (sumRes.ok) setSummary(await sumRes.json())
      if (openRes.ok) setOpenPositions(await openRes.json())
      if (closedRes.ok) setClosedPositions(await closedRes.json())
      if (fillsRes.ok) setFills(await fillsRes.json())
      if (watchRes.ok) setWatchlist(await watchRes.json())
    } catch (e) {
      console.error('Portfolio load error', e)
    } finally {
      setLoading(false)
    }
  }, [selectedPersona])

  useEffect(() => {
    loadAll()
  }, [loadAll])

  const totalPnl = (summary?.total_realized_pnl ?? 0) + (summary?.total_unrealized_pnl ?? 0)

  return (
    <div className="space-y-6">
      {/* 标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">交易总览</h1>
          <p className="text-sm text-muted-foreground mt-1">
            AI Agent 持仓 · 成交 · 收益 · 自选股池
          </p>
        </div>
        <div className="flex items-center gap-3">
          {/* 人格选择器 */}
          <select
            value={selectedPersona}
            onChange={(e) => setSelectedPersona(e.target.value)}
            className="bg-panel-hover border border-panel-border rounded-lg px-3 py-1.5 text-sm text-foreground focus:outline-none focus:border-accent/50"
          >
            {PERSONA_OPTIONS.map((p) => (
              <option key={p.id} value={p.id}>{p.name}</option>
            ))}
          </select>
          <button
            onClick={loadAll}
            className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-panel-hover border border-panel-border text-xs text-muted-foreground hover:text-foreground transition-colors"
          >
            <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
            刷新
          </button>
        </div>
      </div>

      {/* 概览卡片 */}
      <div className="grid grid-cols-2 md:grid-cols-4 xl:grid-cols-6 gap-4">
        <div className="data-card">
          <div className="text-xs text-muted-foreground mb-1">当前持仓</div>
          <div className="text-2xl font-bold text-foreground">{summary?.open_count ?? '-'}</div>
        </div>
        <div className="data-card">
          <div className="text-xs text-muted-foreground mb-1">历史交易</div>
          <div className="text-2xl font-bold text-foreground">{summary?.closed_count ?? '-'}</div>
        </div>
        <div className="data-card">
          <div className="text-xs text-muted-foreground mb-1">持仓市值</div>
          <div className="text-xl font-bold text-foreground">
            ¥{(summary?.total_market_value ?? 0).toLocaleString()}
          </div>
        </div>
        <div className="data-card">
          <div className="text-xs text-muted-foreground mb-1">浮动盈亏</div>
          <div className={cn('text-xl font-bold', pnlColor(summary?.total_unrealized_pnl ?? 0))}>
            {fmtPnl(summary?.total_unrealized_pnl ?? 0)}
          </div>
        </div>
        <div className="data-card">
          <div className="text-xs text-muted-foreground mb-1">已实现盈亏</div>
          <div className={cn('text-xl font-bold', pnlColor(summary?.total_realized_pnl ?? 0))}>
            {fmtPnl(summary?.total_realized_pnl ?? 0)}
          </div>
        </div>
        <div className="data-card">
          <div className="text-xs text-muted-foreground mb-1">胜率</div>
          <div className="flex items-baseline gap-1.5">
            <span className="text-xl font-bold text-foreground">{summary?.win_rate ?? 0}%</span>
            <span className="text-[11px] text-muted-foreground">
              {summary?.win_count ?? 0}W / {summary?.loss_count ?? 0}L
            </span>
          </div>
        </div>
      </div>

      {/* 累计收益曲线 */}
      {summary?.pnl_curve && summary.pnl_curve.length > 1 && (
        <section className="data-card">
          <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider flex items-center gap-2">
            <BarChart3 className="w-4 h-4" />
            累计收益曲线
          </h2>
          <div className="h-56">
            <ResponsiveContainer width="100%" height="100%">
              <AreaChart data={summary.pnl_curve}>
                <defs>
                  <linearGradient id="pnlGrad" x1="0" y1="0" x2="0" y2="1">
                    <stop offset="5%" stopColor={totalPnl >= 0 ? '#10b981' : '#ef4444'} stopOpacity={0.3} />
                    <stop offset="95%" stopColor={totalPnl >= 0 ? '#10b981' : '#ef4444'} stopOpacity={0} />
                  </linearGradient>
                </defs>
                <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--panel-border))" />
                <XAxis
                  dataKey="date"
                  tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
                  tickLine={false}
                  axisLine={false}
                />
                <YAxis
                  tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
                  tickLine={false}
                  axisLine={false}
                  tickFormatter={(v: number) => `¥${v}`}
                />
                <Tooltip
                  contentStyle={{
                    backgroundColor: 'hsl(var(--panel))',
                    border: '1px solid hsl(var(--panel-border))',
                    borderRadius: '8px',
                    fontSize: '12px',
                  }}
                  formatter={(v) => [`¥${Number(v ?? 0).toFixed(2)}`, '累计盈亏']}
                />
                <Area
                  type="monotone"
                  dataKey="cumulative_pnl"
                  stroke={totalPnl >= 0 ? '#10b981' : '#ef4444'}
                  strokeWidth={2}
                  fill="url(#pnlGrad)"
                />
              </AreaChart>
            </ResponsiveContainer>
          </div>
        </section>
      )}

      {/* Tab 切换 */}
      <div className="flex items-center gap-1 bg-panel border border-panel-border rounded-lg p-1 w-fit">
        {([
          { key: 'open', label: '当前持仓', icon: Briefcase, count: openPositions.length },
          { key: 'closed', label: '历史交易', icon: Trophy, count: closedPositions.length },
          { key: 'fills', label: '成交记录', icon: TrendingUp, count: fills.length },
          { key: 'watchlist', label: '自选股池', icon: Eye, count: watchlist.length },
        ] as const).map((tab) => (
          <button
            key={tab.key}
            onClick={() => setActiveTab(tab.key)}
            className={cn(
              'flex items-center gap-1.5 px-3 py-1.5 rounded-md text-xs font-medium transition-colors',
              activeTab === tab.key
                ? 'bg-accent/10 text-accent'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            <tab.icon className="w-3.5 h-3.5" />
            {tab.label}
            {tab.count > 0 && (
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-panel-hover">{tab.count}</span>
            )}
          </button>
        ))}
      </div>

      {/* 当前持仓 */}
      {activeTab === 'open' && (
        <div className="data-card overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-panel-border bg-panel-hover">
                  {['标的', '持仓量', '成本价', '现价', '盈亏%', '盈亏额', '板块', '建仓日'].map((h) => (
                    <th key={h} className="text-left px-4 py-3 text-[11px] font-medium text-muted-foreground uppercase">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {openPositions.length === 0 ? (
                  <tr>
                    <td colSpan={8} className="text-center py-10 text-sm text-muted-foreground">
                      暂无持仓
                    </td>
                  </tr>
                ) : (
                  openPositions.map((p) => {
                    const pnl = (p.current_price - p.entry_price) * p.current_qty
                    return (
                      <tr key={p.position_id} className="border-b border-panel-border/50 hover:bg-panel-hover transition-colors">
                        <td className="px-4 py-3">
                          <div className="text-sm font-medium text-foreground">{p.name || p.symbol}</div>
                          <div className="text-[11px] text-muted-foreground font-mono">{p.symbol}</div>
                        </td>
                        <td className="px-4 py-3 text-sm font-mono text-foreground">{p.current_qty}</td>
                        <td className="px-4 py-3 text-sm font-mono text-foreground">{p.entry_price?.toFixed(2)}</td>
                        <td className="px-4 py-3 text-sm font-mono text-foreground">{p.current_price?.toFixed(2)}</td>
                        <td className="px-4 py-3">
                          <span className={cn('text-sm font-medium', pnlColor(pnl))}>
                            {fmtPct(p.entry_price, p.current_price)}
                          </span>
                        </td>
                        <td className="px-4 py-3">
                          <span className={cn(
                            'inline-flex items-center gap-1 text-sm font-medium px-2 py-0.5 rounded',
                            pnlBg(pnl), pnlColor(pnl),
                          )}>
                            {pnl >= 0 ? <ArrowUpRight className="w-3 h-3" /> : <ArrowDownRight className="w-3 h-3" />}
                            {fmtPnl(pnl)}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-xs text-muted-foreground">{p.sector || '-'}</td>
                        <td className="px-4 py-3 text-xs text-muted-foreground font-mono">{(p.entry_date || '').slice(0, 10)}</td>
                      </tr>
                    )
                  })
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 历史交易 */}
      {activeTab === 'closed' && (
        <div className="data-card overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-panel-border bg-panel-hover">
                  {['标的', '建仓日', '平仓日', '成本价', '已实现盈亏', '结果'].map((h) => (
                    <th key={h} className="text-left px-4 py-3 text-[11px] font-medium text-muted-foreground uppercase">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {closedPositions.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="text-center py-10 text-sm text-muted-foreground">
                      暂无历史交易
                    </td>
                  </tr>
                ) : (
                  closedPositions.map((p) => (
                    <tr key={p.position_id} className="border-b border-panel-border/50 hover:bg-panel-hover transition-colors">
                      <td className="px-4 py-3">
                        <div className="text-sm font-medium text-foreground">{p.name || p.symbol}</div>
                        <div className="text-[11px] text-muted-foreground font-mono">{p.symbol}</div>
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground font-mono">{(p.entry_date || '').slice(0, 10)}</td>
                      <td className="px-4 py-3 text-xs text-muted-foreground font-mono">{(p.closed_at || '').slice(0, 10)}</td>
                      <td className="px-4 py-3 text-sm font-mono text-foreground">{p.entry_price?.toFixed(2)}</td>
                      <td className="px-4 py-3">
                        <span className={cn('text-sm font-medium', pnlColor(p.realized_pnl))}>
                          {fmtPnl(p.realized_pnl || 0)}
                        </span>
                      </td>
                      <td className="px-4 py-3">
                        {(p.realized_pnl || 0) > 0 ? (
                          <span className="flex items-center gap-1 text-[11px] text-bullish">
                            <CheckCircle2 className="w-3.5 h-3.5" /> 盈利
                          </span>
                        ) : (
                          <span className="flex items-center gap-1 text-[11px] text-bearish">
                            <XCircle className="w-3.5 h-3.5" /> 亏损
                          </span>
                        )}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 成交记录 */}
      {activeTab === 'fills' && (
        <div className="data-card overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-panel-border bg-panel-hover">
                  {['成交ID', '标的', '方向', '均价', '数量', '成交时间'].map((h) => (
                    <th key={h} className="text-left px-4 py-3 text-[11px] font-medium text-muted-foreground uppercase">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {fills.length === 0 ? (
                  <tr>
                    <td colSpan={6} className="text-center py-10 text-sm text-muted-foreground">
                      暂无成交记录
                    </td>
                  </tr>
                ) : (
                  fills.map((f) => (
                    <tr key={f.fill_id} className="border-b border-panel-border/50 hover:bg-panel-hover transition-colors">
                      <td className="px-4 py-3 text-xs text-muted-foreground font-mono">{f.fill_id}</td>
                      <td className="px-4 py-3 text-sm font-medium text-foreground">{f.symbol}</td>
                      <td className="px-4 py-3">
                        <span className={cn(
                          'text-[11px] px-2 py-0.5 rounded font-medium',
                          f.side === 'buy'
                            ? 'bg-bullish/10 text-bullish'
                            : 'bg-bearish/10 text-bearish'
                        )}>
                          {f.side === 'buy' ? '买入' : '卖出'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-sm font-mono text-foreground">{f.avg_price?.toFixed(2)}</td>
                      <td className="px-4 py-3 text-sm font-mono text-foreground">{f.quantity}</td>
                      <td className="px-4 py-3 text-xs text-muted-foreground font-mono">
                        {new Date(f.filled_at).toLocaleString('zh-CN')}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* 自选股池 */}
      {activeTab === 'watchlist' && (
        <div className="data-card overflow-hidden p-0">
          <div className="overflow-x-auto">
            <table className="w-full">
              <thead>
                <tr className="border-b border-panel-border bg-panel-hover">
                  {['标的', '板块', '跟踪理由', '入场条件', '目标价', '止损价', '现价', '跟踪天数', '加入时间'].map((h) => (
                    <th key={h} className="text-left px-4 py-3 text-[11px] font-medium text-muted-foreground uppercase">
                      {h}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {watchlist.length === 0 ? (
                  <tr>
                    <td colSpan={9} className="text-center py-10 text-sm text-muted-foreground">
                      自选股池为空 — Agent 扫描后将自动纳入
                    </td>
                  </tr>
                ) : (
                  watchlist.map((w) => (
                    <tr key={w.watch_id} className="border-b border-panel-border/50 hover:bg-panel-hover transition-colors">
                      <td className="px-4 py-3">
                        <div className="text-sm font-medium text-foreground">{w.name || w.symbol}</div>
                        <div className="text-[11px] text-muted-foreground font-mono">{w.symbol}</div>
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground">{w.sector || '-'}</td>
                      <td className="px-4 py-3 text-xs text-foreground max-w-[180px] truncate" title={w.thesis}>
                        {w.thesis || '-'}
                      </td>
                      <td className="px-4 py-3">
                        <span className="text-[11px] font-mono px-1.5 py-0.5 rounded bg-accent/10 text-accent">
                          {w.entry_condition || '-'}
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs font-mono text-bullish">
                        {w.target_price?.toFixed(2) ?? '-'}
                      </td>
                      <td className="px-4 py-3 text-xs font-mono text-bearish">
                        {w.stop_loss?.toFixed(2) ?? '-'}
                      </td>
                      <td className="px-4 py-3">
                        <div className="text-sm font-mono text-foreground">{w.last_price?.toFixed(2) ?? '-'}</div>
                        {w.last_change_pct != null && (
                          <div className={cn('text-[11px]', pnlColor(w.last_change_pct))}>
                            {w.last_change_pct >= 0 ? '+' : ''}{w.last_change_pct.toFixed(2)}%
                          </div>
                        )}
                      </td>
                      <td className="px-4 py-3">
                        <span className="flex items-center gap-1 text-xs text-muted-foreground">
                          <Clock className="w-3 h-3" />
                          {w.days_watched}天
                        </span>
                      </td>
                      <td className="px-4 py-3 text-xs text-muted-foreground font-mono">
                        {(w.added_at || '').slice(0, 10)}
                      </td>
                    </tr>
                  ))
                )}
              </tbody>
            </table>
          </div>
        </div>
      )}
    </div>
  )
}
