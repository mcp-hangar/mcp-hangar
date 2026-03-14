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
import { ConfigPage } from './features/config/ConfigPage'

export function App(): JSX.Element {
  return (
    <Routes>
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
      </Route>
    </Routes>
  )
}
