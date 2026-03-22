import { useState } from 'react'

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
      <label className="block text-xs font-medium text-gray-700 mb-1">{label}</label>
      <div className="min-h-[42px] flex flex-wrap gap-1.5 items-center p-2 border border-gray-300 rounded-md focus-within:ring-2 focus-within:ring-blue-500 focus-within:border-blue-500">
        {tags.map((tag) => (
          <span
            key={tag}
            className="inline-flex items-center gap-1 px-2 py-0.5 text-xs bg-blue-50 text-blue-700 rounded border border-blue-200"
          >
            {tag}
            {!disabled && (
              <button
                type="button"
                onClick={() => onChange(tags.filter((t) => t !== tag))}
                className="text-blue-400 hover:text-blue-600 leading-none"
              >
                &times;
              </button>
            )}
          </span>
        ))}
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
            className="flex-1 min-w-[120px] outline-none text-sm bg-transparent"
          />
        )}
      </div>
      <p className="text-xs text-gray-400 mt-0.5">Press Enter or comma to add. Supports * wildcards.</p>
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
    <div className="space-y-4">
      <TagInput label="Allowed Tools" tags={allowedTools} onChange={onAllowedChange} disabled={disabled} />
      <TagInput label="Denied Tools" tags={deniedTools} onChange={onDeniedChange} disabled={disabled} />
    </div>
  )
}
