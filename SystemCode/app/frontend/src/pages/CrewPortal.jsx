import { useMemo, useState } from 'react'
import { api, clock, label, LEVEL_WORDS, since, titleCase } from '../api.js'
import { ErrorNote, Modal, Skeleton, Tilt, toast, useAsync, useReveal } from '../motion.jsx'
import { Bullet, CityMap, Icon, levelTone } from '../ui.jsx'

const OUTCOMES = [
  { value: 'VALID_RESOLVED', text: 'Fixed, the problem was there', tone: 'primary' },
  { value: 'SUBSIDED', text: 'It had cleared before we arrived' },
  { value: 'INVALID_NOT_FOUND', text: 'Nothing found' },
  { value: 'NO_ACCESS', text: 'Could not get access' },
  { value: 'WRONG_AGENCY', text: 'Another agency should handle it' },
]

export default function CrewPortal() {
  const { data, error, loading, reload } = useAsync(() => api.crewJobs(), [])
  const [open, setOpen] = useState(null)
  const [confirm, setConfirm] = useState(null)
  const [done, setDone] = useState([])
  const [busy, setBusy] = useState(false)
  useReveal(data)
  const jobs = data?.jobs || []
  const route = useMemo(() => jobs.map((j) => ({ id: j.request_id, lat: j.latitude, lon: j.longitude, name: titleCase(j.address) || j.request_id })), [jobs])

  async function close() {
    setBusy(true)
    try {
      const r = await api.close(confirm.job.request_id, confirm.outcome.value)
      setDone((d) => [{ ...confirm.job, closure: r.closure }, ...d])
      toast(`${confirm.job.request_id} closed`)
      setConfirm(null); setOpen(null); reload()
    } catch (e) { toast(e.message, 'bad') } finally { setBusy(false) }
  }

  if (error) return <div className="page"><ErrorNote error={error} onRetry={reload} /></div>
  return (
    <div className="page stack">
      <header className="pagehead reveal">
        <p className="eyebrow">Crew {data?.crew ? `· ${data.crew.crew_id} · ${data.crew.agency} · ${titleCase(data.crew.borough)}` : ''}</p>
        <h1>{loading ? 'Loading your stops...' : jobs.length ? `${jobs.length} stop${jobs.length > 1 ? 's' : ''} today` : 'No open jobs'}</h1>
        {data && <p className="muted"><Icon name="route" size={16} /> Order: {data.order_source}.</p>}
      </header>
      {loading && !data && <div className="card"><Skeleton lines={6} /></div>}
      {route.some((r) => r.lat != null) && <div className="reveal"><CityMap route={route} roads={(data?.roads || []).filter((r) => r.kind === 'locked_leg')} label={`Today's stops: ${route.map((r) => r.name).join(', then ')}`} /></div>}
      <div className="joblist">
        {jobs.map((j, i) => (
          <Tilt as="article" key={j.request_id} className={`card lane ${j.urgency} job reveal ${open === j.request_id ? 'open' : ''}`} style={{ '--i': i }}>
            <button className="jobhead" onClick={() => setOpen(open === j.request_id ? null : j.request_id)} aria-expanded={open === j.request_id}>
              <Bullet tone={levelTone(j.urgency)} size={38}>{i + 1}</Bullet>
              <span className="stack" style={{ gap: 4 }}>
                <span className="row"><span className="plate">{label(j.label)}</span><span className={`lvl ${j.urgency}`}>{LEVEL_WORDS[j.urgency]}</span></span>
                <strong className="addrline">{[titleCase(j.address), titleCase(j.borough)].filter(Boolean).join(', ')}</strong>
                <span className="muted small num">{j.request_id} · assigned {since(j.assigned_at)}</span>
              </span>
              <Icon name="arrow" size={20} />
            </button>
            {open === j.request_id && (
              <div className="jobbody pop">
                {j.latitude != null && <CityMap compact points={[{ id: j.request_id, lat: j.latitude, lon: j.longitude, tone: levelTone(j.urgency), r: 11, pulse: true }]} label="Where it is" />}
                <p className="muted">What did you find?</p>
                <div className="outcomes">
                  {OUTCOMES.map((o) => (
                    <button key={o.value} className={`btn big ${o.tone || ''}`} onClick={() => setConfirm({ job: j, outcome: o })}>{o.text}</button>
                  ))}
                </div>
              </div>
            )}
          </Tilt>
        ))}
      </div>
      {!loading && !jobs.length && <div className="card empty">All done. New jobs appear here when city staff approve a route for your crew.</div>}
      {data?.proposed_next?.length > 0 && (
        <section className="card stack reveal">
          <h3>Proposed next, waiting for city staff</h3>
          <p className="muted small">Crew deployment plans these for you. They become jobs once city staff approve them.</p>
          {data.proposed_next.map((s, i) => (
            <p key={s.request_id} className="row"><Bullet tone="grey" size={28}>{jobs.length + i + 1}</Bullet>
              <span><strong>{label(s.label)}</strong> <span className="muted">· {titleCase(s.address)} · about {clock(s.est_start)}</span></span></p>
          ))}
        </section>
      )}
      {done.map((j) => (
        <div key={j.request_id} className="card done-card stack pop">
          <div className="row"><Icon name="check" /><span className="plate">{label(j.label)}</span><span className="pill LIVE">Closed</span></div>
          <p className="muted">{j.closure?.reasons?.join(' ')}</p>
          {j.closure?.credibility_change != null && <p className="num"><strong>The reporter's record {j.closure.credibility_change >= 0 ? 'improves' : 'drops'} by {Math.abs(j.closure.credibility_change)} points.</strong></p>}
        </div>
      ))}
      <Modal open={!!confirm} onClose={() => setConfirm(null)} title="Confirm">
        {confirm && (
          <div className="stack">
            <p className="eyebrow">Close {confirm.job.request_id}</p>
            <h2>{confirm.outcome.text}?</h2>
            <p className="muted">This closes the job and is recorded for the reporter's record. It cannot be undone here.</p>
            <div className="row"><button className="btn primary" disabled={busy} onClick={close}>{busy ? 'Closing...' : 'Yes, close it'}</button><button className="btn ghost" onClick={() => setConfirm(null)}>Cancel</button></div>
          </div>
        )}
      </Modal>
    </div>
  )
}
