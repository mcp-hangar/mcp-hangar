import { useState } from 'react'
import { cn } from '../../lib/cn'
import { PageContainer } from '../../components/ui'
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
    <PageContainer className="space-y-4 p-6">
      <h2 className="text-lg font-semibold text-text-primary">Configuration</h2>

      {/* Tab bar */}
      <div className="flex gap-1 border-b border-border">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            onClick={() => setActiveTab(tab.id)}
            className={cn(
              'px-4 py-2 text-sm font-medium border-b-2 -mb-px transition-colors',
              activeTab === tab.id
                ? 'border-accent text-accent'
                : 'border-transparent text-text-muted hover:text-text-secondary hover:border-border-strong'
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
    </PageContainer>
  )
}
