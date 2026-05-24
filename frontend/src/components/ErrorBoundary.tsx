import { Component, ReactNode } from 'react'
import { AlertTriangle, RefreshCw } from 'lucide-react'

interface Props {
  children: ReactNode
}

interface State {
  hasError: boolean
  error?: Error
}

export class ErrorBoundary extends Component<Props, State> {
  constructor(props: Props) {
    super(props)
    this.state = { hasError: false }
  }

  static getDerivedStateFromError(error: Error): State {
    return { hasError: true, error }
  }

  componentDidCatch(error: Error, errorInfo: any) {
    console.error('ErrorBoundary caught an error:', error, errorInfo)
  }

  handleReset = () => {
    this.setState({ hasError: false, error: undefined })
  }

  render() {
    if (this.state.hasError) {
      return (
        <div className="flex items-center justify-center min-h-screen bg-background">
          <div className="text-center space-y-6 max-w-md">
            <div className="flex justify-center">
              <div className="w-16 h-16 rounded-full bg-status-error/20 flex items-center justify-center">
                <AlertTriangle className="w-8 h-8 text-status-error" />
              </div>
            </div>

            <div className="space-y-2">
              <h1 className="text-2xl font-bold text-foreground">出错了</h1>
              <p className="text-sm text-muted-foreground">
                应用遇到了一个意外错误。请尝试刷新页面或联系技术支持。
              </p>
            </div>

            {this.state.error && (
              <div className="p-4 rounded-lg bg-panel-hover border border-panel-border text-left">
                <p className="text-xs font-mono text-muted-foreground">
                  {this.state.error.message}
                </p>
              </div>
            )}

            <button
              onClick={this.handleReset}
              className="flex items-center justify-center gap-2 px-4 py-2 rounded-lg bg-accent/10 text-accent text-sm font-medium hover:bg-accent/20 transition-colors border border-accent/20"
            >
              <RefreshCw className="w-4 h-4" />
              重试
            </button>
          </div>
        </div>
      )
    }

    return this.props.children
  }
}
