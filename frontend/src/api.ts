import type { BenchmarkSummary, DemoCase, ReviewEvent, ReviewJob, Strategy } from './types'

export const API_BASE = import.meta.env.VITE_API_BASE || 'http://127.0.0.1:8000'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  })
  if (!response.ok) {
    const detail = await response.text()
    let message = detail || `${response.status} ${response.statusText}`
    try {
      const payload = JSON.parse(detail) as { detail?: string }
      if (payload.detail) message = payload.detail
    } catch {
      // Keep the raw response when the server did not return JSON.
    }
    throw new Error(message)
  }
  return response.json() as Promise<T>
}

export function getDemos() {
  return request<{ cases: DemoCase[]; description: string }>('/api/demos')
}

export function createReview(input: {
  mode: 'online' | 'demo'
  pr_url?: string
  demo_case_id?: string
  strategy: Strategy
}) {
  return request<{ run_id: string; status: string; mode: string }>('/api/reviews', {
    method: 'POST',
    body: JSON.stringify(input),
  })
}

export function getReview(runId: string) {
  return request<ReviewJob>(`/api/reviews/${runId}`)
}

export function getBenchmarkList() {
  return request<{ benchmarks: BenchmarkSummary[]; message: string }>('/api/benchmarks')
}

export function eventUrl(runId: string, after = 0) {
  return `${API_BASE}/api/reviews/${runId}/events?after=${after}`
}

export function reportUrl(runId: string, format: 'json' | 'md') {
  return `${API_BASE}/api/reviews/${runId}/report/${format}`
}

export type EventHandler = (event: ReviewEvent) => void

export function subscribeToReview(runId: string, handler: EventHandler, terminal: (status: string) => void) {
  const source = new EventSource(eventUrl(runId))
  source.addEventListener('node', (event) => handler(JSON.parse((event as MessageEvent).data)))
  source.addEventListener('agent', (event) => handler(JSON.parse((event as MessageEvent).data)))
  source.addEventListener('lifecycle', (event) => handler(JSON.parse((event as MessageEvent).data)))
  source.addEventListener('terminal', (event) => {
    const payload = JSON.parse((event as MessageEvent).data) as { status: string }
    terminal(payload.status)
    source.close()
  })
  source.onerror = () => {
    // Browser EventSource retries automatically. The polling fallback in App.vue
    // keeps the status visible if the backend restarts.
  }
  return () => source.close()
}
