import type { HistoryEntry } from '../types'

export default function EventLog({ history }: { history: HistoryEntry[] }) {
  // Group entries by age, newest age first, keeping the order within each age.
  const groups = new Map<number, HistoryEntry[]>()
  for (const h of history) groups.set(h.age, [...(groups.get(h.age) ?? []), h])
  const ages = [...groups.keys()].sort((a, b) => b - a)

  return (
    <section className="card history-panel">
      <h3>History</h3>
      {ages.length === 0 ? (
        <p className="muted">Your story has not begun yet.</p>
      ) : (
        <ol className="log">
          {ages.map((age) => (
            <li key={age}>
              <span className="log-age">Age {age}</span>
              <div className="log-entries">
                {groups.get(age)!.map((h, i) => (
                  <p key={i} className={`kind-${h.kind}`}>
                    {h.text}
                  </p>
                ))}
              </div>
            </li>
          ))}
        </ol>
      )}
    </section>
  )
}
