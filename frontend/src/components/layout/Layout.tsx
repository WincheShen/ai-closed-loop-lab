import { cn } from '@/lib/utils'
import { Sidebar } from './Sidebar'
import { TopBar } from './TopBar'

interface LayoutProps {
  children: React.ReactNode
}

export function Layout({ children }: LayoutProps) {
  return (
    <div className="flex h-screen w-full bg-background overflow-hidden">
      {/* 左侧全局导航侧边栏 */}
      <Sidebar />

      {/* 主内容区 */}
      <div className="flex flex-col flex-1 min-w-0">
        {/* 顶部状态栏 */}
        <TopBar />

        {/* 核心工作区 */}
        <main className="flex-1 overflow-y-auto p-6">
          <div className="mx-auto max-w-[1600px]">
            {children}
          </div>
        </main>
      </div>
    </div>
  )
}
