export function pretty(id: string): string {
  return id.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export const STAT_INFO: Record<string, { label: string; hint: string }> = {
  constitution: { label: 'Constitution', hint: 'Health, recovery and bodily resilience' },
  dexterity: { label: 'Dexterity', hint: 'Speed, precision and evasion' },
  wisdom: { label: 'Wisdom', hint: 'Perception, judgement and insight' },
  strength: { label: 'Strength', hint: 'Physical force and carrying power' },
  charisma: { label: 'Charisma', hint: 'Persuasion, leadership and influence' },
}

export function modifierText(mods: Record<string, number>): string {
  const parts = Object.entries(mods)
    .filter(([, v]) => v !== 0)
    .sort((a, b) => b[1] - a[1])
    .map(([k, v]) => `${v > 0 ? '+' : ''}${v} ${STAT_INFO[k]?.label ?? pretty(k)}`)
  return parts.join(', ') || 'No stat modifiers'
}

export type Tier = 'common' | 'uncommon' | 'rare' | 'epic' | 'legendary' | 'unique' | 'ultimate' | 'origin'

export const RARITY_INFO: Record<Tier, { label: string; color: string }> = {
  common: { label: 'Common', color: '#a1937f' },
  uncommon: { label: 'Uncommon', color: '#6fbf73' },
  rare: { label: 'Rare', color: '#6aa7d9' },
  epic: { label: 'Epic', color: '#c58ae0' },
  legendary: { label: 'Legendary', color: '#d9a441' },
  unique: { label: 'Unique', color: '#5ed4d4' },
  ultimate: { label: 'Ultimate', color: '#d9695f' },
  origin: { label: 'Origin', color: '#f0e6d3' },
}

export const TIER_ORDER: Tier[] = ['common', 'uncommon', 'rare', 'epic', 'legendary', 'unique', 'ultimate', 'origin']

export function vitalColor(kind: 'health' | 'stress' | 'happiness'): string {
  if (kind === 'health') return '#d9695f'
  if (kind === 'stress') return '#d9a441'
  return '#6fbf73'
}

export function vitalPercent(kind: 'health' | 'stress' | 'happiness', value: number, max?: number): number {
  const v = kind === 'health' ? (value / (max ?? 100)) * 100 : value
  return Math.max(0, Math.min(100, v))
}
