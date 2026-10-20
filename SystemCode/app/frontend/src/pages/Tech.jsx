import { useState } from 'react'
import { api, OUTCOME_WORDS, since, titleCase } from '../api.js'
import { CountUp, ErrorNote, Modal, Skeleton, Switch, Tilt, toast, useAsync, useReveal } from '../motion.jsx'
import { Icon } from '../ui.jsx'

const VIEWS = [{ id: 'components', name: 'Components', icon: 'layers' }, { id: 'measure', name: 'Measurements', icon: 'gauge' },
  { id: 'accounts', name: 'Accounts', icon: 'key' }, { id: 'system', name: 'System', icon: 'bolt' }]

export default function Tech({ view = 'components', go }) {
  return (
    <div className="page stack">
      <div className="segmented wide" role="tablist" aria-label="Tech team views">
        {VIEWS.map((v) => <button key={v.id} role="tab" aria-selected={view === v.id} onClick={() => go(v.id)}><Icon name={v.icon} size={18} /> {v.name}</button>)}
      </div>
      {view === 'components' && <Components />}
      {view === 'measure' && <Measurements />}
      {view === 'accounts' && <Accounts />}
      {view === 'system' && <System />}
    </div>
  )
}

// The pipeline's paired entries are two components each, measured together.
const count = (name) => name.split(' + ').length

function Components() {
  const { data, error, loading, reload } = useAsync(() => api.health(), [])
  const [open, setOpen] = useState(null)
  const [busy, setBusy] = useState('')
  useReveal(data)
  if (error) return <ErrorNote error={error} onRetry={reload} />
  if (loading && !data) return <div className="card"><Skeleton lines={8} /></div>
  const comps = data.components
  const statOf = (c) => data.stats[c.name] || data.stats[`${c.name} (queue)`] || (c.name === 'Urgency + Prioritisation' ? data.stats['Prioritisation (queue)'] : null)
  const live = comps.filter((c) => c.available && c.enabled).reduce((n, c) => n + count(c.name), 0)
  const total = comps.reduce((n, c) => n + count(c.name), 0)
  const failing = comps.filter((c) => (statOf(c)?.error || 0) > 0).length
  const flip = async (c, on) => {
    setBusy(c.name)
    try { await api.setSwitch(c.name, on); toast(`${c.name} ${on ? 'switched on' : 'switched off'} for everyone`); reload() } catch (e) { toast(e.message, 'bad') } finally { setBusy('') }
  }
  return (
    <div className="stack">
      <div className="kpis">
        <div className="kpi reveal"><strong><CountUp value={live} /> of {total}</strong><span>components live</span></div>
        <div className="kpi orange reveal"><strong><CountUp value={comps.filter((c) => !c.enabled).length} /></strong><span>switched off</span></div>
        <div className="kpi red reveal"><strong><CountUp value={failing} /></strong><span>with errors</span></div>
        <div className="kpi blue reveal"><strong><CountUp value={data.emergency_stops} /></strong><span>emergency stops, none filed</span></div>
      </div>
      <p className="muted">A switch applies to every resident, staff member and crew at once: a component that is off runs its agreed safe default, and every screen says so.</p>
      <div className="compgrid">
        {comps.map((c, i) => {
          const s = statOf(c)
          const state = !c.available ? 'MISSING' : !c.enabled ? 'OFF' : s?.error ? 'ERROR' : 'LIVE'
          return (
            <Tilt key={c.name} className={`card comp reveal ${state}`} style={{ '--i': i }}>
              <div className="comp-top">
                <span className={`dotstate ${state}`} aria-hidden="true" />
                <div><h3>{c.name}</h3><span className="muted small">{c.owner} · {c.source}</span></div>
                <Switch on={c.enabled} disabled={!!c.locked || !c.available || busy === c.name} onChange={(on) => flip(c, on)} label={`${c.name} on or off`} />
              </div>
              <span className={`pill ${state}`}>{OUTCOME_WORDS[state]}</span>
              {c.locked && <p className="muted small"><Icon name="shield" size={14} /> {c.locked}</p>}
              <div className="minis">
                <div><strong className="num">{s?.n ?? 0}</strong><span>calls</span></div>
                <div><strong className="num">{s?.n ? Math.round((100 * s.live) / s.n) : 0}%</strong><span>live</span></div>
                <div><strong className="num">{s?.median_ms ?? 'n/a'}</strong><span>median ms</span></div>
                <div><strong className="num">{s?.p90_ms ?? 'n/a'}</strong><span>p90 ms</span></div>
              </div>
              <button className="btn ghost small" onClick={() => setOpen(c)}>Details</button>
            </Tilt>
          )
        })}
      </div>
      <Modal open={!!open} onClose={() => setOpen(null)} title={open?.name} wide>
        {open && (
          <div className="stack">
            <p className="eyebrow">{open.owner}</p>
            <h2>{open.name}</h2>
            <div className="facts">
              <div><span>Source</span><strong>{open.source || 'not delivered'}</strong></div>
              <div><span>Pinned version</span><strong>{open.pinned || 'this repository'}</strong></div>
              <div><span>Runs</span><strong>{open.stage === 'turn' ? 'on every message' : open.stage === 'full' ? 'once per request' : open.stage === 'queue' ? 'over the whole ranked queue' : 'beside the pipeline'}</strong></div>
              <div><span>Errors</span><strong>{statOf(open)?.error ?? 0}{statOf(open)?.last_error ? ` · last ${since(statOf(open).last_error)}` : ''}</strong></div>
            </div>
            {open.reads?.length > 0 && <p><strong>Reads</strong> <span className="muted">{open.reads.join(', ')}</span></p>}
            {open.writes?.length > 0 && <p><strong>Writes</strong> <span className="muted">{open.writes.join(', ')}</span></p>}
            <p><strong>Safe default when off or failing</strong><br /><span className="muted">{open.safe_default}</span></p>
            {data.switch_history[open.name] && <p className="muted small">Last switched {data.switch_history[open.name].enabled ? 'on' : 'off'} by {data.switch_history[open.name].changed_by}, {since(data.switch_history[open.name].changed_at)}.</p>}
          </div>
        )}
      </Modal>
    </div>
  )
}

