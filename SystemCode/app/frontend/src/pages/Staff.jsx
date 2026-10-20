import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { api, BAND_WORDS, BOROUGH_LIST, clock, label, LEVEL_WORDS, OUTCOME_WORDS, since, STATUS_WORDS, titleCase } from '../api.js'
import { CountUp, Drawer, ErrorNote, Modal, Skeleton, Tilt, toast, TypeOut, useAsync, useReveal } from '../motion.jsx'
import { bandTone, BOROUGH_BOXES, boxAround, Bullet, CityMap, Icon, levelTone } from '../ui.jsx'

const VIEWS = [{ id: 'queue', name: 'Queue', icon: 'layers' }, { id: 'plan', name: 'Crew routes', icon: 'route' }, { id: 'ask', name: 'Admin questions', icon: 'sparkle' },
  { id: 'alerts', name: 'Busy day alerts', icon: 'gauge' }]

export default function Staff({ view = 'queue', go }) {
  return (
    <div className="page stack">
      <div className="segmented wide" role="tablist" aria-label="Staff views">
        {VIEWS.map((v) => (
          <button key={v.id} role="tab" aria-selected={view === v.id} onClick={() => go(v.id)}><Icon name={v.icon} size={18} /> {v.name}</button>
        ))}
      </div>
      {view === 'queue' && <Queue />}
      {view === 'plan' && <Plan />}
      {view === 'ask' && <Ask />}
      {view === 'alerts' && <Alerts />}
    </div>
  )
}

// What the AI weighed to put a request here, in a few words, from Prioritisation's own reasons.
function rankedBecause(q) {
  const out = []
  for (const r of q.priority_reasons || []) {
    let m
    if ((m = r.match(/urgency: (\w+)/i))) out.push(`${m[1].toLowerCase()} urgency`)
    else if ((m = r.match(/^Waiting: ([\d.]+)h/))) out.push(`waiting ${m[1]} h`)
    else if ((m = r.match(/^SLA target: ([\d.]+)h/))) out.push(`official deadline ${Math.round(Number(m[1]))} h`)
    else if ((m = r.match(/independent reporters: (\d+)/))) out.push(`${m[1]} reporters`)
    else if (/^Approved escalation/.test(r)) out.push('escalation approved')
    else if (/sensitive.site impact/i.test(r)) out.push(`affects a ${q.sensitive_site_type || 'school or hospital'}`)
  }
  return out
}

const FILTERS = {
  all: { name: 'In the queue', test: (q) => !q.on_hold },
  next: { name: 'To do next', test: (q) => q.priority_band === 'ACT NOW' || q.priority_band === 'NEXT', tone: 'orange' },
  sensitive: { name: 'Near a school or hospital', test: (q) => !!q.sensitive_site_type, tone: 'purple' },
  linked: { name: 'Linked duplicates', test: (q) => q.on_hold, tone: 'blue' },
  escalate: { name: 'Escalations to approve', test: (q) => q.escalation_level && q.escalation_level !== 'NONE' && !q.escalation_approved, tone: 'red' },
  unassigned: { name: 'No crew yet', test: (q) => !q.on_hold && !q.crew, tone: 'grey' },
}

