import { useState, useEffect } from 'react'
import { cn } from '@/lib/utils'
import {
  Calendar,
  Target,
  Shield,
  Zap,
  BarChart3,
  CheckCircle2,
  XCircle,
  AlertTriangle,
  Activity,
  BrainCircuit,
  ArrowRight,
  Lightbulb,
  Clock,
  DollarSign,
  BookOpen,
  TrendingUp,
  TrendingDown,
  Minus,
  ChevronDown,
  ChevronRight,
} from 'lucide-react'

// ── 类型定义 ──
interface StockPick {
  symbol: string
  name: string
  price: number
  change_pct: number
  industry: string
  rule_score: number
  matched_rules: string[]
  agent_decision: string | null
  agent_confidence: number | null
  agent_summary: string | null
  bucket: 'aggressive' | 'stable' | 'candidate'
  reasoning: string
}

interface MarketRegime {
  regime: string
  risk_appetite: string
  recommended_posture: string
  max_total_position_pct: number
  hot_sectors: string[]
  dominant_styles: string[]
  avoid_styles: string[]
  strategy_bias: Record<string, number>
  daily_questions: string[]
  summary: string
  evidence: {
    up_count: number
    down_count: number
    flat_count: number
    strong_count: number
    weak_count: number
    avg_change_pct: number
    up_ratio: number
    total_stocks: number
  }
}

interface TradeSignal {
  signal_id: string
  symbol: string
  action: string
  entry_price: number
  target_price: number
  stop_loss: number
  position_pct: number
  strategy: string
  rationale: string
  timestamp: string
}

interface RiskDecision {
  signal_id: string
  symbol: string
  decision: string
  original_position_pct: number
  approved_position_pct: number
  reason: string
  risk_flags: string[]
}

interface TradeAttribution {
  attribution_id: string
  position_id: string
  symbol: string
  name: string
  entry_price: number
  close_price: number
  realized_pnl: number
  pnl_pct: number
  holding_days: number
  outcome: string          // win | loss | breakeven
  primary_cause: string
  secondary_causes: string[]
  entry_regime: string
  close_regime: string
  regime_changed: boolean
  strategy_id: string
  original_thesis: string
  actual_narrative: string
  lesson: string
  should_have: string
  tags: string[]
  created_at: string
}

interface OrderWithFill {
  order_id: string
  signal_id: string
  symbol: string
  side: string
  quantity: number
  order_type: string
  limit_price: number | null
  status: string
  submitted_at: string
  avg_price: number | null
  fees: number | null
  filled_at: string | null
}

interface AgentReport {
  date: string
  market_regime: MarketRegime | null
  picks: {
    hot_sectors: string[]
    aggressive: StockPick[]
    stable: StockPick[]
    candidates: StockPick[]
    candidates_count: number
    agent_calls_count: number
    elapsed_seconds: number
    is_mock_data: boolean
  } | null
  signals: TradeSignal[]
  risk_decisions: RiskDecision[]
  orders: OrderWithFill[]
  cost: {
    total_llm_cost_usd: number
    total_calls: number
    total_tokens: number
  }
  attributions: TradeAttribution[]
}

// regime 配色映射
const regimeColors: Record<string, { bg: string; text: string; label: string }> = {
  bull: { bg: 'bg-bullish/10', text: 'text-bullish', label: '🔴 牛市' },
  neutral: { bg: 'bg-muted/10', text: 'text-muted', label: '➖ 震荡' },
  bear: { bg: 'bg-bearish/10', text: 'text-bearish', label: '🟢 熊市' },
  panic: { bg: 'bg-purple-500/10', text: 'text-purple-400', label: '🟣 恐慌' },
  rebound: { bg: 'bg-blue-500/10', text: 'text-blue-400', label: '🔵 反弹' },
}

// posture 映射
const postureLabels: Record<string, string> = {
  attack: '全力进攻',
  selective_attack: '精选进攻',
  defend: '防御',
  observe: '观望',
  exit: '清仓',
}

// decision 映射
const decisionConfig: Record<string, { icon: typeof CheckCircle2; color: string; label: string }> = {
  approve: { icon: CheckCircle2, color: 'text-bullish', label: '通过' },
  reduce: { icon: AlertTriangle, color: 'text-warning', label: '降仓' },
  reject: { icon: XCircle, color: 'text-bearish', label: '拒绝' },
}

