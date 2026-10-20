// Motion and interaction: things appear as you scroll, cards tilt towards the pointer, numbers
// count up, answers type themselves out. Every effect respects "reduce motion".
// Modals, drawers and toasts are drawn at the top of the page (a portal), so no animation or
// transform around a page can ever trap them, including the emergency STOP screen.
import { useCallback, useEffect, useRef, useState } from 'react'
import { createPortal } from 'react-dom'
import { Icon } from './ui.jsx'

export const reduced = () => window.matchMedia?.('(prefers-reduced-motion: reduce)').matches

/**
 * Reveals every .reveal element as it scrolls into view, once, wherever and whenever it appears.
 *
 * The "seen" mark is a data attribute, not a class. React rewrites an element's class whenever
 * its className changes (a card becoming selected, for example), which used to wipe a class
 * added here and make the card vanish. React never touches an attribute it did not render.
 * One watcher for the whole page, installed once, also catches elements added later.
 */
export function installReveal() {
  if (window.__revealInstalled) return
  window.__revealInstalled = true
  const show = (el) => el.setAttribute('data-in', '')
  if (!('IntersectionObserver' in window) || reduced()) {
    const all = () => document.querySelectorAll('.reveal:not([data-in])').forEach(show)
    all(); new MutationObserver(all).observe(document.body, { childList: true, subtree: true })
    return
  }
  const io = new IntersectionObserver((entries) => entries.forEach((e) => {
    if (e.isIntersecting) { show(e.target); io.unobserve(e.target) }
  }), { threshold: 0.08, rootMargin: '0px 0px -30px 0px' })
  const scan = () => document.querySelectorAll('.reveal:not([data-in]):not([data-watched])').forEach((el) => {
    el.setAttribute('data-watched', ''); io.observe(el)
  })
  scan()
  new MutationObserver(scan).observe(document.body, { childList: true, subtree: true })
}

/** Kept so pages can still call it; the page wide watcher does the work. */
export function useReveal() {}

/** A number that counts up to its value. */
export function CountUp({ value, ms = 900 }) {
  const [shown, setShown] = useState(0)
  const from = useRef(0)
  useEffect(() => {
    const target = Number(value) || 0
    if (reduced()) { setShown(target); from.current = target; return undefined }
    const start = performance.now(), a = from.current
    let raf
    const step = (t) => {
      const k = Math.min(1, (t - start) / ms), e = 1 - (1 - k) ** 3
      setShown(Math.round(a + (target - a) * e))
      if (k < 1) raf = requestAnimationFrame(step); else from.current = target
    }
    raf = requestAnimationFrame(step)
    return () => cancelAnimationFrame(raf)
  }, [value, ms])
  return <span className="num">{shown}</span>
}

/** A card that tilts towards the pointer and catches a light where the pointer is. */
export function Tilt({ children, className = '', as: Tag = 'div', ...rest }) {
  const ref = useRef(null)
  const move = (e) => {
    const el = ref.current
    if (!el || e.pointerType === 'touch' || reduced()) return
    const r = el.getBoundingClientRect()
    const x = (e.clientX - r.left) / r.width - 0.5
    const y = (e.clientY - r.top) / r.height - 0.5
    el.style.setProperty('--rx', `${(-y * 6).toFixed(2)}deg`)
    el.style.setProperty('--ry', `${(x * 8).toFixed(2)}deg`)
    el.style.setProperty('--mx', `${((x + 0.5) * 100).toFixed(1)}%`)
    el.style.setProperty('--my', `${((y + 0.5) * 100).toFixed(1)}%`)
  }
  const leave = () => { ref.current?.style.setProperty('--rx', '0deg'); ref.current?.style.setProperty('--ry', '0deg') }
  return <Tag ref={ref} className={`tilt ${className}`} onPointerMove={move} onPointerLeave={leave} {...rest}>{children}</Tag>
}

export function Skeleton({ lines = 3, height = 18 }) {
  return (
    <div className="skeleton" aria-busy="true" aria-label="Loading">
      {Array.from({ length: lines }, (_, i) => <span key={i} style={{ height, width: `${92 - i * 14}%` }} />)}
    </div>
  )
}

