import { useEffect, useRef, useState } from 'react'
import { api, label, since, STATUS_WORDS, titleCase } from '../api.js'
import { ErrorNote, Skeleton, Tilt, toast, TypeOut, useReveal } from '../motion.jsx'
import { CityMap, Icon, Journey, levelTone, SubwayLine, Ticket } from '../ui.jsx'

function stationsFor(r) {
  if (r.status === 'HELD_FOR_PERSON') {
    return { at: 1, stations: [{ name: 'Received' }, { name: 'A person reviews it', note: 'It did not fit a standard type' }, { name: 'Routed' }, { name: 'Closed' }] }
  }
  const verdict = { VALID_RESOLVED: 'Fixed', SUBSIDED: 'Had cleared by arrival', INVALID_NOT_FOUND: 'Nothing found', NO_ACCESS: 'No access', WRONG_AGENCY: 'Passed on' }
  const at = { OPEN: 2, ASSIGNED: 3, CLOSED: 5 }[r.status] ?? 1
  return {
    at, stations: [
      { name: 'Received', note: since(r.created_at) },
      { name: 'Routed', note: r.agency || '' },
      { name: 'In the queue', note: r.urgency ? `${titleCase(r.urgency)} urgency` : '' },
      { name: 'Crew assigned', note: r.assigned_at ? since(r.assigned_at) : '' },
      { name: 'Closed', note: r.outcome ? verdict[r.outcome] || '' : '' },
    ],
  }
}

