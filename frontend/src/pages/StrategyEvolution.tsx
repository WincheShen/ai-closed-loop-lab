import { useState, useEffect, useCallback } from 'react'
import { cn } from '@/lib/utils'
import {
  BrainCircuit,
  Shield,
  Target,
  Lightbulb,
  BookOpen,
  RefreshCw,
  Clock,
  CheckCircle2,
  XCircle,
  ChevronDown,
  ChevronRight,
  Zap,
} from 'lucide-react'
import {
  ResponsiveContainer,
  RadarChart,
  Radar,
  PolarGrid,
  PolarAngleAxis,
  PolarRadiusAxis,
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  CartesianGrid,
} from 'recharts'

// ── 类型 ──
interface PromptWeight {
  strategy_name: string
  current_weight: number
  win_count: number
  loss_count: number
  last_updated: string
}

interface Persona {
  id: string
  version: string
  name: string
  philosophy: string[]
  preferred_holding_days: number[]
  preferred_setups: string[]
  avoid_setups: string[]
  risk_limits: Record<string, any>
  strategy_regime_compatibility: Record<string, any>
  social_style: Record<string, any>
}

interface Lesson {
  lesson_id: string
  position_id: string
  strategy_id: string
  regime: string
  lesson_text: string
  action_items: string
  relevance_score: number
  tags: string[]
  created_at: string
}

interface StrategyStats {
  strategy: string
  win: number
  loss: number
  even: number
  total_pnl: number
}

interface ExitReason {
  reason: string
  count: number
}

