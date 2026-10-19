import { RARITY_INFO, STAT_INFO, TIER_ORDER, modifierText, pretty, type Tier } from '../format'
import type { Character } from '../types'
import AxisBar from './AxisBar'

interface Props {
  character: Character
  prevStats: Record<string, number> | null
  skillCategories: Record<string, string>
}

export default function CharacterSheet({ character, prevStats, skillCategories }: Props) {
  const skills = Object.entries(character.skills).sort((a, b) => b[1] - a[1])
  const personality = character.axes.filter((a) => a.group === 'personality')
  const alignment = character.axes.filter((a) => a.group === 'alignment')
  const mods = character.species_stat_modifiers

  return (
    <section className="card character-sheet">
      <header className="sheet-head">
        <h2>{character.name}</h2>
        <p className="muted">
          {character.species.name} · {character.alive ? `Age ${character.age}` : `Died at age ${character.age}`} ·{' '}
          {character.stage.name}
        </p>
        <p className="muted small">Home: {pretty(character.location)}</p>
      </header>

      {character.species_description && (
        <p className="species-desc muted small">{character.species_description}</p>
      )}

      {character.talent && (
        <div className="talent-banner" title={character.talent.description}>
          <strong>✦ Awakened: {character.talent.name}</strong>
          <span>{character.talent.description}</span>
        </div>
      )}

      <h3>Attributes</h3>
      <ul className="stats">
        {Object.entries(STAT_INFO).map(([key, info]) => {
          const value = character.effective_stats[key] ?? 0
          const bonus = value - (character.stats[key] ?? value)
          const delta = prevStats ? value - (prevStats[key] ?? value) : 0
          return (
            <li key={key} title={info.hint}>
              <span className="stat-name">{info.label}</span>
              <span className="bar">
                <span
                  className="bar-fill"
                  style={{ width: `${Math.min(100, (value / character.stat_cap) * 100)}%` }}
                />
              </span>
              <span className="stat-value">
                {value}
                {bonus !== 0 && <small className="muted" title="From traits"> ({bonus > 0 ? '+' : ''}{bonus})</small>}
                {delta !== 0 && (
                  <span className={delta > 0 ? 'delta up' : 'delta down'}>{delta > 0 ? `+${delta}` : delta}</span>
                )}
              </span>
            </li>
          )
        })}
      </ul>
      {character.stat_cap > 100 && <p className="muted small">Your kind can grow beyond the mortal limit (cap {character.stat_cap}).</p>}

      {Object.keys(mods).length > 0 && (
        <p className="muted small racial-mods">Racial modifiers: {modifierText(mods)}</p>
      )}

      <h3>Traits</h3>
      <div className="rarity-legend">
        {TIER_ORDER.map((tier) => (
          <span key={tier} className={`legend-item tier-${tier}`} style={{ color: RARITY_INFO[tier].color }}>
            <span className="legend-dot" style={{ background: RARITY_INFO[tier].color }} />
            <span className={tier === 'origin' ? 'quartz-text' : undefined}>{RARITY_INFO[tier].label}</span>
          </span>
        ))}
      </div>
      {character.traits.length === 0 ? (
        <p className="muted">No notable traits yet.</p>
      ) : (
        <ul className="trait-list">
          {character.traits.map((t) => (
            <li key={t.id} className={`trait-entry tier-${t.tier}`}>
              <div className="trait-head">
                <span className={`trait-name tier-${t.tier}`}>{t.name}</span>
                <span className={`trait-tier-badge tier-${t.tier}`} style={{ color: RARITY_INFO[t.tier as Tier]?.color }}>
                  <span className={t.tier === 'origin' ? 'quartz-text' : undefined}>{RARITY_INFO[t.tier as Tier]?.label}</span>
                </span>
              </div>
              {t.effects && <p className="trait-effects">{t.effects}</p>}
              <p className="trait-desc muted small">{t.description}</p>
            </li>
          ))}
        </ul>
      )}

      <h3>Personality</h3>
      <ul className="axes">{personality.map((a) => <AxisBar key={a.id} axis={a} />)}</ul>

      <h3>Alignment · {character.alignment}</h3>
      <ul className="axes">{alignment.map((a) => <AxisBar key={a.id} axis={a} />)}</ul>

      {character.items.length > 0 && (
        <>
          <h3>Belongings</h3>
          <ul className="chips">
            {character.items.map((i) => (
              <li key={i.id} className="item" title={i.description}>
                {i.name}
                {i.count > 1 ? ` ×${i.count}` : ''}
              </li>
            ))}
          </ul>
        </>
      )}

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
