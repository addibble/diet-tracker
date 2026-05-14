// Minimal frontend telemetry. Records timing/event entries to a small
// in-memory queue, flushes periodically and on page hide via
// `navigator.sendBeacon` so backgrounded tabs don't lose data.
//
// All errors are swallowed — telemetry must never break the app.

export type TelemetryEvent = {
  name: string
  duration_ms?: number
  route?: string
  meta?: Record<string, unknown>
}

const QUEUE: TelemetryEvent[] = []
const FLUSH_INTERVAL_MS = 10_000
const ENDPOINT = '/api/telemetry/frontend'
let started = false

function currentRoute(): string {
  try {
    return window.location.pathname + window.location.search
  } catch {
    return 'unknown'
  }
}

function flush(useBeacon = false): void {
  if (QUEUE.length === 0) return
  const events = QUEUE.splice(0, QUEUE.length)
  const body = JSON.stringify({ events })
  try {
    if (useBeacon && navigator.sendBeacon) {
      const blob = new Blob([body], { type: 'application/json' })
      navigator.sendBeacon(ENDPOINT, blob)
      return
    }
    void fetch(ENDPOINT, {
      method: 'POST',
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
      body,
      keepalive: true,
    }).catch(() => {
      /* swallow */
    })
  } catch {
    /* swallow */
  }
}

function start(): void {
  if (started) return
  started = true
  window.setInterval(() => flush(false), FLUSH_INTERVAL_MS)
  window.addEventListener('pagehide', () => flush(true))
  window.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') flush(true)
  })
}

export function recordEvent(ev: TelemetryEvent): void {
  start()
  QUEUE.push({ route: currentRoute(), ...ev })
  if (QUEUE.length >= 50) flush(false)
}

/** Time a synchronous block and record the result. */
export function timeSync<T>(name: string, fn: () => T, meta?: Record<string, unknown>): T {
  const t0 = performance.now()
  try {
    return fn()
  } finally {
    recordEvent({ name, duration_ms: performance.now() - t0, meta })
  }
}

/** Time an async block and record the result. */
export async function time<T>(
  name: string,
  fn: () => Promise<T>,
  meta?: Record<string, unknown>,
): Promise<T> {
  const t0 = performance.now()
  try {
    return await fn()
  } finally {
    recordEvent({ name, duration_ms: performance.now() - t0, meta })
  }
}

/** Force a flush, e.g. before a navigation we control. */
export function flushNow(): void {
  flush(true)
}

if (typeof window !== 'undefined') {
  start()
}
