import type { Character, Option } from '../types'

interface Props {
  character: Character
  actions: Option[]
  busy: boolean
  onAction: (actionId: string) => void
  onAdvance: () => void
}

export default function ActionPanel({ character, actions, busy, onAction, onAdvance }: Props) {
  return (
    <section className="card">
      <h3>Age {character.age}</h3>

      {character.action_taken_label ? (
        <p className="chosen">
          This year you chose: <strong>{character.action_taken_label}</strong>
        </p>
      ) : (
        <>
          <p className="muted">Choose how to spend your year, or let it pass.</p>
          <div className="choices">
            {actions.map((a) => (
              <button key={a.id} className="choice" disabled={busy} onClick={() => onAction(a.id)}>
                {a.label}
              </button>
            ))}
          </div>
        </>
      )}

      <button className="primary wide" disabled={busy} onClick={onAdvance}>
        {character.action_taken_label ? 'Continue' : 'Let the year pass'} → age {character.age + 1}
      </button>
    </section>
  )
}
