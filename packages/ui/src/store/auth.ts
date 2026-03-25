import { create } from 'zustand'

interface AuthPrincipal {
  id: string
  type: string
}

interface AuthState {
  isAuthenticated: boolean
  isLoading: boolean
  apiKey: string | null
  authRequired: boolean
  principal: AuthPrincipal | null
  setApiKey: (key: string) => void
  clearAuth: () => void
  setAuthenticated: (value: boolean) => void
  setLoading: (value: boolean) => void
  setAuthRequired: (value: boolean) => void
  setPrincipal: (principal: AuthPrincipal | null) => void
}

export const useAuthStore = create<AuthState>((set) => ({
  isAuthenticated: false,
  isLoading: true,
  apiKey: null,
  authRequired: false,
  principal: null,
  setApiKey: (key: string) => set({ apiKey: key, isAuthenticated: true }),
  clearAuth: () => set({ apiKey: null, isAuthenticated: false, principal: null }),
  setAuthenticated: (value: boolean) => set({ isAuthenticated: value }),
  setLoading: (value: boolean) => set({ isLoading: value }),
  setAuthRequired: (value: boolean) => set({ authRequired: value }),
  setPrincipal: (principal: AuthPrincipal | null) => set({ principal }),
}))
