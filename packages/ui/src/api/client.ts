import { HangarApiError } from '../types/common'

const BASE_URL = '/api'

async function request<T>(
  method: string,
  path: string,
  body?: unknown,
): Promise<T> {
  const url = `${BASE_URL}${path}`
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
