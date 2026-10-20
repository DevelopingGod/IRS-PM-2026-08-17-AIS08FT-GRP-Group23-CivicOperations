import { useEffect, useMemo, useRef, useState } from 'react'
import { api, BOROUGH_LIST, titleCase } from '../api.js'
import { StopScreen, Tilt, toast, useReveal } from '../motion.jsx'
import { CityMap, FAMILIES, Icon } from '../ui.jsx'

const STEPS = ['What is it?', 'Where is it?', 'Anything a crew should know?', 'Check and send']

export default function Report({ goTrack }) {
  const [mode, setMode] = useState('form')
  const [stop, setStop] = useState(false)
  useReveal(mode)
  if (stop) return <StopScreen onBack={() => setStop(false)} />
  return (
    <div className="page stack">
      <header className="pagehead reveal">
        <p className="eyebrow">Report a problem</p>
        <h1>Tell the city once.</h1>
        <p className="lede">Every report is checked for danger first. Then it goes to the right team, and you can follow it to the end.</p>
      </header>
      <div className="segmented reveal" role="tablist" aria-label="How do you want to report">
        <button role="tab" aria-selected={mode === 'form'} onClick={() => setMode('form')}><Icon name="layers" size={18} /> Step by step</button>
        <button role="tab" aria-selected={mode === 'chat'} onClick={() => setMode('chat')}><Icon name="chat" size={18} /> Just tell us</button>
      </div>
      {mode === 'form' ? <Wizard onStop={() => setStop(true)} goTrack={goTrack} /> : <ChatReport onStop={() => setStop(true)} goTrack={goTrack} />}
    </div>
  )
}

function Wizard({ onStop, goTrack }) {
  const [step, setStep] = useState(0)
  const [catalog, setCatalog] = useState([])
  const [family, setFamily] = useState(null)
  const [type, setType] = useState(null)
  const [place, setPlace] = useState(null)
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const [ask, setAsk] = useState(false)
  const [error, setError] = useState('')
  const [past, setPast] = useState([])
  useEffect(() => {
    api.catalog().then(setCatalog).catch((e) => setError(e.message))
    api.myRequests().then((rs) => {
      const seen = new Set()
      setPast(rs.filter((r) => r.address && !seen.has(r.address + r.borough) && seen.add(r.address + r.borough)).slice(0, 4)
        .map((r) => ({ incident_address: r.address, borough: r.borough, latitude: r.latitude, longitude: r.longitude })))
    }).catch(() => {})
  }, [])
  const families = useMemo(() => {
    const by = {}
    catalog.forEach((c) => { (by[c.family] ||= []).push(c) })
    return Object.keys(FAMILIES).filter((f) => by[f]).map((f) => ({ key: f, ...FAMILIES[f], types: by[f] }))
  }, [catalog])

  async function send(confirm = null) {
    setBusy(true); setError('')
    try {
      const r = await api.report({ request_type: type?.code || null, description: text, place, confirm })
      if (r.state === 'EMERGENCY_STOP') onStop()
      else if (r.state === 'CONFIRM_DANGER') setAsk(true)
      else if (r.state === 'FILED') { toast(`Filed as ${r.request_id}`); goTrack(r.request_id) }
      else setError(r.question || 'Something is missing.')
    } catch (e) { setError(e.message) } finally { setBusy(false) }
  }

  const can = [type || (family === 'OTHER'), !!place, family !== 'OTHER' || text.trim().length > 4, true]
  return (
    <div className="wizard card">
      <ol className="wizard-steps" aria-label="Steps">
        {STEPS.map((s, i) => (
          <li key={s} className={i === step ? 'here' : i < step ? 'done' : ''}>
            <button type="button" disabled={i > step && !can.slice(0, i).every(Boolean)} onClick={() => setStep(i)}>
              <span className="stepno">{i < step ? <Icon name="check" size={16} /> : i + 1}</span><span className="steplabel">{s}</span>
            </button>
          </li>
        ))}
      </ol>
      <div className="wizard-body" key={step}>
        {step === 0 && (
          <div className="stack">
            <div className="tiles">
              {families.map((f, i) => (
                <Tilt as="button" type="button" key={f.key} className={`tile ${family === f.key ? 'on' : ''}`} aria-pressed={family === f.key}
                  style={{ '--i': i }} onClick={() => { setFamily(f.key); setType(null) }}>
                  <span className="tile-ico"><Icon name={f.icon} size={30} /></span><span>{f.name}</span>
                </Tilt>
              ))}
              <Tilt as="button" type="button" className={`tile ${family === 'OTHER' ? 'on' : ''}`} aria-pressed={family === 'OTHER'}
                style={{ '--i': families.length }} onClick={() => { setFamily('OTHER'); setType(null) }}>
                <span className="tile-ico"><Icon name="other" size={30} /></span><span>Something else</span>
              </Tilt>
            </div>
            {family && family !== 'OTHER' && (
              <div className="chips wrap pop" aria-label="Which one">
                {families.find((f) => f.key === family)?.types.map((t) => (
                  <button type="button" key={t.code} className={`chip ${type?.code === t.code ? 'on' : ''}`} aria-pressed={type?.code === t.code}
                    onClick={() => { setType(t); setTimeout(() => setStep(1), 220) }}>{t.label}</button>
                ))}
              </div>
            )}
            {family === 'OTHER' && <p className="muted">Fine. Describe it in step 3, and a person will choose the right team.</p>}
          </div>
        )}
        {step === 1 && <AddressPicker past={past} place={place} onPick={(p) => { setPlace(p); setTimeout(() => setStep(2), 350) }} onClear={() => setPlace(null)} />}
        {step === 2 && (
          <div className="stack">
            <label className="field"><span>{family === 'OTHER' ? 'Describe the problem' : 'Details (optional)'}</span>
              <textarea className="input area" rows={4} maxLength={2000} value={text} onChange={(e) => setText(e.target.value)} autoFocus
                placeholder={family === 'OTHER' ? 'What is wrong, and anything that helps a crew find it' : 'For example: about a foot wide, a cyclist nearly fell this morning'} />
            </label>
            <p className="muted small">The Emergency check reads this too. If someone may be in danger, you will be asked.</p>
          </div>
        )}
        {step === 3 && (
          <div className="review stack">
            <div className="row"><span className="plate">{type ? type.label : 'Something else'}</span>{type && <span className="pill">{type.agency}</span>}</div>
            <p><Icon name="pin" size={18} /> <strong>{titleCase(place?.incident_address)}</strong>, {titleCase(place?.borough)}{place?.cross_street ? ` near ${place.cross_street}` : ''}</p>
            {text && <blockquote className="said">{text}</blockquote>}
            {place?.latitude != null && <CityMap compact points={[{ id: 'p', lat: place.latitude, lon: place.longitude, tone: 'green', r: 10, pulse: true }]} label="Where it is" />}
          </div>
        )}
      </div>
      {ask && (
        <div className="card danger stack pop" role="group" aria-label="Safety question">
          <h3>Is anyone in danger right now?</h3>
          <div className="row">
            <button className="btn crit" disabled={busy} onClick={() => send('danger')}>Yes, someone is in danger</button>
            <button className="btn" disabled={busy} onClick={() => { setAsk(false); send('no_danger') }}>No, nobody is</button>
          </div>
        </div>
      )}
      {error && <p className="error" role="alert">{error}</p>}
      <div className="wizard-nav">
        <button className="btn ghost" disabled={step === 0} onClick={() => setStep(step - 1)}>Back</button>
        {step < 3
          ? <button className="btn primary" disabled={!can[step]} onClick={() => setStep(step + 1)}>Next <Icon name="arrow" size={18} /></button>
          : <button className="btn primary big send" disabled={busy || !can.every(Boolean)} onClick={() => send()}>
            {busy ? 'Checking it and finding the right team...' : <>Send to the city <Icon name="send" size={18} /></>}</button>}
      </div>
    </div>
  )
}

