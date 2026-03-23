import { Outlet } from 'react-router'
import { Header } from './Header'
import { Sidebar } from './Sidebar'

export function Layout(): JSX.Element {
  return (
    <div className="h-screen flex overflow-hidden bg-surface-secondary">
      <Sidebar />
      <div className="flex-1 flex flex-col min-w-0">
        <Header />
        <main className="flex-1 overflow-y-auto p-6">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