// ── 子组件：市场判断 ──
function MarketRegimeSection({ regime }: { regime: MarketRegime | null }) {
  if (!regime) {
    return (
      <div className="data-card">
        <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider flex items-center gap-2">
          <BrainCircuit className="w-4 h-4 text-accent" />
          市场判断
        </h2>
        <p className="text-sm text-muted-foreground text-center py-4">暂无当日市场判断数据</p>
      </div>
    )
  }

  const rc = regimeColors[regime.regime] || regimeColors.neutral

  return (
    <div className="data-card">
      <h2 className="text-sm font-semibold text-muted-foreground mb-4 uppercase tracking-wider flex items-center gap-2">
        <BrainCircuit className="w-4 h-4 text-accent" />
        市场判断
      </h2>

      <div className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-4">
        {/* Regime */}
        <div className={cn('p-3 rounded-lg border', rc.bg, 'border-opacity-20')}>
          <div className="text-xs text-muted-foreground mb-1">市场制度</div>
          <div className={cn('text-lg font-bold', rc.text)}>{rc.label}</div>
        </div>
        {/* Posture */}
        <div className="p-3 rounded-lg bg-panel-hover border border-panel-border">
          <div className="text-xs text-muted-foreground mb-1">推荐姿态</div>
          <div className="text-lg font-bold text-accent">{postureLabels[regime.recommended_posture] || regime.recommended_posture}</div>
        </div>
        {/* Position */}
        <div className="p-3 rounded-lg bg-panel-hover border border-panel-border">
          <div className="text-xs text-muted-foreground mb-1">最大仓位</div>
          <div className="flex items-center gap-2">
            <div className="text-lg font-bold text-foreground">{(regime.max_total_position_pct * 100).toFixed(0)}%</div>
            <div className="flex-1 h-2 bg-panel-border rounded-full overflow-hidden">
              <div
                className="h-full bg-accent rounded-full"
                style={{ width: `${regime.max_total_position_pct * 100}%` }}
              />
            </div>
          </div>
        </div>
      </div>

      {/* 热点 + 风格 */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-4">
        <div>
          <div className="text-xs text-muted-foreground mb-2">热点板块</div>
          <div className="flex flex-wrap gap-1.5">
            {regime.hot_sectors.map((s) => (
              <span key={s} className="text-[11px] px-2 py-0.5 rounded bg-accent/10 text-accent border border-accent/20">
                {s}
              </span>
            ))}
          </div>
        </div>
        <div>
          <div className="text-xs text-muted-foreground mb-2">推荐风格</div>
          <div className="flex flex-wrap gap-1.5">
            {regime.dominant_styles.map((s) => (
              <span key={s} className="text-[11px] px-2 py-0.5 rounded bg-bullish/10 text-bullish border border-bullish/20">
                {s}
              </span>
            ))}
          </div>
        </div>
      </div>

      {/* Avoid styles */}
      {regime.avoid_styles.length > 0 && (
        <div className="mb-4">
          <div className="text-xs text-muted-foreground mb-2">避免风格</div>
          <div className="flex flex-wrap gap-1.5">
            {regime.avoid_styles.map((s) => (
              <span key={s} className="text-[11px] px-2 py-0.5 rounded bg-bearish/10 text-bearish border border-bearish/20">
                {s}
              </span>
            ))}
          </div>
        </div>
      )}

      {/* Strategy bias */}
      {Object.keys(regime.strategy_bias).length > 0 && (
        <div className="mb-4">
          <div className="text-xs text-muted-foreground mb-2">策略权重</div>
          <div className="space-y-1.5">
            {Object.entries(regime.strategy_bias).map(([k, v]) => (
              <div key={k} className="flex items-center gap-2">
                <span className="text-xs text-muted-foreground w-28 shrink-0">{k}</span>
                <div className="flex-1 h-1.5 bg-panel-border rounded-full overflow-hidden">
                  <div className="h-full bg-accent rounded-full" style={{ width: `${(v as number) * 100}%` }} />
                </div>
                <span className="text-xs font-mono text-accent w-10 text-right">{((v as number) * 100).toFixed(0)}%</span>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Summary */}
      <div className="p-3 rounded-lg bg-accent/5 border border-accent/20 text-sm text-accent/80 leading-relaxed mb-4">
        <Lightbulb className="w-4 h-4 inline mr-1 text-accent" />
        {regime.summary}
      </div>

      {/* Evidence */}
      {regime.evidence && (
        <div className="grid grid-cols-3 md:grid-cols-6 gap-2">
          {[
            { label: '上涨', value: regime.evidence.up_count, color: 'text-bullish' },
            { label: '下跌', value: regime.evidence.down_count, color: 'text-bearish' },
            { label: '平盘', value: regime.evidence.flat_count, color: 'text-muted-foreground' },
            { label: '强势', value: regime.evidence.strong_count, color: 'text-bullish' },
            { label: '弱势', value: regime.evidence.weak_count, color: 'text-bearish' },
            { label: '均涨幅', value: `${regime.evidence.avg_change_pct >= 0 ? '+' : ''}${regime.evidence.avg_change_pct.toFixed(2)}%`, color: regime.evidence.avg_change_pct >= 0 ? 'text-bullish' : 'text-bearish' },
          ].map((item) => (
            <div key={item.label} className="p-2 rounded bg-panel-hover border border-panel-border text-center">
              <div className={cn('text-sm font-bold font-mono', item.color)}>{item.value}</div>
              <div className="text-[10px] text-muted-foreground">{item.label}</div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── 子组件：选股结果 ──
function StockPicksSection({ picks }: { picks: AgentReport['picks'] }) {
  const [activeTab, setActiveTab] = useState<'aggressive' | 'stable' | 'candidates'>('aggressive')

  if (!picks) {
    return (
      <div className="data-card">
        <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider flex items-center gap-2">
          <Target className="w-4 h-4 text-accent" />
          选股结果
        </h2>
        <p className="text-sm text-muted-foreground text-center py-4">暂无选股数据</p>
      </div>
    )
  }

  const tabs: { key: typeof activeTab; label: string; count: number; color: string }[] = [
    { key: 'aggressive', label: '激进推荐', count: picks.aggressive.length, color: 'text-bullish' },
    { key: 'stable', label: '稳健推荐', count: picks.stable.length, color: 'text-accent' },
    { key: 'candidates', label: '候选池', count: picks.candidates_count, color: 'text-muted-foreground' },
  ]

  const currentList = activeTab === 'candidates' ? picks.candidates : picks[activeTab]

  return (
    <div className="data-card">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-2">
          <Target className="w-4 h-4 text-accent" />
          选股结果
          {picks.is_mock_data && (
            <span className="text-[10px] text-warning ml-1">(Mock)</span>
          )}
        </h2>
        <div className="flex items-center gap-2">
          {picks.hot_sectors.map((s) => (
            <span key={s} className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/20">
              {s}
            </span>
          ))}
        </div>
      </div>

      {/* Tabs */}
      <div className="flex items-center gap-1 bg-panel-hover rounded-lg p-0.5 border border-panel-border mb-4">
        {tabs.map((t) => (
          <button
            key={t.key}
            onClick={() => setActiveTab(t.key)}
            className={cn(
              'flex-1 flex items-center justify-center gap-1.5 py-1.5 rounded-md text-xs font-medium transition-all',
              activeTab === t.key
                ? 'bg-panel text-foreground shadow-sm'
                : 'text-muted-foreground hover:text-foreground'
            )}
          >
            {t.label}
            <span className={cn('font-mono', t.color)}>{t.count}</span>
          </button>
        ))}
      </div>

      {/* 股票列表 */}
      {currentList.length === 0 ? (
        <p className="text-sm text-muted-foreground text-center py-6">该分类下暂无股票</p>
      ) : (
        <div className="space-y-2 max-h-[400px] overflow-y-auto">
          {currentList.map((stock) => (
            <div
              key={stock.symbol}
              className="flex items-center gap-3 p-3 rounded-lg bg-panel-hover border border-panel-border hover:border-accent/30 transition-all"
            >
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono text-muted-foreground">{stock.symbol}</span>
                  <span className="text-sm font-medium text-foreground truncate">{stock.name}</span>
                  <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/20 shrink-0">
                    {stock.industry}
                  </span>
                </div>
                <div className="flex items-center gap-3 mt-1.5 text-[11px] text-muted-foreground">
                  <span className={cn('font-mono font-bold', stock.change_pct >= 0 ? 'text-bullish' : 'text-bearish')}>
                    {stock.change_pct >= 0 ? '+' : ''}{stock.change_pct.toFixed(2)}%
                  </span>
                  <span>评分 {stock.rule_score.toFixed(1)}</span>
                  {stock.agent_decision && (
                    <span className={cn(
                      'font-medium',
                      stock.agent_decision === 'BUY' ? 'text-bullish' : 'text-bearish'
                    )}>
                      Agent: {stock.agent_decision} ({(stock.agent_confidence || 0) * 100}%)
                    </span>
                  )}
                </div>
                {stock.reasoning && (
                  <p className="text-[11px] text-muted-foreground mt-1 truncate">{stock.reasoning}</p>
                )}
              </div>
              <div className="text-right shrink-0">
                <div className="text-sm font-mono text-foreground">{stock.price.toFixed(2)}</div>
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}

// ── 子组件：深度评估 ──
function SignalsSection({ signals }: { signals: TradeSignal[] }) {
  if (signals.length === 0) {
    return (
      <div className="data-card">
        <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider flex items-center gap-2">
          <Zap className="w-4 h-4 text-accent" />
          深度评估
        </h2>
        <p className="text-sm text-muted-foreground text-center py-4">暂无交易信号</p>
      </div>
    )
  }

  return (
    <div className="data-card">
      <h2 className="text-sm font-semibold text-muted-foreground mb-4 uppercase tracking-wider flex items-center gap-2">
        <Zap className="w-4 h-4 text-accent" />
        深度评估
        <span className="text-xs text-muted-foreground font-normal normal-case">({signals.length} 条信号)</span>
      </h2>

      <div className="space-y-3">
        {signals.map((sig) => {
          const isBuy = sig.action.toLowerCase() === 'buy'
          return (
            <div
              key={sig.signal_id}
              className={cn(
                'p-3 rounded-lg border transition-all',
                isBuy
                  ? 'bg-bullish/5 border-bullish/20'
                  : 'bg-bearish/5 border-bearish/20'
              )}
            >
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono text-muted-foreground">{sig.symbol}</span>
                  <span className={cn(
                    'text-xs font-bold px-1.5 py-0.5 rounded',
                    isBuy ? 'bg-bullish/10 text-bullish' : 'bg-bearish/10 text-bearish'
                  )}>
                    {sig.action.toUpperCase()}
                  </span>
                  <span className="text-[11px] text-muted-foreground">{sig.strategy}</span>
                </div>
                <span className="text-[10px] text-muted-foreground font-mono">{sig.signal_id.slice(-6)}</span>
              </div>

              {/* 价格条 */}
              <div className="flex items-center gap-2 mb-2">
                <div className="flex-1 flex items-center gap-1 text-[11px]">
                  <span className="text-muted-foreground">入场</span>
                  <span className="font-mono text-foreground">{sig.entry_price.toFixed(2)}</span>
                  <ArrowRight className="w-3 h-3 text-muted-foreground" />
                  <span className="text-muted-foreground">目标</span>
                  <span className="font-mono text-bullish">{sig.target_price.toFixed(2)}</span>
                  <ArrowRight className="w-3 h-3 text-muted-foreground" />
                  <span className="text-muted-foreground">止损</span>
                  <span className="font-mono text-bearish">{sig.stop_loss.toFixed(2)}</span>
                </div>
                <div className="text-[11px] text-muted-foreground">
                  仓位 {(sig.position_pct * 100).toFixed(0)}%
                </div>
              </div>

              <p className="text-xs text-muted-foreground leading-relaxed">{sig.rationale}</p>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── 子组件：风控审核 ──
function RiskSection({ decisions }: { decisions: RiskDecision[] }) {
  if (decisions.length === 0) {
    return (
      <div className="data-card">
        <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider flex items-center gap-2">
          <Shield className="w-4 h-4 text-accent" />
          风控审核
        </h2>
        <p className="text-sm text-muted-foreground text-center py-4">暂无风控记录</p>
      </div>
    )
  }

  return (
    <div className="data-card">
      <h2 className="text-sm font-semibold text-muted-foreground mb-4 uppercase tracking-wider flex items-center gap-2">
        <Shield className="w-4 h-4 text-accent" />
        风控审核
        <span className="text-xs text-muted-foreground font-normal normal-case">({decisions.length} 条)</span>
      </h2>

      <div className="space-y-2">
        {decisions.map((d) => {
          const cfg = decisionConfig[d.decision] || decisionConfig.reject
          const Icon = cfg.icon
          const isReduce = d.decision === 'reduce'

          return (
            <div
              key={`${d.signal_id}-${d.symbol}`}
              className="flex items-center gap-3 p-3 rounded-lg bg-panel-hover border border-panel-border"
            >
              <Icon className={cn('w-5 h-5 shrink-0', cfg.color)} />
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2">
                  <span className="text-xs font-mono text-muted-foreground">{d.symbol}</span>
                  <span className={cn('text-xs font-bold', cfg.color)}>{cfg.label}</span>
                </div>
                <p className="text-[11px] text-muted-foreground mt-0.5">{d.reason}</p>
                {isReduce && (
                  <div className="text-[11px] text-muted-foreground mt-0.5">
                    仓位 {(d.original_position_pct * 100).toFixed(0)}% →{' '}
                    <span className="text-warning font-medium">{(d.approved_position_pct * 100).toFixed(0)}%</span>
                  </div>
                )}
                {d.risk_flags.length > 0 && (
                  <div className="flex flex-wrap gap-1 mt-1">
                    {d.risk_flags.map((f) => (
                      <span key={f} className="text-[10px] px-1.5 py-0.5 rounded bg-warning/10 text-warning border border-warning/20">
                        {f}
                      </span>
                    ))}
                  </div>
                )}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── 子组件：执行记录 ──
function OrdersSection({ orders }: { orders: OrderWithFill[] }) {
  if (orders.length === 0) {
    return (
      <div className="data-card">
        <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider flex items-center gap-2">
          <BarChart3 className="w-4 h-4 text-accent" />
          执行记录
        </h2>
        <p className="text-sm text-muted-foreground text-center py-4">暂无执行记录</p>
      </div>
    )
  }

  return (
    <div className="data-card">
      <h2 className="text-sm font-semibold text-muted-foreground mb-4 uppercase tracking-wider flex items-center gap-2">
        <BarChart3 className="w-4 h-4 text-accent" />
        执行记录
        <span className="text-xs text-muted-foreground font-normal normal-case">({orders.length} 笔)</span>
      </h2>

      <div className="overflow-x-auto">
        <table className="w-full">
          <thead className="border-b border-panel-border">
            <tr>
              <th className="text-left px-2 py-2 text-[10px] text-muted-foreground uppercase">代码</th>
              <th className="text-left px-2 py-2 text-[10px] text-muted-foreground uppercase">方向</th>
              <th className="text-right px-2 py-2 text-[10px] text-muted-foreground uppercase">数量</th>
              <th className="text-right px-2 py-2 text-[10px] text-muted-foreground uppercase">均价</th>
              <th className="text-right px-2 py-2 text-[10px] text-muted-foreground uppercase">手续费</th>
              <th className="text-left px-2 py-2 text-[10px] text-muted-foreground uppercase">状态</th>
            </tr>
          </thead>
          <tbody>
            {orders.map((o) => (
              <tr key={o.order_id} className="border-b border-panel-border/50 hover:bg-panel-hover transition-colors">
                <td className="px-2 py-2 text-xs font-mono text-muted-foreground">{o.symbol}</td>
                <td className="px-2 py-2">
                  <span className={cn(
                    'text-[11px] font-bold',
                    o.side === 'buy' ? 'text-bullish' : 'text-bearish'
                  )}>
                    {o.side === 'buy' ? '买入' : '卖出'}
                  </span>
                </td>
                <td className="px-2 py-2 text-xs font-mono text-right text-foreground">{o.quantity}</td>
                <td className="px-2 py-2 text-xs font-mono text-right text-foreground">
                  {o.avg_price ? o.avg_price.toFixed(2) : '-'}
                </td>
                <td className="px-2 py-2 text-xs font-mono text-right text-muted-foreground">
                  {o.fees ? o.fees.toFixed(2) : '-'}
                </td>
                <td className="px-2 py-2">
                  <span className={cn(
                    'text-[10px] px-1.5 py-0.5 rounded border',
                    o.status === 'filled'
                      ? 'bg-bullish/10 text-bullish border-bullish/20'
                      : 'bg-panel-hover text-muted-foreground border-panel-border'
                  )}>
                    {o.status}
                  </span>
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}

// ── 归因主因中文映射 ──
const causeLabels: Record<string, { label: string; color: string }> = {
  thesis_correct:        { label: '逻辑正确', color: 'text-bullish' },
  thesis_wrong:          { label: '选股错误', color: 'text-bearish' },
  timing_early:          { label: '时机太早', color: 'text-warning' },
  timing_late:           { label: '追高', color: 'text-warning' },
  regime_shift:          { label: 'Regime突变', color: 'text-purple-400' },
  stop_loss_triggered:   { label: '正常止损', color: 'text-muted-foreground' },
  take_profit_triggered: { label: '正常止盈', color: 'text-bullish' },
  position_too_large:    { label: '仓位过重', color: 'text-bearish' },
  held_too_long:         { label: '持仓太久', color: 'text-warning' },
  external_shock:        { label: '外部冲击', color: 'text-purple-400' },
}

// ── 子组件：交易归因 ──
function AttributionSection({ attributions }: { attributions: TradeAttribution[] }) {
  const [expandedId, setExpandedId] = useState<string | null>(null)

  if (attributions.length === 0) {
    return (
      <div className="data-card">
        <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider flex items-center gap-2">
          <BookOpen className="w-4 h-4 text-accent" />
          交易归因
        </h2>
        <p className="text-sm text-muted-foreground text-center py-4">今日暂无平仓归因记录</p>
      </div>
    )
  }

  const wins = attributions.filter((a) => a.outcome === 'win').length
  const losses = attributions.filter((a) => a.outcome === 'loss').length

  return (
    <div className="data-card">
      <div className="flex items-center justify-between mb-4">
        <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-2">
          <BookOpen className="w-4 h-4 text-accent" />
          交易归因
          <span className="text-xs font-normal normal-case">({attributions.length} 笔平仓)</span>
        </h2>
        <div className="flex items-center gap-3 text-xs">
          <span className="flex items-center gap-1 text-bullish">
            <TrendingUp className="w-3 h-3" />{wins} 盈
          </span>
          <span className="flex items-center gap-1 text-bearish">
            <TrendingDown className="w-3 h-3" />{losses} 亏
          </span>
          {attributions.length - wins - losses > 0 && (
            <span className="flex items-center gap-1 text-muted-foreground">
              <Minus className="w-3 h-3" />{attributions.length - wins - losses} 平
            </span>
          )}
        </div>
      </div>

      <div className="space-y-3">
        {attributions.map((attr) => {
          const isWin = attr.outcome === 'win'
          const isLoss = attr.outcome === 'loss'
          const cause = causeLabels[attr.primary_cause] || { label: attr.primary_cause, color: 'text-muted-foreground' }
          const expanded = expandedId === attr.attribution_id

          return (
            <div
              key={attr.attribution_id}
              className={cn(
                'rounded-lg border transition-all',
                isWin ? 'border-bullish/20' : isLoss ? 'border-bearish/20' : 'border-panel-border'
              )}
            >
              {/* 摘要行（点击展开）*/}
              <button
                className="w-full text-left p-3 hover:bg-panel-hover transition-colors rounded-lg"
                onClick={() => setExpandedId(expanded ? null : attr.attribution_id)}
              >
                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-2">
                    {isWin ? <TrendingUp className="w-4 h-4 text-bullish shrink-0" />
                           : isLoss ? <TrendingDown className="w-4 h-4 text-bearish shrink-0" />
                           : <Minus className="w-4 h-4 text-muted-foreground shrink-0" />}
                    <span className="text-xs font-mono text-muted-foreground">{attr.symbol}</span>
                    <span className="text-xs text-foreground">{attr.name}</span>
                    <span className={cn('text-xs font-bold px-1.5 py-0.5 rounded', cause.color,
                      isWin ? 'bg-bullish/10' : isLoss ? 'bg-bearish/10' : 'bg-panel-hover'
                    )}>
                      {cause.label}
                    </span>
                  </div>
                  <div className="flex items-center gap-3">
                    <span className={cn(
                      'text-sm font-mono font-bold',
                      isWin ? 'text-bullish' : isLoss ? 'text-bearish' : 'text-muted-foreground'
                    )}>
                      {attr.pnl_pct > 0 ? '+' : ''}{attr.pnl_pct.toFixed(2)}%
                    </span>
                    <span className="text-[11px] text-muted-foreground">{attr.holding_days}天</span>
                    {expanded
                      ? <ChevronDown className="w-3.5 h-3.5 text-muted-foreground" />
                      : <ChevronRight className="w-3.5 h-3.5 text-muted-foreground" />}
                  </div>
                </div>

                {/* regime 变化 */}
                {attr.entry_regime && (
                  <div className="flex items-center gap-1 mt-1.5">
                    <span className="text-[10px] text-muted-foreground">入场 regime:</span>
                    <span className="text-[10px] font-mono">{attr.entry_regime}</span>
                    {attr.regime_changed && (
                      <>
                        <ArrowRight className="w-2.5 h-2.5 text-warning" />
                        <span className="text-[10px] font-mono text-warning">{attr.close_regime}</span>
                        <span className="text-[10px] text-warning">(已变化)</span>
                      </>
                    )}
                  </div>
                )}
              </button>

              {/* 展开详情 */}
              {expanded && (
                <div className="px-3 pb-3 border-t border-panel-border/50 space-y-3 pt-3">
                  {/* 买入逻辑 vs 实际走势 */}
                  {(attr.original_thesis || attr.actual_narrative) && (
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
                      {attr.original_thesis && (
                        <div className="p-2 rounded bg-panel-hover border border-panel-border">
                          <div className="text-[10px] text-muted-foreground uppercase mb-1">买入逻辑</div>
                          <p className="text-xs text-foreground leading-relaxed">{attr.original_thesis}</p>
                        </div>
                      )}
                      {attr.actual_narrative && (
                        <div className="p-2 rounded bg-panel-hover border border-panel-border">
                          <div className="text-[10px] text-muted-foreground uppercase mb-1">实际走势</div>
                          <p className="text-xs text-foreground leading-relaxed">{attr.actual_narrative}</p>
                        </div>
                      )}
                    </div>
                  )}

                  {/* Lesson */}
                  {attr.lesson && (
                    <div className="flex items-start gap-2 p-2.5 rounded-lg bg-accent/5 border border-accent/20">
                      <Lightbulb className="w-4 h-4 text-accent shrink-0 mt-0.5" />
                      <div>
                        <p className="text-xs text-foreground font-medium">{attr.lesson}</p>
                        {attr.should_have && (
                          <p className="text-[11px] text-muted-foreground mt-1">{attr.should_have}</p>
                        )}
                      </div>
                    </div>
                  )}

                  {/* 价格 + 标签 */}
                  <div className="flex items-center justify-between">
                    <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
                      <span>入场 <span className="font-mono text-foreground">{attr.entry_price.toFixed(2)}</span></span>
                      <ArrowRight className="w-3 h-3" />
                      <span>平仓 <span className="font-mono text-foreground">{attr.close_price.toFixed(2)}</span></span>
                    </div>
                    {attr.tags.length > 0 && (
                      <div className="flex flex-wrap gap-1">
                        {attr.tags.map((t) => (
                          <span key={t} className="text-[10px] px-1.5 py-0.5 rounded bg-panel-hover border border-panel-border text-muted-foreground">
                            {t}
                          </span>
                        ))}
                      </div>
                    )}
                  </div>
                </div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ── 主页面 ──
export default function AgentDailyReport() {
  const [dates, setDates] = useState<string[]>([])
  const [selectedDate, setSelectedDate] = useState<string>('')
  const [report, setReport] = useState<AgentReport | null>(null)
  const [loading, setLoading] = useState(false)

  // 加载日期列表
  useEffect(() => {
    fetch('/api/agent-report/dates')
      .then((r) => r.json())
      .then((data: string[]) => {
        setDates(data)
        if (data.length > 0 && !selectedDate) {
          setSelectedDate(data[0])
        }
      })
      .catch(console.error)
  }, [selectedDate])

  // 加载报告
  useEffect(() => {
    if (!selectedDate) return
    setLoading(true)
    fetch(`/api/agent-report/${selectedDate}`)
      .then((r) => r.json())
      .then((data: AgentReport) => {
        setReport(data)
        setLoading(false)
      })
      .catch((e) => {
        console.error(e)
        setLoading(false)
      })
  }, [selectedDate])

  const regimeColor = report?.market_regime
    ? (regimeColors[report.market_regime.regime] || regimeColors.neutral)
    : null

  return (
    <div className="space-y-6">
      {/* 页面标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground">Agent 日报</h1>
          <p className="text-sm text-muted-foreground mt-1">
            每日选股、评估与执行记录回顾
          </p>
        </div>
      </div>

      {/* 顶部摘要条 */}
      <div className="data-card">
        <div className="flex flex-col md:flex-row md:items-center gap-4">
          {/* 日期选择 */}
          <div className="flex items-center gap-2">
            <Calendar className="w-4 h-4 text-accent" />
            <select
              value={selectedDate}
              onChange={(e) => setSelectedDate(e.target.value)}
              className="bg-panel-hover border border-panel-border rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none focus:border-accent/50"
            >
              {dates.map((d) => (
                <option key={d} value={d}>{d}</option>
              ))}
            </select>
          </div>

          {report && (
            <>
              {/* 市场状态 */}
              {report.market_regime && (
                <div className="flex items-center gap-3">
                  <div className={cn('px-3 py-1.5 rounded-lg border text-sm font-medium', regimeColor?.bg, regimeColor?.text)}>
                    {regimeColor?.label}
                  </div>
                  <div className="text-sm text-muted-foreground">
                    {postureLabels[report.market_regime.recommended_posture] || report.market_regime.recommended_posture}
                  </div>
                  <div className="flex items-center gap-1.5">
                    {report.market_regime.hot_sectors.slice(0, 3).map((s) => (
                      <span key={s} className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/20">
                        {s}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* 统计数字 */}
              <div className="flex items-center gap-4 text-xs text-muted-foreground">
                <span>
                  扫描 <span className="text-foreground font-mono font-bold">{report.market_regime?.evidence?.total_stocks || 0}</span> 只
                </span>
                <span>→</span>
                <span>
                  候选 <span className="text-foreground font-mono font-bold">{report.picks?.candidates_count || 0}</span> 只
                </span>
                <span>→</span>
                <span>
                  信号 <span className="text-foreground font-mono font-bold">{report.signals.length}</span> 条
                </span>
                <span>→</span>
                <span>
                  执行 <span className="text-foreground font-mono font-bold">{report.orders.length}</span> 笔
                </span>
              </div>

              {/* 成本 */}
              <div className="flex items-center gap-3 ml-auto">
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Clock className="w-3 h-3" />
                  <span>{report.picks?.elapsed_seconds || 0}s</span>
                </div>
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <DollarSign className="w-3 h-3" />
                  <span className="font-mono">${report.cost.total_llm_cost_usd.toFixed(2)}</span>
                </div>
                <div className="flex items-center gap-1.5 text-xs text-muted-foreground">
                  <Activity className="w-3 h-3" />
                  <span>{report.cost.total_calls} 次 LLM</span>
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {loading ? (
        <div className="data-card flex items-center justify-center py-12">
          <div className="flex items-center gap-2 text-muted-foreground">
            <div className="w-4 h-4 border-2 border-accent border-t-transparent rounded-full animate-spin" />
            加载中...
          </div>
        </div>
      ) : report ? (
        <div className="space-y-6">
          {/* 市场判断 */}
          <MarketRegimeSection regime={report.market_regime} />

          {/* 选股结果 */}
          <StockPicksSection picks={report.picks} />

          {/* 深度评估 + 风控审核 */}
          <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
            <SignalsSection signals={report.signals} />
            <RiskSection decisions={report.risk_decisions} />
          </div>

          {/* 执行记录 */}
          <OrdersSection orders={report.orders} />

          {/* 交易归因 */}
          <AttributionSection attributions={report.attributions || []} />
        </div>
      ) : (
        <div className="data-card flex items-center justify-center py-12">
          <p className="text-sm text-muted-foreground">选择日期查看报告</p>
        </div>
      )}
    </div>
  )
}
