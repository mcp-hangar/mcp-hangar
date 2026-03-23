import { useState } from 'react'
import { motion, AnimatePresence } from 'framer-motion'
import { X } from 'lucide-react'

interface TagInputProps {
  label: string
  tags: string[]
  onChange: (tags: string[]) => void
  disabled?: boolean
}

function TagInput({ label, tags, onChange, disabled }: TagInputProps): JSX.Element {
  const [inputValue, setInputValue] = useState('')

  function addTag(value: string): void {
    const trimmed = value.trim()
    if (trimmed && !tags.includes(trimmed)) {
      onChange([...tags, trimmed])
    }
    setInputValue('')
  }

  function handleKeyDown(e: React.KeyboardEvent<HTMLInputElement>): void {
    if (e.key === 'Enter' || e.key === ',') {
      e.preventDefault()
      addTag(inputValue)
    }
    if (e.key === 'Backspace' && inputValue === '' && tags.length > 0) {
      onChange(tags.slice(0, -1))
    }
  }

  return (
    <div>
      <label className="block text-xs font-medium text-text-secondary mb-1.5">{label}</label>
      <div className="min-h-[42px] flex flex-wrap gap-1.5 items-center p-2 bg-surface border border-border rounded-lg focus-within:ring-2 focus-within:ring-accent/30 focus-within:border-accent transition-all duration-150">
        <AnimatePresence>
          {tags.map((tag) => (
            <motion.span
              key={tag}
              initial={{ opacity: 0, scale: 0.8 }}
              animate={{ opacity: 1, scale: 1 }}
              exit={{ opacity: 0, scale: 0.8 }}
              transition={{ duration: 0.12 }}
              className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-accent-surface text-accent-text rounded-md border border-accent/15 font-mono"
            >
              {tag}
              {!disabled && (
                <button
                  type="button"
                  onClick={() => onChange(tags.filter((t) => t !== tag))}
                  className="text-accent/50 hover:text-accent transition-colors duration-100 rounded-sm hover:bg-accent/10 p-px"
                >
                  <X size={10} />
                </button>
              )}
            </motion.span>
          ))}
        </AnimatePresence>
        {!disabled && (
          <input
            type="text"
            value={inputValue}
            onChange={(e) => setInputValue(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={() => {
              if (inputValue.trim()) addTag(inputValue)
            }}
            placeholder={tags.length === 0 ? 'Type and press Enter...' : ''}
            className="flex-1 min-w-[120px] outline-none text-sm bg-transparent text-text-primary placeholder:text-text-faint"
          />
        )}
      </div>
      <p className="text-xs text-text-faint mt-1">Press Enter or comma to add. Supports * wildcards.</p>
    </div>
  )
}

interface ToolAccessPolicyEditorProps {
  allowedTools: string[]
  deniedTools: string[]
  onAllowedChange: (tools: string[]) => void
  onDeniedChange: (tools: string[]) => void
  disabled?: boolean
}

export function ToolAccessPolicyEditor({
  allowedTools,
  deniedTools,
  onAllowedChange,
  onDeniedChange,
  disabled,
}: ToolAccessPolicyEditorProps): JSX.Element {
  return (
    <div className="space-y-5">
      <TagInput label="Allowed Tools" tags={allowedTools} onChange={onAllowedChange} disabled={disabled} />
      <TagInput label="Denied Tools" tags={deniedTools} onChange={onDeniedChange} disabled={disabled} />
    </div>
  )
}
