export interface DomainEvent {
  event_id: string
  event_type: string
  occurred_at: string
  provider_id?: string
  [key: string]: unknown
}

export interface WsPingMessage {
  type: 'ping'
}

export interface WsStateMessage {
  type: 'state'
  timestamp: string
  providers: Record<string, unknown>
  groups: Record<string, unknown>
}

export type WsMessage = DomainEvent | WsPingMessage | WsStateMessage

export interface EventSubscriptionFilter {
  event_types?: string[]
  provider_ids?: string[]
}
