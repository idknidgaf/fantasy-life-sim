import type { BattleLine, BattleSummary as Summary } from '../types'

interface Props {
  summary: Summary
  busy: boolean
  onContinue: () => void
}

const num = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 2 })
const pct = new Intl.NumberFormat(undefined, { style: 'percent', maximumFractionDigits: 0 })

interface Headline {
  icon: string
  label: string
  tone: 'win' | 'loss' | 'flee'
}

/** The headline names what happened. A failed flee is reported as such, followed by how the fight then ended. */
function headline(s: Summary): Headline {
  if (s.outcome === 'fled') return { icon: '🏃', label: 'Fled Successfully', tone: 'flee' }
  const result = s.outcome === 'victory' ? 'Victory' : 'Defeat'
  const icon = s.outcome === 'victory' ? '🏆' : s.died ? '☠' : '💥'
  const tone = s.outcome === 'victory' ? 'win' : 'loss'
  return s.flee ? { icon: '🏃', label: `Flee Failed — ${result}`, tone } : { icon, label: result, tone }
}

function Lines({ lines }: { lines: BattleLine[] }) {
  return (
    <ul className="battle-lines">
      {lines.map((l, i) => (
        <li key={`${l.kind}-${i}`} className={`kind-${l.kind}`}>
          {l.text}
        </li>
      ))}
    </ul>
  )
}

/** Renders the stored battle summary exactly as the backend produced it. It calculates nothing. */
export default function BattleSummary({ summary, busy, onContinue }: Props) {
  const h = headline(summary)
  const fought = summary.outcome !== 'fled'
  const hasConsequences = !!summary.damage || summary.died || summary.other_consequences.length > 0
  return (
    <section className={`card event battle ${h.tone}`} aria-label="Battle summary">
      <p className="eyebrow gold">{summary.event_title}</p>
      <h2 className="battle-outcome">
        <span aria-hidden="true">{h.icon} </span>
        {h.label}
      </h2>
      <p className="muted">Opponent: {summary.opponent_name}</p>
      <p className="power-compare">
        Your Power: {num(summary.power.player)} | Opponent Power: {num(summary.power.opponent)}
      </p>
      {fought && (
        <p className="muted small">Estimated win chance before the fight: {summary.estimated_win_percent}%</p>
      )}
      {summary.flee && (
        <p className="muted small">
          Escape attempt: {summary.flee.succeeded ? 'succeeded' : 'failed'} (estimated chance{' '}
          {pct.format(summary.flee.chance)})
        </p>
      )}
      <p className="lead">{summary.result_text}</p>

      {hasConsequences && (
        <div className="battle-section">
          <h3>Consequences</h3>
          {summary.died && (
            <p className="battle-death">
              ☠ {summary.damage?.instant_death ? 'Instant death' : 'You died'}
              {summary.cause_of_death ? ` — ${summary.cause_of_death}` : ''}
            </p>
          )}
          {summary.damage && (
            <p>
              ❤ Health lost: {num(summary.damage.health_lost)} (-{summary.damage.percent}% of maximum) ·
              Health: {num(summary.damage.health_before)} → {num(summary.damage.health_after)} /{' '}
              {num(summary.damage.max_health)}
            </p>
          )}
          {summary.other_consequences.length > 0 && <Lines lines={summary.other_consequences} />}
        </div>
      )}

      <div className="battle-section">
        <h3>Rewards</h3>
        {summary.rewards.length > 0 ? <Lines lines={summary.rewards} /> : <p className="muted">No rewards gained</p>}
      </div>

      <button className="primary battle-continue" disabled={busy} onClick={onContinue}>
        Continue
      </button>
    </section>
  )
}
