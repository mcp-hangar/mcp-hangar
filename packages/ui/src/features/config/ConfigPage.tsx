import { useState } from 'react'
import { cn } from '../../lib/cn'
import { CurrentConfigTab } from './CurrentConfigTab'
import { ExportBackupTab } from './ExportBackupTab'
import { DiffTab } from './DiffTab'

type ConfigTab = 'current' | 'export' | 'diff'

const TABS: { id: ConfigTab; label: string }[] = [
  { id: 'current', label: 'Current Config' },
  { id: 'export', label: 'Export & Backup' },
  { id: 'diff', label: 'Diff' },
]

export function ConfigPage(): JSX.Element {
  const [activeTab, setActiveTab] = useState<ConfigTab>('current')

  return (
    <div className="space-y-4 p-6">
      <h2 className="text-lg font-semibold text-gray-900">Configuration</h2>

      {/* Tab bar */}
      <div className="flex gap-1 border-b">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              'px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
              activeTab === tab.id
                ? 'border-blue-600 text-blue-600'
                : 'border-transparent text-gray-500 hover:text-gray-700 hover:border-gray-300'
            )}
          >
            {tab.label}
          </button>
        ))}
      </div>

      {/* Tab content */}
      <div className="pt-2">
        {activeTab === 'current' && <CurrentConfigTab />}
        {activeTab === 'export' && <ExportBackupTab />}
        {activeTab === 'diff' && <DiffTab />}
      </div>
    </div>
  )
}