function Measurements() {
  const { data: m0, reload } = useAsync(() => api.lastMeasure(), [])
  const [m, setM] = useState(null)
  const [busy, setBusy] = useState(false)
  const shown = m || (m0?.rows ? m0 : null)
  return (
    <div className="stack">
      <div className="card stack">
        <h2>What each component changes</h2>
        <p className="muted">The 34 evaluation scenarios run with everything on, then with one component on its agreed default at a time. They are never filed as real requests.</p>
        <button className="btn primary" disabled={busy} onClick={async () => { setBusy(true); try { setM(await api.measure()); reload() } catch (e) { toast(e.message, 'bad') } finally { setBusy(false) } }}>
          {busy ? 'Running the scenarios, about a minute...' : shown ? 'Run again' : 'Run the measurement'}
        </button>
      </div>
      {busy && <div className="card"><Skeleton lines={6} /></div>}
      {shown && !busy && (
        <div className="card stack pop">
          <h3>{shown.title}</h3>
          <div className="tablewrap">
            <table>
              <thead><tr><th>Switched to default</th><th>Filed</th><th>Dispatch</th><th>Agency</th><th>Urgency</th><th>Duplicate</th><th>Fake action</th><th>Queue order</th></tr></thead>
              <tbody>
                {shown.rows.map((r, i) => (
                  <tr key={r.unit} style={{ '--i': i }}>
                    <td><strong>{r.unit}</strong></td><td className="num">{r.filed} of {shown.cases}</td>
                    <td className="num">{r.dispatch_changed}</td><td className="num">{r.agency_changed}</td><td className="num">{r.urgency_changed}</td>
                    <td className="num">{r.duplicate_changed ?? 'n/a'}</td><td className="num">{r.fake_action_changed ?? 'n/a'}</td>
                    <td className="num">{r.queue_tau_b == null ? 'n/a' : `tau-b ${r.queue_tau_b}`}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="muted small">Numbers are cases that changed. tau-b 1.0 means the queue order did not change at all. Not in this measurement, because it plans the whole queue rather than one request: {shown.not_measurable.join(', ') || 'none'}.</p>
          <details><summary>Conditions of this run</summary><ul>{shown.conditions.map((c) => <li key={c} className="muted">{c}</li>)}</ul></details>
        </div>
      )}
    </div>
  )
}

function Accounts() {
  const { data, error, reload } = useAsync(() => api.users(), [])
  const { data: crews } = useAsync(() => api.allCrews(), [])
  const [role, setRole] = useState('staff')
  const [name, setName] = useState('')
  const [crew, setCrew] = useState('')
  const [issued, setIssued] = useState(null)
  const [busy, setBusy] = useState(false)
  async function create(e) {
    e.preventDefault()
    setBusy(true)
    try { setIssued(await api.createUser(role, name, role === 'crew' ? crew : null)); setName(''); reload() } catch (x) { toast(x.message, 'bad') } finally { setBusy(false) }
  }
  if (error) return <ErrorNote error={error} onRetry={reload} />
  return (
    <div className="stack">
      <form className="card stack" onSubmit={create}>
        <h2>Create an account</h2>
        <div className="row">
          <select className="input" style={{ maxWidth: 180 }} value={role} onChange={(e) => setRole(e.target.value)} aria-label="Role">
            <option value="staff">City staff</option><option value="crew">Crew</option><option value="tech">Tech team</option>
          </select>
          <input className="input" style={{ flex: '1 1 200px' }} required value={name} onChange={(e) => setName(e.target.value)} placeholder="Name" maxLength={60} aria-label="Name" />
          {role === 'crew' && (
            <select className="input" style={{ flex: '1 1 220px' }} required value={crew} onChange={(e) => setCrew(e.target.value)} aria-label="Crew">
              <option value="">Choose the crew</option>
              {(crews || []).map((c) => <option key={c.crew_id} value={c.crew_id}>{c.crew_id} · {c.agency} · {titleCase(c.borough)}</option>)}
            </select>
          )}
          <button className="btn primary" disabled={busy}>Create</button>
        </div>
        <p className="muted small">The ID and a 6 digit PIN are shown once. Only a hash of the PIN is kept.</p>
      </form>
      <div className="card stack">
        <h2>Accounts</h2>
        {!data ? <Skeleton lines={4} /> : (
          <div className="tablewrap">
            <table>
              <thead><tr><th>User ID</th><th>Role</th><th>Name</th><th>Last seen</th><th>Status</th><th /></tr></thead>
              <tbody>
                {data.accounts.map((u) => (
                  <tr key={u.id}>
                    <td className="num">{u.id}</td><td>{u.role}{u.crew_id ? ` · ${u.crew_id}` : ''}</td><td>{u.name}</td>
                    <td className="muted">{u.last_seen ? since(u.last_seen) : 'never'}</td>
                    <td><span className={`pill ${u.active ? 'LIVE' : 'MISSING'}`}>{u.active ? 'Active' : 'Off'}</span></td>
                    <td className="row">
                      <button className="btn ghost small" onClick={async () => { try { setIssued(await api.resetPin(u.id)) } catch (e) { toast(e.message, 'bad') } }}>New PIN</button>
                      <button className="btn ghost small" onClick={async () => { try { await api.setActive(u.id, !u.active); reload() } catch (e) { toast(e.message, 'bad') } }}>{u.active ? 'Switch off' : 'Switch on'}</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
        {data && <p className="muted small">{data.residents} resident IDs registered. Residents are pseudonymous and are not listed.</p>}
      </div>
      <Modal open={!!issued} onClose={() => setIssued(null)} title="New credentials">
        {issued && (
          <div className="stack">
            <p className="eyebrow">Shown once</p>
            <h2>Give these to {issued.name || issued.user_id}</h2>
            <div className="facts"><div><span>User ID</span><strong className="num">{issued.user_id}</strong></div><div><span>PIN</span><strong className="num">{issued.pin}</strong></div></div>
            <p className="muted">The PIN cannot be shown again. If it is lost, give the account a new PIN.</p>
            <button className="btn primary" onClick={() => setIssued(null)}>Done</button>
          </div>
        )}
      </Modal>
    </div>
  )
}

function System() {
  const { data, error, reload } = useAsync(() => api.system(), [])
  if (error) return <ErrorNote error={error} onRetry={reload} />
  if (!data) return <div className="card"><Skeleton lines={5} /></div>
  const cards = data.experiments || []
  const total = (k) => cards.reduce((n, c) => n + (c[k] || 0), 0)
  return (
    <div className="stack">
      <div className="card stack">
        <h2>How each component was chosen</h2>
        <p className="muted">Every component was built by comparing methods on test data it never trained on. These cards show what was compared, how many runs it took and what shipped. Full detail is in the group report, section 4.</p>
        <div className="kpis three">
          <div className="kpi"><strong>{cards.length}</strong><span>Components and parts tested</span></div>
          <div className="kpi"><strong>{total('methods_compared')}</strong><span>Methods compared</span></div>
          <div className="kpi"><strong>{total('runs').toLocaleString()}</strong><span>Experiment runs</span></div>
        </div>
      </div>
      <div className="expcards">
        {cards.map((c) => <ExperimentCard key={c.component} c={c} />)}
      </div>
    </div>
  )
}

function ExperimentCard({ c }) {
  const [open, setOpen] = useState(false)
  return (
    <article className="card expcard stack">
      <div className="row"><h3 style={{ flex: 1, margin: 0 }}>{c.component}</h3><span className="pill">{c.owner}</span></div>
      <div className="expstats">
        <div><strong>{c.methods_compared}</strong><span>methods compared</span></div>
        <div><strong>{c.runs.toLocaleString()}</strong><span>runs</span></div>
      </div>
      <p className="small"><strong>Shipped:</strong> {c.shipped}</p>
      <p className="small"><strong>Result:</strong> {c.headline}</p>
      <p className="small muted"><strong>Tested on:</strong> {c.test_set}</p>
      <button className="btn ghost small" onClick={() => setOpen(!open)} aria-expanded={open}>{open ? 'Hide the methods' : 'Show every method compared'}</button>
      {open && (
        <ul className="explist">
          {c.methods.map((m) => (
            <li key={m.method} className={m.shipped ? 'shipped' : ''}>
              <span><strong>{m.method}</strong>{m.shipped ? ' · shipped' : ''}</span>
              <span className="muted small">{m.runs} run{m.runs === 1 ? '' : 's'} · {m.result}</span>
            </li>
          ))}
        </ul>
      )}
    </article>
  )
}
