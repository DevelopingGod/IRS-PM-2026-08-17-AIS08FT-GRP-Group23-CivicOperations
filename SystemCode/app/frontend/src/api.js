// One place the frontend talks to the backend. In development /api is forwarded to the backend
// by Vite; in Docker nginx forwards it. VITE_API_BASE points elsewhere if needed.
const BASE = import.meta.env.VITE_API_BASE || ''
const KEY = 'civicops.session'

// The session lives in this browser only. Storage can be blocked (private windows), so every
// access is guarded and the app still works, just without staying signed in.
export const session = {
  get() { try { return JSON.parse(localStorage.getItem(KEY)) || null } catch { return null } },
  set(s) { try { s ? localStorage.setItem(KEY, JSON.stringify(s)) : localStorage.removeItem(KEY) } catch { /* not kept */ } },
}
const listeners = new Set()
export function onSessionChange(fn) { listeners.add(fn); return () => listeners.delete(fn) }
export function setSession(s) { session.set(s); listeners.forEach((fn) => fn(s)) }

async function call(path, options = {}) {
  const s = options.token ? { token: options.token } : session.get()
  let res
  try {
    const { token: _t, ...opts } = options
    res = await fetch(BASE + path, {
      ...opts,
      headers: { 'Content-Type': 'application/json', ...(s?.token ? { Authorization: `Bearer ${s.token}` } : {}) },
    })
  } catch {
    throw Object.assign(new Error('Cannot reach the server. Check your connection and try again.'), { status: 0 })
  }
  // A stale reply from an earlier session must never sign out the person who is signed in now.
  if (res.status === 401 && s?.token && session.get()?.token === s.token && !path.startsWith('/api/auth/')) setSession(null)
  if (!res.ok) {
    let msg = `Something went wrong (${res.status}).`
    try { const b = await res.json(); if (typeof b.detail === 'string') msg = b.detail } catch { /* keep */ }
    throw Object.assign(new Error(msg), { status: res.status })
  }
  return res.json()
}
const post = (path, body) => call(path, { method: 'POST', body: JSON.stringify(body ?? {}) })
const put = (path, body) => call(path, { method: 'PUT', body: JSON.stringify(body ?? {}) })
const q = encodeURIComponent

export const api = {
  // public
  pulse: () => call('/api/public/pulse'),
  stats: () => call('/api/public/stats'),
  catalog: () => call('/api/catalog'),
  addresses: (text, borough) => call(`/api/addresses?q=${q(text)}${borough ? `&borough=${q(borough)}` : ''}`),
  near: (lat, lon) => call(`/api/addresses/near?lat=${lat}&lon=${lon}`),
  register: (email) => post('/api/auth/register', { email }),
  login: (user_id, pin) => post('/api/auth/login', { user_id, pin }),
  logout: (token) => call('/api/auth/logout', { method: 'POST', body: '{}', token }),
  me: () => call('/api/auth/me'),
  // resident
  chat: (body) => post('/api/chat', body),
  report: (body) => post('/api/report', body),
  myRequests: () => call('/api/me/requests'),
  myRequest: (id) => call(`/api/me/requests/${q(id)}`),
  trackChat: (text, current) => post('/api/track/chat', { text, current }),
  // staff
  queue: () => call('/api/staff/queue'),
  detail: (id) => call(`/api/staff/requests/${q(id)}`),
  crews: (agency, borough) => call(`/api/staff/crews?agency=${q(agency || '')}&borough=${q(borough || '')}`),
  assign: (id, crew_id) => post(`/api/staff/requests/${q(id)}/assign`, { crew_id }),
  unassign: (id) => post(`/api/staff/requests/${q(id)}/unassign`),
  approveEscalation: (id) => post(`/api/staff/requests/${q(id)}/approve-escalation`),
  plan: (wait) => call(`/api/staff/plan?wait=${wait || 0}`),
  approveRoute: (crew_id, request_ids) => post('/api/staff/plan/approve', { crew_id, request_ids }),
  approveAllRoutes: () => post('/api/staff/plan/approve-all'),
  confirmImpact: (id) => post(`/api/staff/requests/${q(id)}/confirm-impact`),
  note: (id, text) => post(`/api/staff/requests/${q(id)}/note`, { text }),
  ask: (question) => post('/api/staff/ask', { question }),
  alerts: (borough, day) => call(`/api/staff/alerts?borough=${q(borough)}${day ? `&day=${q(day)}` : ''}`),
  stormDays: (borough) => call(`/api/staff/storm-days?borough=${q(borough)}`),
  streets: (b) => call(`/api/map/streets?south=${b.south}&west=${b.west}&north=${b.north}&east=${b.east}`),
  // crew
  crewJobs: () => call('/api/crew/jobs'),
  close: (id, outcome) => post(`/api/crew/jobs/${q(id)}/close`, { outcome }),
  // tech
  health: () => call('/api/tech/health'),
  setSwitch: (name, enabled) => put('/api/tech/components', { name, enabled }),
  measure: () => post('/api/tech/measure'),
  lastMeasure: () => call('/api/tech/measure'),
  users: () => call('/api/tech/users'),
  createUser: (role, name, crew_id) => post('/api/tech/users', { role, name, crew_id }),
  resetPin: (id) => post(`/api/tech/users/${q(id)}/reset-pin`),
  setActive: (id, active) => post(`/api/tech/users/${q(id)}/active`, { active }),
  allCrews: () => call('/api/tech/crews'),
  system: () => call('/api/tech/system'),
}

// Plain words for the values the backend uses.
export const LEVEL_WORDS = { CRITICAL: 'Critical', HIGH: 'High', MEDIUM: 'Medium', LOW: 'Low' }
export const BAND_WORDS = { 'ACT NOW': 'Act now', NEXT: 'Next', PLANNED: 'Planned', MONITOR: 'Monitor', HOLD: 'Linked' }
export const STATUS_WORDS = { OPEN: 'In the queue', ASSIGNED: 'Crew assigned', CLOSED: 'Closed', HELD_FOR_PERSON: 'Being reviewed' }
export const OUTCOME_WORDS = { LIVE: 'Live', MISSING: 'Not delivered yet', ERROR: 'Failed, default used', ABLATED: 'Off for a test', OFF: 'Switched off' }
export const BOROUGH_LIST = ['MANHATTAN', 'BROOKLYN', 'QUEENS', 'BRONX', 'STATEN ISLAND']
export const label = (l) => (l === 'OTHER' ? 'Question for a person' : l || 'Not yet known')
export const titleCase = (s) => (s || '').toLowerCase().replace(/\b\w/g, (c) => c.toUpperCase())

// A time of day in New York, for planned arrivals.
export function clock(iso) {
  if (!iso) return ''
  try { return new Date(iso).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit', timeZone: 'America/New_York' }) } catch { return '' }
}

export function since(iso) {
  if (!iso) return ''
  const mins = Math.max(0, (Date.now() - new Date(iso).getTime()) / 60000)
  if (mins < 1) return 'just now'
  if (mins < 60) return `${Math.round(mins)} min ago`
  if (mins < 60 * 24) return `${Math.round(mins / 60)} h ago`
  const days = Math.round(mins / 1440)
  return `${days} day${days === 1 ? '' : 's'} ago`
}
