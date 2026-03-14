import { useParams } from 'react-router'

export function ProviderDetailPage(): JSX.Element {
  const { id } = useParams<{ id: string }>()
  return (
    <div className="space-y-4">
      <h2 className="text-lg font-semibold text-gray-900">Provider: {id}</h2>
      <p className="text-sm text-gray-500">Provider detail view -- coming in Phase 14.</p>
    </div>
  )
}