export default function Track({ initial, onReport }) {
  const [list, setList] = useState(null)
  const [error, setError] = useState(null)
  const [current, setCurrent] = useState(initial || null)
  const [detail, setDetail] = useState(null)
  const [messages, setMessages] = useState([{ from: 'bot', text: 'Ask me about any of your requests. For example: where is it, or it is still there.' }])
  const [quick, setQuick] = useState(['Where is my latest request?', 'Show all my requests'])
  const [text, setText] = useState('')
  const [busy, setBusy] = useState(false)
  const end = useRef(null)
  useReveal(list)

  const loadList = () => api.myRequests().then((l) => { setList(l); if (!current && l[0]) setCurrent(l[0].request_id) }).catch(setError)
  useEffect(() => { loadList() }, [])
  useEffect(() => { if (initial) setCurrent(initial) }, [initial])
  useEffect(() => {
    if (!current) { setDetail(null); return }
    setDetail(null)
    api.myRequest(current).then(setDetail).catch((e) => toast(e.message, 'bad'))
  }, [current])
  useEffect(() => { end.current?.scrollIntoView({ behavior: 'smooth', block: 'nearest' }) }, [messages, busy])

  async function send(t) {
    if (!t.trim() || busy) return
    setBusy(true)
    setMessages((m) => [...m, { from: 'me', text: t }])
    setText('')
    try {
      const r = await api.trackChat(t, current)
      setMessages((m) => [...m, { from: 'bot', text: r.reply, typing: true }])
      setQuick(r.quick_replies || [])
      if (r.request?.request_id) setCurrent(r.request.request_id)
      if (r.action?.type === 'FOLLOW_UP_FILED') { toast(`Filed again as ${r.action.request_id}`); loadList() }
      if (r.action?.type === 'NOTE_ADDED') { toast('Your message was added to the request'); if (current) api.myRequest(current).then(setDetail) }
    } catch (e) {
      setMessages((m) => [...m, { from: 'bot', text: e.message, err: true }])
    } finally { setBusy(false) }
  }

  return (
    <div className="page stack">
      <header className="pagehead reveal">
        <p className="eyebrow">Track</p>
        <h1>Where is my request?</h1>
        <p className="lede">Ask in your own words, or pick a request. If a problem is still there after it was closed, say so and it comes back with more attention.</p>
      </header>
      <ErrorNote error={error} onRetry={() => { setError(null); loadList() }} />
      <div className="track-grid">
        <section className="card chatcard track-chat reveal" aria-label="Ask about your requests">
          <div className="assistant-head"><span className="bot-avatar"><Icon name="sparkle" size={18} /></span><div><strong>Intake bot</strong><span className="muted small">Answers about your own requests only</span></div></div>
          <div className="chat" aria-live="polite">
            {messages.map((m, i) => (
              <div key={i} className={`msg ${m.from} ${m.err ? 'err' : ''}`}>
                {m.typing && i === messages.length - 1 ? <TypeOut text={m.text} /> : m.text}
              </div>
            ))}
            {busy && <div className="msg bot typing"><i /><i /><i /></div>}
            <div ref={end} />
          </div>
          <div className="chips" aria-label="Suggestions">{quick.map((x) => <button key={x} className="chip" onClick={() => send(x)}>{x}</button>)}</div>
          <form className="composer" onSubmit={(e) => { e.preventDefault(); send(text) }}>
            <label htmlFor="trackmsg" className="sr-only">Your question</label>
            <input id="trackmsg" className="input" value={text} onChange={(e) => setText(e.target.value)} maxLength={500} placeholder="For example: is CO-1A2B3C fixed yet?" autoComplete="off" />
            <button className="btn primary" disabled={busy || !text.trim()} aria-label="Send"><Icon name="send" size={18} /></button>
          </form>
        </section>

        <section className="stack" aria-label="Your requests">
          {!list && !error && <div className="card"><Skeleton lines={4} /></div>}
          {list && !list.length && (
            <div className="card empty stack">
              <p>You have not reported anything yet.</p>
              <button className="btn primary" onClick={onReport}>Report a problem</button>
            </div>
          )}
          {list && list.length > 0 && (
            <div className="reqlist">
              {list.map((r, i) => (
                <Tilt as="button" key={r.request_id} className={`card reqcard lane ${r.urgency} ${current === r.request_id ? 'on' : ''}`}
                  style={{ '--i': i }} onClick={() => setCurrent(r.request_id)} aria-pressed={current === r.request_id}>
                  <span className="row"><span className="plate">{label(r.label)}</span>
                    <span className={`pill ${r.status === 'CLOSED' ? 'LIVE' : r.status === 'HELD_FOR_PERSON' ? 'held' : ''}`}>{STATUS_WORDS[r.status]}</span></span>
                  <span className="muted">{titleCase(r.address)} · {r.request_id} · {since(r.created_at)}</span>
                  {r.follow_up_of && <span className="muted small">Reported again after {r.follow_up_of}</span>}
                </Tilt>
              ))}
            </div>
          )}
          {current && !detail && list?.length > 0 && <div className="card"><Skeleton lines={6} /></div>}
          {detail && <RequestDetail r={detail} />}
        </section>
      </div>
    </div>
  )
}

function RequestDetail({ r }) {
  const s = stationsFor(r)
  return (
    <div className="stack detail-in" key={r.request_id}>
      <Ticket reference={r.request_id} title={label(r.label)} sub={[titleCase(r.address), titleCase(r.borough)].filter(Boolean).join(', ')} />
      <div className="card stack">
        <h2>Where it is now</h2>
        <SubwayLine stations={s.stations} at={s.at} />
        {r.notes?.length > 0 && (
          <div className="notes">
            <h3>Your messages</h3>
            {r.notes.map((n, i) => <p key={i} className="note"><span className="muted small">{since(n.at)}</span> {n.text}</p>)}
          </div>
        )}
      </div>
      <div className="card stack">
        <h2>What happened to your report</h2>
        <Journey steps={r.journey || []} />
        {r.latitude != null && <CityMap compact points={[{ id: 'r', lat: r.latitude, lon: r.longitude, tone: levelTone(r.urgency), r: 11, pulse: true }]} label="Where the problem is" />}
      </div>
    </div>
  )
}
