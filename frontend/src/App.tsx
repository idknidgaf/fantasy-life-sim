import { useEffect, useState } from 'react'
import { api } from './api'
import CharacterSheet from './components/CharacterSheet'
import ChoicePanel from './components/ChoicePanel'
import DeathSummary from './components/DeathSummary'
import EventLog from './components/EventLog'
import StartScreen from './components/StartScreen'
import type { Choice, Life, Summary } from './types'

const SAVED_KEY = 'fantasy-life-sim:current-life'

/** Load a life plus whatever the screen needs next (choices, or a summary if dead). */
async function fetchState(id: number) {
  const life = await api.getLife(id)
  if (life.alive) {
    return { life, choices: await api.getChoices(id), summary: null as Summary | null }
  }
  return { life, choices: [] as Choice[], summary: await api.getSummary(id) }
}

export default function App() {
  const [lives, setLives] = useState<Life[]>([])
  const [skillCategories, setSkillCategories] = useState<Record<string, string>>({})
  const [life, setLife] = useState<Life | null>(null)
  const [prevStats, setPrevStats] = useState<Record<string, number> | null>(null)
  const [choices, setChoices] = useState<Choice[]>([])
  const [summary, setSummary] = useState<Summary | null>(null)
  const [loading, setLoading] = useState(true)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  // First load: skills, saved lives, and resume the life you were playing.
  useEffect(() => {
    let cancelled = false
    async function init() {
      try {
        const [skills, list] = await Promise.all([
          api.getSkills().catch(() => ({}) as Record<string, string>),
          api.listLives(),
        ])
        if (cancelled) return
        setSkillCategories(skills)
        setLives(list)

        const savedId = Number(localStorage.getItem(SAVED_KEY))
        if (savedId && list.some((l) => l.id === savedId)) {
          const s = await fetchState(savedId)
          if (cancelled) return
          setLife(s.life)
          setChoices(s.choices)
          setSummary(s.summary)
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

  async function showLife(id: number) {
    const s = await fetchState(id)
    setPrevStats(null)
    setLife(s.life)
    setChoices(s.choices)
    setSummary(s.summary)
    localStorage.setItem(SAVED_KEY, String(id))
  }

  const openLife = (id: number) => guard(() => showLife(id))

  const createLife = (name: string, seed?: number) =>
    guard(async () => {
      const created = await api.createLife(name, seed)
      await showLife(created.id)
    })

  const advance = (choice: string | null) =>
    guard(async () => {
      if (!life) return
      const before = life.stats
      await api.advance(life.id, choice)
      await showLife(life.id)
      setPrevStats(before)
    })

  const deleteLife = (id: number) =>
    guard(async () => {
      await api.deleteLife(id)
      setLives(await api.listLives())
    })

  const backToMenu = () =>
    guard(async () => {
      localStorage.removeItem(SAVED_KEY)
      setLife(null)
      setSummary(null)
      setChoices([])
      setLives(await api.listLives())
    })

  return (
    <div className="app">
      <header className="topbar">
        <h1>Fantasy Life Sim</h1>
        {life && (
          <button onClick={backToMenu} disabled={busy}>
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
      ) : !life ? (
        <StartScreen
          lives={lives}
          busy={busy}
          onCreate={createLife}
          onOpen={openLife}
          onDelete={deleteLife}
        />
      ) : (
        <main className="game">
          <CharacterSheet life={life} prevStats={prevStats} skillCategories={skillCategories} />
          <div className="column">
            {life.alive ? (
              <ChoicePanel age={life.age} choices={choices} busy={busy} onChoose={advance} />
            ) : (
              summary && <DeathSummary summary={summary} onNewLife={backToMenu} />
            )}
            <EventLog history={life.history} />
          </div>
        </main>
      )}
    </div>
  )
}
