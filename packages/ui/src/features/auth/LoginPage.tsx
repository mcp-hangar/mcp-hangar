import { useState, type FormEvent } from 'react'
import { Shield } from 'lucide-react'
import { useAuthStore } from '../../store/auth'
import { apiClient } from '../../api/client'
import { HangarApiError } from '../../types/common'

interface MeResponse {
  authenticated: boolean
  principal: { id: string; type: string } | null
}

/**
 * Standalone login page -- rendered outside the main Layout (no sidebar/header).
 * v8.0 Phase 47: Auth Guard.
 *
 * Accepts an API key, validates it against GET /api/system/me, and stores it
 * in the Zustand auth store (memory-only, no persistence).
 */
export function LoginPage(): JSX.Element {
  const [apiKeyInput, setApiKeyInput] = useState('')
  const [error, setError] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  async function handleSubmit(e: FormEvent): Promise<void> {
    e.preventDefault()
    setIsSubmitting(true)
    setError(null)

    // Store the key first so apiClient injects it on the validation request
    useAuthStore.getState().setApiKey(apiKeyInput)

    try {
      const result = await apiClient.get<MeResponse>('/system/me')
      if (result.authenticated) {
        useAuthStore.getState().setAuthenticated(true)
        useAuthStore.getState().setPrincipal(result.principal)
      } else {
        // Server says not authenticated even with key -- key is invalid
        useAuthStore.getState().clearAuth()
        setError('Invalid API key')
      }
    } catch (err: unknown) {
      useAuthStore.getState().clearAuth()
      if (err instanceof HangarApiError && err.status === 401) {
        setError('Invalid API key')
      } else {
        setError('Connection failed. Please check the server is running.')
      }
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <div className="min-h-screen flex flex-col items-center justify-center p-6 bg-surface antialiased selection:bg-accent/30">
      <div className="w-full max-w-[440px] flex flex-col items-center space-y-10">
        {/* Logo */}
        <div className="flex flex-col items-center gap-4">
          <div className="w-12 h-12 btn-gradient flex items-center justify-center rounded-lg shadow-lg shadow-accent/20">
            <span className="text-2xl font-black tracking-tighter">H</span>
          </div>
          <h1 className="text-xl font-bold tracking-tight text-text-primary">MCP Hangar</h1>
        </div>

        {/* Login Card */}
        <div
          className="w-full bg-surface-secondary p-10 rounded-xl shadow-xl"
          style={{ border: '1px solid rgba(66, 71, 84, 0.20)' }}
        >
          <div className="mb-10 text-center">
            <h2 className="text-2xl font-bold text-text-primary tracking-tight mb-3">Sign in to MCP Hangar</h2>
            <p className="text-text-muted text-sm font-medium">Enter your API key to access the dashboard</p>
          </div>

          <form onSubmit={handleSubmit} className="space-y-6">
            {/* API Key */}
            <div className="space-y-2">
              <label
                htmlFor="login-api-key"
                className="block text-[10px] font-bold text-text-faint uppercase tracking-[0.1em]"
              >
                API Key
              </label>
              <input
                id="login-api-key"
                type="password"
                value={apiKeyInput}
                onChange={(e) => setApiKeyInput(e.target.value)}
                placeholder="hk_..."
                autoComplete="off"
                required
                className="w-full bg-surface-tertiary border-none rounded-lg py-3.5 px-4 text-text-primary placeholder:text-text-faint/50 focus:ring-2 focus:ring-accent transition-all duration-200 text-sm"
              />
            </div>

            {/* Error Display */}
            {error && (
              <div className="text-red-400 text-sm font-medium text-center">{error}</div>
            )}

            {/* Sign In Button */}
            <button
              type="submit"
              disabled={isSubmitting || !apiKeyInput.trim()}
              className="w-full btn-gradient font-bold py-3.5 rounded-lg transition-all duration-200 active:scale-[0.99] mt-2 shadow-lg shadow-accent/10 disabled:opacity-60"
            >
              {isSubmitting ? 'Signing in...' : 'Sign in'}
            </button>
          </form>

          {/* Divider */}
          <div className="relative my-8">
            <div className="absolute inset-0 flex items-center">
              <div className="w-full" style={{ borderTop: '1px solid rgba(66, 71, 84, 0.30)' }} />
            </div>
            <div className="relative flex justify-center text-[10px]">
              <span className="bg-surface-secondary px-4 text-text-faint font-bold uppercase tracking-widest">or</span>
            </div>
          </div>

          {/* SSO Button (placeholder for future OIDC flow) */}
          <button
            type="button"
            disabled
            className="w-full bg-surface-elevated hover:bg-surface-tertiary text-text-primary font-bold py-3.5 rounded-lg transition-all duration-200 flex items-center justify-center gap-3 active:scale-[0.99] ghost-border-strong disabled:opacity-60"
          >
            <Shield size={16} />
            Sign in with SSO
          </button>

          {/* Footer */}
          <div className="mt-10 pt-4 text-center" style={{ borderTop: '1px solid rgba(66, 71, 84, 0.10)' }}>
            <div className="flex items-center justify-center gap-2 text-[10px] font-bold text-text-faint uppercase tracking-[0.15em]">
              <Shield size={12} />
              Protected by enterprise authentication
            </div>
          </div>
        </div>

        {/* License Tiers */}
        <div className="flex items-center gap-6 text-[11px] font-bold text-text-faint uppercase tracking-widest">
          <span className="hover:text-text-primary transition-colors cursor-default">Community</span>
          <span className="w-1 h-1 bg-text-faint rounded-full" />
          <span className="hover:text-text-primary transition-colors cursor-default">Pro</span>
          <span className="w-1 h-1 bg-text-faint rounded-full" />
          <span className="hover:text-text-primary transition-colors cursor-default">Enterprise</span>
        </div>
      </div>

      {/* Background glow */}
      <div className="fixed inset-0 pointer-events-none -z-10 overflow-hidden">
        <div className="absolute -top-[20%] -left-[10%] w-[50%] h-[50%] bg-accent/5 rounded-full blur-[140px]" />
        <div className="absolute -bottom-[20%] -right-[10%] w-[40%] h-[40%] bg-teal/5 rounded-full blur-[120px]" />
      </div>
    </div>
  )
}
