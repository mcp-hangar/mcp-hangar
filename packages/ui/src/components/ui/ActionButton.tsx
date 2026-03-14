import React from 'react'
import { cn } from '../../lib/cn'
import { LoadingSpinner } from './LoadingSpinner'

interface ActionButtonProps {
  onClick: () => void
  isLoading?: boolean
  disabled?: boolean
  variant?: 'primary' | 'danger' | 'ghost'
  children: React.ReactNode
  className?: string
}

const VARIANT_STYLES: Record<'primary' | 'danger' | 'ghost', string> = {
  primary: 'bg-blue-600 text-white hover:bg-blue-700',
  danger: 'bg-red-600 text-white hover:bg-red-700',
  ghost: 'bg-transparent text-gray-700 border border-gray-300 hover:bg-gray-50',
}

export function ActionButton({
  onClick,
  isLoading = false,
  disabled = false,
  variant = 'primary',
  children,
  className,
}: ActionButtonProps): JSX.Element {
  return (
    <button
      type="button"
      onClick={onClick}
      disabled={disabled || isLoading}
      className={cn(
        'inline-flex items-center gap-1.5 px-3 py-1.5 text-sm font-medium rounded-md transition-colors',
        'disabled:opacity-50 disabled:cursor-not-allowed',
        VARIANT_STYLES[variant],
        className,
      )}
    >
      {isLoading && <LoadingSpinner size={12} />}
      {children}
    </button>
  )
}
