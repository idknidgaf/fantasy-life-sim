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
        {event.choices.map((c) => (
          <button
            key={c.id}
            className="choice"
            disabled={busy}
            onClick={() => onChoose(event.id, c.id)}
          >
            {c.label}
          </button>
        ))}
      </div>
    </section>
  )
}
