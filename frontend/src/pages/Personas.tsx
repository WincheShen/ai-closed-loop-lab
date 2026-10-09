import { useEffect, useState } from 'react'

interface PersonaStats {
  open_positions: number
  closed_trades: number
  wins: number
  losses: number
  win_rate: number | null
  realized_pnl: number
  last_closed_at: string | null
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
                  <div className="grid grid-cols-3 gap-2 text-center">
                    <div>
                      <div className="text-base font-bold text-foreground">
                        {stats.win_rate != null ? `${(stats.win_rate * 100).toFixed(1)}%` : '—'}
                      </div>
                      <div className="text-[10px] text-muted-foreground">胜率</div>
                    </div>
                    <div>
                      <div className="text-base font-bold text-foreground">
                        {stats.wins}<span className="text-muted-foreground text-xs">/{stats.closed_trades}</span>
                      </div>
                      <div className="text-[10px] text-muted-foreground">胜/总平仓</div>
                    </div>
                    <div>
                      <div className={`text-base font-bold ${pnlColor(stats.realized_pnl)}`}>
                        {stats.realized_pnl >= 0 ? '+' : ''}{stats.realized_pnl.toFixed(0)}
                      </div>
                      <div className="text-[10px] text-muted-foreground">已实现盈亏</div>
                    </div>
                  </div>
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