function Queue() {
  const { data, error, loading, reload } = useAsync(() => api.queue(), [])
  const [filter, setFilter] = useState('all')
  const [open, setOpen] = useState(null)
  const [query, setQuery] = useState('')
  const [zoom, setZoom] = useState('ALL')
  useReveal(data)
  useEffect(() => { const t = setInterval(reload, 30000); return () => clearInterval(t) }, [reload])

  const items = data?.items || []
  const shown = useMemo(() => {
    const f = FILTERS[filter]?.test || (() => true)
    const needle = query.trim().toLowerCase()
    // A search looks at every request, linked duplicates included, so a reference is always found.
    if (needle) return items.filter((q) => `${q.request_id} ${q.label} ${q.slots?.incident_address} ${q.agency} ${q.duplicate_of || ''}`.toLowerCase().includes(needle))
    return items.filter(f)
  }, [items, filter, query])
  const points = useMemo(() => shown.filter((q) => !q.on_hold).map((q) => ({
    id: q.request_id, lat: q.slots?.latitude, lon: q.slots?.longitude, tone: levelTone(q.urgency_level),
    r: q.priority_band === 'NEXT' || q.priority_band === 'ACT NOW' ? 9 : 6, pulse: q.priority_band === 'ACT NOW' || q.priority_band === 'NEXT',
    title: `${q.queue_position}. ${label(q.label)}, ${q.slots?.incident_address || ''}`,
  })), [shown])

  if (error) return <ErrorNote error={error} onRetry={reload} />
  if (loading && !data) return <div className="card"><Skeleton lines={8} /></div>
  const p = data.prioritisation
  return (
    <div className="stack">
      <div className="kpis">
        {Object.entries(FILTERS).map(([k, f], i) => (
          <Tilt as="button" key={k} className={`kpi ${f.tone || ''} ${filter === k ? 'on' : ''} reveal`} style={{ '--i': i }}
            aria-pressed={filter === k} onClick={() => setFilter(k)}>
            <strong><CountUp value={items.filter(f.test).length} /></strong><span>{f.name}</span>
          </Tilt>
        ))}
      </div>
      {!items.length && !data.held_for_person?.length && (
        <div className="card empty">The queue is empty. New reports from residents appear here as they arrive.</div>
      )}
      <div className="queue-layout">
        <section className="card mapcard reveal" aria-label="Map of the queue">
          <div className="row"><h2>{FILTERS[filter].name}</h2><span className={`pill ${p?.outcome}`}>Prioritisation: {OUTCOME_WORDS[p?.outcome] || 'not run'}</span></div>
          <div className="mapzoom" role="group" aria-label="Zoom the map">
            {['ALL', ...Object.keys(BOROUGH_BOXES)].map((b) => (
              <button key={b} className="chip" aria-pressed={zoom === b} onClick={() => setZoom(b)}>{b === 'ALL' ? 'All of NYC' : titleCase(b)}</button>))}
          </div>
          <CityMap points={points} selected={open} onPick={setOpen} focus={BOROUGH_BOXES[zoom]} streets
            label={`Open requests on the map of ${zoom === 'ALL' ? 'New York City' : titleCase(zoom)}`} />
          <p className="muted small">Click a dot to open it. Larger, pulsing dots are next in line.</p>
        </section>
        <section className="stack" aria-label="Ranked queue">
          <div className="row">
            <input className="input" style={{ flex: 1 }} value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search by reference, problem, street or agency" aria-label="Search the queue" />
            <button className="btn ghost" onClick={reload} aria-label="Refresh"><Icon name="arrow" size={18} /> Refresh</button>
          </div>
          <div className="queue-list">
            {shown.map((q, i) => (
              <Tilt as="button" key={q.request_id} className={`card lane ${q.urgency_level} queue-item`} aria-pressed={open === q.request_id}
                onClick={() => setOpen(q.request_id)} style={{ '--i': Math.min(i, 14) }}>
                <Bullet tone={q.on_hold ? 'grey' : bandTone(q.priority_band)} title={BAND_WORDS[q.priority_band] || ''}>{q.on_hold ? '=' : q.queue_position}</Bullet>
                <span className="stack" style={{ gap: 6 }}>
                  <span className="row">
                    <span className="plate">{label(q.label)}</span>
                    <span className={`lvl ${q.urgency_level}`}>{LEVEL_WORDS[q.urgency_level] || 'No level'}</span>
                    {q.priority_band && <span className="pill">{BAND_WORDS[q.priority_band] || q.priority_band}</span>}
                    {q.corroboration_count > 1 && <span className="pill held">{q.corroboration_count} reporters</span>}
                    {q.sensitive_site_type && <span className="pill purple">Near a {q.sensitive_site_type}</span>}
                    {q.escalation_level && q.escalation_level !== 'NONE' && <span className="pill ERROR">{q.escalation_approved ? 'Escalated' : 'Escalation to approve'}</span>}
                  </span>
                  <span className="muted">{q.on_hold && q.duplicate_of ? `Same problem as ${q.duplicate_of}. ` : ''}{[titleCase(q.slots?.incident_address), titleCase(q.slots?.borough)].filter(Boolean).join(', ')}</span>
                  <span className="muted num small">{q.request_id} · {q.agency} · {since(q.created_at)} · {STATUS_WORDS[q.status] || q.status}{q.crew ? ` · ${q.crew}` : ''}{q.source ? ' · from 311' : ''}</span>
                  {!q.on_hold && rankedBecause(q).length > 0 && (
                    <span className="aiwhy small"><Icon name="sparkle" size={14} /> Ranked {q.queue_position} by AI: {rankedBecause(q).join(', ')}</span>)}
                </span>
              </Tilt>
            ))}
            {!shown.length && items.length > 0 && <div className="card empty">Nothing matches.</div>}
          </div>
          {data.held_for_person?.length > 0 && (
            <>
              <h3>Held for a person</h3>
              {data.held_for_person.map((q) => (
                <Tilt as="button" key={q.request_id} className="card queue-item" onClick={() => setOpen(q.request_id)}>
                  <Bullet tone="blue">?</Bullet><span>{label(q.label)} <span className="muted">· {q.request_id} · {since(q.created_at)}</span></span>
                </Tilt>
              ))}
            </>
          )}
        </section>
      </div>
      <Drawer open={!!open} onClose={() => setOpen(null)} label="Request detail">
        {open && <Detail id={open} proposed={items.find((q) => q.request_id === open)?.proposed} onChanged={reload} />}
      </Drawer>
    </div>
  )
}

