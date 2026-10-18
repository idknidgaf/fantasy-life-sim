import type { HistoryEntry } from '../types'

export default function EventLog({ history }: { history: HistoryEntry[] }) {
  const entries = [...history].reverse()
  return (
    <section className="card">
      <h3>Life story</h3>
      {entries.length === 0 ? (
        <p className="muted">Your story has not begun yet.</p>
      ) : (
        <ol className="log">
          {entries.map((h) => (
            <li key={h.age}>
              <span className="log-age">Age {h.age}</span>
              <span>{h.events.length ? h.events.join('. ') + '.' : 'A quiet year.'}</span>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
