import { create } from 'zustand'
import { persist } from 'zustand/middleware'

export type ThemePreference = 'light' | 'dark' | 'system'

interface ThemeState {
  /** User preference stored in localStorage */
  preference: ThemePreference
  /** Resolved effective theme after evaluating system preference */
  resolved: 'light' | 'dark'
  setPreference: (pref: ThemePreference) => void
}

function resolveTheme(preference: ThemePreference): 'light' | 'dark' {
  if (preference !== 'system') return preference
  if (typeof window === 'undefined') return 'light'
  return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
}

/** Apply the resolved theme to the <html> element. */
export function applyTheme(resolved: 'light' | 'dark'): void {
  const root = document.documentElement
  if (resolved === 'dark') {
    root.classList.add('dark')
  } else {
    root.classList.remove('dark')
  }
}

export const useThemeStore = create<ThemeState>()(
  persist(
    (set) => ({
      preference: 'system',
      resolved: resolveTheme('system'),
      setPreference: (pref) => {
        const resolved = resolveTheme(pref)
        applyTheme(resolved)
        set({ preference: pref, resolved })
      },
    }),
    {
      name: 'mcp-hangar-theme',
      partialize: (state) => ({ preference: state.preference }),
      onRehydrateStorage: () => (state) => {
        if (state) {
          const resolved = resolveTheme(state.preference)
          applyTheme(resolved)
          state.resolved = resolved
        }
      },
    }
  )
)

/** Initialise theme on app start. Call once in main.tsx. */
export function initTheme(): void {
  const stored = localStorage.getItem('mcp-hangar-theme')
  let preference: ThemePreference = 'system'
  if (stored) {
    try {
      const parsed = JSON.parse(stored)
      if (parsed?.state?.preference) {
        preference = parsed.state.preference as ThemePreference
      }
    } catch {
      // Corrupted storage — fall back to system
    }
  }
  applyTheme(resolveTheme(preference))

  // Listen for OS theme changes (only relevant when preference === 'system')
  const mql = window.matchMedia('(prefers-color-scheme: dark)')
  mql.addEventListener('change', () => {
    const current = useThemeStore.getState()
    if (current.preference === 'system') {
      const resolved = resolveTheme('system')
      applyTheme(resolved)
      useThemeStore.setState({ resolved })
    }
  })
}
