import { useState } from 'react'
import { cn } from '@/lib/utils'
import {
  Smartphone,
  RefreshCw,
  Send,
  Clock,
  CheckCircle2,
  Eye,
  Heart,
  MessageCircle,
  Bookmark,
  Calendar,
  AlertCircle,
  ImageIcon,
  Hash,
  User,
  ChevronRight,
} from 'lucide-react'

// ── 类型定义 ──
type PublishStatus = 'pending' | 'reviewing' | 'published'

interface ContentItem {
  id: string
  title: string
  content: string
  emojiTags: string[]
  status: PublishStatus
  scheduledAt?: string
  publishedAt?: string
  stats?: {
    views: number
    likes: number
    comments: number
    bookmarks: number
  }
}

// ── 模拟数据 ──
const aiReport = `📊 今日盘面速览 | 2026.05.23

【市场 regime】
今日 MarketBrain 判定: bull | posture=selective_attack
全市场上涨家数占比 62%，量能温和放大

【热点板块 TOP3】
🔥 人工智能 — 京东方A 放量突破，量能较昨日放大 180%
🔥 有色金属 — 铜铝联动，伦铜创 18 个月新高
🔥 白酒 — 茅台站稳 1688，北向资金净买入 8.2 亿

【交易信号】
✅ 买入: 京东方A (000725) @ 4.25
   策略: 热点板块前排回踩 + 放量突破确认
   仓位: 5% | 止损: 3.85 (-9.4%)

✅ 买入: 比亚迪 (002594) @ 245.80
   策略: 新能源车趋势延续
   仓位: 5% | 止损: 222.5 (-9.5%)

【风险提醒】
⚠️ 宁德时代今日放量下跌 2.55%，注意板块内分化
⚠️ 当前仓位 35%，距离 70% 上限仍有空间

—— 还没毕业的沈经理`;

const xiaohongshuPreview = `📊 今日盘面速览 | 还没毕业的沈经理

今天市场蛮强势的！
bull regime + selective_attack 💪

🔥 热点板块
❶ 人工智能 — 京东方A 放量突破
❷ 有色金属 — 铜铝联动创新高
❸ 白酒 — 茅台站稳，北向净买 8.2 亿

📌 今日操作
✅ 京东方A @ 4.25 (仓位 5%)
✅ 比亚迪 @ 245.80 (仓位 5%)

⚠️ 注意
宁德时代放量下跌 2.55%，板块分化明显
当前仓位 35%，留有进攻空间

#A股 #量化交易 #投资日记 #还没毕业的沈经理`;

const publishQueue: ContentItem[] = [
  {
    id: '1',
    title: '今日盘面速览 2026.05.23',
    content: xiaohongshuPreview,
    emojiTags: ['📊', '💪', '🔥', '✅'],
    status: 'pending',
    scheduledAt: '2026-05-23 15:45',
  },
  {
    id: '2',
    title: '京东方A 放量突破分析',
    content: '京东方A 今日放量上涨 3.66%...',
    emojiTags: ['📈', '🔥', '💡'],
    status: 'reviewing',
    scheduledAt: '2026-05-23 16:30',
  },
  {
    id: '3',
    title: '宁德时代为什么今天跌？',
    content: '宁德时代今日放量下跌 2.55%...',
    emojiTags: ['⚠️', '📉', '❓'],
    status: 'published',
    publishedAt: '2026-05-22 15:30',
    stats: { views: 2847, likes: 156, comments: 42, bookmarks: 89 },
  },
  {
    id: '4',
    title: '白酒板块北向资金大买',
    content: '茅台站稳 1688，北向净买入 8.2 亿...',
    emojiTags: ['🍶', '💰', '📊'],
    status: 'published',
    publishedAt: '2026-05-21 15:20',
    stats: { views: 4521, likes: 234, comments: 67, bookmarks: 123 },
  },
]

// ── 子组件：状态标签 ──
function StatusBadge({ status }: { status: PublishStatus }) {
  const config = {
    pending: { text: '待发布', className: 'bg-accent/20 text-accent', icon: Clock },
    reviewing: { text: '审核中', className: 'bg-warning/20 text-warning', icon: AlertCircle },
    published: { text: '已发布', className: 'bg-bullish/20 text-bullish', icon: CheckCircle2 },
  }
  const { text, className, icon: Icon } = config[status]

  return (
    <span className={cn('flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium', className)}>
      <Icon className="w-3 h-3" />
      {text}
    </span>
  )
}

