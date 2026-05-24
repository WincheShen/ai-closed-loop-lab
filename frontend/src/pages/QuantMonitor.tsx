import { useState } from 'react'
import { cn, formatPercent } from '@/lib/utils'
import {
  Search,
  Filter,
  ArrowUpDown,
  TrendingUp,
  TrendingDown,
  Eye,
  Activity,
  BarChart3,
  CandlestickChart,
  Minus,
} from 'lucide-react'

// ── 类型定义 ──
interface StockData {
  symbol: string
  name: string
  price: number
  change: number
  changePercent: number
  volume: string
  turnover: string
  pe: number | null
  pb: number
  marketCap: string
  sector: string
  anomaly: 'surge' | 'drop' | 'volume_spike' | null
}

interface WatchlistItem {
  symbol: string
  name: string
  price: number
  changePercent: number
  alert: boolean
}

// ── 模拟数据 ──
const stockData: StockData[] = [
  { symbol: '000725', name: '京东方A', price: 4.25, change: 0.15, changePercent: 3.66, volume: '284.5万', turnover: '12.1亿', pe: 45.2, pb: 1.12, marketCap: '1632亿', sector: '面板', anomaly: 'surge' },
  { symbol: '600519', name: '贵州茅台', price: 1688.00, change: 12.50, changePercent: 0.75, volume: '1.2万', turnover: '20.3亿', pe: 28.5, pb: 8.92, marketCap: '2.1万亿', sector: '白酒', anomaly: null },
  { symbol: '300750', name: '宁德时代', price: 198.50, change: -5.20, changePercent: -2.55, volume: '45.6万', turnover: '90.5亿', pe: 22.1, pb: 4.56, marketCap: '8723亿', sector: '电池', anomaly: 'drop' },
  { symbol: '002594', name: '比亚迪', price: 245.80, change: 8.30, changePercent: 3.49, volume: '32.1万', turnover: '78.9亿', pe: 25.8, pb: 3.89, marketCap: '7156亿', sector: '新能源车', anomaly: 'volume_spike' },
  { symbol: '688981', name: '中芯国际', price: 52.35, change: 1.85, changePercent: 3.66, volume: '89.4万', turnover: '46.8亿', pe: 85.6, pb: 3.21, marketCap: '4182亿', sector: '半导体', anomaly: 'surge' },
  { symbol: '600036', name: '招商银行', price: 35.20, change: -0.30, changePercent: -0.84, volume: '56.7万', turnover: '19.9亿', pe: 5.8, pb: 0.92, marketCap: '8885亿', sector: '银行', anomaly: null },
  { symbol: '000858', name: '五粮液', price: 145.60, change: 2.10, changePercent: 1.46, volume: '8.9万', turnover: '12.9亿', pe: 18.2, pb: 4.15, marketCap: '5654亿', sector: '白酒', anomaly: null },
]

const watchlist: WatchlistItem[] = [
  { symbol: '000725', name: '京东方A', price: 4.25, changePercent: 3.66, alert: true },
  { symbol: '600519', name: '贵州茅台', price: 1688.00, changePercent: 0.75, alert: false },
  { symbol: '300750', name: '宁德时代', price: 198.50, changePercent: -2.55, alert: true },
  { symbol: '002594', name: '比亚迪', price: 245.80, changePercent: 3.49, alert: false },
  { symbol: '688981', name: '中芯国际', price: 52.35, changePercent: 3.66, alert: true },
]

// ── 子组件：异动标签 ──
function AnomalyBadge({ type }: { type: StockData['anomaly'] }) {
  if (!type) return null
  const config = {
    surge: { text: '放量上涨', className: 'bg-bullish/20 text-bullish' },
    drop: { text: '放量下跌', className: 'bg-bearish/20 text-bearish' },
    volume_spike: { text: '量能异动', className: 'bg-warning/20 text-warning' },
  }
  const { text, className } = config[type]
  return (
    <span className={cn('text-[10px] px-1.5 py-0.5 rounded font-medium', className)}>
      {text}
    </span>
  )
}

// ── K 线占位组件 ──
function CandlestickPlaceholder() {
  return (
    <div className="chart-placeholder h-80 relative overflow-hidden">
      {/* 模拟 K 线 */}
      <div className="absolute inset-0 flex items-end justify-around px-4 pb-8 pt-12">
        {Array.from({ length: 40 }).map((_, i) => {
          const height = 20 + Math.random() * 60
          const isBullish = Math.random() > 0.4
          return (
            <div key={i} className="flex flex-col items-center gap-0.5 w-2">
              <div
                className={cn('w-px h-3', isBullish ? 'bg-bullish' : 'bg-bearish')}
              />
              <div
                className={cn(
                  'w-full rounded-sm',
                  isBullish ? 'bg-bullish/70' : 'bg-bearish/70'
                )}
                style={{ height: `${height}%` }}
              />
              <div
                className={cn('w-px h-3', isBullish ? 'bg-bullish' : 'bg-bearish')}
              />
            </div>
          )
        })}
      </div>
      {/* 网格线 */}
      <div className="absolute inset-0 pointer-events-none">
        {[...Array(5)].map((_, i) => (
          <div
            key={i}
            className="absolute left-0 right-0 border-t border-panel-border/30"
            style={{ top: `${20 + i * 15}%` }}
          />
        ))}
      </div>
      {/* 标签 */}
      <div className="absolute bottom-2 left-2 flex items-center gap-2 text-xs text-muted-foreground">
        <CandlestickChart className="w-4 h-4 text-accent" />
        <span>600519 贵州茅台 — 日K</span>
      </div>
    </div>
  )
}

