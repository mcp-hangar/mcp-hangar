import { describe, it, expect } from 'vitest'
import { http, HttpResponse } from 'msw'
import { server } from '../../test/msw/server'
import { apiClient } from '../client'
import { HangarApiError } from '../../types/common'

describe('apiClient', () => {
  it('GET: deserializes JSON response', async () => {
    server.use(
      http.get('/api/test-endpoint/', () => {
        return HttpResponse.json({ value: 42 })
      }),
    )

    const result = await apiClient.get<{ value: number }>('/test-endpoint')
    expect(result).toEqual({ value: 42 })
  })

  it('POST: sends JSON body and deserializes response', async () => {
    let capturedBody: unknown = null
    server.use(
      http.post('/api/test-post/', async ({ request }) => {
        capturedBody = await request.json()
        return HttpResponse.json({ ok: true })
      }),
    )

    const result = await apiClient.post<{ ok: boolean }>('/test-post', { name: 'test' })
    expect(result).toEqual({ ok: true })
    expect(capturedBody).toEqual({ name: 'test' })
  })

  it('throws HangarApiError on 404 with JSON error body', async () => {
    server.use(
      http.get('/api/not-found/', () => {
        return HttpResponse.json(
          { error: { code: 'PROVIDER_NOT_FOUND', message: 'Provider xyz not found' } },
          { status: 404 },
        )
      }),
    )

    await expect(apiClient.get('/not-found')).rejects.toThrow(HangarApiError)

    try {
      await apiClient.get('/not-found')
    } catch (e) {
      const err = e as HangarApiError
      expect(err.code).toBe('PROVIDER_NOT_FOUND')
      expect(err.message).toBe('Provider xyz not found')
      expect(err.status).toBe(404)
    }
  })

  it('throws HangarApiError with statusText fallback on non-JSON error body', async () => {
    server.use(
      http.get('/api/server-error/', () => {
        return new HttpResponse('Internal Server Error', {
          status: 500,
          statusText: 'Internal Server Error',
        })
      }),
    )

    await expect(apiClient.get('/server-error')).rejects.toThrow(HangarApiError)
  })

  it('returns undefined for 204 No Content responses', async () => {
    server.use(
      http.delete('/api/resource/', () => {
        return new HttpResponse(null, { status: 204 })
      }),
    )

    const result = await apiClient.delete('/resource')
    expect(result).toBeUndefined()
  })

  it('adds trailing slash to paths without query string', async () => {
    let capturedUrl = ''
    server.use(
      http.get('/api/trailing/', ({ request }) => {
        capturedUrl = request.url
        return HttpResponse.json({ ok: true })
      }),
    )

    await apiClient.get('/trailing')
    expect(capturedUrl).toContain('/api/trailing/')
  })

  it('preserves query string when adding trailing slash', async () => {
    let capturedUrl = ''
    server.use(
      http.get('/api/query/', ({ request }) => {
        capturedUrl = request.url
        return HttpResponse.json({ ok: true })
      }),
    )

    await apiClient.get('/query?foo=bar')
    expect(capturedUrl).toContain('/api/query/?foo=bar')
  })

  it('DELETE: sends JSON body', async () => {
    let capturedBody: unknown = null
    server.use(
      http.delete('/api/delete-test/', async ({ request }) => {
        capturedBody = await request.json()
        return HttpResponse.json({ deleted: true })
      }),
    )

    const result = await apiClient.delete<{ deleted: boolean }>('/delete-test', { id: '123' })
    expect(result).toEqual({ deleted: true })
    expect(capturedBody).toEqual({ id: '123' })
  })
})
