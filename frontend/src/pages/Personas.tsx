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

function pnlColor(v: number) {
  return v >= 0 ? 'text-bullish' : 'text-bearish'
}

export default function Personas() {
  const [personas, setPersonas] = useState<Persona[]>([])
  const [accounts, setAccounts] = useState<Account[]>([])
  const [loading, setLoading] = useState(true)

  useEffect(() => {
    const fetchData = async () => {
      try {
        const [personasRes, accountsRes] = await Promise.all([
          fetch('/api/personas'),
          fetch('/api/accounts')
        ])
        const personasData = await personasRes.json()
        const accountsData = await accountsRes.json()
        setPersonas(personasData.personas || [])
        setAccounts(accountsData.accounts || [])
      } catch (error) {
        console.error('Failed to fetch personas/accounts:', error)
      } finally {
        setLoading(false)
      }
    }
    fetchData()
  }, [])

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
    </div>
  )
}
