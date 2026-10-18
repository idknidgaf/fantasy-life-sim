export function pretty(id: string): string {
  return id.replace(/_/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

export function lifeStage(age: number): string {
  if (age < 3) return 'Infant'
  if (age < 13) return 'Child'
  if (age < 18) return 'Teenager'
  if (age < 30) return 'Young adult'
  if (age < 50) return 'Adult'
  if (age < 65) return 'Middle-aged'
  return 'Elder'
}

export const STAT_INFO: Record<string, { label: string; hint: string }> = {
  constitution: { label: 'Constitution', hint: 'Health, recovery and bodily resilience' },
  dexterity: { label: 'Dexterity', hint: 'Speed, precision and evasion' },
  wisdom: { label: 'Wisdom', hint: 'Perception, judgement and insight' },
  strength: { label: 'Strength', hint: 'Physical force and carrying power' },
  charisma: { label: 'Charisma', hint: 'Persuasion, leadership and influence' },
}
