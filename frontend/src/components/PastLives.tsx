import type { PastLife } from '../types'

/** A compact record of the lives already lived in this family line. Not a family tree: just who, and how it ended. */
export default function PastLives({ lives }: { lives: PastLife[] }) {
  const past = lives.filter((l) => !l.current || l.successor_name)
  if (past.length === 0) return null
  return (
    <section className="card past-lives">
      <h3>Earlier lives</h3>
      <ul className="plain">
        {past.map((l) => (
          <li key={l.character_id}>
            <span>
              {l.name} <span className="muted small">{l.species_name}</span>
            </span>
            <span className="muted small">
              died at {l.age} of {l.cause_of_death ?? 'unknown causes'}
              {l.successor_name ? ` · carried on by ${l.successor_name}` : ''}
            </span>
          </li>
        ))}
      </ul>
    </section>
  )
}
