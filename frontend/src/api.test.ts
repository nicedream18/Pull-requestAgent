import { describe, expect, it } from 'vitest'
import { eventUrl, reportUrl } from './api'

describe('dashboard API helpers', () => {
  it('builds resumable SSE and report URLs', () => {
    expect(eventUrl('run-123', 7)).toContain('/api/reviews/run-123/events?after=7')
    expect(reportUrl('run-123', 'md')).toContain('/api/reviews/run-123/report/md')
  })
})
