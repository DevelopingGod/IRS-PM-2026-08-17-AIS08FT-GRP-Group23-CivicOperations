import { Tilt, useReveal } from '../motion.jsx'
import { Icon } from '../ui.jsx'

// What the product does, for anyone: residents, city staff, crews, the tech team and visitors.
const PEOPLE = [
  { who: 'Residents', icon: 'chat', points: [
    'Report a problem by picking it from a few choices, or by describing it in your own words.',
    'Only what is missing is asked for. The address and the time are read from what you wrote.',
    'If someone may be in danger, you are told to call 911 and nothing is filed.',
    'Follow your report step by step like a subway line, and ask the bot where it is.',
    'If the problem is still there after it was closed, say so and it comes back with more attention.',
  ] },
  { who: 'City staff', icon: 'layers', points: [
    'One ranked queue for the whole city, with the reason for every position in plain words.',
    'A map of open requests that zooms into each borough with its real streets.',
    'Crews get their routes automatically. Staff can pull a job back or give it to another crew at any time.',
    'Escalations and nearby schools or hospitals only count once a person approves them.',
    'Ask questions about the queue in plain English, and see busy day alerts for each borough.',
  ] },
  { who: 'Crews', icon: 'route', points: [
    'Today\'s stops in the order that matters, on a street map that starts at the depot.',
    'The map shows where the crew is now and ticks each stop as it is closed.',
    'Closing a job takes one tap: fixed, cleared before arrival, nothing found, no access or wrong agency.',
    'What the crew finds teaches the system which reports are reliable.',
  ] },
  { who: 'Tech team', icon: 'gauge', points: [
    'Switch any component off. The rest keep working on a safe default, and nothing breaks.',
    'Measure what each component changes in the queue by switching it off, one at a time.',
    'See how each component was chosen: every method compared, every run and what shipped.',
    'Create staff and crew accounts, reset PINs and turn accounts off.',
  ] },
]

const PARTS = [
  ['Intake bot', 'Reads the problem, the address and the time from a message.'],
  ['Emergency check', 'Stops any report where someone may be in danger and points to 911.'],
  ['Agency and skill routing', 'Picks the city agency and the skill the job needs.'],
  ['Duplicate detection', 'Joins reports of the same problem, so one crew goes for all of them.'],
  ['External factors', 'Notices a school, hospital or other sensitive place nearby.'],
  ['Urgency', 'Sets how urgent a problem is from known hazards.'],
  ['Prioritisation', 'Ranks every open request together, with the city\'s own deadlines.'],
  ['Credibility and escalation', 'Learns from crew findings how reliable each reporter is.'],
  ['Fake request detection', 'Asks a person to check a doubtful report. It never rejects one.'],
  ['Crew deployment and routing', 'Plans each crew\'s day and its route on the real road network.'],
  ['Admin questions', 'Answers staff questions about the queue in plain English.'],
  ['Predictive alerting', 'Warns of busy days ahead from weather and past demand.'],
]

export default function Features() {
  useReveal(true)
  return (
    <div className="page stack">
      <header className="pagehead reveal">
        <p className="eyebrow">What it does</p>
        <h1>Every report heard. Every decision explained.</h1>
        <p className="lede">Civic Operations takes a city problem from the first message to the crew that closes it. Twelve reasoning components each do one job, each explains itself, and a person stays in charge of every decision that matters.</p>
      </header>
      <div className="featgrid">
        {PEOPLE.map((p, i) => (
          <Tilt as="section" key={p.who} className="card stack reveal" style={{ '--i': i }}>
            <div className="row"><span className="chapter-ico"><Icon name={p.icon} size={22} /></span><h2 style={{ margin: 0 }}>For {p.who.toLowerCase()}</h2></div>
            <ul className="featlist">{p.points.map((t) => <li key={t}>{t}</li>)}</ul>
          </Tilt>
        ))}
      </div>
      <section className="card stack reveal">
        <h2>The twelve components</h2>
        <p className="muted">Each sits behind its own switch. Off or failing, a safe default takes its place and the app keeps working.</p>
        <div className="partgrid">
          {PARTS.map(([name, text], i) => (
            <div key={name} className="part" style={{ '--i': i }}><strong>{name}</strong><span className="muted small">{text}</span></div>
          ))}
        </div>
      </section>
      <section className="card stack reveal">
        <h2>Safe by design</h2>
        <ul className="featlist">
          <li>No password for residents. An email is kept only as a scrambled fingerprint.</li>
          <li>Nothing is filed during an emergency, and no report is ever rejected by a machine.</li>
          <li>Every ranking, route and flag shows its reasons, so a person can check and change it.</li>
        </ul>
        <div className="row"><a className="btn primary" href="#/report">Report a problem <Icon name="arrow" size={18} /></a><a className="btn ghost" href="#/work">Staff, crew and tech sign in</a></div>
      </section>
    </div>
  )
}
