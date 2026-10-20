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
  tier: 'common' | 'uncommon' | 'rare' | 'epic' | 'legendary' | 'unique' | 'ultimate' | 'origin'
  effects: string
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
  species_description: string
  species_stat_modifiers: Record<string, number>
  stage: Stage
  location: string
  age: number
  alive: boolean
  stats: Record<string, number>
  effective_stats: Record<string, number>
  stat_cap: number
  health: number
  max_health: number
  stress: number
  happiness: number
  action_budget: number
  actions_remaining: number
  actions_taken_labels: string[]
  skills: Record<string, number>
  traits: Trait[]
  axes: Axis[]
  alignment: string
  items: Item[]
  talent: Talent | null
  cause_of_death: string | null
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

/** An event from the data-driven story engine (/api/story). */
export interface StoryChoice {
  id: string
  text: string
  available: boolean
  locked_hint: string | null
}

export interface StoryEvent {
  instance_id: number
  event_id: string
  event_version: number
  event_type: string | null
  /** true: the player may Age Up without resolving it (it can expire). Default: it blocks play. */
  skippable: boolean
  title: string
  narrative: string
  status: 'pending' | 'presented' | 'resolved' | 'expired'
  created_year: number
  expires_year: number | null
  selected_choice_id: string | null
  choices: StoryChoice[]
}

export interface StoryResolution {
  instance_id: number
  event_id: string
  choice_id: string
  replayed: boolean
  effects: Record<string, unknown>[]
  next_event: string | null
  next_instance_id: number | null
  next_skipped_reason: string | null
  triggered_events: string[]
}

/** Power breakdown from GET /api/power. The frontend only renders it; every number is computed by the backend. */
export interface PowerLine {
  source_type: string
  source_id: string
  label: string
  value: number
  detail: string
  inputs: Record<string, number>
  sources: string[]
}

export interface PowerComponent {
  key: string
  label: string
  total: number
  implemented: boolean
  note: string
  lines: PowerLine[]
}

export interface PowerModifiers {
  total: number
  multiplier: number
  percent_text: string
  note: string
  lines: PowerLine[]
}

export interface PowerFormula {
  general: string
  substituted: string
  result: string
}

export interface PowerBreakdown {
  total: number
  subtotal: number
  stats: PowerComponent
  equipment: PowerComponent
  skills: PowerComponent
  modifiers: PowerModifiers
  formula: PowerFormula
  notes: string[]
}
