import { useEffect, useState } from 'react'

interface StatBlock {
  closed_trades: number
  wins: number
  losses: number
  win_rate: number | null
  realized_pnl: number
}

interface ExcludedTrade {
  position_id: string
  symbol: string
  entry_date: string
  closed_at: string
  realized_pnl: number | null
  flags: string[]
}

interface PersonaStats extends StatBlock {
  open_positions: number
  last_closed_at: string | null
  clean?: StatBlock
  excluded_count?: number
  excluded?: ExcludedTrade[]
}

const FLAG_LABELS: Record<string, string> = {
  holiday_entry: '休市日入场',
  holiday_exit: '休市日平仓',
  cost_price_exit: '成本价伪平仓',
  implausible_price: '价格超涨跌停',
}

function StatGrid({ s }: { s: StatBlock }) {
  return (
    <div className="grid grid-cols-3 gap-2 text-center">
      <div>
        <div className="text-base font-bold text-foreground">
          {s.win_rate != null ? `${(s.win_rate * 100).toFixed(1)}%` : '—'}
        </div>
        <div className="text-[10px] text-muted-foreground">胜率</div>
      </div>
      <div>
        <div className="text-base font-bold text-foreground">
          {s.wins}<span className="text-muted-foreground text-xs">/{s.closed_trades}</span>
        </div>
        <div className="text-[10px] text-muted-foreground">胜/总平仓</div>
      </div>
      <div>
        <div className={`text-base font-bold ${pnlColor(s.realized_pnl)}`}>
          {s.realized_pnl >= 0 ? '+' : ''}{s.realized_pnl.toFixed(0)}
        </div>
        <div className="text-[10px] text-muted-foreground">已实现盈亏</div>
      </div>
    </div>
  )
}

interface Persona {
  id: string
  name: string
  capital: number
  enabled: boolean
  daily_pipeline: boolean
  preferred_holding_days?: number[]
  stats?: PersonaStats
}

interface Account {
  account_id: string
  persona_id: string
  persona_name: string
  capital: number
  available_cash: number
  total_value: number
  total_pnl: number
  created_at: string
  updated_at: string
}

interface Proposal {
  proposal_id: string
  persona_id: string
  kind: string
  change_json?: string
  change?: any
  rationale: string
  evidence_json?: string
  evidence?: any
  backtest_report?: string
  status: 'proposed' | 'observe' | 'backtested' | 'approved' | 'rejected'
  reviewer?: string
  review_note?: string
  created_at: string
  reviewed_at?: string
}

function statusBadge(status: string) {
  switch (status) {
    case 'approved':
      return 'bg-emerald-500/15 text-emerald-400 border border-emerald-500/30'
    case 'rejected':
      return 'bg-rose-500/15 text-rose-400 border border-rose-500/30'
    case 'backtested':
      return 'bg-amber-500/15 text-amber-400 border border-amber-500/30'
    case 'proposed':
      return 'bg-blue-500/15 text-blue-400 border border-blue-500/30'
    default:
      return 'bg-panel-border text-muted-foreground'
  }
}

function statusLabel(status: string) {
  switch (status) {
    case 'approved': return '已批准'
    case 'rejected': return '已驳回'
    case 'backtested': return '已回测'
    case 'proposed': return '待审批'
    case 'observe': return '观察中(样本不足)'
    default: return status
  }
}

function kindLabel(kind: string) {
  switch (kind) {
    case 'param_change': return '参数微调'
    case 'binding_change': return '转盘绑定'
    case 'strategy_toggle': return '策略启停'
    case 'setup_preference': return '选股偏好'
    default: return kind
  }
}

function pnlColor(v: number) {
  return v >= 0 ? 'text-bullish' : 'text-bearish'
}

