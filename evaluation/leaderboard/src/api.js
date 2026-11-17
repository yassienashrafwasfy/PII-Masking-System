const JSON_HEADERS = { Accept: 'application/json' }

// The arena API only ever returns 400, 404, 409, 502, and 503. A 401, 403, or
// 407 therefore did not come from it, so say so rather than blaming the arena:
// in practice it is a proxy, VPN, CDN, or browser extension answering first.
const INTERPOSED_STATUSES = new Set([401, 403, 407])

async function readJson(response) {
  if (!response.ok) {
    let detail = `Request failed with status ${response.status}`
    try {
      const body = await response.json()
      if (body && body.detail) detail = body.detail
    } catch {
      /* keep the status-based message */
    }
    if (INTERPOSED_STATUSES.has(response.status)) {
      detail =
        `${response.url || 'The arena API'} answered ${response.status}, ` +
        'which this API never returns. Something is intercepting the request ' +
        '— a reverse proxy, VPN, corporate filter, or browser extension. ' +
        'Compare with curl against the same URL.'
    }
    const error = new Error(detail)
    error.status = response.status
    throw error
  }
  return response.json()
}

export function fetchLeaderboard(round) {
  const query = round ? `?round=${encodeURIComponent(round)}` : ''
  return fetch(`/api/v1/leaderboard${query}`, { headers: JSON_HEADERS }).then(readJson)
}

export function fetchConfig() {
  return fetch('/api/v1/config', { headers: JSON_HEADERS }).then(readJson)
}

export function submitRun({ team, file, sample }) {
  const payload = new FormData()
  payload.append('team', team)
  if (sample) {
    payload.append('sample', sample)
  } else {
    payload.append('file', file)
  }
  return fetch('/api/v1/runs', { method: 'POST', body: payload }).then(readJson)
}

export function fetchSamples() {
  return fetch('/api/v1/samples', { headers: JSON_HEADERS }).then(readJson)
}

export function fetchRun(runId) {
  return fetch(`/api/v1/runs/${encodeURIComponent(runId)}`, { headers: JSON_HEADERS }).then(readJson)
}

// Runs this API process happens to be holding in memory. Distinct from the
// library below, which is rebuilt from object storage and so outlives a
// restart -- the two answer different questions and are deliberately separate.
export function fetchRuns() {
  return fetch('/api/v1/runs', { headers: JSON_HEADERS }).then(readJson)
}

export function fetchLibrary({ limit = 25, offset = 0, team = '', status = '', q = '' } = {}) {
  const params = new URLSearchParams({ limit, offset })
  // Filtering is done server-side against the whole index, so an empty filter
  // must not be sent as `team=` -- that would match a team literally named ''.
  if (team) params.set('team', team)
  if (status) params.set('status', status)
  if (q) params.set('q', q)
  return fetch(`/api/v1/library?${params}`, { headers: JSON_HEADERS }).then(readJson)
}

export function documentUrl(runId, side) {
  return `/api/v1/runs/${encodeURIComponent(runId)}/content/${side}`
}

export async function fetchDocumentText(runId, side) {
  const response = await fetch(documentUrl(runId, side))
  if (!response.ok) throw new Error(`Could not load the ${side} document`)
  return response.text()
}
