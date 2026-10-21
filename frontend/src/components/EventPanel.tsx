import type { CombatWarning, Option, PendingEvent } from '../types'

interface Props {
  event: PendingEvent
  busy: boolean
  onChoose: (eventId: string, choiceId: string) => void
}

const WARNING_ICON: Record<CombatWarning, string> = { none: '', caution: '⚠', danger: '☠' }

const num = (n: number) => n.toLocaleString(undefined, { maximumFractionDigits: 2 })

/** One Fight or Flee button. Every number and the warning come from the backend's combat preview (red already takes precedence over yellow there). */
function CombatChoice({ option, busy, onPick }: { option: Option; busy: boolean; onPick: () => void }) {
  const preview = option.combat
  const locked = option.available === false
  const isFight = option.kind === 'fight'
  const warning: CombatWarning = isFight && preview ? preview.warning : 'none'
  return (
    <button
      className={`choice ${isFight ? 'fight' : 'flee'}${warning !== 'none' ? ` warn-${warning}` : ''}${locked ? ' locked' : ''}`}
      disabled={busy || locked}
      onClick={onPick}
    >
      <span>
        {isFight ? '⚔ ' : '🏃 '}
        {option.label}
      </span>
      {preview && isFight && (
        <>
          <small className="combat-odds">Estimated win chance: {preview.win_percent}%</small>
          <small className="combat-odds">{preview.defeat.label}</small>
          {warning !== 'none' && preview.warning_label && (
            <small className={`combat-warning ${warning}`} role="note">
              {WARNING_ICON[warning]} {preview.warning_label}
            </small>
          )}
        </>
      )}
      {preview && !isFight && preview.flee_percent !== null && (
        <small className="combat-odds">Estimated escape chance: {preview.flee_percent}%</small>
      )}
      {locked && option.requirement && <small className="requirement">🔒 {option.requirement}</small>}
    </button>
  )
}

export default function EventPanel({ event, busy, onChoose }: Props) {
  const fightPreview = event.choices.find((c) => c.kind === 'fight' && c.combat)?.combat
  return (
    <section className="card event">
      <p className="eyebrow gold">Something happens</p>
      <h2>{event.title}</h2>
      <p className="lead">{event.text}</p>
      {fightPreview && (
        <p className="power-compare">
          <strong>{fightPreview.opponent_name}</strong> · Your Power: {num(fightPreview.player_power)} | Opponent
          Power: {num(fightPreview.opponent_power)}
        </p>
      )}
      <div className="choices">
        {event.choices.map((c) => {
          if (c.kind === 'fight' || c.kind === 'flee') {
            return <CombatChoice key={c.id} option={c} busy={busy} onPick={() => onChoose(event.id, c.id)} />
          }
          const locked = c.available === false
          return (
            <button
              key={c.id}
              className={`choice${locked ? ' locked' : ''}`}
              disabled={busy || locked}
              onClick={() => onChoose(event.id, c.id)}
            >
              <span>{c.label}</span>
              {locked && c.requirement && <small className="requirement">🔒 {c.requirement}</small>}
            </button>
          )
        })}
      </div>
    </section>
  )
}
