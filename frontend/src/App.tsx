import { useEffect, useState } from 'react'
import { api } from './api'
import CharacterSheet from './components/CharacterSheet'
import DeathSummary from './components/DeathSummary'
import EventLog from './components/EventLog'
import EventPanel from './components/EventPanel'
import LifeTab from './components/LifeTab'
import NavSidebar, { type Tab } from './components/NavSidebar'
import StartScreen from './components/StartScreen'
import VitalsPanel from './components/VitalsPanel'
import type { Campaign, Character, HistoryEntry, Option, Species } from './types'

/** Everything the game screen needs, fetched from the active campaign. */
async function loadActive() {
  const [character, actions, history] = await Promise.all([
    api.getCharacter(),
    api.getActions(),
    api.getHistory(),
  ])
  return { character, actions, history }
}

export default function App() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [skillCategories, setSkillCategories] = useState<Record<string, string>>({})
  const [species, setSpecies] = useState<Species[]>([])
  const [character, setCharacter] = useState<Character | null>(null)
  const [actions, setActions] = useState<Option[]>([])
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [prevStats, setPrevStats] = useState<Record<string, number> | null>(null)
  const [tab, setTab] = useState<Tab>('character')
  const [inMenu, setInMenu] = useState(true)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // First load: skills, saved campaigns, and resume the active one.
  useEffect(() => {
    let cancelled = false
    async function init() {
      try {
        const [skills, speciesList, list] = await Promise.all([
          api.getSkills().catch(() => ({}) as Record<string, string>),
          api.getSpecies(),
          api.listCampaigns(),
        ])
        if (cancelled) return
        setSkillCategories(skills)
        setSpecies(speciesList)
        setCampaigns(list)
        if (list.some((c) => c.active)) {
          const s = await loadActive()
          if (cancelled) return
          setCharacter(s.character)
          setActions(s.actions)
          setHistory(s.history)
          setInMenu(false)
        }
      } catch (e) {
        if (!cancelled) setError(e instanceof Error ? e.message : 'Something went wrong')
      } finally {
        if (!cancelled) setLoading(false)
      }
    }
    void init()
    return () => {
      cancelled = true
    }
  }, [])

  async function guard(fn: () => Promise<void>) {
    setBusy(true)
    setError(null)
    try {
      await fn()
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Something went wrong')
    } finally {
      setBusy(false)
    }
  }

  async function showActive(previous: Record<string, number> | null = null) {
    const s = await loadActive()
    setCharacter(s.character)
    setActions(s.actions)
    setHistory(s.history)
    setPrevStats(previous)
    setInMenu(false)
  }

  /** Run a change on the active character, then refresh the screen and show stat deltas. */
  const change = (call: () => Promise<unknown>) =>
    guard(async () => {
      const before = character?.effective_stats ?? null
      await call()
      await showActive(before)
    })

  const createCampaign = (name: string, speciesId: string, seed?: number) =>
    guard(async () => {
      await api.createCampaign(name, speciesId, seed)
      await showActive()
    })

  const openCampaign = (id: number) =>
    guard(async () => {
      await api.activateCampaign(id)
      await showActive()
    })

  const deleteCampaign = (id: number) =>
    guard(async () => {
      await api.deleteCampaign(id)
      setCampaigns(await api.listCampaigns())
    })

  const goToMenu = () =>
    guard(async () => {
      setCampaigns(await api.listCampaigns())
      setInMenu(true)
    })

  const performAction = (id: string) => change(() => api.performAction(id))
  const advanceTurn = () => change(() => api.advanceTurn())
  const resolveEvent = (eventId: string, choiceId: string) =>
    change(() => api.resolveEvent(eventId, choiceId))

  // When actions are taken, switch to Life tab so the user sees their action budget.
  const onAction = (id: string) => {
    void performAction(id)
  }

  return (
    <div className="app">
      <header className="topbar">
        <h1>Fantasy Life Sim</h1>
        {!inMenu && (
          <button onClick={goToMenu} disabled={busy}>
            ← Menu
          </button>
        )}
      </header>

      {error && (
        <div className="error" role="alert">
          {error}
        </div>
      )}

      {loading ? (
        <p className="muted center">Loading…</p>
      ) : inMenu || !character ? (
        <StartScreen
          campaigns={campaigns}
          species={species}
          busy={busy}
          onCreate={createCampaign}
          onOpen={openCampaign}
          onDelete={deleteCampaign}
        />
      ) : (
        <main className="game-layout">
          <aside className="sidebar-left">
            <NavSidebar activeTab={tab} onTabChange={setTab} character={character} />
          </aside>

          <section className="content-main">
            {!character.alive && character.summary ? (
              <DeathSummary summary={character.summary} onNewLife={goToMenu} />
            ) : character.pending_event ? (
              <EventPanel event={character.pending_event} busy={busy} onChoose={resolveEvent} />
            ) : tab === 'character' ? (
              <CharacterSheet
                character={character}
                prevStats={prevStats}
                skillCategories={skillCategories}
              />
            ) : (
              <LifeTab
                character={character}
                actions={actions}
                busy={busy}
                onAction={onAction}
              />
            )}
          </section>

          <aside className="sidebar-right">
            <VitalsPanel character={character} busy={busy} onAdvance={advanceTurn} />
            <EventLog history={history} />
          </aside>
        </main>
      )}
    </div>
  )
}
