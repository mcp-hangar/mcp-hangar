import { useState } from 'react'
import type { ToolInfo } from '../../types/provider'
import { EmptyState } from '../ui'
import { cn } from '../../lib/cn'

interface ToolListProps {
  tools: ToolInfo[]
  className?: string
}

export function ToolList({ tools, className }: ToolListProps): JSX.Element {
  const [expandedTool, setExpandedTool] = useState<string | null>(null)

  if (tools.length === 0) {
    return <EmptyState message="No tools registered." />
  }

  return (
    <ul className={cn('space-y-2', className)}>
      {tools.map((tool) => (
        <li key={tool.name} className="border border-gray-100 rounded-md p-2">
          <div className="flex items-center gap-2">
            <span className="text-sm font-mono font-medium text-gray-800">{tool.name}</span>
            {tool.description && (
              <span className="text-xs text-gray-500 ml-2">{tool.description}</span>
            )}
            {tool.schema && (
              <button
                type="button"
                className="text-xs text-blue-500 hover:underline ml-auto"
                onClick={() => setExpandedTool(expandedTool === tool.name ? null : tool.name)}
              >
                {expandedTool === tool.name ? 'Hide schema' : 'Show schema'}
              </button>
            )}
          </div>
          {expandedTool === tool.name && tool.schema && (
            <pre className="text-xs bg-gray-50 p-3 rounded overflow-x-auto mt-1">
              {JSON.stringify(tool.schema, null, 2)}
            </pre>
          )}
        </li>
      ))}
    </ul>
  )
}
