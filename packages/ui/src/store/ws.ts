import { create } from 'zustand'

export type WsConnectionStatus = 'disconnected' | 'connecting' | 'connected' | 'error'

interface WsState {
  eventsStatus: WsConnectionStatus
  stateStatus: WsConnectionStatus
  eventsError: string | null
  stateError: string | null
  setEventsStatus: (status: WsConnectionStatus, error?: string) => void
  setStateStatus: (status: WsConnectionStatus, error?: string) => void
}

export const useWsStore = create<WsState>((set) => ({
  eventsStatus: 'disconnected',
  stateStatus: 'disconnected',
  eventsError: null,
  stateError: null,
  setEventsStatus: (status, error) =>
    set({ eventsStatus: status, eventsError: error ?? null }),
  setStateStatus: (status, error) =>
    set({ stateStatus: status, stateError: error ?? null }),
}))
