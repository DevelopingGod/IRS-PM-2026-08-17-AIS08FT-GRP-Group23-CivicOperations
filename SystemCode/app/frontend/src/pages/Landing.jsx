import { lazy, Suspense, useEffect, useRef, useState } from 'react'
import { api } from '../api.js'
// The 3D city (three.js) is the largest part of the site, so it loads on its own, after the words.
const City3D = lazy(() => import('../City3D.jsx'))
const CHAPTER_COUNT = 8
import { CountUp, Tilt, useReveal } from '../motion.jsx'
import { Icon } from '../ui.jsx'

// The story follows the proposal's process (slide 5): a resident reports, the system checks for
// danger, routes, merges duplicates, weighs urgency, ranks the queue, sends a crew, and keeps the
// resident informed. Only at the end does it ask whether the visitor sees a problem.
const STORY = [
  { eyebrow: 'New York City 311, with reasons', title: 'Every report heard. Every decision explained.',
    text: 'Civic Operations helps the city handle non emergency problems from the moment you report one until a crew closes it, and tells you why at every step.' },
  { eyebrow: '1 · Tell us once', title: 'Say it your way.',
    text: 'Pick the problem from a few clear choices, or just describe it. The Intake bot reads what it is and where it is, and asks only for what is missing.', icon: 'chat' },
  { eyebrow: '2 · Danger first', title: 'A 911 situation is never filed as 311.',
    text: 'The Emergency check reads every message before anything else happens. If someone may be in danger, it stops, tells you to call 911, and keeps nothing.', icon: 'shield' },
  { eyebrow: '3 · The right team, once', title: 'Routed to the right agency. Repeats joined, not repeated.',
    text: 'Agency and skill routing picks the team and the skill needed. Duplicate detection joins reports of the same problem, so one crew goes, and notices when a problem comes back.', icon: 'layers' },
  { eyebrow: '4 · What matters goes first', title: 'Urgent and sensitive problems rise.',
    text: 'External factors notices a school or hospital nearby. Urgency sets how urgent it is, Prioritisation ranks every open request together, and Credibility and Fake request detection weigh reports fairly, never rejecting one.', icon: 'bolt' },
  { eyebrow: '5 · On the road', title: 'Crews get their jobs in the order that matters.',
    text: 'Crews see their stops in priority order. When they close a job, what they found is recorded, and it teaches the system who reports reliably.', icon: 'route' },
  { eyebrow: '6 · Always informed', title: 'You see every step, and you can talk back.',
    text: 'Follow your report like a subway line. Ask the bot where it is. If the problem is still there after it was closed, say so, and it comes back with more attention.', icon: 'sparkle' },
]

export default function Landing({ onStart }) {
  const [chapter, setChapter] = useState(0)
  const [pulse, setPulse] = useState([])
  const [stats, setStats] = useState(null)
  const sections = useRef([])
  useReveal(stats)

  useEffect(() => {
    let off = false
    const load = () => Promise.all([api.pulse(), api.stats()])
      .then(([p, s]) => { if (!off) { setPulse(p); setStats(s) } })
      .catch(() => { if (!off) setTimeout(load, 4000) })
    load()
    return () => { off = true }
  }, [])

  useEffect(() => {
    const io = new IntersectionObserver((entries) => {
      entries.forEach((e) => { if (e.isIntersecting) setChapter(Number(e.target.dataset.chapter)) })
    }, { threshold: 0.55 })
    sections.current.forEach((s) => s && io.observe(s))
    return () => io.disconnect()
  }, [])

  return (
    <div className="landing">
      <div className="stage" aria-hidden="true">
        <Suspense fallback={null}><City3D chapter={chapter} pulse={pulse} /></Suspense>
        <div className="stage-fade" />
      </div>
      <nav className="chapters" aria-label="Story progress">
        {Array.from({ length: CHAPTER_COUNT }, (_, i) => (
          <button key={i} className={i === chapter ? 'on' : ''} aria-label={`Go to part ${i + 1}`}
            onClick={() => sections.current[i]?.scrollIntoView({ behavior: 'smooth', block: 'center' })} />
        ))}
      </nav>

      {STORY.map((s, i) => (
        <section key={s.title} className={`chapter ${i === 0 ? 'hero-chapter' : ''} ${i % 2 ? 'right' : 'left'}`}
          data-chapter={i} ref={(el) => { sections.current[i] = el }}>
          <Tilt className="glass reveal">
            {s.icon && <span className="chapter-ico"><Icon name={s.icon} size={26} /></span>}
            <p className="eyebrow">{s.eyebrow}</p>
            {i === 0 ? <h1 className="mega">{s.title}</h1> : <h2 className="big">{s.title}</h2>}
            <p className="lede">{s.text}</p>
            {i === 0 && (
              <div className="hero-stats">
                <div><strong><CountUp value={stats?.open ?? 0} /></strong><span>problems open right now</span></div>
                <div><strong><CountUp value={stats?.closed ?? 0} /></strong><span>closed by crews</span></div>
                <div><strong><CountUp value={12} /></strong><span>reasoning components, each explained</span></div>
              </div>
            )}
            {i === 0 && <p className="scrollhint"><span /> Scroll to see how it works</p>}
          </Tilt>
        </section>
      ))}

      <section className="chapter final" data-chapter={7} ref={(el) => { sections.current[7] = el }}>
        <div className="glass ask reveal">
          <p className="eyebrow">Your street</p>
          <h2 className="mega">Do you see a problem?</h2>
          <p className="lede">A pothole, a dark street light, a blocked drain, no heat at home. Tell the city once, and follow it to the end.</p>
          <div className="row center">
            <button className="btn primary huge" onClick={onStart}>Yes, report it <Icon name="arrow" size={22} /></button>
            <a className="btn ghost huge" href="#/track">I already reported one</a>
          </div>
          <p className="muted small">No password. You register with an email once and get a user ID. We keep only a scrambled fingerprint of the email.</p>
        </div>
      </section>
    </div>
  )
}