function AddressPicker({ past, place, onPick, onClear }) {
  const [q, setQ] = useState('')
  const [borough, setBorough] = useState('')
  const [hits, setHits] = useState([])
  const [cross, setCross] = useState('')
  const [note, setNote] = useState('')
  useEffect(() => {
    if (q.trim().length < 3) { setHits([]); return undefined }
    const t = setTimeout(() => api.addresses(q, borough || null).then(setHits).catch(() => setHits([])), 200)
    return () => clearTimeout(t)
  }, [q, borough])
  if (place) {
    return (
      <div className="placecard">
        <div className="placemap"><CityMap compact points={place.latitude != null ? [{ id: 'here', lat: place.latitude, lon: place.longitude, tone: 'green', r: 10, pulse: true }] : []} label="Where the problem is" /></div>
        <div className="stack" style={{ gap: 6 }}>
          <span className="plate">{titleCase(place.incident_address)}</span>
          <span className="muted">{titleCase(place.borough)}{place.zip ? ` ${place.zip}` : ''}{place.cross_street ? ` · near ${place.cross_street}` : ''}</span>
          <span className="muted small">{place.latitude != null ? 'Pinned on the map' : 'Kept as typed, without a map pin'}</span>
          <button type="button" className="btn ghost" onClick={onClear}>Change the place</button>
        </div>
      </div>
    )
  }
  const locate = () => {
    setNote('Finding you...')
    if (!navigator.geolocation) { setNote('This browser cannot share a location.'); return }
    navigator.geolocation.getCurrentPosition(
      (pos) => api.near(pos.coords.latitude, pos.coords.longitude).then((p) => { setNote(''); onPick({ ...p, cross_street: cross || null }) })
        .catch(() => setNote('No address we know is that close to you. Please type it.')),
      () => setNote('Location was not shared. Please type the address.'), { timeout: 8000 })
  }
  const typed = q.trim()
  return (
    <div className="stack">
      {past.length > 0 && (
        <div className="chips wrap" aria-label="Places you reported before">
          {past.map((p) => <button type="button" key={p.incident_address + p.borough} className="chip saved" onClick={() => onPick(p)}>
            <Icon name="star" size={16} /> {titleCase(p.incident_address)}, {titleCase(p.borough)}</button>)}
        </div>
      )}
      <div className="addr">
        <label className="field"><span>Street address</span>
          <input className="input" value={q} onChange={(e) => setQ(e.target.value)} autoComplete="street-address" autoFocus
            placeholder="Start typing, for example 37-61 79 Street" role="combobox" aria-expanded={hits.length > 0} aria-controls="addr-list" />
        </label>
        {(hits.length > 0 || typed.length >= 3) && (
          <ul id="addr-list" className="suggest pop" role="listbox">
            {hits.map((h, i) => (
              <li key={h.label} role="option" aria-selected="false" style={{ '--i': i }}>
                <button type="button" onClick={() => onPick({ ...h, cross_street: cross || null })}>
                  <Icon name="pin" size={18} /><span>{h.incident_address}<small>{titleCase(h.borough)} {h.zip}</small></span>
                </button>
              </li>
            ))}
            {typed.length >= 3 && (
              <li role="option" aria-selected="false">
                <button type="button" disabled={!borough} onClick={() => onPick({ incident_address: typed, borough, cross_street: cross || null })}>
                  <Icon name="other" size={18} /><span>Use &ldquo;{typed}&rdquo; as typed<small>{borough ? titleCase(borough) : 'Choose the borough below first'}, no map pin</small></span>
                </button>
              </li>
            )}
          </ul>
        )}
      </div>
      <div className="row">
        <select className="input" style={{ flex: '1 1 160px' }} value={borough} onChange={(e) => setBorough(e.target.value)} aria-label="Borough">
          <option value="">Any borough</option>
          {BOROUGH_LIST.map((b) => <option key={b} value={b}>{titleCase(b)}</option>)}
        </select>
        <input className="input" style={{ flex: '2 1 200px' }} value={cross} onChange={(e) => setCross(e.target.value)} placeholder="Cross street (optional)" aria-label="Cross street" maxLength={200} />
        <button type="button" className="btn" onClick={locate}><Icon name="locate" size={18} /> Use my location</button>
      </div>
      {note && <p className="muted" role="status">{note}</p>}
    </div>
  )
}

