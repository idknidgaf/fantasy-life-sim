export interface Option {
  id: string
  label: string
  available?: boolean
  requirement?: string | null
}

export interface PendingEvent {
  id: string
  title: string
  text: string
  choices: Option[]
}

export interface Trait {
  id: string
  name: string
  description: string
  tier: 'common' | 'uncommon' | 'rare' | 'legendary'
}

export interface SpeciesBrief {
  id: string
  name: string
  category: string
}

export interface Species extends SpeciesBrief {
  description: string
  lifespan: number
  stat_cap: number
  stat_modifiers: Record<string, number>
  tags: string[]
  traits: Trait[]
  habitats: string[]
}

export interface Stage {
  id: string
  name: string
  description: string
}

export interface Axis {
  id: string
  name: string
  group: 'personality' | 'alignment'
  low: string
  high: string
  value: number
}

export interface Item {
  id: string
  name: string
  description: string
  count: number
}

export interface Talent {
  id: string
  name: string
  description: string
}

export interface Summary {
  name: string
  species: string
  age: number
  alive: boolean
  cause_of_death: string | null
  stats: Record<string, number>
  top_skills: { skill: string; level: number }[]
  traits: string[]
  alignment: string
  events_faced: number
  defining_skill: string | null
  awakened_talent: string | null
  unawakened_talent: string | null
}

export interface Character {
  id: number
  campaign_id: number
  name: string
  species: SpeciesBrief
  stage: Stage
  location: string
  age: number
  alive: boolean
  stats: Record<string, number>
  effective_stats: Record<string, number>
  stat_cap: number
  skills: Record<string, number>
  traits: Trait[]
  axes: Axis[]
  alignment: string
  items: Item[]
  talent: Talent | null
  cause_of_death: string | null
  action_taken: string | null
  action_taken_label: string | null
  pending_event: PendingEvent | null
  summary: Summary | null
}

export interface Campaign {
  id: number
  name: string
  seed: number
  active: boolean
  created_at: string
  character: Character | null
}

export interface HistoryEntry {
  age: number
  kind: string
  text: string
  event_id: string | null
}