export function Switch({ on, onChange, disabled, label }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={disabled}
      className={`switch ${on ? 'on' : ''}`} onClick={() => onChange(!on)}>
      <span className="knob" />
    </button>
  )
}

function useEscape(open, onClose) {
  useEffect(() => {
    if (!open) return undefined
    const k = (e) => { if (e.key === 'Escape') onClose?.() }
    document.addEventListener('keydown', k)
    document.body.classList.add('locked')
    return () => { document.removeEventListener('keydown', k); document.body.classList.remove('locked') }
  }, [open, onClose])
}

export function Modal({ open, onClose, title, children, wide }) {
  useEscape(open, onClose)
  if (!open) return null
  return createPortal(
    <div className="overlay" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose?.() }}>
      <div className={`modal ${wide ? 'wide' : ''}`} role="dialog" aria-modal="true" aria-label={title}>
        {onClose && <button className="iconbtn close" onClick={onClose} aria-label="Close"><Icon name="close" size={20} /></button>}
        {children}
      </div>
    </div>, document.body)
}

export function Drawer({ open, onClose, children, label }) {
  useEscape(open, onClose)
  if (!open) return null
  return createPortal(
    <div className="overlay drawer-wrap" onMouseDown={(e) => { if (e.target === e.currentTarget) onClose?.() }}>
      <aside className="drawer" role="dialog" aria-modal="true" aria-label={label}>
        <button className="iconbtn close" onClick={onClose} aria-label="Close"><Icon name="close" size={20} /></button>
        {children}
      </aside>
    </div>, document.body)
}

/** The full screen STOP: drawn at the top of the page, above everything, always. */
export function StopScreen({ onBack }) {
  return createPortal(
    <div className="stop" role="alertdialog" aria-labelledby="stop-title">
      <div className="octagon">STOP</div>
      <h1 id="stop-title">This sounds like an emergency</h1>
      <a className="call" href="tel:911">Call 911 now</a>
      <p>Nothing has been filed and your message was not stored.</p>
      <button className="btn light" onClick={onBack}>I am safe, go back</button>
    </div>, document.body)
}

const toastListeners = new Set()
export function toast(text, tone = 'ok') { toastListeners.forEach((fn) => fn({ id: Math.random(), text, tone })) }

export function Toasts() {
  const [items, setItems] = useState([])
  useEffect(() => {
    const add = (t) => {
      setItems((x) => [...x, t])
      setTimeout(() => setItems((x) => x.filter((y) => y.id !== t.id)), 4200)
    }
    toastListeners.add(add)
    return () => { toastListeners.delete(add) }
  }, [])
  return createPortal(
    <div className="toasts" aria-live="polite">
      {items.map((t) => <div key={t.id} className={`toast ${t.tone}`}><Icon name={t.tone === 'bad' ? 'close' : 'check'} size={18} />{t.text}</div>)}
    </div>, document.body)
}

/** Text that types itself out, for the assistants' answers. */
export function TypeOut({ text, onDone }) {
  const [n, setN] = useState(0)
  const done = useRef(onDone)
  done.current = onDone
  useEffect(() => {
    if (reduced()) { setN(text.length); done.current?.(); return undefined }
    let i = 0
    setN(0)
    const t = setInterval(() => {
      i += Math.max(1, Math.round(text.length / 90))
      setN(i)
      if (i >= text.length) { clearInterval(t); done.current?.() }
    }, 14)
    return () => clearInterval(t)
  }, [text])
  return <span>{text.slice(0, n)}{n < text.length && <span className="caret" aria-hidden="true" />}</span>
}

/** Load something, with loading and error states, and a way to load it again. */
export function useAsync(fn, deps = []) {
  const [state, setState] = useState({ loading: true, data: null, error: null })
  const run = useCallback(() => {
    setState((s) => ({ ...s, loading: true, error: null }))
    return fn().then((data) => setState({ loading: false, data, error: null }))
      .catch((error) => setState({ loading: false, data: null, error }))
  }, deps) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => { run() }, [run])
  return { ...state, reload: run }
}

export function ErrorNote({ error, onRetry }) {
  if (!error) return null
  return (
    <div className="card errorcard" role="alert">
      <strong>That did not load.</strong> <span className="muted">{error.message}</span>
      {onRetry && <button className="btn ghost" onClick={onRetry}>Try again</button>}
    </div>
  )
}