function ChatReport({ onStop, goTrack }) {
  const [messages, setMessages] = useState([{ from: 'bot', text: 'Tell me what the problem is and where it is. I will check it for danger, then file it with the right team.' }])
  const [text, setText] = useState('')
  const [session, setSessionId] = useState(null)
  const [pending, setPending] = useState(null)
  const [busy, setBusy] = useState(false)
  const end = useRef(null)
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [messages, pending, busy])
  async function send(message, confirm = null) {
    if (!message.trim() || busy) return
    setBusy(true)
    if (!confirm) setMessages((m) => [...m, { from: 'me', text: message }])
    try {
      const r = await api.chat({ text: message, session_id: session, confirm })
      if (r.session_id) setSessionId(r.session_id)
      if (r.state === 'EMERGENCY_STOP') onStop()
      else if (r.state === 'CONFIRM_DANGER') setPending(message)
      else if (r.state === 'FILED') { toast(`Filed as ${r.request_id}`); goTrack(r.request_id) }
      else { setPending(null); setMessages((m) => [...m, { from: 'bot', text: r.question }]) }
    } catch (e) { setMessages((m) => [...m, { from: 'bot', text: e.message, error: true }]) } finally { setBusy(false); setText('') }
  }
  return (
    <section className="card chatcard">
      <div className="chat" aria-live="polite">
        {messages.map((m, i) => <div key={i} className={`msg ${m.from} ${m.error ? 'err' : ''}`}>{m.text}</div>)}
        {busy && <div className="msg bot typing" aria-label="Reading your message"><i /><i /><i /></div>}
        {pending && (
          <div className="card danger stack pop" role="group" aria-label="Safety question">
            <h3>Is anyone in danger right now?</h3>
            <button className="btn crit big" disabled={busy} onClick={() => send(pending, 'danger')}>Yes, someone is in danger</button>
            <button className="btn big" disabled={busy} onClick={() => { const p = pending; setPending(null); send(p, 'no_danger') }}>No, nobody is in danger</button>
          </div>
        )}
        <div ref={end} />
      </div>
      {messages.length === 1 && (
        <div className="chips" aria-label="Examples">
          {['There is a deep pothole at 37-61 79 Street in Queens', 'The street is flooded and cars cannot get through', 'No heat in my apartment for three days'].map((e) =>
            <button key={e} className="chip" onClick={() => send(e)}>{e}</button>)}
        </div>
      )}
      <form className="composer" onSubmit={(e) => { e.preventDefault(); send(text) }}>
        <label htmlFor="msg" className="sr-only">Your message</label>
        <input id="msg" className="input" value={text} onChange={(e) => setText(e.target.value)} maxLength={2000}
          placeholder="Describe the problem and where it is" disabled={busy || !!pending} autoComplete="off" />
        <button className="btn primary" disabled={busy || !text.trim() || !!pending} aria-label="Send"><Icon name="send" size={18} /></button>
      </form>
    </section>
  )
}

