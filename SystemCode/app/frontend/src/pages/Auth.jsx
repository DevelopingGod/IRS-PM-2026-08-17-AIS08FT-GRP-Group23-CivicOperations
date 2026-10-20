import { useState } from 'react'
import { api, setSession } from '../api.js'
import { Modal, toast } from '../motion.jsx'
import { Icon } from '../ui.jsx'

/** Residents: register with an email only, or sign in with the user ID they saved. */
export function ResidentAuth({ open, onClose, onDone }) {
  const [mode, setMode] = useState('new')
  const [email, setEmail] = useState('')
  const [uid, setUid] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const [issued, setIssued] = useState(null)
  const [saved, setSaved] = useState(false)

  const finish = (r) => { setSession({ token: r.token, role: r.role, user_id: r.user_id }); onDone?.(r) }

  async function submit(e) {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      if (mode === 'new') setIssued(await api.register(email))
      else { const r = await api.login(uid); toast(`Welcome back, ${r.user_id}`); finish(r) }
    } catch (x) { setError(x.message) } finally { setBusy(false) }
  }

  const copy = async () => {
    try { await navigator.clipboard.writeText(issued.user_id); toast('User ID copied') } catch { toast('Select the ID and copy it by hand', 'bad') }
  }
  const download = () => {
    const blob = new Blob([`Civic Operations, New York City\nYour user ID: ${issued.user_id}\nKeep this file. You need the ID to sign in. We do not keep your email, so we cannot send it to you.\n`], { type: 'text/plain' })
    const a = Object.assign(document.createElement('a'), { href: URL.createObjectURL(blob), download: 'civic-operations-user-id.txt' })
    a.click(); URL.revokeObjectURL(a.href)
  }

  return (
    <Modal open={open} onClose={issued ? null : onClose} title="Sign in">
      {issued ? (
        <div className="stack idscreen">
          <p className="eyebrow">Your user ID</p>
          <h2>Save this ID. It is your key.</h2>
          <div className="ticket big" aria-label={`Your user ID is ${issued.user_id}`}>
            <div className="ticket-top"><span>CIVIC OPS</span><span>RESIDENT</span></div>
            <div className="ticket-ref num">{issued.user_id}</div>
            <div className="ticket-stripe" aria-hidden="true" />
          </div>
          <p className="muted">You will need it every time you sign in. We keep only a scrambled fingerprint of your email, never the email itself, so we cannot send the ID to you or look it up.</p>
          <div className="row">
            <button className="btn" onClick={copy}><Icon name="copy" size={18} /> Copy</button>
            <button className="btn" onClick={download}><Icon name="download" size={18} /> Download as a file</button>
          </div>
          <label className="check"><input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} /> I have saved my user ID</label>
          <button className="btn primary big" disabled={!saved} onClick={() => finish(issued)}>Continue <Icon name="arrow" size={20} /></button>
        </div>
      ) : (
        <form className="stack" onSubmit={submit}>
          <p className="eyebrow">Report a problem</p>
          <h2>{mode === 'new' ? 'Get your user ID' : 'Welcome back'}</h2>
          <div className="segmented wide" role="tablist">
            <button type="button" role="tab" aria-selected={mode === 'new'} onClick={() => { setMode('new'); setError('') }}>I am new</button>
            <button type="button" role="tab" aria-selected={mode === 'id'} onClick={() => { setMode('id'); setError('') }}>I have a user ID</button>
          </div>
          {mode === 'new' ? (
            <>
              <label className="field"><span>Email</span>
                <input className="input" type="email" required value={email} onChange={(e) => setEmail(e.target.value)} autoComplete="email" autoFocus maxLength={200} placeholder="you@example.com" />
              </label>
              <p className="muted small">No password. One email gets one user ID. We keep a scrambled fingerprint of the email only to stop it registering twice.</p>
            </>
          ) : (
            <label className="field"><span>User ID</span>
              <input className="input mono" required value={uid} onChange={(e) => setUid(e.target.value.toUpperCase())} autoFocus maxLength={20} placeholder="NYC-XXXX-XXXX" autoComplete="username" />
            </label>
          )}
          {error && <p className="error" role="alert">{error}</p>}
          <button className="btn primary big" disabled={busy}>{busy ? 'One moment...' : mode === 'new' ? 'Create my user ID' : 'Sign in'}</button>
        </form>
      )}
    </Modal>
  )
}

/** Staff, crew and tech: their user ID and PIN. Reached only from the footer link. */
export function WorkSignIn({ onDone }) {
  const [uid, setUid] = useState('')
  const [pin, setPin] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit(e) {
    e.preventDefault()
    setBusy(true); setError('')
    try {
      const r = await api.login(uid, pin)
      if (r.role === 'resident') { setError('This page is for city staff, crews and the tech team.'); return }
      setSession({ token: r.token, role: r.role, user_id: r.user_id, name: r.name, crew_id: r.crew_id })
      toast(`Signed in as ${r.name || r.user_id}`)
      onDone?.(r)
    } catch (x) { setError(x.message) } finally { setBusy(false); setPin('') }
  }
  return (
    <div className="worksign">
      <form className="card stack pop" onSubmit={submit}>
        <span className="chapter-ico"><Icon name="key" size={26} /></span>
        <p className="eyebrow">City staff, crews and tech team</p>
        <h1>Sign in to work</h1>
        <label className="field"><span>User ID</span>
          <input className="input mono" required value={uid} onChange={(e) => setUid(e.target.value.toUpperCase())} autoFocus maxLength={20} placeholder="STF-, CRW- or TEC-..." autoComplete="username" />
        </label>
        <label className="field"><span>PIN</span>
          <input className="input mono" required value={pin} onChange={(e) => setPin(e.target.value.replace(/\D/g, '').slice(0, 6))} inputMode="numeric" type="password" autoComplete="current-password" placeholder="6 digits" />
        </label>
        {error && <p className="error" role="alert">{error}</p>}
        <button className="btn primary big" disabled={busy || pin.length !== 6}>{busy ? 'Checking...' : 'Sign in'}</button>
        <p className="muted small">Accounts are created by the tech team. Five wrong PINs lock an ID for 15 minutes.</p>
      </form>
    </div>
  )
}
