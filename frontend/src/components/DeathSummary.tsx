import { STAT_INFO } from '../format'
import type { Summary } from '../types'

interface Props {
  summary: Summary
  onNewLife: () => void
}

export default function DeathSummary({ summary, onNewLife }: Props) {
  return (
    <section className="card death">
      <p className="eyebrow">A life has ended</p>
      <h2>{summary.name}</h2>
      <p className="lead">
        Died at age {summary.age} of {summary.cause_of_death ?? 'unknown causes'}.
      </p>
      <p className="muted">
        {summary.defining_skill
          ? `They will be remembered for their ${summary.defining_skill}.`
          : 'They never found a calling.'}
      </p>

      <div className="summary-grid">
        <div>
          <h3>Final attributes</h3>
          <ul className="plain">
            {Object.entries(STAT_INFO).map(([key, info]) => (
              <li key={key}>
                <span>{info.label}</span>
                <strong>{summary.stats[key]}</strong>
              </li>
            ))}
          </ul>
        </div>
        <div>
          <h3>Greatest skills</h3>
          {summary.top_skills.length === 0 ? (
            <p className="muted">None.</p>
          ) : (
            <ul className="plain">
              {summary.top_skills.map((s) => (
                <li key={s.skill}>
                  <span>{s.skill}</span>
                  <strong>{s.level}</strong>
                </li>
              ))}
            </ul>
          )}
        </div>
      </div>

      <button className="primary" onClick={onNewLife}>
        Begin a new life
      </button>
    </section>
  )
}
