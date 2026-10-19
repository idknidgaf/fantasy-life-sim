import { vitalColor, vitalPercent } from '../format'
import type { Character } from '../types'

interface Props {
  character: Character
  busy: boolean
  onAdvance: () => void
}

function VitalBar({
  label,
  value,
  max,
  kind,
  suffix,
}: {
  label: string
  value: number
  max?: number
  kind: 'health' | 'stress' | 'happiness'
  suffix?: string
}) {
  const pct = vitalPercent(kind, value, max)
  const color = vitalColor(kind)
  const display = kind === 'health' ? `${value} / ${max}` : `${Math.round(value)}${suffix ?? ''}`
  return (
    <div className="vital">
      <div className="vital-head">
        <span className="vital-label">{label}</span>
        <span className="vital-value" style={{ color }}>
          {display}
        </span>
      </div>
      <div className="vital-bar">
        <span
          className="vital-fill"
          style={{ width: `${pct}%`, background: color }}
        />
      </div>
    </div>
  )
}

export default function VitalsPanel({ character, busy, onAdvance }: Props) {
  const dead = !character.alive
  const remaining = character.actions_remaining
  const budget = character.action_budget
  const taken = character.actions_taken_labels
  const canAge = !dead && !character.pending_event

  return (
    <aside className="vitals-panel">
      <h3 className="vitals-title">Vitals</h3>

      <VitalBar label="Health" value={character.health} max={character.max_health} kind="health" />

      <VitalBar label="Stress" value={character.stress} kind="stress" suffix="%" />

      <VitalBar label="Happiness" value={character.happiness} kind="happiness" suffix="%" />

      <div className="action-budget">
        <span className="budget-label">Actions</span>
        <span className="budget-value">
          {remaining} / {budget} left
        </span>
      </div>

      {taken.length > 0 && (
        <ul className="actions-taken">
          {taken.map((label, i) => (
            <li key={i}>{label}</li>
          ))}
        </ul>
      )}

      {canAge && (
        <button className="primary wide age-up" disabled={busy} onClick={onAdvance}>
          Age Up → {character.age + 1}
        </button>
      )}

      {dead && (
        <p className="muted small center" style={{ marginTop: '0.5rem' }}>
          This character has passed on.
        </p>
      )}
    </aside>
  )
}
