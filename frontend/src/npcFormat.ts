import { pretty } from './format'
import type { NpcGroup, NpcSummary } from './types'

/** What the tab shows for each group. The backend decides membership; this is only wording. */
export const GROUPS: { id: NpcGroup; label: string; hint: string; empty: string }[] = [
  {
    id: 'family',
    label: 'Family',
    hint: 'Relatives by birth and descent.',
    empty: 'No family to show yet.',
  },
  {
    id: 'acquaintances',
    label: 'Acquaintances',
    hint: 'People you have a personal relationship with.',
    empty: "You haven't built a personal relationship with anyone yet.",
  },
  {
    id: 'notable',
    label: 'Notable Figures',
    hint: 'Important people you have heard of or met in your story.',
    empty: "You haven't come across anyone notable yet.",
  },
]

export interface StatusInfo {
  label: string
  tone: 'alive' | 'deceased' | 'away' | 'unknown'
  hint: string
}

/** Wording for a lifecycle status. Anything the backend adds later is shown as Unknown, never guessed at. */
export function statusInfo(status: string): StatusInfo {
  switch (status) {
    case 'active':
      return { label: 'Alive', tone: 'alive', hint: 'Alive' }
    case 'deceased':
      return { label: 'Deceased', tone: 'deceased', hint: 'Has died' }
    case 'retired':
      return { label: 'Away', tone: 'away', hint: 'Has left the story (moved away or gone)' }
    default:
      return { label: 'Unknown', tone: 'unknown', hint: 'Status unknown' }
  }
}

/** A closeness bar is only drawn for a real number; it is clamped to 0-100. Missing or invalid means no bar. */
export function closenessPercent(value: number | null | undefined): number | null {
  if (typeof value !== 'number' || !Number.isFinite(value)) return null
  return Math.max(0, Math.min(100, value))
}

/** "Age 40", or "Died at 40" for the dead (their age stops at death). */
export function ageText(npc: Pick<NpcSummary, 'age' | 'status'>): string {
  return npc.status === 'deceased' ? `Died at ${npc.age}` : `Age ${npc.age}`
}

/** Their relation to the player as readable words ("half_sibling" becomes "Half-sibling"), without repeats. */
export function relationWords(relations: string[]): string[] {
  const words = relations.map((r) => pretty(r).replace(/^Half Sibling$/, 'Half-sibling'))
  return [...new Set(words)]
}

/** Each NPC at most once per list, first occurrence kept. The backend already guarantees it; this keeps the screen
 *  correct whatever it is handed. */
export function uniqueById<T extends { id: string }>(items: T[]): T[] {
  const seen = new Set<string>()
  return items.filter((i) => (seen.has(i.id) ? false : (seen.add(i.id), true)))
}
