import { lazy, Suspense, useEffect, useState } from 'react'
import { api, onSessionChange, session, setSession } from './api.js'
import { toast, Toasts } from './motion.jsx'
import { Icon } from './ui.jsx'
import Landing from './pages/Landing.jsx'
import { Skeleton } from './motion.jsx'
// Each portal loads only when it is opened, so a resident never downloads the staff or tech code.
const Report = lazy(() => import('./pages/Report.jsx'))
const Track = lazy(() => import('./pages/Track.jsx'))
const Staff = lazy(() => import('./pages/Staff.jsx'))
const CrewPortal = lazy(() => import('./pages/CrewPortal.jsx'))
const Tech = lazy(() => import('./pages/Tech.jsx'))
const Features = lazy(() => import('./pages/Features.jsx'))
import { ResidentAuth, WorkSignIn } from './pages/Auth.jsx'

// Each role has its own home. A page outside your role is never shown, and the server refuses it too.
const HOME = { resident: '#/', staff: '#/staff/queue', crew: '#/crew', tech: '#/tech/components' }
const RESIDENT_PAGES = ['', 'report', 'track']
const TEAM = 'Khalid Alhazmi · Mohammad Alharthi · Nabeel Taresh · Sankalp Sandeep Indish'

function parse() {
  const [page = '', sub = ''] = window.location.hash.replace(/^#\/?/, '').split('/')
  return { page, sub }
}

export default function App() {
  const [route, setRoute] = useState(parse)
  const [user, setUser] = useState(session.get())
  const [auth, setAuth] = useState(null)        // where to go after a resident signs in

  useEffect(() => {
    const on = () => { setRoute(parse()); window.scrollTo({ top: 0 }) }
    window.addEventListener('hashchange', on)
    const off = onSessionChange(setUser)
    if (session.get()?.token) api.me().catch(() => setSession(null))
    return () => { window.removeEventListener('hashchange', on); off() }
  }, [])

  const go = (hash) => { if (window.location.hash === hash) setRoute(parse()); else window.location.hash = hash }
  const role = user?.role
  const { page, sub } = route

  // Keep every role inside its own portal.
  useEffect(() => {
    if (role && role !== 'resident' && RESIDENT_PAGES.includes(page)) go(HOME[role])
    const needs = { staff: 'staff', crew: 'crew', tech: 'tech' }[page]
    if (needs && role !== needs) {
      if (role) toast('That page is not part of your account.', 'bad')
      go(role ? HOME[role] : '#/work')
    }
    if ((page === 'report' || page === 'track') && role !== 'resident' && !auth) setAuth(`#/${page}${sub ? `/${sub}` : ''}`)
  }, [page, role])

  // Sign out at once on screen, leaving the page in the same step, and tell the server in the
  // background with the old token. Waiting for the server first let a late "go home" override
  // wherever the person went next, and briefly showed a protected page as signed out.
  const signOut = () => {
    const old = session.get()?.token
    window.location.hash = role === 'resident' ? '#/' : '#/work'
    setRoute(parse())
    setSession(null)
    api.logout(old).catch(() => {})
    toast('Signed out')
  }

  let body
  if (page === '' ) body = <Landing onStart={() => (role === 'resident' ? go('#/report') : setAuth('#/report'))} />
  else if (page === 'report') body = role === 'resident' ? <Report goTrack={(id) => go(`#/track/${id}`)} /> : <Gate onSignIn={() => setAuth('#/report')} />
  else if (page === 'track') body = role === 'resident' ? <Track initial={sub || null} onReport={() => go('#/report')} /> : <Gate onSignIn={() => setAuth(`#/track${sub ? `/${sub}` : ''}`)} />
  else if (page === 'features') body = <Features />
  else if (page === 'work') body = <WorkSignIn onDone={(r) => go(HOME[r.role])} />
  else if (page === 'staff' && role === 'staff') body = <Staff view={sub || 'queue'} go={(v) => go(`#/staff/${v}`)} />
  else if (page === 'crew' && role === 'crew') body = <CrewPortal />
  else if (page === 'tech' && role === 'tech') body = <Tech view={sub || 'components'} go={(v) => go(`#/tech/${v}`)} />
  else body = <NotFound />

  const residentNav = [['#/', 'Home', ''], ['#/report', 'Report', 'report'], ['#/track', 'Track', 'track'], ['#/features', 'Features', 'features']]
  const portalName = { staff: 'City staff', crew: 'Crew', tech: 'Tech team' }[role]

  return (
    <div className={`shell ${page === '' ? 'on-landing' : ''}`}>
      <a className="skip" href="#content">Skip to the content</a>
      <header className="top">
        <div className="top-inner">
          <a className="brand" href={role ? HOME[role] : '#/'} aria-label="Civic Operations, home">
            <span className="streetsign"><b>Civic Ops</b><small>New York City</small></span>
          </a>
          {(!role || role === 'resident') ? (
            <nav className="portals" aria-label="Main">
              {residentNav.map(([href, name, p]) => <a key={href} href={href} aria-current={page === p ? 'page' : undefined}>{name}</a>)}
            </nav>
          ) : <span className="portal-name"><Icon name="key" size={16} /> {portalName}{user?.name && user.name !== portalName ? ` · ${user.name}` : ''}</span>}
          <div className="account">
            {role ? (
              <>
                <span className="idchip" title="Your user ID"><Icon name="user" size={16} /> {user.user_id}</span>
                <button className="btn ghost light" onClick={signOut}><Icon name="logout" size={16} /> Sign out</button>
              </>
            ) : page !== 'work' && <button className="btn light" onClick={() => setAuth('#/report')}>Sign in</button>}
          </div>
        </div>
      </header>
      <main id="content" key={`${page}/${sub}/${role}`} className="portal-enter">
        <Suspense fallback={<div className="page"><div className="card"><Skeleton lines={6} /></div></div>}>{body}</Suspense>
      </main>
      <footer className="foot">
        <div>
          <strong>Civic Operations</strong>
          <span>NUS ISS Intelligent Reasoning Systems · Team MANTIS · 2026</span>
          <span>{TEAM}</span>
        </div>
        <div>
          <span>Every decision is explained. Nothing is filed during an emergency: call 911.</span>
          <a href="#/features">What it does</a>
          {!role && <a href="#/work">City staff, crew and tech team sign in</a>}
        </div>
      </footer>
      {/* Created fresh each time it opens, so it never shows the previous person's user ID. */}
      {!!auth && role !== 'resident' && <ResidentAuth open onClose={() => { setAuth(null); if (page === 'report' || page === 'track') go('#/') }}
        onDone={() => { const to = auth; setAuth(null); go(to || '#/report') }} />}
      <Toasts />
    </div>
  )
}

function Gate({ onSignIn }) {
  return (
    <div className="page"><div className="card empty stack">
      <h2>Sign in to continue</h2>
      <p className="muted">Reporting and tracking need your user ID, so your requests stay yours.</p>
      <button className="btn primary" onClick={onSignIn}>Sign in or get a user ID</button>
    </div></div>
  )
}

function NotFound() {
  return <div className="page"><div className="card empty stack"><h2>That page does not exist.</h2><a className="btn primary" href="#/">Go home</a></div></div>
}
