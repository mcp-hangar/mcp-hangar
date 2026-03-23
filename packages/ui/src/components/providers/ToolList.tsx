import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { ChevronRight } from 'lucide-react'
import type { ToolInfo } from '../../types/provider'
import { EmptyState } from '../ui'
import { cn } from '../../lib/cn'
import { staggerContainer, staggerItem, expandVariants } from '../../lib/animations'

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
    <motion.ul variants={staggerContainer} initial="hidden" animate="visible" className={cn('space-y-1.5', className)}>
      {tools.map((tool) => {
        const isExpanded = expandedTool === tool.name
        return (
          <motion.li
            key={tool.name}
            variants={staggerItem}
            className="border border-border rounded-lg bg-surface transition-colors duration-150 hover:bg-surface-secondary/50 overflow-hidden"
          >
            <div className="flex items-center gap-3 px-3.5 py-2.5">
              <span className="text-sm font-mono font-medium text-text-primary">{tool.name}</span>
              {tool.description && <span className="text-xs text-text-muted truncate">{tool.description}</span>}
              {tool.schema && (
                <button
                  type="button"
                  className="ml-auto flex items-center gap-1 text-xs text-text-faint hover:text-accent transition-colors duration-150 shrink-0"
                  onClick={() => setExpandedTool(isExpanded ? null : tool.name)}
                >
                  Schema
                  <motion.span
                    animate={{ rotate: isExpanded ? 90 : 0 }}
                    transition={{ duration: 0.15 }}
                    className="inline-flex"
                  >
                    <ChevronRight size={12} />
                  </motion.span>
                </button>
              )}
            </div>
            <AnimatePresence>
              {isExpanded && tool.schema && (
                <motion.div
                  variants={expandVariants}
                  initial="hidden"
                  animate="visible"
                  exit="exit"
                  className="overflow-hidden"
                >
                  <pre className="text-xs bg-surface-secondary text-text-secondary px-3.5 py-3 border-t border-border overflow-x-auto font-mono leading-relaxed">
                    {JSON.stringify(tool.schema, null, 2)}
                  </pre>
                </motion.div>
              )}
            </AnimatePresence>
          </motion.li>
        )
      })}
    </motion.ul>
  )
}
