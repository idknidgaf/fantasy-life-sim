interface Props {
  remaining: number
  budget: number
  busy: boolean
  onStart: () => void
}

/** The yearly Social Action budget and the Socialise entry. The budget is separate from the Life actions: it has its
 *  own count, and it stays usable when every Life action is spent. The backend owns the numbers; this only shows them. */
export default function SocialActions({ remaining, budget, busy, onStart }: Props) {
  const spent = remaining <= 0
  return (
    <div className="life-category social-actions">
      <h4 className="cat-title">Social</h4>
      <p className="budget-badge social-budget" aria-live="polite">
        <span className="muted">Social Actions: </span>
        <span className="budget-num">{remaining}</span>
        <span className="budget-of">/{budget}</span>
      </p>
      <div className="choices">
        <button className="choice" disabled={busy || spent} onClick={onStart}>
          Socialise
        </button>
      </div>
      <p className="muted small cat-hint">
        {spent
          ? 'No Social Actions left this year. Age Up for a fresh three.'
          : 'Three a year, apart from your other actions. Socialising has no events yet: for now this only spends one.'}
      </p>
    </div>
  )
}