// ── 主页面 ──
export default function StrategyEvolution() {
  const [weights, setWeights] = useState<PromptWeight[]>([])
  const [persona, setPersona] = useState<Persona | null>(null)
  const [lessons, setLessons] = useState<Lesson[]>([])
  const [strategyStats, setStrategyStats] = useState<StrategyStats[]>([])
  const [exitReasons, setExitReasons] = useState<ExitReason[]>([])
  const [loading, setLoading] = useState(true)
  const [personaExpanded, setPersonaExpanded] = useState(false)
  const [expandedLesson, setExpandedLesson] = useState<string | null>(null)

  const loadAll = useCallback(async () => {
    setLoading(true)
    try {
      const [wRes, pRes, lRes, aRes] = await Promise.all([
        fetch('/api/strategy-weights'),
        fetch('/api/persona'),
        fetch('/api/lessons-timeline?limit=30'),
        fetch('/api/attribution-stats?days=60'),
      ])
      if (wRes.ok) {
        const data = await wRes.json()
        setWeights(data.weights || [])
      }
      if (pRes.ok) setPersona(await pRes.json())
      if (lRes.ok) setLessons(await lRes.json())
      if (aRes.ok) {
        const data = await aRes.json()
        setStrategyStats(data.by_strategy || [])
        setExitReasons(data.by_exit_reason || [])
      }
    } catch (e) {
      console.error('Strategy Evolution load error', e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { loadAll() }, [loadAll])

  // 雷达图数据
  const radarData = weights.map((w) => ({
    strategy: w.strategy_name,
    weight: Math.round(w.current_weight * 100),
    winRate: (w.win_count + w.loss_count) > 0
      ? Math.round(w.win_count / (w.win_count + w.loss_count) * 100)
      : 50,
  }))

  return (
    <div className="space-y-6">
      {/* 标题 */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-foreground flex items-center gap-2">
            <BrainCircuit className="w-6 h-6 text-accent" />
            AI 策略进化
          </h1>
          <p className="text-sm text-muted-foreground mt-1">
            交易人格 · 策略权重 · 经验教训 · 归因分析
          </p>
        </div>
        <button
          onClick={loadAll}
          className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-panel-hover border border-panel-border text-xs text-muted-foreground hover:text-foreground transition-colors"
        >
          <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
          刷新
        </button>
      </div>

      {/* ── 交易人格概览 ── */}
      {persona && (
        <section className="data-card">
          <button
            onClick={() => setPersonaExpanded(!personaExpanded)}
            className="w-full flex items-center justify-between"
          >
            <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider flex items-center gap-2">
              <Shield className="w-4 h-4 text-accent" />
              交易人格 — {persona.name}
              <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent font-mono">
                {persona.version}
              </span>
            </h2>
            {personaExpanded
              ? <ChevronDown className="w-4 h-4 text-muted-foreground" />
              : <ChevronRight className="w-4 h-4 text-muted-foreground" />
            }
          </button>

          {personaExpanded && (
            <div className="mt-4 grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
              {/* 投资哲学 */}
              <div className="p-3 rounded-lg bg-panel-hover border border-panel-border">
                <h3 className="text-xs font-medium text-accent mb-2 uppercase tracking-wider">投资哲学</h3>
                <ul className="space-y-1">
                  {persona.philosophy?.map((p, i) => (
                    <li key={i} className="text-xs text-foreground flex items-start gap-1.5">
                      <Lightbulb className="w-3 h-3 text-accent mt-0.5 shrink-0" />
                      {p}
                    </li>
                  ))}
                </ul>
              </div>

              {/* 偏好策略 */}
              <div className="p-3 rounded-lg bg-panel-hover border border-panel-border">
                <h3 className="text-xs font-medium text-bullish mb-2 uppercase tracking-wider">偏好策略</h3>
                <ul className="space-y-1">
                  {persona.preferred_setups?.map((s, i) => (
                    <li key={i} className="text-xs text-foreground flex items-start gap-1.5">
                      <CheckCircle2 className="w-3 h-3 text-bullish mt-0.5 shrink-0" />
                      {s}
                    </li>
                  ))}
                </ul>
              </div>

              {/* 回避策略 */}
              <div className="p-3 rounded-lg bg-panel-hover border border-panel-border">
                <h3 className="text-xs font-medium text-bearish mb-2 uppercase tracking-wider">回避策略</h3>
                <ul className="space-y-1">
                  {persona.avoid_setups?.map((s, i) => (
                    <li key={i} className="text-xs text-foreground flex items-start gap-1.5">
                      <XCircle className="w-3 h-3 text-bearish mt-0.5 shrink-0" />
                      {s}
                    </li>
                  ))}
                </ul>
              </div>

              {/* 风控参数 */}
              <div className="p-3 rounded-lg bg-panel-hover border border-panel-border">
                <h3 className="text-xs font-medium text-warning mb-2 uppercase tracking-wider">风控限制</h3>
                <div className="space-y-1.5 text-xs">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">单仓位上限</span>
                    <span className="text-foreground font-mono">{(persona.risk_limits?.max_single_position_pct * 100)}%</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">板块集中度上限</span>
                    <span className="text-foreground font-mono">{(persona.risk_limits?.max_sector_concentration_pct * 100)}%</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">默认止损</span>
                    <span className="text-bearish font-mono">{(persona.risk_limits?.default_stop_loss_pct * 100)}%</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">默认止盈</span>
                    <span className="text-bullish font-mono">{(persona.risk_limits?.default_take_profit_pct * 100)}%</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">连亏降频阈值</span>
                    <span className="text-foreground font-mono">{persona.risk_limits?.consecutive_loss_limit} 笔</span>
                  </div>
                </div>
              </div>

              {/* 仓位 regime 分级 */}
              <div className="p-3 rounded-lg bg-panel-hover border border-panel-border">
                <h3 className="text-xs font-medium text-accent mb-2 uppercase tracking-wider">仓位 × 市场状态</h3>
                <div className="space-y-1.5 text-xs">
                  {persona.risk_limits?.max_total_position_pct && Object.entries(persona.risk_limits.max_total_position_pct).map(([regime, pct]) => (
                    <div key={regime} className="flex justify-between">
                      <span className="text-muted-foreground capitalize">{regime}</span>
                      <div className="flex items-center gap-2">
                        <div className="w-20 h-1.5 bg-panel rounded-full overflow-hidden">
                          <div
                            className="h-full bg-accent rounded-full"
                            style={{ width: `${Number(pct) * 100}%` }}
                          />
                        </div>
                        <span className="text-foreground font-mono w-10 text-right">{(Number(pct) * 100)}%</span>
                      </div>
                    </div>
                  ))}
                </div>
              </div>

              {/* 持仓天数 */}
              <div className="p-3 rounded-lg bg-panel-hover border border-panel-border">
                <h3 className="text-xs font-medium text-accent mb-2 uppercase tracking-wider">其他参数</h3>
                <div className="space-y-1.5 text-xs">
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">偏好持仓天数</span>
                    <span className="text-foreground font-mono">{persona.preferred_holding_days?.join('-')} 天</span>
                  </div>
                  <div className="flex justify-between">
                    <span className="text-muted-foreground">社媒风格</span>
                    <span className="text-foreground">{persona.social_style?.tone}</span>
                  </div>
                </div>
              </div>
            </div>
          )}
        </section>
      )}

      {/* ── 上半：策略权重 + 归因统计 ── */}
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* 策略权重雷达图 */}
        <section className="data-card">
          <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider flex items-center gap-2">
            <Target className="w-4 h-4 text-accent" />
            策略权重分布
          </h2>
          {radarData.length > 0 ? (
            <div className="h-72">
              <ResponsiveContainer width="100%" height="100%">
                <RadarChart data={radarData}>
                  <PolarGrid stroke="hsl(var(--panel-border))" />
                  <PolarAngleAxis
                    dataKey="strategy"
                    tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
                  />
                  <PolarRadiusAxis
                    tick={{ fontSize: 10, fill: 'hsl(var(--muted-foreground))' }}
                    domain={[0, 100]}
                  />
                  <Radar
                    name="权重%"
                    dataKey="weight"
                    stroke="#6366f1"
                    fill="#6366f1"
                    fillOpacity={0.2}
                    strokeWidth={2}
                  />
                  <Radar
                    name="胜率%"
                    dataKey="winRate"
                    stroke="#10b981"
                    fill="#10b981"
                    fillOpacity={0.1}
                    strokeWidth={2}
                  />
                  <Tooltip
                    contentStyle={{
                      backgroundColor: 'hsl(var(--panel))',
                      border: '1px solid hsl(var(--panel-border))',
                      borderRadius: '8px',
                      fontSize: '12px',
                    }}
                  />
                </RadarChart>
              </ResponsiveContainer>
            </div>
          ) : (
            <div className="h-72 flex items-center justify-center text-sm text-muted-foreground">
              暂无策略权重数据 — AI 完成交易并复盘后将自动生成
            </div>
          )}

          {/* 权重明细表 */}
          {weights.length > 0 && (
            <div className="mt-4 overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-panel-border">
                    {['策略', '权重', '胜', '负', '胜率', '更新时间'].map((h) => (
                      <th key={h} className="text-left px-3 py-2 text-[10px] text-muted-foreground uppercase">{h}</th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {weights.map((w) => {
                    const total = w.win_count + w.loss_count
                    const wr = total > 0 ? (w.win_count / total * 100).toFixed(0) : '-'
                    return (
                      <tr key={w.strategy_name} className="border-b border-panel-border/50 hover:bg-panel-hover transition-colors">
                        <td className="px-3 py-2 text-xs font-medium text-foreground">{w.strategy_name}</td>
                        <td className="px-3 py-2">
                          <div className="flex items-center gap-2">
                            <div className="w-16 h-1.5 bg-panel-hover rounded-full overflow-hidden">
                              <div className="h-full bg-accent rounded-full" style={{ width: `${w.current_weight * 100}%` }} />
                            </div>
                            <span className="text-xs font-mono text-accent">{(w.current_weight * 100).toFixed(1)}%</span>
                          </div>
                        </td>
                        <td className="px-3 py-2 text-xs font-mono text-bullish">{w.win_count}</td>
                        <td className="px-3 py-2 text-xs font-mono text-bearish">{w.loss_count}</td>
                        <td className="px-3 py-2 text-xs font-mono text-foreground">{wr}%</td>
                        <td className="px-3 py-2 text-[11px] text-muted-foreground font-mono">{(w.last_updated || '').slice(0, 10)}</td>
                      </tr>
                    )
                  })}
                </tbody>
              </table>
            </div>
          )}
        </section>

        {/* 归因统计 */}
        <section className="data-card">
          <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider flex items-center gap-2">
            <Zap className="w-4 h-4 text-accent" />
            归因分析（近 60 天）
          </h2>

          {strategyStats.length > 0 ? (
            <>
              {/* 策略胜负柱状图 */}
              <div className="h-56 mb-4">
                <ResponsiveContainer width="100%" height="100%">
                  <BarChart data={strategyStats} layout="vertical">
                    <CartesianGrid strokeDasharray="3 3" stroke="hsl(var(--panel-border))" />
                    <XAxis type="number" tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }} />
                    <YAxis
                      type="category"
                      dataKey="strategy"
                      tick={{ fontSize: 11, fill: 'hsl(var(--muted-foreground))' }}
                      width={100}
                    />
                    <Tooltip
                      contentStyle={{
                        backgroundColor: 'hsl(var(--panel))',
                        border: '1px solid hsl(var(--panel-border))',
                        borderRadius: '8px',
                        fontSize: '12px',
                      }}
                    />
                    <Bar dataKey="win" name="盈利" fill="#10b981" stackId="a" />
                    <Bar dataKey="loss" name="亏损" fill="#ef4444" stackId="a" />
                    <Bar dataKey="even" name="持平" fill="#6b7280" stackId="a" />
                  </BarChart>
                </ResponsiveContainer>
              </div>

              {/* 策略盈亏明细 */}
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-panel-border">
                      {['策略', '盈利', '亏损', '胜率', '累计盈亏'].map((h) => (
                        <th key={h} className="text-left px-3 py-2 text-[10px] text-muted-foreground uppercase">{h}</th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {strategyStats.map((s) => {
                      const total = s.win + s.loss + s.even
                      const wr = total > 0 ? (s.win / total * 100).toFixed(0) : '-'
                      return (
                        <tr key={s.strategy} className="border-b border-panel-border/50 hover:bg-panel-hover">
                          <td className="px-3 py-2 text-xs font-medium text-foreground">{s.strategy}</td>
                          <td className="px-3 py-2 text-xs font-mono text-bullish">{s.win}</td>
                          <td className="px-3 py-2 text-xs font-mono text-bearish">{s.loss}</td>
                          <td className="px-3 py-2 text-xs font-mono text-foreground">{wr}%</td>
                          <td className={cn('px-3 py-2 text-xs font-mono font-medium',
                            s.total_pnl > 0 ? 'text-bullish' : s.total_pnl < 0 ? 'text-bearish' : 'text-muted-foreground'
                          )}>
                            {s.total_pnl >= 0 ? '+' : ''}{s.total_pnl.toFixed(2)}
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </>
          ) : (
            <div className="h-56 flex items-center justify-center text-sm text-muted-foreground">
              暂无归因数据 — AI 平仓后将自动生成交易归因
            </div>
          )}

          {/* 离场原因分布 */}
          {exitReasons.length > 0 && (
            <div className="mt-4 pt-4 border-t border-panel-border">
              <h3 className="text-xs font-medium text-muted-foreground mb-2 uppercase tracking-wider">离场原因分布</h3>
              <div className="flex flex-wrap gap-2">
                {exitReasons.map((er) => (
                  <span
                    key={er.reason}
                    className="text-[11px] px-2.5 py-1 rounded-full bg-panel-hover border border-panel-border text-foreground"
                  >
                    {er.reason} <span className="font-mono text-accent ml-1">{er.count}</span>
                  </span>
                ))}
              </div>
            </div>
          )}
        </section>
      </div>

      {/* ── Lessons 时间线 ── */}
      <section className="data-card">
        <h2 className="text-sm font-semibold text-muted-foreground mb-4 uppercase tracking-wider flex items-center gap-2">
          <BookOpen className="w-4 h-4 text-accent" />
          经验教训时间线（{lessons.length} 条）
        </h2>

        {lessons.length === 0 ? (
          <div className="text-center py-10 text-sm text-muted-foreground">
            暂无教训记录 — AI 平仓后会自动提取经验教训
          </div>
        ) : (
          <div className="relative pl-6 border-l-2 border-panel-border space-y-4">
            {lessons.map((lesson) => {
              const isExpanded = expandedLesson === lesson.lesson_id
              return (
                <div key={lesson.lesson_id} className="relative">
                  {/* 时间线节点 */}
                  <div className="absolute -left-[25px] top-1.5 w-3 h-3 rounded-full bg-accent border-2 border-background" />

                  <button
                    onClick={() => setExpandedLesson(isExpanded ? null : lesson.lesson_id)}
                    className="w-full text-left"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <div className="flex-1 min-w-0">
                        <div className="flex items-center gap-2 flex-wrap">
                          <span className="text-[11px] font-mono text-muted-foreground">
                            <Clock className="w-3 h-3 inline mr-1" />
                            {(lesson.created_at || '').slice(0, 16).replace('T', ' ')}
                          </span>
                          {lesson.strategy_id && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/20">
                              {lesson.strategy_id}
                            </span>
                          )}
                          {lesson.regime && (
                            <span className="text-[10px] px-1.5 py-0.5 rounded bg-warning/10 text-warning border border-warning/20 capitalize">
                              {lesson.regime}
                            </span>
                          )}
                          <span className="text-[10px] px-1.5 py-0.5 rounded bg-panel-hover text-muted-foreground font-mono">
                            相关度 {(lesson.relevance_score ?? 0).toFixed(1)}
                          </span>
                        </div>
                        <p className={cn(
                          'text-xs text-foreground mt-1',
                          !isExpanded && 'line-clamp-2'
                        )}>
                          {lesson.lesson_text}
                        </p>
                      </div>
                      {isExpanded
                        ? <ChevronDown className="w-4 h-4 text-muted-foreground shrink-0 mt-1" />
                        : <ChevronRight className="w-4 h-4 text-muted-foreground shrink-0 mt-1" />
                      }
                    </div>
                  </button>

                  {isExpanded && (
                    <div className="mt-2 ml-0 space-y-2">
                      {lesson.action_items && (
                        <div className="p-2.5 rounded-lg bg-bullish/5 border border-bullish/20">
                          <h4 className="text-[10px] text-bullish font-medium uppercase tracking-wider mb-1">行动项</h4>
                          <p className="text-xs text-foreground whitespace-pre-line">{lesson.action_items}</p>
                        </div>
                      )}
                      {lesson.tags?.length > 0 && (
                        <div className="flex flex-wrap gap-1">
                          {lesson.tags.map((tag, i) => (
                            <span key={i} className="text-[10px] px-1.5 py-0.5 rounded bg-panel-hover text-muted-foreground border border-panel-border">
                              #{tag}
                            </span>
                          ))}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        )}
      </section>
    </div>
  )
}
