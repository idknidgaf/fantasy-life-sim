import type { PendingEvent } from '../types'

interface Props {
  event: PendingEvent
  busy: boolean
  onChoose: (eventId: string, choiceId: string) => void
}

export default function EventPanel({ event, busy, onChoose }: Props) {
  return (
    <section className="card event">
      <p className="eyebrow gold">Something happens</p>
      <h2>{event.title}</h2>
      <p className="lead">{event.text}</p>
      <div className="choices">
        {event.choices.map((c) => {
          const locked = c.available === false
          return (
            <button
              key={c.id}
              className={`choice${locked ? ' locked' : ''}`}
              disabled={busy || locked}
              onClick={() => onChoose(event.id, c.id)}
            >
              <span>{c.label}</span>
              {locked && c.requirement && <small className="requirement">🔒 {c.requirement}</small>}
            </button>
          )
        })}
      </div>
    </section>
  )
}
