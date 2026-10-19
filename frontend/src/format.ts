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
