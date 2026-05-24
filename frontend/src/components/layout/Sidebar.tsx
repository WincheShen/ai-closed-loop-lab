import { useState } from 'react'
import { NavLink, useLocation } from 'react-router-dom'
import { cn } from '@/lib/utils'
import {
  LayoutDashboard,
  LineChart,
  Bot,
  PenTool,
  ChevronLeft,
  ChevronRight,
  Zap,
  Target,
  Search,
  ClipboardList,
} from 'lucide-react'

const navItems = [
  {
    path: '/',
    label: '核心控制台',
    icon: LayoutDashboard,
    badge: 'Live',
  },
  {
    path: '/quant',
    label: '金融数据中台',
    icon: LineChart,
    badge: null,
    children: [
      { path: '/strategy', label: '策略选股', icon: Target },
      { path: '/analyze', label: '个股分析', icon: Search },
    ],
  },
  {
    path: '/agents',
    label: '多智能体工作流',
    icon: Bot,
    badge: '3 运行中',
  },
  {
    path: '/content',
    label: '社交媒体管线',
    icon: PenTool,
    badge: '2 待发',
  },
  {
    path: '/records',
    label: '交易记录管理',
    icon: ClipboardList,
    badge: null,
  },
]

export function Sidebar() {
  const [collapsed, setCollapsed] = useState(false)
  const [expandedGroup, setExpandedGroup] = useState<string | null>('/quant')
  const location = useLocation()

  return (
    <aside
      className={cn(
        'flex flex-col bg-panel border-r border-panel-border transition-all duration-300',
        collapsed ? 'w-16' : 'w-64'
      )}
    >
      {/* Logo 区 */}
      <div className="flex items-center gap-3 px-4 h-14 border-b border-panel-border shrink-0">
        <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-accent to-cyan-600 flex items-center justify-center shrink-0">
          <Zap className="w-5 h-5 text-background" />
        </div>
        {!collapsed && (
          <div className="flex flex-col">
            <span className="text-sm font-semibold text-foreground leading-tight">
              AI Lab
            </span>
            <span className="text-[10px] text-muted-foreground leading-tight tracking-wider">
              QUANT × CONTENT
            </span>
          </div>
        )}
      </div>

      {/* 导航区 */}
      <nav className="flex-1 py-4 px-2 space-y-1 overflow-y-auto">
        {navItems.map((item) => {
          const isActive = location.pathname === item.path
          const isChildActive = item.children?.some((c) => location.pathname === c.path)
          const isGroupActive = isActive || isChildActive
          const hasChildren = item.children && item.children.length > 0
          const isExpanded = expandedGroup === item.path

          return (
            <div key={item.path}>
              <NavLink
                to={item.path}
                onClick={(e) => {
                  if (hasChildren && !collapsed) {
                    e.preventDefault()
                    setExpandedGroup(isExpanded ? null : item.path)
                  }
                }}
                className={cn(
                  'flex items-center gap-3 px-3 py-2.5 rounded-lg transition-all duration-200 group relative',
                  isGroupActive
                    ? 'bg-accent/10 text-accent border border-accent/20'
                    : 'text-muted-foreground hover:text-foreground hover:bg-panel-hover'
                )}
              >
                <item.icon
                  className={cn(
                    'w-5 h-5 shrink-0',
                    isGroupActive && 'text-accent'
                  )}
                />
                {!collapsed && (
                  <>
                    <span className="text-sm font-medium flex-1">{item.label}</span>
                    {item.badge && (
                      <span
                        className={cn(
                          'text-[10px] px-1.5 py-0.5 rounded font-mono',
                          item.badge.includes('运行中')
                            ? 'bg-status-running/20 text-status-running'
                            : item.badge.includes('待发')
                            ? 'bg-warning/20 text-warning'
                            : 'bg-accent/20 text-accent'
                        )}
                      >
                        {item.badge}
                      </span>
                    )}
                    {hasChildren && (
                      <ChevronRight
                        className={cn(
                          'w-4 h-4 transition-transform',
                          isExpanded && 'rotate-90'
                        )}
                      />
                    )}
                  </>
                )}
                {/* 激活指示条 */}
                {isGroupActive && (
                  <div className="absolute left-0 top-1/2 -translate-y-1/2 w-[3px] h-6 bg-accent rounded-r-full" />
                )}
              </NavLink>

              {/* 子导航 */}
              {!collapsed && hasChildren && isExpanded && (
                <div className="ml-6 mt-1 space-y-0.5 border-l border-panel-border pl-2">
                  {item.children.map((child) => {
                    const childActive = location.pathname === child.path
                    return (
                      <NavLink
                        key={child.path}
                        to={child.path}
                        className={cn(
                          'flex items-center gap-2 px-3 py-2 rounded-lg text-sm transition-colors',
                          childActive
                            ? 'text-accent bg-accent/5'
                            : 'text-muted-foreground hover:text-foreground hover:bg-panel-hover'
                        )}
                      >
                        <child.icon className="w-4 h-4" />
                        {child.label}
                      </NavLink>
                    )
                  })}
                </div>
              )}
            </div>
          )
        })}
      </nav>

      {/* 折叠按钮 */}
      <div className="p-2 border-t border-panel-border shrink-0">
        <button
          onClick={() => setCollapsed(!collapsed)}
          className="flex items-center justify-center w-full py-2 rounded-lg text-muted-foreground hover:text-foreground hover:bg-panel-hover transition-colors"
        >
          {collapsed ? (
            <ChevronRight className="w-4 h-4" />
          ) : (
            <div className="flex items-center gap-2 text-xs">
              <ChevronLeft className="w-4 h-4" />
              <span>收起侧边栏</span>
            </div>
          )}
        </button>
      </div>
    </aside>
  )
}
