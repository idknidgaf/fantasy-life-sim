import { useCallback, useEffect, useState } from 'react'
import { api } from '../api'
import { GROUPS, ageText, closenessPercent, relationWords, statusInfo, uniqueById } from '../npcFormat'
import type { NpcGroup, NpcList, NpcSummary } from '../types'

type Loader = (group: NpcGroup) => Promise<NpcList>

type Slot = { state: 'loading' } | { state: 'error'; message: string } | { state: 'ready'; list: NpcList }

const LOADING: Record<NpcGroup, Slot> = {
  family: { state: 'loading' },
  acquaintances: { state: 'loading' },
  notable: { state: 'loading' },
}

/** One NPC, compactly. Only what the game actually knows is shown; a missing detail is left out, never invented. */
export function NpcEntry({ npc }: { npc: NpcSummary }) {
  const status = statusInfo(npc.status)
  const closeness = closenessPercent(npc.closeness)
  const roles = relationWords(npc.relations)
  return (
    <li className="npc-entry">
      <div className="npc-line">
        <span className="npc-name">{npc.name}</span>
        {npc.title && <span className="npc-title">{npc.title}</span>}
        <span className={`npc-status status-${status.tone}`} title={status.hint}>
          {status.label}
        </span>
      </div>
      <div className="npc-meta muted small">
        <span>{ageText(npc)}</span>
        <span>{npc.species.name}</span>
        {roles.length > 0 && <span>{roles.join(', ')}</span>}
      </div>
      {closeness !== null && (
        <div className="closeness" title={`Closeness ${Math.round(closeness)} / 100`}>
          <span className="muted small">Closeness</span>
          <span
            className="bar"
            role="meter"
            aria-label={`Closeness with ${npc.name}`}
            aria-valuemin={0}
            aria-valuemax={100}
            aria-valuenow={Math.round(closeness)}
          >
            <span className="bar-fill closeness-fill" style={{ width: `${closeness}%` }} />
          </span>
        </div>
      )}
    </li>
  )
}

// A stable function: a new one every render would re-run the loading effect forever.
const defaultLoad: Loader = (group) => api.listNpcs(group)

interface Props {
  /** Where the lists come from. Defaults to the game API; tests pass their own. */
  load?: Loader
}

/** The Character tab: who the player knows, in three groups. Read-only: no actions, no relationship changes. */
export default function PeopleTab({ load = defaultLoad }: Props) {
  const [active, setActive] = useState<NpcGroup>('family')
  const [slots, setSlots] = useState<Record<NpcGroup, Slot>>(LOADING)
  const [attempt, setAttempt] = useState(0)

  useEffect(() => {
    let cancelled = false
    for (const g of GROUPS) {
      load(g.id).then(
        (list) => !cancelled && setSlots((s) => ({ ...s, [g.id]: { state: 'ready', list } })),
        (e: unknown) =>
          !cancelled &&
          setSlots((s) => ({ ...s, [g.id]: { state: 'error', message: e instanceof Error ? e.message : 'Something went wrong' } })),
      )
    }
    return () => {
      cancelled = true
    }
  }, [load, attempt])

  const retry = useCallback(() => {
    setSlots(LOADING)
    setAttempt((n) => n + 1)
  }, [])

  const group = GROUPS.find((g) => g.id === active)!
  const slot = slots[active]
  const items = slot.state === 'ready' ? uniqueById(slot.list.items) : []

  return (
    <section className="card people-tab">
      <header className="life-head">
        <h3>People</h3>
        <p className="muted small">The people in your life, and those whose names have reached you.</p>
      </header>

      <div className="people-groups" role="tablist" aria-label="People groups">
        {GROUPS.map((g) => {
          const s = slots[g.id]
          return (
            <button
              key={g.id}
              role="tab"
              aria-selected={g.id === active}
              className={`people-group${g.id === active ? ' active' : ''}`}
              onClick={() => setActive(g.id)}
            >
              {g.label}
              {s.state === 'ready' && <span className="people-count">{s.list.total}</span>}
            </button>
          )
        })}
      </div>

      <p className="muted small people-hint">{group.hint}</p>

      {slot.state === 'loading' ? (
        <p className="muted">Loading…</p>
      ) : slot.state === 'error' ? (
        <div className="people-error" role="alert">
          <p>{slot.message}</p>
          <button onClick={retry}>Try again</button>
        </div>
      ) : items.length === 0 ? (
        <p className="muted people-empty">{group.empty}</p>
      ) : (
        <>
          <ul className="npc-list">
            {items.map((npc) => (
              <NpcEntry key={npc.id} npc={npc} />
            ))}
          </ul>
          {slot.list.total > items.length && (
            <p className="muted small">
              Showing {items.length} of {slot.list.total}.
            </p>
          )}
        </>
      )}
    </section>
  )
}
