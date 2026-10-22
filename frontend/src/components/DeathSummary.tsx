import { STAT_INFO } from '../format'
import type { Legacy, Summary } from '../types'

interface Props {
  summary: Summary
  legacy: Legacy | null
  busy: boolean
  onContinueAs: (npcId: string) => void
  onNewLife: () => void
}

export default function DeathSummary({ summary, legacy, busy, onContinueAs, onNewLife }: Props) {
  const heirs = legacy?.status === 'awaiting_heir' ? legacy.heirs : []
  return (
    <section className="card death">
      <p className="eyebrow">A life has ended</p>
      <h2>{summary.name}</h2>
      <p className="muted">
        {summary.species} · {summary.alignment}
      </p>
      <p className="lead">
        Died at age {summary.age} of {summary.cause_of_death ?? 'unknown causes'}.
      </p>
      <p className="muted">
        {summary.defining_skill
          ? `They will be remembered for their ${summary.defining_skill}.`
          : 'They never found a calling.'}{' '}
        They faced {summary.events_faced} defining {summary.events_faced === 1 ? 'moment' : 'moments'}.
      </p>

      {summary.awakened_talent && (
        <p className="talent-banner">
          <strong>✦ Awakened talent: {summary.awakened_talent}</strong>
        </p>
      )}
      {summary.unawakened_talent && (
        <p className="talent-banner dormant">
          <strong>✧ A hidden talent was never awakened: {summary.unawakened_talent}</strong>
          <span>It slept inside them all their life.</span>
        </p>
      )}

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
          {summary.traits.length > 0 && (
            <>
              <h3>Traits</h3>
              <ul className="chips">
                {summary.traits.map((t) => (
                  <li key={t} className="trait">
                    {t}
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      </div>

      {heirs.length > 0 && (
        <div className="legacy-choice">
          <h3>{heirs.length === 1 ? 'Your heir' : 'Who will carry on?'}</h3>
          <ul className="heirs">
            {heirs.map((h) => (
              <li key={h.npc_id} className="heir">
                <div>
                  <strong>{h.name}</strong>
                  <span className="muted small">
                    {' '}
                    {h.species_name} · age {h.age} · {h.relation} · Power {h.power}
                  </span>
                  {h.traits.length > 0 && (
                    <ul className="chips">
                      {h.traits.map((t) => (
                        <li key={t} className="trait">
                          {t}
                        </li>
                      ))}
                    </ul>
                  )}
                </div>
                <button className="primary" disabled={busy} onClick={() => onContinueAs(h.npc_id)}>
                  Continue as {h.name}
                </button>
              </li>
            ))}
          </ul>
        </div>
      )}
      {legacy?.status === 'game_over' && (
        <p className="lead">Your family line ends here: no descendants remain to carry on.</p>
      )}

      <button className={heirs.length > 0 ? 'link' : 'primary'} onClick={onNewLife}>
        {heirs.length > 0 ? 'Or begin a new, unrelated life' : 'Begin a new life'}
      </button>
    </section>
  )
}