function Detail({ id, proposed, onChanged }) {
  const [d, setD] = useState(null)
  const [tab, setTab] = useState('overview')
  const [crews, setCrews] = useState([])
  const [crew, setCrew] = useState('')
  const [note, setNote] = useState('')
  const load = () => api.detail(id).then((x) => {
    setD(x)
    if (x.agency) api.crews(x.agency, x.slots?.borough).then((c) => {
      setCrews(c)
      // Crew deployment's proposal first, otherwise the crew with the fewest open jobs.
      setCrew((proposed && c.some((k) => k.crew_id === proposed.crew_id) && proposed.crew_id) || c.sort((a, b) => a.open_jobs - b.open_jobs)[0]?.crew_id || '')
    })
  }).catch((e) => toast(e.message, 'bad'))
  useEffect(() => { setD(null); setTab('overview'); load() }, [id])
  if (!d) return <Skeleton lines={10} />
  const notes = d.notes || []
  return (
    <div className="stack">
      <div className="row"><span className="plate">{label(d.label)}</span><span className={`lvl ${d.urgency_level}`}>{LEVEL_WORDS[d.urgency_level] || 'No level'}</span><span className="muted num">{d.request_id}</span></div>
      <blockquote className="said">{[...(d.history || []), d.text].filter(Boolean).join(' ')}</blockquote>
      <div className="segmented wide small" role="tablist">
        {['overview', 'why', 'notes'].map((t) => <button key={t} role="tab" aria-selected={tab === t} onClick={() => setTab(t)}>
          {t === 'overview' ? 'Overview' : t === 'why' ? 'Why, step by step' : `Notes (${notes.length})`}</button>)}
      </div>
      {tab === 'overview' && (
        <div className="stack tabbody">
          <div className="facts">
            <div><span>Agency</span><strong>{d.agency || 'A person decides'}</strong></div>
            <div><span>Status</span><strong>{STATUS_WORDS[d.status] || d.status}</strong></div>
            <div><span>Reporter record</span><strong className="num">{d.credibility_score ?? 'n/a'}</strong></div>
            <div><span>Nearby</span><strong>{d.sensitive_site_type ? titleCase(d.sensitive_site_type) : d.external_factor === 0 ? 'Nothing sensitive' : 'Not checked'}</strong></div>
            <div><span>Filed</span><strong>{since(d.created_at)}</strong></div>
            <div><span>Source</span><strong>{d.source ? 'NYC 311 record' : 'A resident, here'}</strong></div>
          </div>
          {d.slots?.latitude != null && <CityMap compact points={[{ id: d.request_id, lat: d.slots.latitude, lon: d.slots.longitude, tone: levelTone(d.urgency_level), r: 11, pulse: true }]} label="Where it is" />}
          {d.sensitive_site_type && (
            <div className="row">
              <span className="pill purple">External factors found a {d.sensitive_site_type} nearby</span>
              {d.sensitive_impact_verified ? <span className="muted">Confirmed, so Prioritisation counts it.</span> : (
                <button className="btn" onClick={async () => { try { await api.confirmImpact(d.request_id); toast(`Confirmed: it affects the ${d.sensitive_site_type}`); load(); onChanged() } catch (e) { toast(e.message, 'bad') } }}>
                  Confirm it affects the {d.sensitive_site_type}</button>)}
            </div>
          )}
          {d.duplicate_relation === 'DUPLICATE' && <p className="pill held">Same problem as {d.duplicate_of}. One crew goes for both.</p>}
          {d.duplicate_relation === 'RECURRENCE' && <p className="pill held">Came back after {d.recurrence_of} was closed.</p>}
          {d.fake_action === 'HOLD_FOR_REVIEW' && <p className="pill ERROR">Fake request detection asks a person to check this report. It is never rejected.</p>}
          {d.escalation_level && d.escalation_level !== 'NONE' && (
            <div className="row">
              <span className="pill ERROR">Escalation recommended: {d.escalation_level.replace('_', ' ').toLowerCase()}</span>
              {d.escalation_approved ? <span className="muted">Approved, so it now counts in the queue.</span> : (
                <button className="btn primary" onClick={async () => { await api.approveEscalation(d.request_id); toast('Escalation approved'); load(); onChanged() }}>Approve escalation</button>)}
            </div>
          )}
          {d.status !== 'CLOSED' && d.agency && (
            <div className="stack assign">
              <h3>{d.crew_job ? 'Crew' : 'Assign a crew'}</h3>
              {d.crew_job ? (
                <div className="row">
                  <p className="muted small" style={{ flex: 1 }}>With {d.crew_job.crew_id} since {since(d.crew_job.assigned_at)}. Give it to another crew below, or pull it back to the queue.</p>
                  <button className="btn ghost" onClick={async () => { try { await api.unassign(d.request_id); toast(`Pulled back from ${d.crew_job.crew_id}`); load(); onChanged() } catch (e) { toast(e.message, 'bad') } }}>Pull back</button>
                </div>
              ) : (
                <p className="muted small">{d.dispatch_hold ? 'Pulled back by staff, so Crew deployment leaves it alone. Choose a crew: those with fewer open jobs are listed first.'
                  : proposed ? `Crew deployment is sending it to ${proposed.crew_id}, as stop ${proposed.sequence} of its route. Choose another crew to change that.`
                    : 'Crew deployment has not planned this request, for example while it waits for review. Choose a crew: those with fewer open jobs are listed first.'}</p>
              )}
              {crews.length ? (
                <div className="row">
                  <select className="input" style={{ flex: 1 }} value={crew} onChange={(e) => setCrew(e.target.value)} aria-label="Crew">
                    {crews.map((c) => <option key={c.crew_id} value={c.crew_id}>{c.crew_id} · {titleCase(c.borough)} · {c.open_jobs} open</option>)}
                  </select>
                  <button className="btn primary" onClick={async () => { try { await api.assign(d.request_id, crew); toast(`Assigned to ${crew}`); load(); onChanged() } catch (e) { toast(e.message, 'bad') } }}>{d.crew_job ? 'Give to this crew' : 'Assign'}</button>
                </div>
              ) : <p className="muted">No {d.agency} crew in this borough.</p>}
            </div>
          )}
        </div>
      )}
      {tab === 'why' && (
        <ol className="journey tabbody">
          {d.why?.map((w, i) => (
            <li key={w.component + i} className={`on ${w.outcome !== 'LIVE' ? 'fallback' : ''}`} style={{ '--i': i }}>
              <span className="dot" aria-hidden="true"><Icon name="check" size={14} /></span>
              <span>
                <span className="row"><strong>{w.component}</strong><span className={`pill ${w.outcome}`}>{OUTCOME_WORDS[w.outcome]}</span><span className="muted num small">{w.ms} ms</span></span>
                {w.reasons.slice(0, 4).map((r, j) => <span key={j} className="muted">{r}</span>)}
              </span>
            </li>
          ))}
          {d.priority_reasons?.length > 0 && <li className="on"><span className="dot"><Icon name="layers" size={14} /></span><span><strong>Queue position</strong>{d.priority_reasons.map((r, j) => <span key={j} className="muted">{r}</span>)}</span></li>}
        </ol>
      )}
      {tab === 'notes' && (
        <div className="stack tabbody">
          {notes.length ? notes.map((n, i) => <p key={i} className="note"><strong>{n.by === 'resident' ? 'Resident' : n.by}</strong> <span className="muted small">{since(n.at)}</span><br />{n.text}</p>)
            : <p className="muted">No notes yet.</p>}
          <form className="row" onSubmit={async (e) => { e.preventDefault(); await api.note(d.request_id, note); setNote(''); toast('Note added'); load() }}>
            <input className="input" style={{ flex: 1 }} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Add a note for other staff" maxLength={500} aria-label="Note" />
            <button className="btn" disabled={!note.trim()}>Add</button>
          </form>
        </div>
      )}
    </div>
  )
}

