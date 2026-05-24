import { useNavigate } from 'react-router-dom'
import { Home, ArrowLeft } from 'lucide-react'

export default function NotFound() {
  const navigate = useNavigate()

  return (
    <div className="flex items-center justify-center min-h-screen bg-background">
      <div className="text-center space-y-6">
        <div className="space-y-2">
          <h1 className="text-9xl font-bold text-accent">404</h1>
          <p className="text-xl text-muted-foreground">页面未找到</p>
        </div>

        <p className="text-sm text-muted-foreground max-w-md mx-auto">
          您访问的页面不存在或已被移除。请检查 URL 或返回首页。
        </p>

        <div className="flex items-center justify-center gap-4">
          <button
            onClick={() => navigate(-1)}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-panel-hover border border-panel-border text-sm text-muted-foreground hover:text-foreground transition-colors"
          >
            <ArrowLeft className="w-4 h-4" />
            返回上一页
          </button>
          <button
            onClick={() => navigate('/')}
            className="flex items-center gap-2 px-4 py-2 rounded-lg bg-accent/10 text-accent text-sm font-medium hover:bg-accent/20 transition-colors border border-accent/20"
          >
            <Home className="w-4 h-4" />
            返回首页
          </button>
        </div>
      </div>
    </div>
  )
}
