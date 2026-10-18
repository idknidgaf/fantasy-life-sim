import { STAT_INFO, lifeStage, pretty } from '../format'
import type { Character } from '../types'

interface Props {
  character: Character
  prevStats: Record<string, number> | null
  skillCategories: Record<string, string>
}

export default function CharacterSheet({ character, prevStats, skillCategories }: Props) {
  const skills = Object.entries(character.skills).sort((a, b) => b[1] - a[1])

  return (
    <section className="card">
      <header className="sheet-head">
        <h2>{character.name}</h2>
        <p className="muted">
          {character.alive
            ? `Age ${character.age} · ${lifeStage(character.age)}`
            : `Died at age ${character.age}`}
        </p>
      </header>

      <h3>Attributes</h3>
      <ul className="stats">
        {Object.entries(STAT_INFO).map(([key, info]) => {
          const value = character.stats[key] ?? 0
          const delta = prevStats ? value - (prevStats[key] ?? value) : 0
          return (
            <li key={key} title={info.hint}>
              <span className="stat-name">{info.label}</span>
              <span className="bar">
                <span className="bar-fill" style={{ width: `${Math.min(100, value)}%` }} />
              </span>
              <span className="stat-value">
                {value}
                {delta !== 0 && (
                  <span className={delta > 0 ? 'delta up' : 'delta down'}>
                    {delta > 0 ? `+${delta}` : delta}
                  </span>
                )}
              </span>
            </li>
          )
        })}
      </ul>

      <h3>Skills</h3>
      {skills.length === 0 ? (
        <p className="muted">No training yet. Pick a training action to start a skill.</p>
      ) : (
        <ul className="stats">
          {skills.map(([id, level]) => (
            <li key={id}>
              <span className="stat-name">
                {pretty(id)}
                {skillCategories[id] && <small className="chip">{pretty(skillCategories[id])}</small>}
              </span>
              <span className="bar">
                <span className="bar-fill skill" style={{ width: `${Math.min(100, level)}%` }} />
              </span>
              <span className="stat-value">{level.toFixed(1)}</span>
            </li>
          ))}
        </ul>
      )}
    </section>
  )
}
