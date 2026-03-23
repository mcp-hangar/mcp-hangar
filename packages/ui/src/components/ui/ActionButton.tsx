import React from 'react'
import { motion } from 'framer-motion'
import { cn } from '../../lib/cn'
import { LoadingSpinner } from './LoadingSpinner'
import { buttonTap } from '../../lib/animations'

interface ActionButtonProps {
  onClick: () => void
  isLoading?: boolean
  disabled?: boolean
  variant?: 'primary' | 'danger' | 'ghost'
  size?: 'sm' | 'md'
  children: React.ReactNode
  className?: string
}

const VARIANT_STYLES: Record<'primary' | 'danger' | 'ghost', string> = {
  primary: 'bg-accent text-white shadow-xs hover:bg-accent-hover hover:shadow-sm active:shadow-xs',
  danger: 'bg-danger text-white shadow-xs hover:bg-danger-hover hover:shadow-sm active:shadow-xs',
  ghost: 'text-text-secondary border border-border hover:bg-surface-secondary hover:border-border-strong',
}

const SIZE_STYLES: Record<'sm' | 'md', string> = {
  sm: 'px-2.5 py-1 text-xs gap-1',
  md: 'px-3.5 py-1.5 text-sm gap-1.5',
}

export function ActionButton({
  onClick,
  isLoading = false,
  disabled = false,
  variant = 'primary',
  size = 'md',
  children,
  className,
}: ActionButtonProps): JSX.Element {
  return (
    <motion.button
      type="button"
      onClick={onClick}
      disabled={disabled || isLoading}
      whileTap={disabled ? undefined : buttonTap}
      className={cn(
        'inline-flex items-center font-medium rounded-lg transition-all duration-150',
        'disabled:opacity-40 disabled:cursor-not-allowed disabled:pointer-events-none',
        VARIANT_STYLES[variant],
        SIZE_STYLES[size],
        className
      )}
    >
      {isLoading && <LoadingSpinner size={size === 'sm' ? 10 : 12} />}
      {children}
    </motion.button>
  )
}