// Why Crew deployment could not plan a request, in plain words.
const REASONS = {
  NO_COMPATIBLE_CREW: 'No crew of that agency has the skill',
  NO_AVAILABLE_COMPATIBLE_CREW: 'Every crew with the skill is unavailable',
  INSUFFICIENT_TIME_CAPACITY: "Not enough time left in any crew's day",
  CAPACITY_USED_BY_OTHER_REQUESTS: 'Crews are full with higher ranked work',
  UNROUTABLE_LOCATION: 'The location is missing or too far from a road',
  DEFERRED_BY_OPTIMIZER: 'Left for a later plan',
  INVALID_INPUT: 'Some details are missing',
  ALREADY_COMPLETED_OR_CANCELLED: 'Already closed',
}

function Plan() {
  const [state, setState] = useState(null)
  const [error, setError] = useState(null)
  const [open, setOpen] = useState(null)
  const [busy, setBusy] = useState(null)
  const alive = useRef(true)
  const load = useCallback(async () => {
    try {
      const r = await api.plan(12)
      if (!alive.current) return
      setState(r); setError(null)
      if (r.planning) setTimeout(() => alive.current && load(), 3000)
    } catch (e) { if (alive.current) setError(e) }
  }, [])
  useEffect(() => {
    alive.current = true; load()
    const t = setInterval(() => alive.current && load(), 30000)   // crews move on as they close jobs
    return () => { alive.current = false; clearInterval(t) }
  }, [load])
  useReveal(state)

  async function pullBack(rid, crewId) {
    setBusy(rid)
    try {
      await api.unassign(rid)
      toast(`${rid} pulled back from ${crewId}. Assign it by hand from the queue.`)
      load()
    } catch (e) { toast(e.message, 'bad'); load() } finally { setBusy(null) }
  }

  if (error) return <ErrorNote error={error} onRetry={load} />
  if (!state) return <div className="card"><Skeleton lines={8} /></div>
  const plan = state.plan
  if (!plan) {
    return (
      <div className="card empty stack">
        <h2>{state.planning ? 'Planning the crews...' : 'No crew routes'}</h2>
        <p className="muted">{state.planning ? 'Crew deployment is working through the queue and will send each route to its crew. This takes a few seconds.'
          : state.outcome === 'OFF' ? 'The tech team switched Crew deployment off, so nothing is assigned automatically. Assign crews by hand from the queue.'
            : 'Crew deployment is not available right now, so nothing is assigned automatically. Assign crews by hand from the queue.'}</p>
        {state.planning && <Skeleton lines={3} />}
      </div>
    )
  }
  const crews = plan.crews.map((c) => ({ ...c, jobs: [...c.approved, ...c.stops] })).filter((c) => c.jobs.length)
  crews.sort((a, b) => Math.min(...a.jobs.map((j) => j.queue_position || 999)) - Math.min(...b.jobs.map((j) => j.queue_position || 999)))
  const assigned = crews.reduce((n, c) => n + c.approved.length, 0)
  const kpis = [['Jobs with a crew', assigned + plan.planned, 'green'], ['Crews out', crews.length, 'blue'],
    ['Waiting for review', plan.pending_review, 'orange'], ['On hold', plan.held, 'grey'], ['Minutes of driving', Math.round(plan.travel_min), '']]
  return (
    <div className="stack">
      <div className="kpis">
        {kpis.map(([name, n, tone], i) => (
          <Tilt key={name} className={`kpi ${tone} reveal`} style={{ '--i': i }}><strong><CountUp value={n} /></strong><span>{name}</span></Tilt>
        ))}
      </div>
      <div className="card row reveal">
        <Icon name="route" />
        <p style={{ flex: 1 }}><strong>Assigned automatically.</strong> <span className="muted">Crew deployment sends each route to its crew as soon as it is planned, in the order shown. Requests waiting for review or on hold are never sent. To change one, pull it back and assign it by hand from the queue, or give it to another crew from the request itself.</span></p>
        <span className={`pill ${state.outcome}`}>{state.planning ? 'Updating...' : `Planned ${since(plan.made_at)}`}</span>
      </div>
      {!crews.length && <div className="card empty">No request is ready for a crew. Requests appear here once they are ranked and need no review.</div>}
      <div className="joblist">
        {crews.map((c, i) => {
          const isOpen = open === c.crew_id
          const route = c.jobs.map((s) => ({ id: s.request_id, lat: s.lat, lon: s.lon, name: titleCase(s.address) || s.request_id }))
          const focus = isOpen ? boxAround([c.depot, c.position, ...route]) : null
          return (
            <Tilt as="article" key={c.crew_id} className={`card job reveal ${isOpen ? 'open' : ''}`} style={{ '--i': Math.min(i, 12) }}>
              <button className="jobhead" onClick={() => setOpen(isOpen ? null : c.crew_id)} aria-expanded={isOpen}>
                <Bullet tone="blue" size={38}>{c.jobs.length}</Bullet>
                <span className="stack" style={{ gap: 4 }}>
                  <span className="row"><span className="plate">{c.crew_id}</span><span className="pill">{c.agency}</span>
                    <span className="pill LIVE">{c.approved.length} sent to the crew</span>
                    {c.stops.length > 0 && <span className="pill">{c.stops.length} being sent</span>}</span>
                  <strong className="addrline">{c.jobs.map((s) => label(s.label)).join(', then ')}</strong>
                  <span className="muted small num">{c.travel_min ? `${c.travel_min} min more driving · ` : ''}{Math.round(c.remaining_min)} min free in its day</span>
                </span>
                <Icon name="arrow" size={20} />
              </button>
              {isOpen && (
                <div className="jobbody pop stack">
                  <CityMap route={route} roads={c.roads} focus={focus} streets start={c.depot} now={c.position}
                    label={`Route for ${c.crew_id} from its depot: ${route.map((r) => r.name).join(', then ')}`} />
                  <p className="muted small"><span className="legend depot" /> Start, the crew's depot · <span className="legend now" /> where the crew is now, its last closed job · numbers are its stops in order</p>
                  <ol className="stoplist">
                    {c.jobs.map((s, j) => (
                      <li key={s.request_id} className="row">
                        <Bullet tone={levelTone(s.urgency)} size={30}>{j + 1}</Bullet>
                        <span className="stack" style={{ gap: 2, flex: 1 }}>
                          <span><strong>{label(s.label)}</strong> <span className="muted">· {[titleCase(s.address), titleCase(s.borough)].filter(Boolean).join(', ')}</span></span>
                          <span className="muted small num">{s.approved ? 'With the crew' : `About ${clock(s.est_start)} · ${s.travel_min} min drive, ${s.distance_km} km`} · queue position {s.queue_position ?? 'n/a'} · {s.request_id}</span>
                        </span>
                        {s.approved && <button className="btn ghost small" disabled={busy === s.request_id} onClick={() => pullBack(s.request_id, c.crew_id)}>{busy === s.request_id ? 'Pulling back...' : 'Pull back'}</button>}
                      </li>
                    ))}
                  </ol>
                </div>
              )}
            </Tilt>
          )
        })}
      </div>
      {plan.unplanned.length > 0 && (
        <section className="card stack reveal">
          <h3>Not given to a crew this time ({plan.unplanned.length})</h3>
          {plan.unplanned.map((u) => (
            <p key={u.request_id} className="row"><span className="plate">{label(u.label)}</span>
              <span className="muted">{u.reasons.map((r) => REASONS[r] || r).join('. ')} · {u.request_id}</span></p>
          ))}
        </section>
      )}
    </div>
  )
}

