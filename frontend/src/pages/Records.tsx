import { useState, useEffect, useCallback } from 'react'
import { cn } from '@/lib/utils'
import {
  Upload,
  RefreshCw,
  Smartphone,
  MessageSquare,
  CheckCircle2,
  XCircle,
  AlertCircle,
  Activity,
  Clock,
  ImageIcon,
  FileText,
  Loader2,
  Wifi,
  WifiOff,
} from 'lucide-react'

// ── 类型定义 ──
interface ServiceStatus {
  name: string
  status: 'online' | 'offline' | 'checking'
  detail?: string
}

interface TradeRecord {
  id: string
  received_at: string
  source: string
  safe_text: string
  is_publishable: boolean
}

interface SocialPost {
  sma_task_id: string
  sma_status: 'pending' | 'running' | 'completed' | 'failed'
  topic: string
  dispatched_at: string | null
}

// ── Toast Hook ──
function useToast() {
  const [toasts, setToasts] = useState<{ id: number; msg: string; type: 'success' | 'error' }[]>([])

  const show = useCallback((msg: string, type: 'success' | 'error') => {
    const id = Date.now()
    setToasts((prev) => [...prev, { id, msg, type }])
    setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
    }, 3000)
  }, [])

  return { toasts, show }
}

// ── 状态徽章 ──
function StatusBadge({ status }: { status: SocialPost['sma_status'] }) {
  const config = {
    pending: { text: '排队中', className: 'bg-accent/20 text-accent', icon: Clock },
    running: { text: '运行中', className: 'bg-status-running/20 text-status-running', icon: Activity },
    completed: { text: '已完成', className: 'bg-bullish/20 text-bullish', icon: CheckCircle2 },
    failed: { text: '失败', className: 'bg-status-error/20 text-status-error', icon: XCircle },
  }
  const { text, className, icon: Icon } = config[status]

  return (
    <span className={cn('flex items-center gap-1 px-2 py-0.5 rounded text-[11px] font-medium', className)}>
      <Icon className="w-3 h-3" />
      {text}
    </span>
  )
}

