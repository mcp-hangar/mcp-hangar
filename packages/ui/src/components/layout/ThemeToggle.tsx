import { motion } from 'framer-motion'
import { Sun, Moon, Monitor } from 'lucide-react'
import { useThemeStore, type ThemePreference } from '../../store/theme'
import { cn } from '../../lib/cn'

const OPTIONS: { value: ThemePreference; icon: typeof Sun; label: string }[] = [
  { value: 'light', icon: Sun, label: 'Light' },
  { value: 'dark', icon: Moon, label: 'Dark' },
  { value: 'system', icon: Monitor, label: 'System' },
]

export function ThemeToggle(): JSX.Element {
  const { preference, setPreference } = useThemeStore()

  return (
    <div className="relative flex items-center rounded-lg bg-surface-secondary p-0.5 gap-0.5">
      {OPTIONS.map(({ value, icon: Icon, label }) => (
        <button
          key={value}
          type="button"
          aria-label={label}
          onClick={() => setPreference(value)}
          className={cn(
            'relative z-10 rounded-md px-1.5 py-1 transition-colors duration-150',
            preference === value ? 'text-accent' : 'text-text-faint hover:text-text-muted'
          )}
        >
          {preference === value && (
            <motion.div
              layoutId="theme-toggle-active"
              className="absolute inset-0 rounded-md bg-surface shadow-xs"
              transition={{ type: 'spring', stiffness: 500, damping: 35 }}
            />
          )}
          <Icon size={13} className="relative z-10" />
        </button>
      ))}
    </div>
  )
}
