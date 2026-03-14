import { useEffect, useRef, useMemo } from 'react'
import { useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import * as d3 from 'd3'
import { queryKeys } from '../../lib/queryKeys'
import { providersApi } from '../../api/providers'
import { groupsApi } from '../../api/groups'
import { useProviderState } from '../../hooks/useProviderState'
import { LoadingSpinner } from '../../components/ui'
import type { ProviderState } from '../../types/provider'

// Node and link types for D3 simulation
interface TopoNode extends d3.SimulationNodeDatum {
  id: string
  kind: 'provider' | 'group'
  state?: ProviderState
  label: string
}

interface TopoLink extends d3.SimulationLinkDatum<TopoNode> {
  source: string | TopoNode
  target: string | TopoNode
}

const STATE_COLORS: Record<ProviderState, string> = {
  cold: '#94a3b8',
  initializing: '#60a5fa',
  ready: '#22c55e',
  degraded: '#f59e0b',
  dead: '#ef4444',
}

const GROUP_COLOR = '#8b5cf6'
const DEFAULT_PROVIDER_COLOR = '#94a3b8'
const NODE_RADIUS_PROVIDER = 18
const NODE_RADIUS_GROUP = 24

function nodeColor(node: TopoNode): string {
  if (node.kind === 'group') return GROUP_COLOR
  return node.state ? (STATE_COLORS[node.state] ?? DEFAULT_PROVIDER_COLOR) : DEFAULT_PROVIDER_COLOR
}

function nodeRadius(node: TopoNode): number {
  return node.kind === 'group' ? NODE_RADIUS_GROUP : NODE_RADIUS_PROVIDER
}

interface TopologyGraphProps {
  nodes: TopoNode[]
  links: TopoLink[]
  onNodeClick: (node: TopoNode) => void
}

function TopologyGraph({ nodes, links, onNodeClick }: TopologyGraphProps): JSX.Element {
  const svgRef = useRef<SVGSVGElement>(null)
  const simulationRef = useRef<d3.Simulation<TopoNode, TopoLink> | null>(null)

  useEffect(() => {
    if (!svgRef.current) return

    const svg = d3.select(svgRef.current)
    svg.selectAll('*').remove()

    const width = svgRef.current.clientWidth || 800
    const height = svgRef.current.clientHeight || 500

    // Clone nodes/links to avoid mutating props
    const simNodes: TopoNode[] = nodes.map((n) => ({ ...n }))
    const nodeById = new Map<string, TopoNode>(simNodes.map((n) => [n.id, n]))
    const simLinks: TopoLink[] = links.map((l) => ({
      source: nodeById.get(l.source as string) ?? (l.source as TopoNode),
      target: nodeById.get(l.target as string) ?? (l.target as TopoNode),
    }))

    const simulation = d3
      .forceSimulation<TopoNode>(simNodes)
      .force('link', d3.forceLink<TopoNode, TopoLink>(simLinks).id((d) => d.id).distance(100))
      .force('charge', d3.forceManyBody<TopoNode>().strength(-300))
      .force('center', d3.forceCenter(width / 2, height / 2))
      .force('collision', d3.forceCollide<TopoNode>((d) => nodeRadius(d) + 8))

    simulationRef.current = simulation

    // Zoom/pan container
    const container = svg.append('g')
    svg.call(
      d3.zoom<SVGSVGElement, unknown>().scaleExtent([0.3, 3]).on('zoom', (event: d3.D3ZoomEvent<SVGSVGElement, unknown>) => {
        container.attr('transform', event.transform.toString())
      }),
    )

    // Links
    const link = container
      .append('g')
      .attr('stroke', '#e2e8f0')
      .attr('stroke-width', 2)
      .selectAll<SVGLineElement, TopoLink>('line')
      .data(simLinks)
      .join('line')

    // Node groups
    const node = container
      .append('g')
      .selectAll<SVGGElement, TopoNode>('g')
      .data(simNodes)
      .join('g')
      .attr('cursor', 'pointer')
      .on('click', (_event, d) => onNodeClick(d))
      .call(
        d3
          .drag<SVGGElement, TopoNode>()
          .on('start', (event, d) => {
            if (!event.active) simulation.alphaTarget(0.3).restart()
            d.fx = d.x
            d.fy = d.y
          })
          .on('drag', (event, d) => {
            d.fx = event.x
            d.fy = event.y
          })
          .on('end', (event, d) => {
            if (!event.active) simulation.alphaTarget(0)
            d.fx = null
            d.fy = null
          }),
      )

    // Circles
    node
      .append('circle')
      .attr('r', (d) => nodeRadius(d))
      .attr('fill', (d) => nodeColor(d))
      .attr('stroke', '#fff')
      .attr('stroke-width', 2)

    // Group nodes get a distinct ring
    node
      .filter((d) => d.kind === 'group')
      .append('circle')
      .attr('r', (d) => nodeRadius(d) + 4)
      .attr('fill', 'none')
      .attr('stroke', GROUP_COLOR)
      .attr('stroke-width', 2)
      .attr('stroke-dasharray', '4 2')

    // Labels
    node
      .append('text')
      .attr('text-anchor', 'middle')
      .attr('dy', (d) => nodeRadius(d) + 14)
      .attr('font-size', '11px')
      .attr('fill', '#374151')
      .text((d) => d.label)

    simulation.on('tick', () => {
      link
        .attr('x1', (d) => (d.source as TopoNode).x ?? 0)
        .attr('y1', (d) => (d.source as TopoNode).y ?? 0)
        .attr('x2', (d) => (d.target as TopoNode).x ?? 0)
        .attr('y2', (d) => (d.target as TopoNode).y ?? 0)

      node.attr('transform', (d) => `translate(${d.x ?? 0},${d.y ?? 0})`)
    })

    return () => {
      simulation.stop()
    }
  }, [nodes, links, onNodeClick])

  return <svg ref={svgRef} width="100%" height="100%" />
}

export function TopologyPage(): JSX.Element {
  const navigate = useNavigate()

  const { data: providersData, isLoading: providersLoading } = useQuery({
    queryKey: queryKeys.providers.list(),
    queryFn: () => providersApi.list(),
    refetchInterval: 30_000,
  })

  const { data: groupsData, isLoading: groupsLoading } = useQuery({
    queryKey: queryKeys.groups.list(),
    queryFn: () => groupsApi.list(),
    refetchInterval: 30_000,
  })

  // WebSocket updates trigger query invalidation via useProviderState
  useProviderState(30)

  const isLoading = providersLoading || groupsLoading

  const { nodes, links } = useMemo<{ nodes: TopoNode[]; links: TopoLink[] }>(() => {
    const providers = providersData?.providers ?? []
    const groups = groupsData?.groups ?? []

    const providerNodes: TopoNode[] = providers.map((p) => ({
      id: p.provider_id,
      kind: 'provider',
      state: p.state,
      label: p.provider_id,
    }))

    const groupNodes: TopoNode[] = groups.map((g) => ({
      id: `group:${g.group_id}`,
      kind: 'group',
      label: g.group_id,
    }))

    // Links: groups → their members (we don't have member data from GroupSummary,
    // so render isolated group nodes; detail links require GroupDetails).
    // For now emit no links — topology still shows all nodes clearly.
    const topoLinks: TopoLink[] = []

    return { nodes: [...providerNodes, ...groupNodes], links: topoLinks }
  }, [providersData, groupsData])

  function handleNodeClick(node: TopoNode): void {
    if (node.kind === 'provider') {
      void navigate(`/providers/${node.id}`)
    } else {
      // Strip the 'group:' prefix
      void navigate(`/groups/${node.id.replace(/^group:/, '')}`)
    }
  }

  // Legend data
  const legendEntries: { label: string; color: string }[] = [
    { label: 'ready', color: STATE_COLORS.ready },
    { label: 'initializing', color: STATE_COLORS.initializing },
    { label: 'degraded', color: STATE_COLORS.degraded },
    { label: 'dead', color: STATE_COLORS.dead },
    { label: 'cold / unknown', color: DEFAULT_PROVIDER_COLOR },
    { label: 'group', color: GROUP_COLOR },
  ]

  return (
    <div className="p-6 flex flex-col h-full space-y-4">
      <div className="flex items-center justify-between">
        <h2 className="text-lg font-semibold text-gray-900">Topology</h2>
        <div className="flex items-center gap-4">
          {legendEntries.map((e) => (
            <span key={e.label} className="flex items-center gap-1 text-xs text-gray-600">
              <span
                className="inline-block w-3 h-3 rounded-full flex-shrink-0"
                style={{ backgroundColor: e.color }}
              />
              {e.label}
            </span>
          ))}
        </div>
      </div>

      <div className="flex-1 bg-white rounded-lg border border-gray-200 overflow-hidden min-h-[500px]">
        {isLoading ? (
          <div className="flex items-center justify-center h-full">
            <LoadingSpinner />
          </div>
        ) : nodes.length === 0 ? (
          <div className="flex items-center justify-center h-full text-sm text-gray-500">
            No providers or groups found.
          </div>
        ) : (
          <TopologyGraph nodes={nodes} links={links} onNodeClick={handleNodeClick} />
        )}
      </div>

      <p className="text-xs text-gray-400">
        Click a node to navigate to its detail page. Drag nodes to reposition. Scroll to zoom.
      </p>
    </div>
  )
}
