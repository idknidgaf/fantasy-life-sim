import { useEffect, useState } from 'react'
import { api, newRequestId } from './api'
import BattleSummary from './components/BattleSummary'
import CharacterSheet from './components/CharacterSheet'
import DeathSummary from './components/DeathSummary'
import EventLog from './components/EventLog'
import EventPanel from './components/EventPanel'
import LifeTab from './components/LifeTab'
import PastLives from './components/PastLives'
import NavSidebar, { type Tab } from './components/NavSidebar'
import PeopleTab from './components/PeopleTab'
import PowerTab from './components/PowerTab'
import StoryEventPanel from './components/StoryEventPanel'
import StartScreen from './components/StartScreen'
import VitalsPanel from './components/VitalsPanel'
import type {
  Campaign, Character, HistoryEntry, Legacy, Option, PowerBreakdown, Species, StoryEvent,
} from './types'

/** Everything the game screen needs, fetched from the active campaign. */
async function loadActive() {
  const [character, actions, history, storyEvents, power, legacy] = await Promise.all([
    api.getCharacter(),
    api.getActions(),
    api.getHistory(),
    api.getPendingStoryEvents().catch(() => [] as StoryEvent[]),
    api.getPower().catch(() => null as PowerBreakdown | null),
    api.getLegacy().catch(() => null as Legacy | null),
  ])
  return { character, actions, history, storyEvents, power, legacy }
}

export default function App() {
  const [campaigns, setCampaigns] = useState<Campaign[]>([])
  const [skillCategories, setSkillCategories] = useState<Record<string, string>>({})
  const [species, setSpecies] = useState<Species[]>([])
  const [character, setCharacter] = useState<Character | null>(null)
  const [actions, setActions] = useState<Option[]>([])
  const [history, setHistory] = useState<HistoryEntry[]>([])
  const [storyEvents, setStoryEvents] = useState<StoryEvent[]>([])
  const [power, setPower] = useState<PowerBreakdown | null>(null)
  const [legacy, setLegacy] = useState<Legacy | null>(null)
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
          setStoryEvents(s.storyEvents)
          setPower(s.power)
          setLegacy(s.legacy)
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
    setStoryEvents(s.storyEvents)
          setPower(s.power)
          setLegacy(s.legacy)
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
  const startSocialAction = () => change(() => api.startSocialAction(newRequestId()))
  const advanceTurn = () => change(() => api.advanceTurn())
  const resolveEvent = (eventId: string, choiceId: string) =>
    change(() => api.resolveEvent(eventId, choiceId))

  // The backend orders the queue and presents only its head, and nothing while a legacy event is pending.
  const currentStoryEvent = storyEvents[0]?.status === 'presented' ? storyEvents[0] : null
  // Encounters stop time: Age Up and the tabs are unavailable until they are resolved. A skippable
  // story event is the explicit exception.
  const eventPending = !!character?.battle_summary || !!character?.pending_event || storyEvents.some((e) => !e.skippable)

  // Continue dismisses the stored summary. The backend's queue is untouched, so the next pending encounter appears.
  const continueAfterBattle = (battleId: number) => change(() => api.acknowledgeBattle(battleId))

  // The chosen heir becomes the playable character; the screen then reloads as that character. Safe to repeat.
  // No stat-change baseline: the heir's numbers are not "changes" from the dead character's.
  const continueAsHeir = (npcId: string) =>
    guard(async () => {
      await api.continueAsHeir(npcId)
      await showActive()
    })

  const resolveStoryEvent = (instanceId: number, choiceId: string) =>
    change(() => api.resolveStoryEvent(instanceId, choiceId))

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
            <NavSidebar
              activeTab={tab}
              onTabChange={setTab}
              character={character}
              eventPending={eventPending}
            />
          </aside>

          <section className="content-main">
            {character.battle_summary && character.battle_summary.id !== null ? (
              <BattleSummary
                summary={character.battle_summary}
                busy={busy}
                onContinue={() => continueAfterBattle(character.battle_summary!.id!)}
              />
            ) : !character.alive && character.summary ? (
              <DeathSummary
                summary={character.summary}
                legacy={legacy}
                busy={busy}
                onContinueAs={continueAsHeir}
                onNewLife={goToMenu}
              />
            ) : character.pending_event ? (
              <EventPanel event={character.pending_event} busy={busy} onChoose={resolveEvent} />
            ) : currentStoryEvent ? (
              <StoryEventPanel event={currentStoryEvent} busy={busy} onChoose={resolveStoryEvent} />
            ) : tab === 'character' ? (
              <>
                <CharacterSheet
                  character={character}
                  prevStats={prevStats}
                  skillCategories={skillCategories}
                />
                {legacy && <PastLives lives={legacy.lives} />}
              </>
            ) : tab === 'power' ? (
              <PowerTab power={power} />
            ) : tab === 'people' ? (
              <PeopleTab />
            ) : (
              <LifeTab
                character={character}
                actions={actions}
                busy={busy}
                onAction={onAction}
                onSocial={startSocialAction}
              />
            )}
          </section>

          <aside className="sidebar-right">
            <VitalsPanel
              character={character}
              busy={busy}
              eventPending={eventPending}
              onAdvance={advanceTurn}
            />
            <EventLog history={history} />
          </aside>
        </main>
      )}
    </div>
  )
}
