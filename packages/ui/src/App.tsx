import { useEffect, type ReactNode } from 'react'
import { Routes, Route } from 'react-router'
import { Layout } from './components/layout/Layout'
import { DashboardPage } from './features/dashboard/DashboardPage'
import { ProvidersPage } from './features/providers/ProvidersPage'
import { ProviderDetailPage } from './features/providers/ProviderDetailPage'
import { GroupsPage } from './features/groups/GroupsPage'
import { ExecutionsPage } from './features/executions/ExecutionsPage'
import { MetricsPage } from './features/metrics/MetricsPage'
import { EventsPage } from './features/events/EventsPage'
import { DiscoveryPage } from './features/discovery/DiscoveryPage'
import { AuthPage } from './features/auth/AuthPage'
import { LoginPage } from './features/auth/LoginPage'
import { ConfigPage } from './features/config/ConfigPage'
import { SecurityPage } from './features/security/SecurityPage'
import { TopologyPage } from './features/topology/TopologyPage'
import { CatalogPage } from './features/catalog/CatalogPage'
import { DetectionPage } from './features/detection/DetectionPage'
import { Toaster } from './components/ui'
import { useAuthStore } from './store/auth'
import { apiClient } from './api/client'
import { HangarApiError } from './types/common'

interface MeResponse {
  authenticated: boolean
  principal: { id: string; type: string } | null
}

/**
 * Auth guard -- checks /api/system/me on mount to determine whether
 * authentication is required. When auth is enabled and the user is not
 * authenticated, renders LoginPage instead of children.
 */
function AuthGuard({ children }: { children: ReactNode }): JSX.Element {
  const isAuthenticated = useAuthStore((s) => s.isAuthenticated)
  const isLoading = useAuthStore((s) => s.isLoading)
  const authRequired = useAuthStore((s) => s.authRequired)

  useEffect(() => {
    const checkAuth = async (): Promise<void> => {
      try {
        const result = await apiClient.get<MeResponse>('/system/me')
        if (result.authenticated) {
          useAuthStore.getState().setAuthenticated(true)
          useAuthStore.getState().setPrincipal(result.principal)
          useAuthStore.getState().setAuthRequired(true)
        } else {
          // /me returned 200 with authenticated=false -- auth middleware not active
          useAuthStore.getState().setAuthRequired(false)
          useAuthStore.getState().setAuthenticated(true)
        }
      } catch (err: unknown) {
        if (err instanceof HangarApiError && err.status === 401) {
          // Auth is required but user is not authenticated
          useAuthStore.getState().setAuthRequired(true)
          useAuthStore.getState().setAuthenticated(false)
        } else {
          // Network error or server down -- allow access (graceful degradation)
          useAuthStore.getState().setAuthRequired(false)
          useAuthStore.getState().setAuthenticated(true)
        }
      } finally {
        useAuthStore.getState().setLoading(false)
      }
    }
    checkAuth()
  }, [])

  if (isLoading) {
    return (
      <div className="flex h-screen items-center justify-center bg-surface">
        <div className="text-text-muted text-sm font-medium">Loading...</div>
      </div>
    )
  }

  if (authRequired && !isAuthenticated) {
    return <LoginPage />
  }

  return <>{children}</>
}

export function App(): JSX.Element {
  return (
    <>
      <AuthGuard>
        <Routes>
          {/* Dashboard -- inside Layout */}
          <Route element={<Layout />}>
            <Route index element={<DashboardPage />} />
            <Route path="providers" element={<ProvidersPage />} />
            <Route path="providers/:id" element={<ProviderDetailPage />} />
            <Route path="groups" element={<GroupsPage />} />
            <Route path="executions" element={<ExecutionsPage />} />
            <Route path="metrics" element={<MetricsPage />} />
            <Route path="events" element={<EventsPage />} />
            <Route path="discovery" element={<DiscoveryPage />} />
            <Route path="auth" element={<AuthPage />} />
            <Route path="config" element={<ConfigPage />} />
            <Route path="security" element={<SecurityPage />} />
            <Route path="topology" element={<TopologyPage />} />
            <Route path="catalog" element={<CatalogPage />} />
            {/* Governance (v8.0-v10.0) */}
            <Route path="detection" element={<DetectionPage />} />
          </Route>
        </Routes>
      </AuthGuard>
      <Toaster />
    </>
  )
}
