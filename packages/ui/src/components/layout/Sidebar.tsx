import { NavLink } from 'react-router'
import {
  LayoutDashboard,
  Server,
  Layers,
  Activity,
  BarChart2,
  Radio,
  Search,
  BookOpen,
  Shield,
  Settings,
  Network,
} from 'lucide-react'
import { cn } from '../../lib/cn'

const NAV_ITEMS = [
  { label: 'Dashboard', path: '/', icon: LayoutDashboard, end: true },
  { label: 'Providers', path: '/providers', icon: Server, end: false },
  { label: 'Groups', path: '/groups', icon: Layers, end: false },
  { label: 'Topology', path: '/topology', icon: Network, end: false },
  { label: 'Executions', path: '/executions', icon: Activity, end: false },
  { label: 'Metrics', path: '/metrics', icon: BarChart2, end: false },
  { label: 'Events', path: '/events', icon: Radio, end: false },
  { label: 'Discovery', path: '/discovery', icon: Search, end: false },
  { label: 'Catalog', path: '/catalog', icon: BookOpen, end: false },
  { label: 'Auth', path: '/auth', icon: Shield, end: false },
  { label: 'Config', path: '/config', icon: Settings, end: false },
]

export function Sidebar(): JSX.Element {
  return (
    <aside className="w-56 shrink-0 border-r border-gray-200 bg-white flex flex-col h-full">
      <div className="h-14 flex items-center px-4 border-b border-gray-200">
        <span className="font-semibold text-gray-900 text-sm tracking-tight">MCP Hangar</span>
      </div>
      <nav className="flex-1 overflow-y-auto py-2">
        {NAV_ITEMS.map(({ label, path, icon: Icon, end }) => (
          <NavLink
            key={path}
            to={path}
            end={end}
            className={({ isActive }) =>
              cn(
                'flex items-center gap-2.5 px-4 py-2 text-sm transition-colors',
                isActive ? 'bg-blue-50 text-blue-700 font-medium' : 'text-gray-600 hover:bg-gray-50 hover:text-gray-900'
              )
            }
          >
            <Icon size={16} />
            {label}
          </NavLink>
        ))}
      </nav>
    </aside>
  )
}