// ── 子组件：小红书手机预览框 ──
function XiaohongshuPreview({ content }: { content: string }) {
  return (
    <div className="w-[320px] mx-auto bg-white rounded-[24px] overflow-hidden border-4 border-panel-border shadow-2xl">
      {/* 手机状态栏 */}
      <div className="bg-white px-5 pt-3 pb-2 flex items-center justify-between">
        <span className="text-xs font-semibold text-gray-900">9:41</span>
        <div className="flex gap-1">
          <div className="w-4 h-2.5 rounded-sm bg-gray-900" />
          <div className="w-3 h-2.5 rounded-sm bg-gray-900" />
        </div>
      </div>

      {/* 小红书导航 */}
      <div className="bg-white px-4 py-2 flex items-center justify-between border-b border-gray-100">
        <div className="flex items-center gap-2">
          <div className="w-7 h-7 rounded-full bg-gray-200 flex items-center justify-center">
            <User className="w-4 h-4 text-gray-500" />
          </div>
          <span className="text-xs font-medium text-gray-700">还没毕业的沈经理</span>
        </div>
        <button className="text-xs px-3 py-1 rounded-full bg-red-500 text-white font-medium">
          关注
        </button>
      </div>

      {/* 内容区 */}
      <div className="bg-white px-4 py-3">
        {/* 图片占位 */}
        <div className="w-full aspect-square rounded-xl bg-gradient-to-br from-gray-100 to-gray-200 flex items-center justify-center mb-3">
          <ImageIcon className="w-12 h-12 text-gray-300" />
        </div>

        {/* 文字内容 */}
        <div className="text-sm text-gray-800 leading-relaxed whitespace-pre-wrap">
          {content}
        </div>

        {/* 标签 */}
        <div className="flex flex-wrap gap-1.5 mt-3">
          {['#A股', '#量化交易', '#投资日记', '#还没毕业的沈经理'].map((tag) => (
            <span key={tag} className="text-xs text-blue-500">
              {tag}
            </span>
          ))}
        </div>

        {/* 时间 */}
        <div className="text-[11px] text-gray-400 mt-3">
          05-23 上海
        </div>
      </div>

      {/* 底部操作栏 */}
      <div className="bg-white px-4 py-3 border-t border-gray-100 flex items-center justify-between">
        <div className="flex items-center gap-4">
          <Heart className="w-5 h-5 text-gray-400" />
          <MessageCircle className="w-5 h-5 text-gray-400" />
          <Bookmark className="w-5 h-5 text-gray-400" />
        </div>
        <span className="text-[11px] text-gray-400">浏览 0</span>
      </div>
    </div>
  )
}

