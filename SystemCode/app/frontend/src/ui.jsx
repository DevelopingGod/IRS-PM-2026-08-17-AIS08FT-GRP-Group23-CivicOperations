// The visual language, in one place. New York's own street furniture carries meaning:
// street sign plates name the problem, subway bullets mark places in a line, the subway
// line shows progress, and a MetroCard holds the reference. Everything else stays calm.
import { useEffect, useMemo, useState } from 'react'
import { BOROUGHS, VIEW, project } from './nycOutline.js'

export const FAMILIES = {
  STREETS: { name: 'Streets and lights', icon: 'road' },
  WATER_SEWER: { name: 'Water and sewers', icon: 'drop' },
  SANITATION: { name: 'Trash and street cleaning', icon: 'trash' },
  PARKS_TREES: { name: 'Trees', icon: 'tree' },
  HOUSING: { name: 'Heat and housing', icon: 'home' },
  NOISE_PARKING: { name: 'Noise and parking', icon: 'car' },
  BUILDINGS: { name: 'Unsafe buildings', icon: 'building' },
  HEALTH: { name: 'Rats and pests', icon: 'bug' },
}

const PATHS = {
  road: <><path d="M8 3 4.5 21M16 3l3.5 18" /><path d="M12 4v2.5M12 10v2.5M12 16v2.5" /></>,
  drop: <path d="M12 3c3 4.3 6 7.6 6 11a6 6 0 0 1-12 0c0-3.4 3-6.7 6-11Z" />,
  trash: <><path d="M4.5 7h15M9.5 7V4.5h5V7M6.5 7l1 13h9l1-13" /><path d="M10 11v5.5M14 11v5.5" /></>,
  tree: <><path d="M12 21v-5" /><path d="M12 3 6 11h3.5L5.5 16h13l-4-5H18Z" /></>,
  home: <><path d="M3.5 11 12 4l8.5 7" /><path d="M6 9.5V20h12V9.5" /><path d="M10 20v-5h4v5" /></>,
  car: <><path d="M4 16.5V12l2.2-5h11.6L20 12v4.5Z" /><circle cx="8" cy="16.5" r="1.6" /><circle cx="16" cy="16.5" r="1.6" /><path d="M4 12h16" /></>,
  building: <><path d="M5 21V4h9v17M14 9h5v12M3 21h18" /><path d="M8 8h3M8 12h3M8 16h3" /></>,
  bug: <><ellipse cx="12" cy="14" rx="5" ry="6" /><path d="M12 8V5M7 13H3.5M20.5 13H17M8 9 5.5 6.5M16 9l2.5-2.5M8 18l-2.5 2.5M16 18l2.5 2.5" /></>,
  other: <><path d="M4 5h16v11H9l-5 4Z" /><path d="M10 9.5a2 2 0 1 1 2.5 1.9c-.4.2-.5.5-.5 1.1M12 14.2v.1" /></>,
  pin: <><path d="M12 21s-6.5-6.2-6.5-11A6.5 6.5 0 0 1 18.5 10c0 4.8-6.5 11-6.5 11Z" /><circle cx="12" cy="10" r="2.4" /></>,
  locate: <><circle cx="12" cy="12" r="4" /><path d="M12 2v3M12 19v3M2 12h3M19 12h3" /></>,
  check: <path d="m5 12.5 4.5 4.5L19 7.5" />,
  user: <><circle cx="12" cy="8.5" r="3.5" /><path d="M5 20c1-3.6 3.8-5.5 7-5.5s6 1.9 7 5.5" /></>,
  chat: <path d="M4 5h16v11H9l-5 4Z" />,
  arrow: <path d="M5 12h14M13 6l6 6-6 6" />,
  star: <path d="m12 3.5 2.6 5.4 5.9.8-4.3 4.1 1 5.8L12 16.9 6.8 19.6l1-5.8L3.5 9.7l5.9-.8Z" />,
  shield: <><path d="M12 3 5 6v5c0 4.5 3 8.3 7 10 4-1.7 7-5.5 7-10V6Z" /><path d="m9 12 2 2 4-4" /></>,
  route: <><circle cx="6" cy="18" r="2" /><circle cx="18" cy="6" r="2" /><path d="M8 18h6a3 3 0 0 0 0-6h-4a3 3 0 0 1 0-6h6" /></>,
  layers: <><path d="m12 3 9 5-9 5-9-5Z" /><path d="m3 13 9 5 9-5" /></>,
  copy: <><rect x="8" y="8" width="12" height="12" rx="2" /><path d="M16 8V5a1 1 0 0 0-1-1H5a1 1 0 0 0-1 1v10a1 1 0 0 0 1 1h3" /></>,
  download: <><path d="M12 4v11M7 10l5 5 5-5" /><path d="M5 20h14" /></>,
  close: <path d="M6 6l12 12M18 6 6 18" />,
  logout: <><path d="M15 4h4v16h-4" /><path d="M10 8l-4 4 4 4M6 12h10" /></>,
  bolt: <path d="M13 3 5 14h6l-1 7 8-11h-6Z" />,
  gauge: <><path d="M4 16a8 8 0 1 1 16 0" /><path d="m12 16 4-5" /></>,
  key: <><circle cx="8" cy="15" r="4" /><path d="m11 12 9-9M17 6l2 2" /></>,
  send: <path d="M4 12 20 4l-6 16-3-7Z" />,
  sparkle: <path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6" />,
}

