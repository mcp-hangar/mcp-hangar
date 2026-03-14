import { useEffect, useRef, useState } from 'react'
import type { LogLine } from '../../hooks/useProviderLogs'
import type { WsStatus } from '../../hooks/useWebSocket'

interface LogViewerProps {
  logs: LogLine[]
  status: WsStatus
  onClear: () => void
}

const STATUS_LABEL: Record<WsStatus, string> = {
  connected: 'Live',
  connecting: 'Connecting...',
  disconnected: 'Disconnected',
  error: 'Error',
}

const STATUS_DOT_CLASS: Record<WsStatus, string> = {
  connected: 'bg-green-400',
  connecting: 'bg-yellow-400 animate-pulse',
  disconnected: 'bg-gray-400',
  error: 'bg-red-400',
}

export function LogViewer({ logs, status, onClear }: LogViewerProps): JSX.Element {
  const bottomRef = useRef<HTMLDivElement>(null)
  const containerRef = useRef<HTMLDivElement>(null)
  const [autoScroll, setAutoScroll] = useState(true)

  // Auto-scroll to bottom when new lines arrive (only if autoScroll is enabled)
  useEffect(() => {
    if (autoScroll) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [logs, autoScroll])

  // Detect manual scroll-up to disable auto-scroll
  function handleScroll() {
    const el = containerRef.current
    if (!el) return
    const atBottom = el.scrollHeight - el.scrollTop - el.clientHeight < 8
    setAutoScroll(atBottom)
  }

  return (
    <div className="flex flex-col h-64">
      {/* Toolbar */}
      <div className="flex items-center justify-between px-3 py-1.5 border-b border-gray-200 bg-gray-50">
        <div className="flex items-center gap-2">
          <span
            className={`inline-block w-2 h-2 rounded-full ${STATUS_DOT_CLASS[status]}`}
            aria-hidden="true"
          />
          <span className="text-xs text-gray-500">{STATUS_LABEL[status]}</span>
          <span className="text-xs text-gray-400">{logs.length} lines</span>
        </div>
        <div className="flex items-center gap-3">
          <label className="flex items-center gap-1.5 cursor-pointer select-none">
            <input
              type="checkbox"
              className="w-3 h-3 rounded accent-blue-600"
              checked={autoScroll}
              onChange={(e) => setAutoScroll(e.target.checked)}
            />
            <span className="text-xs text-gray-500">Auto-scroll</span>
          </label>
          <button
            type="button"
            className="text-xs text-gray-400 hover:text-gray-600 transition-colors"
            onClick={onClear}
          >
            Clear
          </button>
        </div>
      </div>

      {/* Log output */}
      <div
        ref={containerRef}
        onScroll={handleScroll}
        className="flex-1 overflow-y-auto bg-gray-950 font-mono text-xs leading-5 p-3 space-y-0"
      >
        {logs.length === 0 ? (
          <span className="text-gray-600">No log output yet.</span>
        ) : (
          logs.map((line, idx) => (
            <div
              // eslint-disable-next-line react/no-array-index-key
              key={idx}
              className={line.stream === 'stderr' ? 'text-amber-400' : 'text-gray-300'}
            >
              <span className="text-gray-600 select-none mr-2">
                {new Date(line.recorded_at).toLocaleTimeString([], {
                  hour: '2-digit',
                  minute: '2-digit',
                  second: '2-digit',
                })}
              </span>
              {line.content}
            </div>
          ))
        )}
        <div ref={bottomRef} />
      </div>
    </div>
  )
}