// ── 主页面 ──
export default function ContentPipeline() {
  const [previewContent, setPreviewContent] = useState(xiaohongshuPreview)
  const [activeTab, setActiveTab] = useState<'all' | 'pending' | 'published'>('all')

  const filteredQueue = publishQueue.filter((item) => {
    if (activeTab === 'pending') return item.status === 'pending' || item.status === 'reviewing'
    if (activeTab === 'published') return item.status === 'published'
    return true
  })

  return (
    <div className="space-y-6 h-full flex flex-col">
      {/* 页面标题 */}
      <div className="shrink-0">
        <h1 className="text-2xl font-bold text-foreground">社交媒体管线</h1>
        <p className="text-sm text-muted-foreground mt-1">
          AI 产出区 · 小红书预览 · 发布队列管理
        </p>
      </div>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6 flex-1 min-h-0">
        {/* 左侧：AI 产出区 + 编辑 */}
        <div className="space-y-4 overflow-y-auto">
          <section>
            <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider">
              AI 金融分析报告
            </h2>
            <div className="data-card">
              <div className="flex items-center justify-between mb-3">
                <div className="flex items-center gap-2">
                  <div className="w-8 h-8 rounded-lg bg-accent/10 flex items-center justify-center">
                    <Hash className="w-4 h-4 text-accent" />
                  </div>
                  <span className="text-sm font-medium text-foreground">原始分析报告</span>
                </div>
                <button
                  onClick={() => setPreviewContent(xiaohongshuPreview)}
                  className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-accent/10 text-accent text-xs font-medium hover:bg-accent/20 transition-colors border border-accent/20"
                >
                  <RefreshCw className="w-3.5 h-3.5" />
                  重新生成
                </button>
              </div>
              <textarea
                value={aiReport}
                readOnly
                className="w-full h-64 bg-panel-hover border border-panel-border rounded-lg p-3 text-sm text-foreground font-mono resize-none focus:outline-none"
              />
            </div>
          </section>

          {/* 小红书预览 */}
          <section>
            <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider">
              小红书预览
            </h2>
            <div className="data-card flex justify-center py-6">
              <XiaohongshuPreview content={previewContent} />
            </div>

            {/* 操作按钮 */}
            <div className="flex items-center gap-3 mt-3">
              <button className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg bg-accent/10 text-accent text-sm font-medium hover:bg-accent/20 transition-colors border border-accent/20">
                <RefreshCw className="w-4 h-4" />
                重新生成
              </button>
              <button className="flex-1 flex items-center justify-center gap-2 py-2.5 rounded-lg bg-bullish/10 text-bullish text-sm font-medium hover:bg-bullish/20 transition-colors border border-bullish/20">
                <Send className="w-4 h-4" />
                一键发布
              </button>
            </div>
          </section>
        </div>

        {/* 右侧：发布队列管理器 */}
        <section className="flex flex-col min-h-0">
          <div className="flex items-center justify-between mb-3 shrink-0">
            <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
              发布队列
            </h2>
            <div className="flex items-center gap-1 bg-panel-hover rounded-lg p-0.5 border border-panel-border">
              {([
                { key: 'all', label: '全部' },
                { key: 'pending', label: '待发布' },
                { key: 'published', label: '已发布' },
              ] as const).map((tab) => (
                <button
                  key={tab.key}
                  onClick={() => setActiveTab(tab.key)}
                  className={cn(
                    'px-3 py-1 rounded text-xs font-medium transition-colors',
                    activeTab === tab.key
                      ? 'bg-accent/20 text-accent'
                      : 'text-muted-foreground hover:text-foreground'
                  )}
                >
                  {tab.label}
                </button>
              ))}
            </div>
          </div>

          <div className="space-y-3 overflow-y-auto">
            {filteredQueue.map((item) => (
              <div
                key={item.id}
                className="data-card hover:border-accent/30 transition-all cursor-pointer group"
              >
                <div className="flex items-start justify-between mb-2">
                  <div className="flex items-center gap-2">
                    <StatusBadge status={item.status} />
                    <h3 className="text-sm font-medium text-foreground">{item.title}</h3>
                  </div>
                  <ChevronRight className="w-4 h-4 text-muted-foreground opacity-0 group-hover:opacity-100 transition-opacity" />
                </div>

                <p className="text-xs text-muted-foreground line-clamp-2 mb-3">
                  {item.content.slice(0, 100)}...
                </p>

                <div className="flex items-center justify-between">
                  <div className="flex items-center gap-1.5">
                    {item.emojiTags.map((emoji, i) => (
                      <span key={i} className="text-sm">{emoji}</span>
                    ))}
                  </div>

                  <div className="flex items-center gap-3 text-[11px] text-muted-foreground">
                    {item.scheduledAt && (
                      <span className="flex items-center gap-1">
                        <Calendar className="w-3 h-3" />
                        {item.scheduledAt}
                      </span>
                    )}
                    {item.publishedAt && (
                      <span className="flex items-center gap-1">
                        <CheckCircle2 className="w-3 h-3 text-bullish" />
                        {item.publishedAt}
                      </span>
                    )}
                  </div>
                </div>

                {/* 已发布内容的数据统计 */}
                {item.stats && (
                  <div className="flex items-center gap-4 mt-3 pt-3 border-t border-panel-border">
                    <span className="flex items-center gap-1 text-[11px] text-muted-foreground">
                      <Eye className="w-3 h-3" />
                      {item.stats.views.toLocaleString()}
                    </span>
                    <span className="flex items-center gap-1 text-[11px] text-bearish">
                      <Heart className="w-3 h-3" />
                      {item.stats.likes}
                    </span>
                    <span className="flex items-center gap-1 text-[11px] text-accent">
                      <MessageCircle className="w-3 h-3" />
                      {item.stats.comments}
                    </span>
                    <span className="flex items-center gap-1 text-[11px] text-bullish">
                      <Bookmark className="w-3 h-3" />
                      {item.stats.bookmarks}
                    </span>
                  </div>
                )}
              </div>
            ))}
          </div>
        </section>
      </div>
    </div>
  )
}