export default function Personas() {
  const [personas, setPersonas] = useState<Persona[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [proposals, setProposals] = useState<Proposal[]>([])
  const [proposalFilter, setProposalFilter] = useState<string>('all')
  const [actionLoading, setActionLoading] = useState<string | null>(null)
  const [actionMsg, setActionMsg] = useState<{ id: string; text: string; ok: boolean } | null>(null)
  const [loading, setLoading] = useState(true)

  const fetchProposals = async () => {
    try {
      const res = await fetch('/api/wheel/proposals?limit=100')
      const data = await res.json()
      setProposals(Array.isArray(data) ? data : [])
    } catch (e) {
      console.error('Failed to fetch proposals:', e)
    }
  }

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [personasRes, accountsRes, proposalsRes] = await Promise.all([
          fetch('/api/personas'),
          fetch('/api/accounts'),
          fetch('/api/wheel/proposals?limit=100')
        ])
        const personasData = await personasRes.json()
        const accountsData = await accountsRes.json()
        const proposalsData = await proposalsRes.json()
        setPersonas(personasData.personas || [])
        setAccounts(accountsData.accounts || [])
        setProposals(Array.isArray(proposalsData) ? proposalsData : [])
      } catch (error) {
        console.error('Failed to fetch personas/accounts/proposals:', error)
      } finally {
        setLoading(false)
      }
    }
    fetchData()
  }, [])

  const handleBacktest = async (id: string) => {
    setActionLoading(id)
    setActionMsg(null)
    try {
      const res = await fetch(`/api/wheel/proposals/${id}/backtest`, { method: 'POST' })
      const data = await res.json()
      if (res.ok && data.success) {
        setActionMsg({ id, text: '回测与对照检验完成', ok: true })
        await fetchProposals()
      } else {
        setActionMsg({ id, text: data.detail || '回测失败', ok: false })
      }
    } catch (e: any) {
      setActionMsg({ id, text: e.message || '网络异常', ok: false })
    } finally {
      setActionLoading(null)
    }
  }

  const handleReview = async (id: string, status: 'approved' | 'rejected') => {
    setActionLoading(id)
    setActionMsg(null)
    try {
      const res = await fetch(`/api/wheel/proposals/${id}/review`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ status, reviewer: 'admin', review_note: `Web 管理后台${status === 'approved' ? '批准' : '驳回'}` })
      })
      const data = await res.json()
      if (res.ok && data.success) {
        setActionMsg({ id, text: status === 'approved' ? '已批准' : '已驳回', ok: true })
        await fetchProposals()
      } else {
        setActionMsg({ id, text: data.detail || '审批操作失败', ok: false })
      }
    } catch (e: any) {
      setActionMsg({ id, text: e.message || '网络异常', ok: false })
    } finally {
      setActionLoading(null)
    }
  }

  const handleApply = async (id: string) => {
    setActionLoading(id)
    setActionMsg(null)
    try {
      const res = await fetch(`/api/wheel/proposals/${id}/apply`, { method: 'POST' })
      const data = await res.json()
      if (res.ok && data.success) {
        setActionMsg({ id, text: '变更已成功导出并写入 wheels.yaml', ok: true })
        await fetchProposals()
      } else {
        setActionMsg({ id, text: data.detail || '应用配置失败', ok: false })
      }
    } catch (e: any) {
      setActionMsg({ id, text: e.message || '网络异常', ok: false })
    } finally {
      setActionLoading(null)
    }
  }

  const filteredProposals = proposals.filter(p => {
    if (proposalFilter === 'all') return true
    return p.status === proposalFilter
  })

  if (loading) {
    return <div className="p-6 text-muted-foreground">加载中...</div>
  }

  return (
    <div className="space-y-6">
      <div>
        <h1 className="text-2xl font-bold text-foreground">交易人格管理</h1>
        <p className="text-sm text-muted-foreground mt-1">
          各人格资金账户 · 历史战绩 · 运行状态
        </p>
      </div>

      <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
        {personas.map((persona) => {
          const account = accounts.find(a => a.persona_id === persona.id)
          const stats = persona.stats
          return (
            <div key={persona.id} className="data-card space-y-4">
              {/* 头部：名称 + 状态标签 */}
              <div className="flex items-start justify-between">
                <div>
                  <h2 className="text-lg font-bold text-foreground">{persona.name}</h2>
                  <span className="text-[11px] font-mono text-muted-foreground">{persona.id}</span>
                </div>
                <div className="flex flex-col items-end gap-1">
                  <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${persona.enabled ? 'bg-bullish/15 text-bullish' : 'bg-panel-border text-muted-foreground'}`}>
                    {persona.enabled ? '已启用' : '已停用'}
                  </span>
                  <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${persona.daily_pipeline ? 'bg-accent/15 text-accent' : 'bg-warning/15 text-warning'}`}>
                    {persona.daily_pipeline ? 'LLM 日常管线' : '专属调度(无LLM管线)'}
                  </span>
                </div>
              </div>

              {/* 资金 */}
              <div className="space-y-1.5">
                <div className="flex justify-between text-xs">
                  <span className="text-muted-foreground">初始资金</span>
                  <span className="font-semibold text-foreground">¥{persona.capital.toLocaleString()}</span>
                </div>
                {account && (
                  <>
                    <div className="flex justify-between text-xs">
                      <span className="text-muted-foreground">可用资金</span>
                      <span className="font-semibold text-foreground">¥{account.available_cash.toLocaleString()}</span>
                    </div>
                    <div className="flex justify-between text-xs">
                      <span className="text-muted-foreground">账户总值</span>
                      <span className="font-semibold text-foreground">¥{account.total_value.toLocaleString()}</span>
                    </div>
                    <div className="flex justify-between text-xs">
                      <span className="text-muted-foreground">浮动盈亏</span>
                      <span className={`font-semibold ${pnlColor(account.total_pnl)}`}>
                        ¥{account.total_pnl.toLocaleString()} ({((account.total_pnl / Math.max(account.capital, 1)) * 100).toFixed(2)}%)
                      </span>
                    </div>
                  </>
                )}
              </div>

              {/* 战绩 */}
              <div className="border-t border-panel-border pt-3">
                <div className="text-[10px] uppercase tracking-wider text-muted-foreground mb-2">历史战绩</div>
                {stats && stats.closed_trades > 0 ? (
                  <>
                    <StatGrid s={stats.clean ?? stats} />
                    {(stats.excluded_count ?? 0) > 0 && (
                      <details className="mt-2 text-[11px] text-muted-foreground">
                        <summary className="cursor-pointer select-none">
                          已剔除 {stats.excluded_count} 笔无效成交 · 查看原始口径
                        </summary>
                        <div className="mt-2 opacity-70">
                          <div className="text-[10px] mb-1">原始口径（含无效成交）</div>
                          <StatGrid s={stats} />
                        </div>
                        <ul className="mt-2 space-y-0.5 font-mono">
                          {stats.excluded?.map(e => (
                            <li key={e.position_id} className="flex justify-between gap-2">
                              <span>{e.symbol} {e.entry_date?.slice(5, 10)}→{e.closed_at?.slice(5, 10)}</span>
                              <span>{e.flags.map(f => FLAG_LABELS[f] ?? f).join('/')}</span>
                              <span className={pnlColor(e.realized_pnl ?? 0)}>{(e.realized_pnl ?? 0).toFixed(0)}</span>
                            </li>
                          ))}
                        </ul>
                      </details>
                    )}
                  </>
                ) : (
                  <div className="text-xs text-muted-foreground">尚无平仓记录</div>
                )}
                <div className="flex justify-between text-[11px] text-muted-foreground mt-2 font-mono">
                  <span>当前持仓 {stats?.open_positions ?? 0} 只</span>
                  {stats?.last_closed_at && <span>最近平仓 {stats.last_closed_at.slice(0, 10)}</span>}
                </div>
              </div>
            </div>
          )
        })}
      </div>

      {/* Phase 4b: 复盘提议与审批通道 */}
      <div className="mt-10 space-y-4">
        <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-3 border-b border-panel-border pb-3">
          <div>
            <h2 className="text-xl font-bold text-foreground">配置变更提议与审批流 (Proposals)</h2>
            <p className="text-xs text-muted-foreground mt-0.5">
              复盘发现与元规则归纳 · 随机买点对照回测 · 审批生效闭环
            </p>
          </div>
          <div className="flex items-center gap-1.5 flex-wrap">
            {['all', 'proposed', 'backtested', 'approved', 'observe', 'rejected'].map(st => (
              <button
                key={st}
                onClick={() => setProposalFilter(st)}
                className={`text-xs px-2.5 py-1 rounded transition-colors ${
                  proposalFilter === st
                    ? 'bg-accent text-accent-foreground font-semibold'
                    : 'bg-panel-border/40 text-muted-foreground hover:text-foreground'
                }`}
              >
                {st === 'all' ? '全部' : statusLabel(st)}
              </button>
            ))}
          </div>
        </div>

        {filteredProposals.length === 0 ? (
          <div className="data-card text-center py-8 text-sm text-muted-foreground">
            暂无符合条件的复盘提议
          </div>
        ) : (
          <div className="space-y-3">
            {filteredProposals.map(p => {
              let parsedChange = p.change
              if (!parsedChange && p.change_json) {
                try { parsedChange = JSON.parse(p.change_json) } catch {}
              }
              let parsedReport: any = null
              if (p.backtest_report) {
                try { parsedReport = JSON.parse(p.backtest_report) } catch {}
              }
              const isLoading = actionLoading === p.proposal_id
              const msg = actionMsg?.id === p.proposal_id ? actionMsg : null

              return (
                <div key={p.proposal_id} className="data-card p-4 space-y-3">
                  <div className="flex flex-col sm:flex-row sm:items-center justify-between gap-2">
                    <div className="flex items-center gap-2 flex-wrap">
                      <span className="font-mono text-xs font-bold text-foreground">{p.proposal_id}</span>
                      <span className="text-[11px] font-mono text-muted-foreground">[{p.persona_id}]</span>
                      <span className="text-[10px] px-1.5 py-0.5 rounded bg-panel-border/70 text-muted-foreground">
                        {kindLabel(p.kind)}
                      </span>
                      <span className={`text-[10px] px-2 py-0.5 rounded font-medium ${statusBadge(p.status)}`}>
                        {statusLabel(p.status)}
                      </span>
                    </div>
                    <div className="text-[11px] text-muted-foreground font-mono">
                      {p.created_at ? p.created_at.slice(0, 16).replace('T', ' ') : ''}
                    </div>
                  </div>

                  <div className="text-sm text-foreground/90">
                    <span className="font-semibold text-muted-foreground text-xs mr-2">调整理由:</span>
                    {p.rationale}
                  </div>

                  {parsedChange && Object.keys(parsedChange).length > 0 && (
                    <div className="bg-background/50 rounded p-2 text-xs font-mono text-foreground/80 overflow-x-auto">
                      <span className="text-[10px] uppercase text-muted-foreground block mb-1">提议变更项</span>
                      <pre className="text-[11px]">{JSON.stringify(parsedChange, null, 2)}</pre>
                    </div>
                  )}

                  {parsedReport && (
                    <div className="bg-panel-border/20 border border-panel-border/50 rounded p-2.5 text-xs space-y-1">
                      <div className="flex items-center justify-between">
                        <span className="font-semibold text-muted-foreground text-[11px]">随机买点对照检验 (Benchmark)</span>
                        <span className={`text-[10px] px-1.5 py-0.2 rounded font-bold ${
                          parsedReport.passed ? 'bg-emerald-500/20 text-emerald-400' : 'bg-rose-500/20 text-rose-400'
                        }`}>
                          {parsedReport.passed ? '对照通过 (显著优于随机买点)' : '未显著优于随机分布'}
                        </span>
                      </div>
                      {parsedReport.benchmark && (
                        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2 text-[11px] font-mono pt-1 text-muted-foreground">
                          <div>策略均值: <span className="text-foreground font-semibold">{parsedReport.benchmark.strategy_mean_pct}%</span></div>
                          <div>随机均值: <span className="text-foreground font-semibold">{parsedReport.benchmark.random_mean_pct}%</span></div>
                          <div>超额收益: <span className="text-bullish font-semibold">+{parsedReport.benchmark.edge_pct}%</span></div>
                          <div>超额分位: <span className="text-foreground font-semibold">{(parsedReport.benchmark.percentile * 100).toFixed(0)}%</span></div>
                        </div>
                      )}
                    </div>
                  )}

                  {msg && (
                    <div className={`text-xs px-2 py-1 rounded ${msg.ok ? 'bg-emerald-500/10 text-emerald-400' : 'bg-rose-500/10 text-rose-400'}`}>
                      {msg.text}
                    </div>
                  )}

                  <div className="flex items-center gap-2 pt-1 flex-wrap">
                    {p.status === 'proposed' && (
                      <button
                        disabled={isLoading}
                        onClick={() => handleBacktest(p.proposal_id)}
                        className="text-xs px-2.5 py-1 rounded bg-amber-500/15 text-amber-400 hover:bg-amber-500/25 transition-colors disabled:opacity-50"
                      >
                        {isLoading ? '回测中...' : '运行随机买点回测'}
                      </button>
                    )}
                    {(p.status === 'proposed' || p.status === 'backtested') && (
                      <>
                        <button
                          disabled={isLoading}
                          onClick={() => handleReview(p.proposal_id, 'approved')}
                          className="text-xs px-2.5 py-1 rounded bg-emerald-500/15 text-emerald-400 hover:bg-emerald-500/25 transition-colors disabled:opacity-50 font-medium"
                        >
                          批准提议
                        </button>
                        <button
                          disabled={isLoading}
                          onClick={() => handleReview(p.proposal_id, 'rejected')}
                          className="text-xs px-2.5 py-1 rounded bg-rose-500/15 text-rose-400 hover:bg-rose-500/25 transition-colors disabled:opacity-50"
                        >
                          驳回提议
                        </button>
                      </>
                    )}
                    {p.status === 'approved' && (
                      <button
                        disabled={isLoading}
                        onClick={() => handleApply(p.proposal_id)}
                        className="text-xs px-2.5 py-1 rounded bg-accent text-accent-foreground hover:bg-accent/80 transition-colors disabled:opacity-50 font-semibold"
                      >
                        {isLoading ? '应用中...' : '导出并应用到 wheels.yaml'}
                      </button>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
