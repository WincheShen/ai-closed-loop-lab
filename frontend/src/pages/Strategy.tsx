import { useState, useEffect, useCallback } from 'react'
import { cn } from '@/lib/utils'
import {
  Lightbulb,
  Zap,
  CheckCircle2,
  Save,
  Trash2,
  Play,
  Edit3,
  ArrowUpDown,
  TrendingUp,
  TrendingDown,
  Tag,
  BarChart3,
  Activity,
  Loader2,
  ChevronRight,
} from 'lucide-react'

// ── 类型定义 ──
interface FilterSpec {
  field: string
  op: string
  value: unknown
  description?: string
}

interface RankingSpec {
  field: string
  direction: 'asc' | 'desc'
  weight: number
  description?: string
}

interface StrategySpec {
  filters: FilterSpec[]
  rankings: RankingSpec[]
  technicals?: string[]
  explanation?: string
}

interface StrategyItem {
  id: string
  name: string
  description: string
  filters_count: number
  rankings_count: number
  created_at: string
}

interface StockPick {
  symbol: string
  name: string
  price: number
  change_pct: number
  industry: string
  pe_ttm: number | null
  pb: number | null
  market_cap_yi: number | null
  turnover_rate: number
  main_fund_net_inflow: number
  score: number
}

interface StrategyResult {
  success: boolean
  strategy_name: string
  is_mock_data: boolean
  total_stocks: number
  filtered_count: number
  picks: StockPick[]
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

// ── 主页面 ──
export default function Strategy() {
  const [strategyText, setStrategyText] = useState('')
  const [currentSpec, setCurrentSpec] = useState<StrategySpec | null>(null)
  const [results, setResults] = useState<StrategyResult | null>(null)
  const [savedStrategies, setSavedStrategies] = useState<StrategyItem[]>([])
  const [compiling, setCompiling] = useState(false)
  const [executing, setExecuting] = useState(false)
  const [showReview, setShowReview] = useState(false)
  const { toasts, show } = useToast()

  // 解析策略
  const compileStrategy = async (useMock: boolean) => {
    if (!strategyText.trim()) { show('请输入策略描述', 'error'); return }

    setCompiling(true)
    try {
      const resp = await fetch('/api/strategy/compile', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy_text: strategyText, use_mock_llm: useMock }),
      })
      const data = await resp.json()
      if (data.success) {
        setCurrentSpec(data.spec)
        setShowReview(true)
        show('策略解析成功', 'success')
      } else {
        show('解析失败: ' + (data.error || '未知错误'), 'error')
      }
    } catch (e: any) {
      show('请求失败: ' + e.message, 'error')
    } finally {
      setCompiling(false)
    }
  }

  // 执行选股
  const executeStrategy = async () => {
    if (!currentSpec) { show('请先解析策略', 'error'); return }

    setExecuting(true)
    try {
      const resp = await fetch('/api/strategy/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ spec: currentSpec }),
      })
      const data = await resp.json()
      if (data.success) {
        setResults(data)
        show(`选出 ${data.filtered_count} 只股票`, 'success')
      } else {
        show('执行失败: ' + (data.error || '未知错误'), 'error')
      }
    } catch (e: any) {
      show('请求失败: ' + e.message, 'error')
    } finally {
      setExecuting(false)
    }
  }

  // 保存策略
  const saveStrategy = async () => {
    if (!currentSpec) return
    try {
      const resp = await fetch('/api/strategy/save', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ strategy_text: strategyText, spec: currentSpec }),
      })
      const data = await resp.json()
      if (data.success) {
        show('策略已保存: ' + data.name, 'success')
        loadSavedStrategies()
      }
    } catch (e: any) {
      show('保存失败: ' + e.message, 'error')
    }
  }

  // 编辑 Spec
  const editSpec = () => {
    if (!currentSpec) return
    const json = JSON.stringify(currentSpec, null, 2)
    const newJson = window.prompt('编辑策略 JSON（修改后点确定）：', json)
    if (newJson) {
      try {
        setCurrentSpec(JSON.parse(newJson))
        show('规则已更新', 'success')
      } catch {
        show('JSON 格式错误', 'error')
      }
    }
  }

  // 加载已保存策略
  const loadSavedStrategies = useCallback(async () => {
    try {
      const resp = await fetch('/api/strategy/list')
      const items = await resp.json()
      setSavedStrategies(items)
    } catch {
      show('加载策略列表失败', 'error')
    }
  }, [show])

  // 加载单个策略
  const loadStrategy = async (id: string) => {
    try {
      const resp = await fetch(`/api/strategy/${id}`)
      const data = await resp.json()
      setStrategyText(data.strategy_text)
      setCurrentSpec(data.spec)
      setShowReview(true)
      show('已加载策略: ' + data.name, 'success')
    } catch {
      show('加载失败', 'error')
    }
  }

  // 删除策略
  const deleteStrategy = async (id: string) => {
    if (!window.confirm('确定删除？')) return
    try {
      await fetch(`/api/strategy/${id}`, { method: 'DELETE' })
      loadSavedStrategies()
      show('已删除', 'success')
    } catch {
      show('删除失败', 'error')
    }
  }

  useEffect(() => {
    loadSavedStrategies()
  }, [loadSavedStrategies])

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
        <h1 className="text-2xl font-bold text-foreground">策略选股</h1>
        <p className="text-sm text-muted-foreground mt-1">
          自然语言输入 → AI 解析指标 → 执行选股
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* 左侧：输入 + Review + 已保存 */}
        <div className="space-y-4">
          {/* 策略输入 */}
          <div className="data-card">
            <h2 className="text-sm font-semibold text-foreground mb-4 flex items-center gap-2">
              <Lightbulb className="w-4 h-4 text-accent" />
              输入选股策略
            </h2>
            <textarea
              value={strategyText}
              onChange={(e) => setStrategyText(e.target.value)}
              placeholder={`例如：\n找低估值的半导体股票，PE不超过30，市值100亿以上，最好有主力资金流入\n\n或者：\n找今天放量上涨超过5%的股票，换手率要高，成交额5亿以上`}
              className="w-full h-32 bg-panel-hover border border-panel-border rounded-lg p-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-accent/50 resize-vertical mb-4"
            />
            <div className="flex items-center gap-3">
              <button
                onClick={() => compileStrategy(false)}
                disabled={compiling}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent/10 text-accent text-sm font-medium hover:bg-accent/20 transition-colors border border-accent/20 disabled:opacity-50"
              >
                {compiling ? <Loader2 className="w-4 h-4 animate-spin" /> : <Activity className="w-4 h-4" />}
                AI 解析
              </button>
              <button
                onClick={() => compileStrategy(true)}
                disabled={compiling}
                className="flex items-center gap-2 px-4 py-2 rounded-lg bg-panel-hover text-muted-foreground text-sm hover:text-foreground transition-colors border border-panel-border disabled:opacity-50"
              >
                <Zap className="w-4 h-4" />
                快速解析（Mock）
              </button>
            </div>
          </div>

          {/* Review 区 */}
          {showReview && currentSpec && (
            <div className="data-card">
              <h2 className="text-sm font-semibold text-foreground mb-4 flex items-center gap-2">
                <CheckCircle2 className="w-4 h-4 text-bullish" />
                策略 Review
              </h2>

              {/* 过滤条件 */}
              {currentSpec.filters?.length > 0 && (
                <div className="mb-4">
                  <h3 className="text-xs text-muted-foreground mb-2 uppercase tracking-wider">
                    过滤条件 ({currentSpec.filters.length})
                  </h3>
                  <div className="space-y-1.5">
                    {currentSpec.filters.map((f, i) => (
                      <div key={i} className="flex items-center gap-2 px-3 py-2 rounded-lg bg-panel-hover border border-panel-border text-xs">
                        <span className="text-bullish font-bold">✓</span>
                        <span className="font-semibold text-accent">{f.field}</span>
                        <span className="text-muted-foreground">{f.op}</span>
                        <span className="text-bearish font-medium">{JSON.stringify(f.value)}</span>
                        {f.description && <span className="text-muted-foreground ml-auto italic">{f.description}</span>}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* 排序偏好 */}
              {currentSpec.rankings?.length > 0 && (
                <div className="mb-4">
                  <h3 className="text-xs text-muted-foreground mb-2 uppercase tracking-wider">
                    排序偏好 ({currentSpec.rankings.length})
                  </h3>
                  <div className="space-y-1.5">
                    {currentSpec.rankings.map((r, i) => (
                      <div key={i} className="flex items-center gap-2 px-3 py-2 rounded-lg bg-panel-hover border border-panel-border text-xs">
                        <ArrowUpDown className="w-3 h-3 text-warning" />
                        <span className="font-semibold text-foreground">{r.field}</span>
                        <span className="text-muted-foreground">权重 {r.weight}</span>
                        {r.description && <span className="text-muted-foreground ml-auto italic">{r.description}</span>}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* 技术面 */}
              {currentSpec.technicals?.length > 0 && (
                <div className="mb-4">
                  <h3 className="text-xs text-muted-foreground mb-2 uppercase tracking-wider">技术面要求</h3>
                  <div className="space-y-1.5">
                    {currentSpec.technicals.map((t, i) => (
                      <div key={i} className="px-3 py-2 rounded-lg bg-status-running/10 border border-status-running/20 text-xs text-status-running">
                        ⚡ {t}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* 解读 */}
              {currentSpec.explanation && (
                <div className="mb-4 p-3 rounded-lg bg-accent/5 border border-accent/20 text-xs text-accent/80 leading-relaxed">
                  <strong className="text-accent">策略解读：</strong>{currentSpec.explanation}
                </div>
              )}

              {/* 操作按钮 */}
              <div className="flex items-center gap-3 pt-3 border-t border-panel-border">
                <button
                  onClick={executeStrategy}
                  disabled={executing}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg bg-bullish/10 text-bullish text-sm font-medium hover:bg-bullish/20 transition-colors border border-bullish/20 disabled:opacity-50"
                >
                  {executing ? <Loader2 className="w-4 h-4 animate-spin" /> : <Play className="w-4 h-4" />}
                  {executing ? '选股中...' : '确认执行选股'}
                </button>
                <button
                  onClick={saveStrategy}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg bg-panel-hover text-muted-foreground text-sm hover:text-foreground transition-colors border border-panel-border"
                >
                  <Save className="w-4 h-4" />
                  保存策略
                </button>
                <button
                  onClick={editSpec}
                  className="flex items-center gap-2 px-4 py-2 rounded-lg bg-panel-hover text-muted-foreground text-sm hover:text-foreground transition-colors border border-panel-border"
                >
                  <Edit3 className="w-4 h-4" />
                  修改条件
                </button>
              </div>
            </div>
          )}

          {/* 已保存策略 */}
          <div className="data-card">
            <h2 className="text-sm font-semibold text-foreground mb-4 flex items-center gap-2">
              <Tag className="w-4 h-4 text-accent" />
              已保存策略
            </h2>
            <div className="space-y-2">
              {savedStrategies.length === 0 ? (
                <p className="text-sm text-muted-foreground text-center py-4">暂无保存的策略</p>
              ) : (
                savedStrategies.map((item) => (
                  <div
                    key={item.id}
                    className="flex items-center justify-between p-3 rounded-lg bg-panel-hover border border-panel-border hover:border-accent/30 transition-all"
                  >
                    <div>
                      <h4 className="text-sm font-medium text-foreground">{item.name}</h4>
                      <p className="text-[11px] text-muted-foreground">
                        {item.description} | {item.filters_count} 条件 + {item.rankings_count} 排序 | {new Date(item.created_at).toLocaleDateString('zh-CN')}
                      </p>
                    </div>
                    <div className="flex items-center gap-2">
                      <button
                        onClick={() => loadStrategy(item.id)}
                        className="px-3 py-1.5 rounded text-xs bg-accent/10 text-accent hover:bg-accent/20 transition-colors border border-accent/20"
                      >
                        加载
                      </button>
                      <button
                        onClick={() => deleteStrategy(item.id)}
                        className="px-3 py-1.5 rounded text-xs bg-status-error/10 text-status-error hover:bg-status-error/20 transition-colors border border-status-error/20"
                      >
                        <Trash2 className="w-3 h-3" />
                      </button>
                    </div>
                  </div>
                ))
              )}
            </div>
          </div>
        </div>

        {/* 右侧：选股结果 */}
        <div>
          <div className="data-card">
            <h2 className="text-sm font-semibold text-foreground mb-4 flex items-center gap-2">
              <BarChart3 className="w-4 h-4 text-accent" />
              选股结果
            </h2>

            {!results ? (
              <div className="text-center py-12 text-muted-foreground">
                <TargetIcon className="w-12 h-12 mx-auto mb-3 opacity-30" />
                <p>输入策略并执行后，结果将显示在这里</p>
              </div>
            ) : (
              <div>
                <div className="flex items-center justify-between mb-4">
                  <div>
                    <strong className="text-foreground">{results.strategy_name}</strong>
                    {results.is_mock_data && (
                      <span className="text-[11px] text-warning ml-2">(Mock数据)</span>
                    )}
                  </div>
                  <span className="text-xs text-muted-foreground">
                    总扫描 {results.total_stocks} 只 → 筛出 {results.filtered_count} 只
                  </span>
                </div>

                {results.picks.length === 0 ? (
                  <p className="text-center text-muted-foreground py-8">没有符合条件的股票</p>
                ) : (
                  <div className="overflow-x-auto max-h-[600px] overflow-y-auto">
                    <table className="w-full">
                      <thead className="sticky top-0 bg-panel">
                        <tr className="border-b border-panel-border">
                          <th className="text-left px-3 py-2 text-[10px] text-muted-foreground uppercase">#</th>
                          <th className="text-left px-3 py-2 text-[10px] text-muted-foreground uppercase">代码</th>
                          <th className="text-left px-3 py-2 text-[10px] text-muted-foreground uppercase">名称</th>
                          <th className="text-right px-3 py-2 text-[10px] text-muted-foreground uppercase">价格</th>
                          <th className="text-right px-3 py-2 text-[10px] text-muted-foreground uppercase">涨跌%</th>
                          <th className="text-left px-3 py-2 text-[10px] text-muted-foreground uppercase">行业</th>
                          <th className="text-right px-3 py-2 text-[10px] text-muted-foreground uppercase">PE</th>
                          <th className="text-right px-3 py-2 text-[10px] text-muted-foreground uppercase">评分</th>
                        </tr>
                      </thead>
                      <tbody>
                        {results.picks.map((p, i) => (
                          <tr key={p.symbol} className="border-b border-panel-border/50 hover:bg-panel-hover transition-colors">
                            <td className="px-3 py-2 text-xs text-muted-foreground">{i + 1}</td>
                            <td className="px-3 py-2 text-xs font-mono text-muted-foreground">{p.symbol}</td>
                            <td className="px-3 py-2 text-xs font-medium text-foreground">{p.name}</td>
                            <td className="px-3 py-2 text-xs font-mono text-right text-foreground">{p.price.toFixed(2)}</td>
                            <td className={cn(
                              'px-3 py-2 text-xs font-mono text-right',
                              p.change_pct >= 0 ? 'text-bullish' : 'text-bearish'
                            )}>
                              <div className="flex items-center justify-end gap-1">
                                {p.change_pct >= 0 ? <TrendingUp className="w-3 h-3" /> : <TrendingDown className="w-3 h-3" />}
                                {p.change_pct >= 0 ? '+' : ''}{p.change_pct.toFixed(2)}%
                              </div>
                            </td>
                            <td className="px-3 py-2">
                              <span className="text-[10px] px-1.5 py-0.5 rounded bg-accent/10 text-accent border border-accent/20">
                                {p.industry}
                              </span>
                            </td>
                            <td className="px-3 py-2 text-xs font-mono text-right text-muted-foreground">
                              {p.pe_ttm ? p.pe_ttm.toFixed(1) : '-'}
                            </td>
                            <td className="px-3 py-2 text-xs font-mono text-right text-accent font-bold">
                              {p.score.toFixed(1)}
                            </td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                )}
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function TargetIcon({ className }: { className?: string }) {
  return (
    <svg className={className} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}>
      <circle cx="12" cy="12" r="10" />
      <circle cx="12" cy="12" r="6" />
      <circle cx="12" cy="12" r="2" />
    </svg>
  )
}
