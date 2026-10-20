// New York in 3D, for the landing page. Everything in it comes from real data: the land is the
// outline of where people really report problems, each building stands on a real 311 report
// location, and the glowing beacons are the problems open right now. The camera moves through
// the story as the page scrolls.
//
// Mobile first: lower resolution and fewer buildings on small screens, rendering pauses when the
// scene is off screen or the tab is hidden, "reduce motion" keeps the camera still, and without
// WebGL the flat map is shown instead.
import { useEffect, useRef, useState } from 'react'
import * as THREE from 'three'
import { BOROUGHS, BOROUGH_ORDER, SAMPLE, VIEW, project } from './nycOutline.js'
import { CityMap } from './ui.jsx'
import { reduced } from './motion.jsx'

const S = 10                                  // drawing units per world unit
const toWorld = (x, y) => [(x - VIEW.width / 2) / S, (y - VIEW.height / 2) / S]
const TONE = { CRITICAL: 0xff3b3b, HIGH: 0xff7a1a, MEDIUM: 0xffcc0a, LOW: 0x9aa3a0 }

// Camera stops, one per story chapter: [camera x, y, z, look at x, z]
export const CHAPTERS = [
  [0, 68, 62, 0, 4],          // the whole city
  [8, 30, 34, 2, 12],         // a report arrives (Brooklyn)
  [-6, 22, 20, -8, 2],        // danger first (lower Manhattan)
  [22, 30, 26, 14, -2],       // the right team, no double work (Queens)
  [-14, 26, 6, -6, -14],      // what matters goes first (upper Manhattan and the Bronx)
  [6, 40, 40, 0, 6],          // the crew's route
  [0, 52, 48, 0, 0],          // you see every step
  [0, 74, 30, 0, 0],          // your turn
]

function shapesFromPath(d) {
  const shapes = []
  for (const part of d.split('Z')) {
    const pts = part.replace('M', '').split('L').map((p) => p.trim().split(/\s+/).map(Number)).filter((p) => p.length === 2 && !Number.isNaN(p[0]))
    if (pts.length < 3) continue
    const shape = new THREE.Shape()
    pts.forEach(([x, y], i) => {
      const [wx, wz] = toWorld(x, y)
      // a Shape is drawn in x/y and laid flat later, so z becomes -y
      if (i === 0) shape.moveTo(wx, -wz); else shape.lineTo(wx, -wz)
    })
    shapes.push(shape)
  }
  return shapes
}

function seeded(i) { const x = Math.sin(i * 12.9898) * 43758.5453; return x - Math.floor(x) }

