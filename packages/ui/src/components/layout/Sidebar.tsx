import { NavLink, useLocation } from 'react-router'
import { motion } from 'framer-motion'
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

const NAV_SECTIONS = [
  {
    items: [{ label: 'Dashboard', path: '/', icon: LayoutDashboard, end: true }],
  },
  {
    heading: 'Infrastructure',
    items: [
      { label: 'Providers', path: '/providers', icon: Server, end: false },
      { label: 'Groups', path: '/groups', icon: Layers, end: false },
      { label: 'Topology', path: '/topology', icon: Network, end: false },
    ],
  },
  {
    heading: 'Observability',
    items: [
      { label: 'Executions', path: '/executions', icon: Activity, end: false },
      { label: 'Metrics', path: '/metrics', icon: BarChart2, end: false },
      { label: 'Events', path: '/events', icon: Radio, end: false },
    ],
  },
  {
    heading: 'Management',
    items: [
      { label: 'Discovery', path: '/discovery', icon: Search, end: false },
      { label: 'Catalog', path: '/catalog', icon: BookOpen, end: false },
      { label: 'Auth', path: '/auth', icon: Shield, end: false },
      { label: 'Config', path: '/config', icon: Settings, end: false },
    ],
  },
]

function NavItem({
  label,
  path,
  icon: Icon,
  end,
}: {
  label: string
  path: string
  icon: typeof LayoutDashboard
  end: boolean
}): JSX.Element {
  const location = useLocation()
  const isActive = end ? location.pathname === path : location.pathname.startsWith(path)

  return (
    <NavLink
      to={path}
      end={end}
      className={cn(
        'group relative flex items-center gap-2.5 rounded-md px-3 py-1.5 text-[13px] transition-colors duration-150',
        isActive ? 'text-accent-text font-medium' : 'text-text-muted hover:text-text-primary hover:bg-surface-secondary'
      )}
    >
      {/* Animated active indicator bar */}
      {isActive && (
        <motion.div
          layoutId="sidebar-active"
          className="absolute inset-0 rounded-md bg-accent-surface"
          transition={{ type: 'spring', stiffness: 500, damping: 35 }}
          style={{ zIndex: 0 }}
        />
      )}
      <Icon size={15} className="relative z-10 shrink-0" />
      <span className="relative z-10">{label}</span>
    </NavLink>
  )
}

export function Sidebar(): JSX.Element {
  return (
    <aside className="w-[220px] shrink-0 border-r border-border bg-surface flex flex-col h-full">
      {/* Logo / Brand */}
      <div className="h-14 flex items-center gap-2 px-5 border-b border-border">
        <div className="h-6 w-6 rounded-md bg-accent flex items-center justify-center">
          <span className="text-white text-xs font-bold leading-none">H</span>
        </div>
        <span className="font-semibold text-text-primary text-sm tracking-tight">MCP Hangar</span>
      </div>

      {/* Navigation */}
      <nav className="flex-1 overflow-y-auto px-3 py-3 space-y-4">
        {NAV_SECTIONS.map((section, idx) => (
          <div key={idx}>
            {section.heading && (
              <p className="text-[10px] font-semibold uppercase tracking-wider text-text-faint px-3 mb-1.5">
                {section.heading}
              </p>
            )}
            <div className="space-y-0.5">
              {section.items.map((item) => (
                <NavItem key={item.path} {...item} />
              ))}
            </div>
          </div>
        ))}
      </nav>
    </aside>
  )
}
