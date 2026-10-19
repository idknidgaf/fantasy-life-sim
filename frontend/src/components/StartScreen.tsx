import { useState } from 'react'
import type { SyntheticEvent } from 'react'
import { modifierText } from '../format'
import type { Campaign, Species } from '../types'

interface Props {
  campaigns: Campaign[]
  species: Species[]
  busy: boolean
  onCreate: (name: string, species: string, seed?: number) => void
  onOpen: (id: number) => void
  onDelete: (id: number) => void
}

export default function StartScreen({ campaigns, species, busy, onCreate, onOpen, onDelete }: Props) {
  const [name, setName] = useState('')
  const [seedText, setSeedText] = useState('')
  const [speciesId, setSpeciesId] = useState('human')

  function submit(e: SyntheticEvent<HTMLFormElement>) {
    e.preventDefault()
    const trimmed = name.trim()
    if (!trimmed) return
    const seed = seedText.trim() === '' ? undefined : Number(seedText)
    onCreate(trimmed, speciesId, Number.isInteger(seed) ? seed : undefined)
  }

  return (
    <>
      <section className="card">
        <h2>Begin a new life</h2>
        <p className="muted">
          Choose what you will be born as. A wolf, a slime and an elf will live very different lives, and meet
          very different fates.
        </p>
        <form onSubmit={submit} className="form">
          <fieldset className="species-grid">
            <legend>Species</legend>
            {species.map((s) => (
              <label key={s.id} className={`species-card${speciesId === s.id ? ' selected' : ''}`}>
                <input
                  type="radio"
                  name="species"
                  value={s.id}
                  checked={speciesId === s.id}
                  onChange={() => setSpeciesId(s.id)}
                />
                <span className="species-head">
                  <strong>{s.name}</strong>
                  <small className={`chip cat-${s.category}`}>{s.category}</small>
                </span>
                <span className="muted small">{s.description}</span>
                <span className="small">
                  Lifespan ~{s.lifespan} yrs · {modifierText(s.stat_modifiers)}
                  {s.stat_cap > 100 ? ` · stat cap ${s.stat_cap}` : ''}
                </span>
                {s.traits.length > 0 && (
                  <span className="small muted">Born with: {s.traits.map((t) => t.name).join(', ')}</span>
                )}
              </label>
            ))}
          </fieldset>
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
            Seed <small className="muted">(optional, the same seed and species replay the same fate)</small>
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
                          ? ` · ${ch.species.name}, alive, age ${ch.age}`
                          : ` · ${ch.species.name}, died at ${ch.age} of ${ch.cause_of_death ?? 'unknown causes'}`}
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
