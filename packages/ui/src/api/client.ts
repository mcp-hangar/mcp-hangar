import { HangarApiError } from '../types/common'

const BASE_URL = '/api'

/**
 * Ensure mount-root paths have a trailing slash before any query string.
 *
 * Starlette Mount routes redirect /path -> /path/ which breaks through
 * the Vite proxy (redirect Location points at the backend origin, causing
 * a cross-origin failure in the browser). Adding the slash upfront avoids
 * the redirect for mount-root paths like /providers, /groups, etc.
 *
 * Sub-paths with resource IDs (e.g. /groups/123) must NOT get a trailing
 * slash because Starlette Route patterns don't include one, and the
 * resulting 307 redirect also breaks through the proxy.
 */
function normalizePath(path: string): string {
  const qIdx = path.indexOf('?')
  const base = qIdx === -1 ? path : path.slice(0, qIdx)
  const query = qIdx === -1 ? '' : path.slice(qIdx)

  // Only add trailing slash for mount-root paths (single path segment).
  // e.g. /providers -> /providers/  but  /providers/abc -> unchanged
  const segments = base.split('/').filter(Boolean)
  if (segments.length <= 1 && !base.endsWith('/')) {
    return `${base}/${query}`
  }
  return `${base}${query}`
}

async function request<T>(method: string, path: string, body?: unknown): Promise<T> {
  const url = `${BASE_URL}${normalizePath(path)}`
  const headers: Record<string, string> = { 'Content-Type': 'application/json' }

  const response = await fetch(url, {
    method,
    headers,
    body: body !== undefined ? JSON.stringify(body) : undefined,
  })

  if (!response.ok) {
    let code = `HTTP_${response.status}`
    let message = response.statusText
    let details: Record<string, unknown> | undefined

    try {
      const errorBody = await response.json()
      if (errorBody.error) {
        code = errorBody.error.code ?? code
        message = errorBody.error.message ?? message
        details = errorBody.error.details
      }
    } catch {
      // Non-JSON error body -- use status text
    }

    throw new HangarApiError(code, message, details, response.status)
  }

  if (response.status === 204) {
    return undefined as T
  }

  return response.json() as Promise<T>
}

export const apiClient = {
  get: <T>(path: string) => request<T>('GET', path),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
  delete: <T>(path: string, body?: unknown) => request<T>('DELETE', path, body),
  put: <T>(path: string, body?: unknown) => request<T>('PUT', path, body),
}