const STARTERS = ['How many open requests are in Brooklyn?', 'Which agency has the most open requests?',
  'Why is the first request ranked first?', 'How many open potholes are there?', 'Show the top 5 open requests in Queens']

function Ask() {
  const [thread, setThread] = useState([])
  const [q, setQ] = useState('')
  const [busy, setBusy] = useState(false)
  const end = useRef(null)
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [thread, busy])
  async function ask(text) {
    if (!text.trim() || busy) return
    setQ('')
    setThread((t) => [...t, { from: 'me', text }])
    setBusy(true)
    try {
      const a = await api.ask(text)
      setThread((t) => [...t, { from: 'bot', a }])
    } catch (e) {
      setThread((t) => [...t, { from: 'bot', a: { answer: e.message, outcome: 'ERROR', table: [], suggestions: [] } }])
    } finally { setBusy(false) }
  }
  return (
    <section className="card askcard">
      <div className="assistant-head">
        <span className="bot-avatar"><Icon name="sparkle" size={18} /></span>
        <div><strong>Admin questions</strong><span className="muted small">Answers from a read only copy of every request. It cannot change anything, and never shows who reported.</span></div>
      </div>
      <div className="chat tall" aria-live="polite">
        {!thread.length && (
          <div className="ask-empty">
            <p className="lede">Ask about the queue in plain English.</p>
            <div className="starters">{STARTERS.map((s, i) => <Tilt as="button" key={s} className="starter" style={{ '--i': i }} onClick={() => ask(s)}>{s}</Tilt>)}</div>
          </div>
        )}
        {thread.map((m, i) => m.from === 'me'
          ? <div key={i} className="msg me">{m.text}</div>
          : <Answer key={i} a={m.a} latest={i === thread.length - 1} onAsk={ask} />)}
        {busy && <div className="msg bot typing"><i /><i /><i /></div>}
        <div ref={end} />
      </div>
      <form className="composer" onSubmit={(e) => { e.preventDefault(); ask(q) }}>
        <input className="input" value={q} onChange={(e) => setQ(e.target.value)} maxLength={300} placeholder="Ask a question about requests, queues, crews or trends" aria-label="Question" />
        <button className="btn primary" disabled={busy || !q.trim()} aria-label="Ask"><Icon name="send" size={18} /></button>
      </form>
    </section>
  )
}

