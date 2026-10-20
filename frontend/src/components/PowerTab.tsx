import type { PowerBreakdown, PowerComponent, PowerLine } from '../types'

/** Renders the backend's Power breakdown as-is. No Power arithmetic happens in the frontend. */

function Lines({ lines }: { lines: PowerLine[] }) {
  return (
    <table className="power-table">
      <tbody>
        {lines.map((l) => (
          <tr key={`${l.source_type}:${l.source_id}`}>
            <td>
              {l.label}
              {l.sources.length > 0 && <small className="muted"> {l.sources.join(', ')}</small>}
            </td>
            <td className="muted small">{l.detail}</td>
            <td className="power-num">{l.value}</td>
          </tr>
        ))}
      </tbody>
    </table>
  )
}

function Component({ part }: { part: PowerComponent }) {
  return (
    <div className="power-part">
      <h4>
        {part.label} <span className="power-num">{part.total}</span>
      </h4>
      {part.lines.length > 0 && <Lines lines={part.lines} />}
      {part.note && <p className="muted small">{part.note}</p>}
    </div>
  )
}

export default function PowerTab({ power }: { power: PowerBreakdown | null }) {
  if (!power) {
    return (
      <section className="card power-tab">
        <h3>Power</h3>
        <p className="muted">Power could not be calculated right now.</p>
      </section>
    )
  }
  const m = power.modifiers
  return (
    <section className="card power-tab">
      <header className="life-head">
        <h3>Power</h3>
        <p className="muted small">How strong you are, and exactly why. Recalculated from your current state.</p>
        <div className="power-total">{power.total}</div>
      </header>

      <div className="power-formula">
        <div className="muted small">{power.formula.general}</div>
        <div>{power.formula.substituted}</div>
        <div className="power-result">{power.formula.result}</div>
      </div>

      <Component part={power.stats} />
      <Component part={power.equipment} />
      <Component part={power.skills} />

      <div className="power-part">
        <h4>
          Modifiers <span className="power-num">{m.percent_text}</span>
          <small className="muted"> × {m.multiplier}</small>
        </h4>
        {m.lines.length > 0 && (
          <table className="power-table">
            <tbody>
              {m.lines.map((l) => (
                <tr key={`${l.source_type}:${l.source_id}`}>
                  <td>{l.label}</td>
                  <td className="muted small">{l.source_type}</td>
                  <td className="power-num">{l.detail}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
        {m.note && <p className="muted small">{m.note}</p>}
        <p className="muted small">Modifiers add together: +10% and +20% make +30%.</p>
      </div>

      {power.notes.map((note, i) => (
        <p key={i} className="muted small">
          {note}
        </p>
      ))}
    </section>
  )
}
