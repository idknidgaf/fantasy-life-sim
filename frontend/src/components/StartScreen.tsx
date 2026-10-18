import { useState } from 'react'
import type { SyntheticEvent } from 'react'
import type { Campaign } from '../types'

interface Props {
  campaigns: Campaign[]
  busy: boolean
  onCreate: (name: string, seed?: number) => void
  onOpen: (id: number) => void
  onDelete: (id: number) => void
}

export default function StartScreen({ campaigns, busy, onCreate, onOpen, onDelete }: Props) {
  const [name, setName] = useState('')
  const [seedText, setSeedText] = useState('')

  function submit(e: SyntheticEvent<HTMLFormElement>) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    const seed = seedText.trim() === '' ? undefined : Number(seedText)
    onCreate(trimmed, Number.isInteger(seed) ? seed : undefined)
  }

  return (
    <>
      <section className="card">
        <h2>Begin a new life</h2>
        <p className="muted">
          You will be born into a world of swords, sorcery and old, forgotten things. What
          becomes of you is up to your choices, and a little luck.
        </p>
        <form onSubmit={submit} className="form">
          <label>
            Name
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              maxLength={40}
              placeholder="Aren of the Hollow"
              autoFocus
            />
          </label>
          <label>
            Seed <small className="muted">(optional, the same seed replays the same fate)</small>
            <input
              value={seedText}
              onChange={(e) => setSeedText(e.target.value)}
              inputMode="numeric"
              placeholder="random"
            />
          </label>
          <button className="primary" type="submit" disabled={busy || !name.trim()}>
            Be born
          </button>
        </form>
      </section>

      {campaigns.length > 0 && (
        <section className="card">
          <h3>Saved campaigns</h3>
          <ul className="lives">
            {campaigns.map((c) => {
              const ch = c.character
              return (
                <li key={c.id}>
                  <div>
                    <strong>{c.name}</strong>
                    <span className="muted">
                      {!ch
                        ? ' · empty'
                        : ch.alive
                          ? ` · alive, age ${ch.age}`
                          : ` · died at ${ch.age} of ${ch.cause_of_death ?? 'unknown causes'}`}
                    </span>
                  </div>
                  <div className="row">
                    <button disabled={busy} onClick={() => onOpen(c.id)}>
                      {ch?.alive === false ? 'View' : 'Continue'}
                    </button>
                    <button
                      className="danger"
                      disabled={busy}
                      onClick={() => {
                        if (window.confirm(`Delete "${c.name}" forever?`)) onDelete(c.id)
                      }}
                    >
                      Delete
                    </button>
                  </div>
                </li>
              )
            })}
          </ul>
        </section>
      )}
    </>
  )
}