function Answer({ a, latest, onAsk }) {
  const [typed, setTyped] = useState(!latest)
  const cols = a.table?.length ? Object.keys(a.table[0]).slice(0, 6) : []
  return (
    <div className="msg bot answer">
      <div className="row"><span className={`pill ${a.outcome}`}>{OUTCOME_WORDS[a.outcome] || a.outcome}</span>{a.intent && a.intent !== 'UNKNOWN' && <span className="muted small">{a.intent.toLowerCase().replace('_', ' ')}</span>}</div>
      <p><strong>{latest && !typed ? <TypeOut text={a.answer} onDone={() => setTyped(true)} /> : a.answer}</strong></p>
      {typed && cols.length > 0 && (
        <div className="tablewrap pop">
          <table>
            <thead><tr>{cols.map((c) => <th key={c}>{c.replace(/_/g, ' ')}</th>)}</tr></thead>
            <tbody>{a.table.slice(0, 10).map((r, i) => <tr key={i} style={{ '--i': i }}>{cols.map((c) => <td key={c} className="num">{String(r[c] ?? '')}</td>)}</tr>)}</tbody>
          </table>
        </div>
      )}
      {typed && [...(a.suggestions || []), ...(a.examples || [])].length > 0 && (
        <div className="chips wrap pop">{[...(a.suggestions || []), ...(a.examples || [])].map((x) => <button key={x} className="chip" onClick={() => onAsk(x)}>{x}</button>)}</div>
      )}
    </div>
  )
}