// ── 主页面 ──
export default function Records() {
  const [services, setServices] = useState<ServiceStatus[]>([
    { name: 'Webhook Listener', status: 'checking' },
    { name: 'SMA 创作服务', status: 'checking' },
  ])
  const [records, setRecords] = useState<TradeRecord[]>([])
  const [posts, setPosts] = useState<SocialPost[]>([])
  const [loading, setLoading] = useState(false)
  const [submitting, setSubmitting] = useState(false)
  const { toasts, show } = useToast()

  const [formText, setFormText] = useState('')
  const [formSource, setFormSource] = useState('wechat')
  const [formImage, setFormImage] = useState<File | null>(null)

  // 检查服务状态
  const checkStatus = useCallback(async () => {
    const results: ServiceStatus[] = []

    try {
      const resp = await fetch('/health')
      if (resp.ok) {
        results.push({ name: 'Webhook Listener', status: 'online' })
      } else {
        results.push({ name: 'Webhook Listener', status: 'offline' })
      }
    } catch {
      results.push({ name: 'Webhook Listener', status: 'offline' })
    }

    try {
      const resp = await fetch('http://127.0.0.1:8003/health')
      if (resp.ok) {
        const data = await resp.json()
        results.push({ name: 'SMA 创作服务', status: 'online', detail: `${data.accounts?.length || 0} 账号` })
      } else {
        results.push({ name: 'SMA 创作服务', status: 'offline' })
      }
    } catch {
      results.push({ name: 'SMA 创作服务', status: 'offline' })
    }

    setServices(results)
  }, [])

  // 加载交易记录
  const loadRecords = useCallback(async () => {
    setLoading(true)
    try {
      const resp = await fetch('/webhook/records/recent?limit=10')
      const data = await resp.json()
      setRecords(data)
    } catch {
      show('加载交易记录失败', 'error')
    } finally {
      setLoading(false)
    }
  }, [show])

  // 加载社媒任务
  const loadPosts = useCallback(async () => {
    try {
      const resp = await fetch('/api/social-posts')
      const data = await resp.json()
      setPosts(data)
    } catch {
      show('加载社媒任务失败', 'error')
    }
  }, [show])

  // 上传表单
  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!formText.trim()) {
      show('请输入交易描述', 'error')
      return
    }

    setSubmitting(true)
    try {
      const formData = new FormData()
      formData.append('text', formText)
      formData.append('source', formSource)
      if (formImage) formData.append('image', formImage)

      const resp = await fetch('/webhook/trade', { method: 'POST', body: formData })
      const result = await resp.json()

      if (result.is_publishable) {
        show(`提交成功: ${result.record_id}`, 'success')
      } else {
        show(`需人工审核: ${result.forbidden_hits?.join(', ')}`, 'error')
      }

      setFormText('')
      setFormImage(null)
      setTimeout(() => { loadRecords(); loadPosts() }, 1000)
    } catch (err: any) {
      show(`提交失败: ${err.message}`, 'error')
    } finally {
      setSubmitting(false)
    }
  }

  useEffect(() => {
    checkStatus()
    loadRecords()
    loadPosts()

    const timer = setInterval(() => {
      checkStatus()
      loadPosts()
    }, 30000)

    return () => clearInterval(timer)
  }, [checkStatus, loadRecords, loadPosts])

  const recordCount = records.length
  const taskCount = posts.length
  const lastUpdate = new Date().toLocaleString('zh-CN')

  return (
    <div className="space-y-6 relative">
      {/* Toast */}
      <div className="fixed top-20 right-6 z-50 space-y-2">
        {toasts.map((t) => (
          <div
            key={t.id}
            className={cn(
              'px-4 py-3 rounded-lg text-sm font-medium shadow-lg animate-fade-in',
              t.type === 'success' ? 'bg-bullish/90 text-white' : 'bg-status-error/90 text-white'
            )}
          >
            {t.msg}
          </div>
        ))}
      </div>

      {/* 页面标题 */}
      <div>
        <h1 className="text-2xl font-bold text-foreground">交易记录管理</h1>
        <p className="text-sm text-muted-foreground mt-1">
          服务监控 · 交易记录上传 · 社媒发布任务
        </p>
      </div>

      {/* 服务状态卡片 */}
      <section>
        <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider">
          服务状态
        </h2>
        <div className="grid grid-cols-1 md:grid-cols-4 gap-4">
          {services.map((svc) => (
            <div key={svc.name} className="data-card">
              <div className="text-xs text-muted-foreground mb-2">{svc.name}</div>
              <div className="flex items-center gap-2">
                {svc.status === 'online' ? (
                  <>
                    <Wifi className="w-5 h-5 text-status-running" />
                    <span className="text-lg font-bold text-status-running">在线</span>
                    {svc.detail && <span className="text-xs text-muted-foreground">({svc.detail})</span>}
                  </>
                ) : svc.status === 'offline' ? (
                  <>
                    <WifiOff className="w-5 h-5 text-status-error" />
                    <span className="text-lg font-bold text-status-error">离线</span>
                  </>
                ) : (
                  <>
                    <Loader2 className="w-5 h-5 text-accent animate-spin" />
                    <span className="text-lg font-bold text-accent">检查中...</span>
                  </>
                )}
              </div>
            </div>
          ))}
          <div className="data-card">
            <div className="text-xs text-muted-foreground mb-2">今日交易记录</div>
            <div className="text-2xl font-bold text-foreground">{recordCount}</div>
          </div>
          <div className="data-card">
            <div className="text-xs text-muted-foreground mb-2">社媒任务</div>
            <div className="text-2xl font-bold text-foreground">{taskCount}</div>
          </div>
        </div>
      </section>

      {/* 上传表单 */}
      <section>
        <h2 className="text-sm font-semibold text-muted-foreground mb-3 uppercase tracking-wider">
          上传交易记录
        </h2>
        <form onSubmit={handleSubmit} className="data-card space-y-4">
          <div>
            <label className="block text-sm font-medium text-foreground mb-2">交易描述 *</label>
            <textarea
              value={formText}
              onChange={(e) => setFormText(e.target.value)}
              placeholder="例如：今天关注半导体板块，东芯股份688110值得留意，国产替代逻辑还在..."
              required
              className="w-full h-28 bg-panel-hover border border-panel-border rounded-lg p-3 text-sm text-foreground placeholder:text-muted-foreground focus:outline-none focus:border-accent/50 resize-vertical"
            />
          </div>
          <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
            <div>
              <label className="block text-sm font-medium text-foreground mb-2">截图（可选，自动脱敏）</label>
              <div className="relative">
                <input
                  type="file"
                  accept="image/*"
                  onChange={(e) => setFormImage(e.target.files?.[0] || null)}
                  className="w-full bg-panel-hover border border-panel-border rounded-lg px-3 py-2 text-sm text-foreground file:mr-4 file:py-1 file:px-3 file:rounded file:border-0 file:text-xs file:bg-accent/20 file:text-accent"
                />
                {formImage && (
                  <div className="mt-2 flex items-center gap-2 text-xs text-accent">
                    <ImageIcon className="w-3 h-3" />
                    {formImage.name}
                  </div>
                )}
              </div>
            </div>
            <div>
              <label className="block text-sm font-medium text-foreground mb-2">来源</label>
              <select
                value={formSource}
                onChange={(e) => setFormSource(e.target.value)}
                className="w-full bg-panel-hover border border-panel-border rounded-lg px-3 py-2 text-sm text-foreground focus:outline-none focus:border-accent/50"
              >
                <option value="wechat">微信</option>
                <option value="feishu">飞书</option>
                <option value="manual">手动录入</option>
              </select>
            </div>
          </div>
          <button
            type="submit"
            disabled={submitting}
            className="flex items-center gap-2 px-6 py-2.5 rounded-lg bg-accent/10 text-accent text-sm font-medium hover:bg-accent/20 transition-colors border border-accent/20 disabled:opacity-50"
          >
            {submitting ? <Loader2 className="w-4 h-4 animate-spin" /> : <Upload className="w-4 h-4" />}
            {submitting ? '提交中...' : '提交记录'}
          </button>
        </form>
      </section>

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        {/* 交易记录列表 */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
              近期交易记录
            </h2>
            <button
              onClick={loadRecords}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-panel-hover border border-panel-border text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              <RefreshCw className={cn('w-3.5 h-3.5', loading && 'animate-spin')} />
              刷新
            </button>
          </div>
          <div className="data-card overflow-hidden p-0">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-panel-border bg-panel-hover">
                    <th className="text-left px-4 py-3 text-[11px] font-medium text-muted-foreground uppercase">时间</th>
                    <th className="text-left px-4 py-3 text-[11px] font-medium text-muted-foreground uppercase">来源</th>
                    <th className="text-left px-4 py-3 text-[11px] font-medium text-muted-foreground uppercase">内容</th>
                    <th className="text-left px-4 py-3 text-[11px] font-medium text-muted-foreground uppercase">可发布</th>
                  </tr>
                </thead>
                <tbody>
                  {records.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="text-center py-8 text-sm text-muted-foreground">
                        {loading ? '加载中...' : '暂无记录'}
                      </td>
                    </tr>
                  ) : (
                    records.map((r) => (
                      <tr key={r.id} className="border-b border-panel-border/50 hover:bg-panel-hover transition-colors">
                        <td className="px-4 py-3 text-xs text-muted-foreground font-mono">
                          {new Date(r.received_at).toLocaleString('zh-CN')}
                        </td>
                        <td className="px-4 py-3">
                          <span className="text-[11px] px-2 py-0.5 rounded bg-panel-hover text-muted-foreground border border-panel-border capitalize">
                            {r.source === 'wechat' && <Smartphone className="w-3 h-3 inline mr-1" />}
                            {r.source === 'feishu' && <MessageSquare className="w-3 h-3 inline mr-1" />}
                            {r.source === 'manual' && <FileText className="w-3 h-3 inline mr-1" />}
                            {r.source}
                          </span>
                        </td>
                        <td className="px-4 py-3 text-xs text-foreground max-w-xs truncate">
                          {r.safe_text}
                        </td>
                        <td className="px-4 py-3">
                          {r.is_publishable ? (
                            <CheckCircle2 className="w-4 h-4 text-bullish" />
                          ) : (
                            <XCircle className="w-4 h-4 text-status-error" />
                          )}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
        </section>

        {/* 社媒任务列表 */}
        <section>
          <div className="flex items-center justify-between mb-3">
            <h2 className="text-sm font-semibold text-muted-foreground uppercase tracking-wider">
              社媒发布任务
            </h2>
            <button
              onClick={loadPosts}
              className="flex items-center gap-1.5 px-3 py-1.5 rounded-lg bg-panel-hover border border-panel-border text-xs text-muted-foreground hover:text-foreground transition-colors"
            >
              <RefreshCw className="w-3.5 h-3.5" />
              刷新
            </button>
          </div>
          <div className="data-card overflow-hidden p-0">
            <div className="overflow-x-auto">
              <table className="w-full">
                <thead>
                  <tr className="border-b border-panel-border bg-panel-hover">
                    <th className="text-left px-4 py-3 text-[11px] font-medium text-muted-foreground uppercase">任务ID</th>
                    <th className="text-left px-4 py-3 text-[11px] font-medium text-muted-foreground uppercase">状态</th>
                    <th className="text-left px-4 py-3 text-[11px] font-medium text-muted-foreground uppercase">主题</th>
                    <th className="text-left px-4 py-3 text-[11px] font-medium text-muted-foreground uppercase">发布时间</th>
                  </tr>
                </thead>
                <tbody>
                  {posts.length === 0 ? (
                    <tr>
                      <td colSpan={4} className="text-center py-8 text-sm text-muted-foreground">
                        暂无任务
                      </td>
                    </tr>
                  ) : (
                    posts.map((p) => (
                      <tr key={p.sma_task_id} className="border-b border-panel-border/50 hover:bg-panel-hover transition-colors">
                        <td className="px-4 py-3 text-xs font-mono text-muted-foreground">
                          {p.sma_task_id.substring(0, 12)}...
                        </td>
                        <td className="px-4 py-3">
                          <StatusBadge status={p.sma_status} />
                        </td>
                        <td className="px-4 py-3 text-xs text-foreground">
                          {p.topic ? `${p.topic.substring(0, 30)}...` : '-'}
                        </td>
                        <td className="px-4 py-3 text-xs text-muted-foreground font-mono">
                          {p.dispatched_at ? new Date(p.dispatched_at).toLocaleString('zh-CN') : '-'}
                        </td>
                      </tr>
                    ))
                  )}
                </tbody>
              </table>
            </div>
          </div>
          <div className="text-[11px] text-muted-foreground mt-2">
            上次更新: {lastUpdate}
          </div>
        </section>
      </div>
    </div>
  )
}