export function Icon({ name, size = 24, title }) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.8"
      strokeLinecap="round" strokeLinejoin="round" role={title ? 'img' : undefined} aria-hidden={title ? undefined : true}
      aria-label={title}>
      {PATHS[name] || PATHS.other}
    </svg>
  )
}

/** A subway route bullet: a coloured disc with one short mark in it. */
export function Bullet({ children, tone = 'grey', size = 40, title }) {
  return <span className={`bullet ${tone}`} style={{ '--s': `${size}px` }} title={title}>{children}</span>
}

export const levelTone = (lvl) => ({ CRITICAL: 'red', HIGH: 'orange', MEDIUM: 'yellow', LOW: 'grey' }[lvl] || 'grey')
export const bandTone = (b) => ({ 'ACT NOW': 'red', NEXT: 'orange', PLANNED: 'blue', MONITOR: 'grey', HOLD: 'grey' }[b] || 'blue')

/** Progress as a subway line: stations passed are filled, the train sits at the current one. */
export function SubwayLine({ stations, at, tone = 'green' }) {
  return (
    <ol className={`subway ${tone}`} aria-label="Progress">
      {stations.map((s, i) => (
        <li key={s.name} className={i < at ? 'passed' : i === at ? 'here' : ''} style={{ '--i': i }}>
          <span className="st" aria-hidden="true" />
          <span className="name">{s.name}</span>
          {s.note && <span className="note">{s.note}</span>}
          {i === at && <span className="sr-only">(you are here)</span>}
        </li>
      ))}
    </ol>
  )
}

/** The MetroCard: the reference a reporter keeps. */
export function Ticket({ reference, title, sub }) {
  return (
    <div className="ticket" role="group" aria-label={`Reference ${reference}`}>
      <div className="ticket-top"><span>CIVIC OPS</span><span>NYC</span></div>
      <div className="ticket-ref num">{reference}</div>
      <div className="ticket-sub">{title}{sub ? <><br /><small>{sub}</small></> : null}</div>
      <div className="ticket-stripe" aria-hidden="true" />
    </div>
  )
}

/** The way the mockup told the story: each step lights up in turn, then the result. */
export function Journey({ steps, delay = 420 }) {
  const [shown, setShown] = useState(0)
  useEffect(() => {
    setShown(0)
    const reduce = window.matchMedia?.('(prefers-reduced-motion: reduce)').matches
    if (reduce) { setShown(steps.length); return undefined }
    const t = steps.map((_, i) => setTimeout(() => setShown(i + 1), 250 + i * delay))
    return () => t.forEach(clearTimeout)
  }, [steps, delay])
  return (
    <ol className="journey" aria-live="polite">
      {steps.map((s, i) => (
        <li key={s.step} className={`${i < shown ? 'on' : ''} ${s.outcome !== 'LIVE' ? 'fallback' : ''}`}>
          <span className="dot" aria-hidden="true"><Icon name="check" size={14} /></span>
          <span><strong>{s.step}</strong><span className="muted">{s.text}</span></span>
        </li>
      ))}
    </ol>
  )
}

