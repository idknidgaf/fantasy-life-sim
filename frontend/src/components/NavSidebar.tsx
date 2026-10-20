import type { Character } from '../types'

export type Tab = 'character' | 'life' | 'power'

interface Props {
  activeTab: Tab
  onTabChange: (tab: Tab) => void
  character: Character
  /** A legacy or blocking story event is waiting for a decision. */
  eventPending: boolean
}

export default function NavSidebar({ activeTab, onTabChange, character, eventPending }: Props) {
  const dead = !character.alive

  return (
    <nav className="nav-sidebar">
      <button
        className={`nav-tab${activeTab === 'character' ? ' active' : ''}`}
        disabled={eventPending || dead}
        onClick={() => onTabChange('character')}
      >
        <span className="nav-icon">❤</span>
        <span className="nav-label">Character</span>
      </button>
      <button
        className={`nav-tab${activeTab === 'life' ? ' active' : ''}`}
        disabled={eventPending || dead}
        onClick={() => onTabChange('life')}
      >
        <span className="nav-icon">✦</span>
        <span className="nav-label">Life</span>
      </button>
      <button
        className={`nav-tab${activeTab === 'power' ? ' active' : ''}`}
        disabled={eventPending || dead}
        onClick={() => onTabChange('power')}
      >
        <span className="nav-icon">⚡</span>
        <span className="nav-label">Power</span>
      </button>
    </nav>
  )
}
