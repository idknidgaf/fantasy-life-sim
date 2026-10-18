import type { Choice } from '../types'

interface Props {
  age: number
  choices: Choice[]
  busy: boolean
  onChoose: (choiceId: string | null) => void
}

export default function ChoicePanel({ age, choices, busy, onChoose }: Props) {
  return (
    <section className="card">
      <h3>Age {age}: what will you do this year?</h3>
      <div className="choices">
        {choices.map((c) => (
          <button key={c.id} className="choice" disabled={busy} onClick={() => onChoose(c.id)}>
            {c.label}
          </button>
        ))}
      </div>
      <button className="link" disabled={busy} onClick={() => onChoose(null)}>
        Let the year pass
      </button>
    </section>
  )
}