export default function City3D({ chapter = 0, pulse = [], className = '' }) {
  const host = useRef(null)
  const state = useRef({ chapter: 0 })
  const [failed, setFailed] = useState(false)
  state.current.chapter = chapter

  useEffect(() => {
    const el = host.current
    if (!el) return undefined
    let renderer
    try {
      renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true, powerPreference: 'high-performance' })
    } catch {
      setFailed(true)
      return undefined
    }
    const small = window.innerWidth < 760
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, small ? 1.25 : 1.75))
    renderer.setSize(el.clientWidth, el.clientHeight)
    renderer.outputColorSpace = THREE.SRGBColorSpace
    el.appendChild(renderer.domElement)

    const dark = window.matchMedia?.('(prefers-color-scheme: dark)').matches
    const scene = new THREE.Scene()
    scene.fog = new THREE.Fog(dark ? 0x0b1512 : 0xdfe8e4, 70, 170)
    const camera = new THREE.PerspectiveCamera(42, el.clientWidth / el.clientHeight, 0.5, 400)
    camera.position.set(...CHAPTERS[0].slice(0, 3))

    scene.add(new THREE.HemisphereLight(dark ? 0x7fb3a0 : 0xffffff, dark ? 0x0a1410 : 0x8aa39a, dark ? 0.9 : 1.15))
    const sun = new THREE.DirectionalLight(0xfff2dd, dark ? 0.8 : 1.4)
    sun.position.set(-40, 80, 30)
    scene.add(sun)

    // water
    const water = new THREE.Mesh(new THREE.PlaneGeometry(400, 400),
      new THREE.MeshStandardMaterial({ color: dark ? 0x0d2430 : 0xb9d6df, roughness: 0.35, metalness: 0.1 }))
    water.rotation.x = -Math.PI / 2
    water.position.y = -0.4
    scene.add(water)

    // land
    const landMat = new THREE.MeshStandardMaterial({ color: dark ? 0x1d2a25 : 0xeef1ea, roughness: 0.95 })
    const edgeMat = new THREE.LineBasicMaterial({ color: dark ? 0x3d5a4e : 0x9fb3a8 })
    Object.values(BOROUGHS).forEach((b) => {
      const geo = new THREE.ExtrudeGeometry(shapesFromPath(b.d), { depth: 0.6, bevelEnabled: false })
      geo.rotateX(-Math.PI / 2)
      scene.add(new THREE.Mesh(geo, landMat))
      const edges = new THREE.LineSegments(new THREE.EdgesGeometry(geo, 30), edgeMat)
      scene.add(edges)
    })

    // buildings on real report locations; taller in Manhattan
    const count = small ? 650 : SAMPLE.length
    const box = new THREE.BoxGeometry(0.62, 1, 0.62)
    box.translate(0, 0.5, 0)
    const bMat = new THREE.MeshStandardMaterial({ roughness: 0.6, metalness: 0.05 })
    const buildings = new THREE.InstancedMesh(box, bMat, count)
    const m = new THREE.Matrix4()
    const col = new THREE.Color()
    const palette = dark ? [0x2c4a3f, 0x35584b, 0x3f6656, 0x24403a] : [0xd7ddd5, 0xc6d0c8, 0xb8c5bd, 0xe4e7df]
    for (let i = 0; i < count; i++) {
      const [x, y, bi] = SAMPLE[i]
      const [wx, wz] = toWorld(x, y)
      const manhattan = BOROUGH_ORDER[bi] === 'MANHATTAN'
      const h = 0.5 + seeded(i) * (manhattan ? 7.5 : 2.2) + (manhattan && seeded(i + 7) > 0.85 ? 6 : 0)
      m.compose(new THREE.Vector3(wx + (seeded(i + 3) - 0.5) * 0.5, 0.6, wz + (seeded(i + 5) - 0.5) * 0.5),
        new THREE.Quaternion().setFromAxisAngle(new THREE.Vector3(0, 1, 0), seeded(i + 9) * 0.6),
        new THREE.Vector3(1, h, 1))
      buildings.setMatrixAt(i, m)
      buildings.setColorAt(i, col.setHex(palette[i % palette.length]))
    }
    scene.add(buildings)

    // beacons: problems open right now
    const beacons = new THREE.Group()
    const beamGeo = new THREE.CylinderGeometry(0.16, 0.16, 9, 8, 1, true)
    beamGeo.translate(0, 4.5, 0)
    const orbGeo = new THREE.SphereGeometry(0.42, 16, 12)
    const pts = pulse.slice(0, small ? 60 : 160)
    pts.forEach((p, i) => {
      const [x, y] = project(p.lat, p.lon)
      const [wx, wz] = toWorld(x, y)
      const c = TONE[p.urgency] || TONE.LOW
      const g = new THREE.Group()
      g.position.set(wx, 0.6, wz)
      const beam = new THREE.Mesh(beamGeo, new THREE.MeshBasicMaterial({ color: c, transparent: true, opacity: 0.32, depthWrite: false, blending: THREE.AdditiveBlending }))
      const orb = new THREE.Mesh(orbGeo, new THREE.MeshBasicMaterial({ color: c }))
      orb.position.y = 9
      g.add(beam, orb)
      g.userData = { phase: seeded(i + 11) * Math.PI * 2, urgent: p.urgency === 'HIGH' || p.urgency === 'CRITICAL' }
      beacons.add(g)
    })
    scene.add(beacons)

    // the crew's route between a few urgent problems, drawn in chapter 5
    const stops = beacons.children.filter((g) => g.userData.urgent).slice(0, 5)
    let route = null
    if (stops.length >= 2) {
      const curve = new THREE.CatmullRomCurve3(stops.map((g) => new THREE.Vector3(g.position.x, 1.4, g.position.z)))
      const tube = new THREE.TubeGeometry(curve, 160, 0.28, 8, false)
      route = new THREE.Mesh(tube, new THREE.MeshBasicMaterial({ color: 0x2f7dff }))
      route.geometry.setDrawRange(0, 0)
      scene.add(route)
    }

    const lookAt = new THREE.Vector3(...[CHAPTERS[0][3], 0, CHAPTERS[0][4]])
    const want = new THREE.Vector3()
    const wantLook = new THREE.Vector3()
    let visible = true
    let raf = 0
    let last = performance.now()
    let routeK = 0
    const still = reduced()
    const io = new IntersectionObserver(([e]) => { visible = e.isIntersecting })
    io.observe(el)

    const frame = (t) => {
      raf = requestAnimationFrame(frame)
      if (!visible || document.hidden) return
      const dt = Math.min(0.05, (t - last) / 1000)
      last = t
      const c = CHAPTERS[Math.max(0, Math.min(CHAPTERS.length - 1, state.current.chapter))]
      want.set(c[0], c[1], c[2])
      wantLook.set(c[3], 0, c[4])
      if (still) { camera.position.copy(want); lookAt.copy(wantLook) } else {
        const k = 1 - Math.exp(-dt * 1.6)
        camera.position.lerp(want, k)
        lookAt.lerp(wantLook, k)
        // a slow drift, so the city feels alive
        camera.position.x += Math.sin(t / 5200) * 0.015
      }
      camera.lookAt(lookAt)
      beacons.children.forEach((g, i) => {
        const s = 1 + Math.sin(t / 420 + g.userData.phase) * (g.userData.urgent ? 0.18 : 0.08)
        g.children[1].scale.setScalar(s)
        g.children[0].material.opacity = 0.22 + (Math.sin(t / 600 + i) + 1) * 0.08
      })
      if (route) {
        const target = state.current.chapter >= 5 ? 1 : 0
        routeK += (target - routeK) * (still ? 1 : 1 - Math.exp(-dt * 1.2))
        route.geometry.setDrawRange(0, Math.floor(route.geometry.index.count * routeK / 3) * 3)
      }
      renderer.render(scene, camera)
    }
    raf = requestAnimationFrame(frame)

    const resize = () => {
      camera.aspect = el.clientWidth / el.clientHeight
      camera.updateProjectionMatrix()
      renderer.setSize(el.clientWidth, el.clientHeight)
    }
    window.addEventListener('resize', resize)
    return () => {
      cancelAnimationFrame(raf)
      io.disconnect()
      window.removeEventListener('resize', resize)
      scene.traverse((o) => { o.geometry?.dispose?.(); (Array.isArray(o.material) ? o.material : [o.material]).forEach((mm) => mm?.dispose?.()) })
      renderer.dispose()
      el.removeChild(renderer.domElement)
    }
  }, [pulse])

  if (failed) {
    return <div className={className}><CityMap points={pulse.map((p, i) => ({ id: i, lat: p.lat, lon: p.lon, tone: 'orange' }))} label="New York City" /></div>
  }
  return <div ref={host} className={`city3d ${className}`} aria-hidden="true" />
}