// ── MACD / KDJ 缩略图 ──
function IndicatorThumbnail({ title, color }: { title: string; color: string }) {
  return (
    <div className="data-card">
      <div className="text-xs text-muted-foreground mb-2 flex items-center gap-2">
        <BarChart3 className="w-3.5 h-3.5" style={{ color }} />
        {title}
      </div>
      <svg viewBox="0 0 200 60" className="w-full h-14">
        <polyline
          fill="none"
          stroke={color}
          strokeWidth={1.5}
          points={Array.from({ length: 20 }).map((_, i) => {
            const x = (i / 19) * 200
            const y = 30 + Math.sin(i * 0.5) * 15 + (Math.random() - 0.5) * 10
            return `${x},${y}`
          }).join(' ')}
        />
        <polyline
          fill="none"
          stroke={color}
          strokeWidth={1}
          strokeDasharray="2,2"
          opacity={0.5}
          points={Array.from({ length: 20 }).map((_, i) => {
            const x = (i / 19) * 200
            const y = 30 + Math.cos(i * 0.5) * 10 + (Math.random() - 0.5) * 8
            return `${x},${y}`
          }).join(' ')}
        />
      </svg>
    </div>
  )
}

// ── 主页面 ──
export default function QuantMonitor() {
  const [searchTerm, setSearchTerm] = useState('')
  const [sortField, setSortField] = useState<keyof StockData>('changePercent')
  const [sortDesc, setSortDesc] = useState(true)

  const filteredData = stockData
    .filter((s) => s.symbol.includes(searchTerm) || s.name.includes(searchTerm))
    .sort((a, b) => {
      const aVal = a[sortField] ?? 0
      const bVal = b[sortField] ?? 0
      return sortDesc ? (bVal as number) - (aVal as number) : (aVal as number) - (bVal as number)
    })

  const toggleSort = (field: keyof StockData) => {
    if (sortField === field) {
      setSortDesc(!sortDesc)
    } else {
      setSortField(field)
      setSortDesc(true)
    }
  }

  return (
    <div className="space-y-6">
      {/* 页面标题 */}
      <div>
        <h1 className="text-2xl font-bold text-foreground">金融数据中台</h1>
        <p className="text-sm text-muted-foreground mt-1">
          AKShare 实时数据流 · 技术面分析 · 关注列表
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-4 gap-6">
        {/* 左侧主内容区 */}
        <div className="lg:col-span-3 space-y-6">
          {/* AKShare 数据流面板 */}
          <section>
            <div className="flex items-center justify-between mb-3">
              <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
                AKShare 实时异动数据
              </h2>
              <div className="flex items-center gap-2">
                <div className="relative">
                  <Search className="absolute left-2.5 top-1/2 -translate-y-1/2 w-4 h-4 text-muted-foreground" />
                  <input
                    type="text"
                    placeholder="搜索股票代码/名称..."
                    value={searchTerm}
                    onChange={(e) => setSearchTerm(e.target.value)}
                    className="w-56 bg-panel border border-panel-border rounded-lg pl-9 pr-3 py-1.5 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-accent/50"
                  />
                </div>
                <button className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-panel-hover border border-panel-border text-xs text-muted-foreground hover:text-foreground">
                  <Filter className="w-3.5 h-3.5" />
                  筛选
                </button>
              </div>
            </div>

            <div className="data-card overflow-hidden p-0">
              <div className="overflow-x-auto">
                <table className="w-full">
                  <thead>
                    <tr className="border-b border-panel-border bg-panel-hover">
                      {[
                        { key: 'symbol', label: '代码' },
                        { key: 'name', label: '名称' },
                        { key: 'price', label: '最新价' },
                        { key: 'changePercent', label: '涨跌幅' },
                        { key: 'volume', label: '成交量' },
                        { key: 'turnover', label: '成交额' },
                        { key: 'pe', label: 'PE' },
                        { key: 'pb', label: 'PB' },
                        { key: 'marketCap', label: '市值' },
                        { key: 'sector', label: '板块' },
                      ].map((col) => (
                        <th
                          key={col.key}
                          className="text-left px-4 py-3 text-[11px] font-medium text-muted-foreground uppercase tracking-wider cursor-pointer hover:text-foreground"
                          onClick={() => toggleSort(col.key as keyof StockData)}
                        >
                          <div className="flex items-center gap-1">
                            {col.label}
                            {sortField === col.key && (
                              <ArrowUpDown className="w-3 h-3 text-accent" />
                            )}
                          </div>
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {filteredData.map((stock) => {
                      const isBullish = stock.changePercent >= 0
                      return (
                        <tr
                          key={stock.symbol}
                          className="border-b border-panel-border/50 hover:bg-panel-hover transition-colors"
                        >
                          <td className="px-4 py-3 text-xs font-mono text-muted-foreground">
                            {stock.symbol}
                          </td>
                          <td className="px-4 py-3">
                            <div className="flex items-center gap-2">
                              <span className="text-sm font-medium text-foreground">{stock.name}</span>
                              <AnomalyBadge type={stock.anomaly} />
                            </div>
                          </td>
                          <td className="px-4 py-3 text-sm font-mono text-foreground">
                            {stock.price.toFixed(2)}
                          </td>
                          <td className="px-4 py-3">
                            <div className={cn(
                              'flex items-center gap-1 text-sm font-mono',
                              isBullish ? 'text-bullish' : 'text-bearish'
                            )}>
                              {isBullish ? (
                                <TrendingUp className="w-3.5 h-3.5" />
                              ) : (
                                <TrendingDown className="w-3.5 h-3.5" />
                              )}
                              {formatPercent(stock.changePercent)}
                            </div>
                          </td>
                          <td className="px-4 py-3 text-xs text-muted-foreground font-mono">{stock.volume}</td>
                          <td className="px-4 py-3 text-xs text-muted-foreground font-mono">{stock.turnover}</td>
                          <td className="px-4 py-3 text-xs text-muted-foreground font-mono">
                            {stock.pe ?? '-'}
                          </td>
                          <td className="px-4 py-3 text-xs text-muted-foreground font-mono">{stock.pb.toFixed(2)}</td>
                          <td className="px-4 py-3 text-xs text-muted-foreground font-mono">{stock.marketCap}</td>
                          <td className="px-4 py-3">
                            <span className="text-[11px] px-2 py-0.5 rounded bg-panel-hover text-muted-foreground border border-panel-border">
                              {stock.sector}
                            </span>
                          </td>
                        </tr>
                      )
                    })}
                  </tbody>
                </table>
              </div>
            </div>
          </section>

          {/* 技术面分析区 */}
          <section>
            <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider">
              技术面分析
            </h2>
            <CandlestickPlaceholder />
            <div className="grid grid-cols-2 gap-4 mt-4">
              <IndicatorThumbnail title="MACD" color="#00f0ff" />
              <IndicatorThumbnail title="KDJ" color="#ffab00" />
            </div>
          </section>
        </div>

        {/* 右侧关注列表 */}
        <aside className="space-y-4">
          <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
            关注列表
          </h2>
          <div className="data-card space-y-3">
            {watchlist.map((item) => {
              const isBullish = item.changePercent >= 0
              return (
                <div
                  key={item.symbol}
                  className="flex items-center justify-between p-2.5 rounded-lg bg-panel-hover border border-panel-border hover:border-accent/30 transition-all cursor-pointer group"
                >
                  <div className="flex items-center gap-2">
                    {item.alert && (
                      <div className="w-1.5 h-1.5 rounded-full bg-warning animate-pulse" />
                    )}
                    <div>
                      <div className="text-sm font-medium text-foreground">{item.name}</div>
                      <div className="text-[10px] text-muted-foreground font-mono">{item.symbol}</div>
                    </div>
                  </div>
                  <div className="text-right">
                    <div className="text-sm font-mono text-foreground">{item.price.toFixed(2)}</div>
                    <div className={cn(
                      'text-xs font-mono',
                      isBullish ? 'text-bullish' : 'text-bearish'
                    )}>
                      {formatPercent(item.changePercent)}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>

          {/* 快速操作 */}
          <div className="data-card">
            <h3 className="text-xs font-semibold text-muted-foreground mb-3 uppercase">快捷操作</h3>
            <div className="space-y-2">
              <button className="w-full flex items-center justify-center gap-2 py-2 rounded-lg bg-accent/10 text-accent text-sm font-medium hover:bg-accent/20 transition-colors border border-accent/20">
                <Eye className="w-4 h-4" />
                添加自选股
              </button>
              <button className="w-full flex items-center justify-center gap-2 py-2 rounded-lg bg-panel-hover text-muted-foreground text-sm hover:text-foreground transition-colors border border-panel-border">
                <Activity className="w-4 h-4" />
                设置异动提醒
              </button>
            </div>
          </div>
        </aside>
      </div>
    </div>
  )
}