function Alerts() {
  const [borough, setBorough] = useState('BROOKLYN')
  const [days, setDays] = useState([])
  const [res, setRes] = useState(null)
  const [busy, setBusy] = useState(false)
  const run = (day) => { setBusy(true); api.alerts(borough, day).then(setRes).catch((e) => toast(e.message, 'bad')).finally(() => setBusy(false)) }
  useEffect(() => { setRes(null); api.stormDays(borough).then(setDays).catch(() => setDays([])); run(null) }, [borough])
  const max = Math.max(3, ...(res?.result?.alerts || []).map((a) => a.lift))
  return (
    <div className="stack">
      <div className="card stack">
        <h2>Busy day alerts</h2>
        <p className="muted">Predictive alerting reads the weather for a day and warns which kinds of report will rise, so crews can be ready before the calls arrive.</p>
        <div className="row">
          <select className="input" style={{ maxWidth: 240 }} value={borough} onChange={(e) => setBorough(e.target.value)} aria-label="Borough">
            {BOROUGH_LIST.map((b) => <option key={b} value={b}>{titleCase(b)}</option>)}
          </select>
          <span className="muted small">Try a recorded storm:</span>
        </div>
        <div className="chips wrap">{days.map((d) => <button key={d.day} className="chip num" onClick={() => run(d.day)}>{d.day} · {Math.round(d.rain_mm)} mm rain</button>)}</div>
      </div>
      {busy && <div className="card"><Skeleton lines={4} /></div>}
      {res && !busy && (
        <div className="card stack pop">
          <div className="row"><h3>{titleCase(res.borough)}, {res.day || 'latest day'}</h3>
            {res.weather?.wx_precip_mm != null && <span className="pill">{Math.round(res.weather.wx_precip_mm)} mm rain · {Math.round(res.weather.wx_tmax_c)}°C</span>}</div>
          {res.switched_off && <p className="pill MISSING">Switched off by the tech team.</p>}
          {!res.switched_off && !res.result.ok && <p className="error">The forecast failed, so no warnings are shown. That is reported, never shown as a quiet day.</p>}
          {res.result.ok && !res.result.alerts.length && <p className="muted">A normal day expected. No warnings.</p>}
          {res.result.alerts?.map((a, i) => (
            <div key={a.complaint_family} className="alertrow" style={{ '--i': i }}>
              <div className="row"><strong>{a.complaint_family.replace('_', ' and ').toLowerCase()}</strong><span className={`pill ${a.severity === 'WARNING' ? 'ERROR' : 'held'}`}>{a.severity}</span></div>
              <div className="bar"><span style={{ width: `${Math.min(100, (a.lift / max) * 100)}%` }} className={a.severity === 'WARNING' ? 'warn' : ''} /></div>
              <p className="muted num small">{a.lift.toFixed(1)} times a normal day: about {Math.round(a.expected_count)} reports against {Math.round(a.baseline_count)}</p>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
