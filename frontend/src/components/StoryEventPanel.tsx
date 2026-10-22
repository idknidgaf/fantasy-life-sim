import type { StoryEvent } from '../types'

interface Props {
  event: StoryEvent
  busy: boolean
  onChoose: (instanceId: number, choiceId: string) => void
}

/** Renders any story-engine event generically: text plus its choices. Same window as a legacy event (EventPanel), shown in place of the action area until resolved. */
export default function StoryEventPanel({ event, busy, onChoose }: Props) {
  return (
    <section className="card event">
      <p className="eyebrow gold">Something happens</p>
      {event.npc && (
        <p className="npc-speaker">
          <strong>{event.npc.name}</strong>
          {event.npc.title && <span className="muted"> · {event.npc.title}</span>}
          {event.npc.status && event.npc.status !== 'active' && (
            <span className="muted small"> ({event.npc.status})</span>
          )}
          {event.npc.description && <span className="muted small npc-desc">{event.npc.description}</span>}
        </p>
      )}
      <h2>{event.title}</h2>
      <p className="lead">{event.narrative}</p>
      {event.skippable && (
        <p className="muted small">You can Age Up to leave this behind; it will not wait forever.</p>
      )}
      <div className="choices">
        {event.choices.map((c) => (
          <button
            key={c.id}
            className={`choice${c.available ? '' : ' locked'}`}
            disabled={busy || !c.available}
            onClick={() => onChoose(event.instance_id, c.id)}
          >
            <span>{c.text}</span>
            {!c.available && c.locked_hint && <small className="requirement">🔒 {c.locked_hint}</small>}
          </button>
        ))}
      </div>
    </section>
  )
}
