import type { Character, Option } from '../types'

interface Props {
  character: Character
  actions: Option[]
  busy: boolean
  onAction: (actionId: string) => void
}

interface Category {
  title: string
  hint: string
  items: Option[]
}

function categorize(actions: Option[]): Category[] {
  const training: Option[] = []
  const skills: Option[] = []
  const lifestyle: Option[] = []
  const special: Option[] = []

  for (const a of actions) {
    if (a.id.startsWith('train_stat:')) training.push(a)
    else if (a.id.startsWith('train:')) skills.push(a)
    else if (a.id === 'rest' || a.id === 'socialize') lifestyle.push(a)
    else special.push(a)
  }

  return [
    { title: 'Attribute Training', hint: 'Drill a base attribute to grow stronger.', items: training },
    { title: 'Skill Practice', hint: 'Hone a specific craft or ability.', items: skills },
    { title: 'Lifestyle', hint: 'Rest and recover, or socialize for happiness.', items: lifestyle },
    { title: 'Special Actions', hint: 'Unique opportunities at this stage of life.', items: special },
  ].filter((c) => c.items.length > 0)
}

export default function LifeTab({ character, actions, busy, onAction }: Props) {
  const remaining = character.actions_remaining
  const budget = character.action_budget
  const taken = character.actions_taken_labels
  const cats = categorize(actions)
  const noActions = remaining === 0

  return (
    <section className="card life-tab">
      <header className="life-head">
        <h3>Life · {character.stage.name}</h3>
        <p className="muted small">{character.stage.description}</p>
        <div className="budget-badge">
          <span className="budget-num">{remaining}</span>
          <span className="budget-of"> / {budget} actions remaining</span>
        </div>
      </header>

      {taken.length > 0 && (
        <div className="taken-list">
          <span className="muted small">Done this year:</span>
          <ul className="chips">
            {taken.map((label, i) => (
              <li key={i} className="item">{label}</li>
            ))}
          </ul>
        </div>
      )}

      {noActions ? (
        <div className="stage-note">
          You have used all {budget} actions for this year. Age Up to continue your journey.
        </div>
      ) : cats.length === 0 ? (
        <div className="stage-note">
          {character.stage.description}
        </div>
      ) : (
        <div className="life-categories">
          {cats.map((cat) => (
            <div key={cat.title} className="life-category">
              <h4 className="cat-title">{cat.title}</h4>
              <p className="muted small cat-hint">{cat.hint}</p>
              <div className="choices">
                {cat.items.map((a) => (
                  <button
                    key={a.id}
                    className="choice"
                    disabled={busy || !a.available}
                    onClick={() => onAction(a.id)}
                  >
                    {a.label}
                    {a.available === false && a.requirement && (
                      <small className="requirement">🔒 {a.requirement}</small>
                    )}
                  </button>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  )
}
