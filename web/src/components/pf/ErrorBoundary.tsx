// Page-level error boundary (DESIGN §8.3): centred card, [Обновить], <details> with mono text.
import { Component, type ErrorInfo, type ReactNode } from 'react'
import { CircleAlert } from 'lucide-react'
import { Button } from '@/components/ui/button'

interface State { error: Error | null }

export class ErrorBoundary extends Component<{ children: ReactNode; resetKey?: unknown }, State> {
  state: State = { error: null }

  static getDerivedStateFromError(error: Error): State {
    return { error }
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error('page crashed', error, info.componentStack)
  }

  componentDidUpdate(prev: { resetKey?: unknown }) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState({ error: null })
  }

  render() {
    if (!this.state.error) return this.props.children
    return (
      <div className="flex min-h-[50vh] items-center justify-center p-4">
        <div className="w-full max-w-md rounded-lg border bg-card p-5 text-center">
          <CircleAlert className="mx-auto mb-2 size-6 text-destructive" />
          <div className="text-title-3">Что-то сломалось на этой странице</div>
          <Button className="mt-3" onClick={() => window.location.reload()}>Обновить</Button>
          <details className="mt-3 text-left">
            <summary className="cursor-pointer text-small text-muted-foreground">Технические детали</summary>
            <pre className="mt-2 overflow-auto rounded-sm bg-muted p-2 font-mono text-micro whitespace-pre-wrap">
              {String(this.state.error?.stack ?? this.state.error)}
            </pre>
          </details>
        </div>
      </div>
    )
  }
}