/**
 * New York, drawn from where people really report problems. Points are placed by their
 * real latitude and longitude. A route is drawn as an animated line with the place names:
 * along the real roads when Crew deployment planned it, otherwise straight from stop to stop.
 */
export function CityMap({ points = [], route = [], roads = [], selected, onPick, label = 'Map of New York City', compact }) {
  const proj = useMemo(() => points
    .filter((p) => p.lat != null && p.lon != null)
    .map((p) => ({ ...p, xy: project(p.lat, p.lon) })), [points])
  const stops = useMemo(() => route.filter((p) => p.lat != null).map((p) => ({ ...p, xy: project(p.lat, p.lon) })), [route])
  const line = roads.length ? '' : stops.map((s) => s.xy.join(',')).join(' ')
  const drawn = useMemo(() => roads.map((r) => ({ ...r, d: r.points.map(([lon, lat]) => project(lat, lon).join(',')).join(' ') })), [roads])
  const k = compact ? 2.2 : 1.5           // the drawing is 1000 wide and shown much smaller
  return (
    <figure className={`citymap ${compact ? 'compact' : ''}`}>
      <svg viewBox={`0 0 ${VIEW.width} ${VIEW.height}`} role="img" aria-label={label}>
        <defs>
          <pattern id="grid" width="22" height="22" patternUnits="userSpaceOnUse">
            <path d="M22 0H0V22" fill="none" stroke="var(--grid)" strokeWidth="1" />
          </pattern>
        </defs>
        <rect width={VIEW.width} height={VIEW.height} fill="var(--water)" />
        {Object.entries(BOROUGHS).map(([name, b]) => (
          <g key={name}>
            <path d={b.d} className="land" />
            <path d={b.d} fill="url(#grid)" opacity=".7" />
            {!compact && <text x={b.label[0]} y={b.label[1]} className="boro">{name}</text>}
          </g>
        ))}
        {line && <polyline points={line} className="route" />}
        {drawn.map((r, i) => <polyline key={i} points={r.d} pathLength="1" className={`road ${r.kind || 'leg'}`} style={{ '--i': i }} />)}
        {proj.map((p, i) => (
          // The position is on the outer group; the inner one animates. A CSS transform on the
          // same element would replace the SVG translate and stack every dot in the corner.
          <g key={p.id ?? `${p.lat}${p.lon}`} transform={`translate(${p.xy[0]} ${p.xy[1]})`}>
            <g className={`pt ${p.tone || 'grey'} ${p.pulse ? 'pulse' : ''} ${selected === p.id ? 'sel' : ''}`}
              style={{ '--i': Math.min(i, 40) }}
              onClick={onPick && p.id ? () => onPick(p.id) : undefined}
              role={onPick && p.id ? 'button' : undefined} tabIndex={onPick && p.id ? 0 : undefined}
              aria-label={p.title} onKeyDown={(e) => { if (onPick && p.id && (e.key === 'Enter' || e.key === ' ')) onPick(p.id) }}>
              {p.pulse && <circle r={(p.r || 7) * k * 2} className="ring" />}
              <circle r={(p.r || 7) * k} className="dotc" />
            </g>
          </g>
        ))}
        {stops.map((s, i) => (
          <g key={s.id ?? i} transform={`translate(${s.xy[0]} ${s.xy[1]})`}>
            <g className="stopmark" style={{ '--i': i }}>
              <circle r="15" className="stopc" />
              <text className="stopn" dy="6">{i + 1}</text>
              <text className="stopl" x="22" dy="6">{s.name}</text>
            </g>
          </g>
        ))}
      </svg>
    </figure>
  )
}
