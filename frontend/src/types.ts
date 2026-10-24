export interface Option {
  id: string
  label: string
  available?: boolean
  requirement?: string | null
  /** 'fight' and 'flee' options come from a combat encounter and carry the resolver's preview. */
  kind?: 'normal' | 'fight' | 'flee'
  combat?: CombatPreview | null
}

// ---- Combat (shapes mirror backend/app/battle.py; the frontend renders them and calculates nothing) ----

export type CombatWarning = 'none' | 'caution' | 'danger'

export interface DefeatEstimate {
  band: string
  min_percent: number
  max_percent: number
  instant_death: boolean
  label: string
}

/** Shown BEFORE the player chooses. Every figure is an estimate from the combat resolver. */
export interface CombatPreview {
  opponent_name: string
  player_power: number
  opponent_power: number
  win_probability: number
  win_percent: number
  defeat: DefeatEstimate
  warning: CombatWarning
  warning_label: string | null
  flee_available: boolean
  flee_chance: number | null
  flee_percent: number | null
}

export interface BattleLine {
  kind: 'stat' | 'skill' | 'item' | 'trait' | 'stress' | 'happiness'
  text: string
}

export interface BattlePower {
  player: number
  opponent: number
}

export interface FleeResult {
  chance: number
  succeeded: boolean
}

export interface BattleDamage {
  percent: number
  health_lost: number
  health_before: number
  health_after: number
  max_health: number
  instant_death: boolean
}

/** The authoritative record of one resolved fight, as stored by the backend. */
export interface BattleSummary {
  id: number | null
  source: 'legacy' | 'story'
  event_id: string
  event_title: string
  opponent_name: string
  action: 'fight' | 'flee'
  outcome: 'victory' | 'defeat' | 'fled'
  flee: FleeResult | null
  result_text: string
  power: BattlePower
  estimated_win_probability: number
  estimated_win_percent: number
  damage: BattleDamage | null
  died: boolean
  cause_of_death: string | null
  other_consequences: BattleLine[]
  rewards: BattleLine[]
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
  battle_summary: BattleSummary | null
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

/** The named character a story encounter is with (GET /api/story/events/pending). */
export interface StoryNpc {
  id: string
  name: string
  title: string | null
  description: string
  /** active | deceased | retired, or null until the character has been introduced in this game */
  status: string | null
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
  npc: StoryNpc | null
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

/** Family legacy, from GET /api/legacy. */
export interface Heir {
  npc_id: string
  name: string
  species_id: string
  species_name: string
  age: number
  relation: 'child' | 'descendant'
  power: number
  traits: string[]
}

export interface PastLife {
  character_id: number
  name: string
  species_name: string
  age: number
  cause_of_death: string | null
  successor_name: string | null
  current: boolean
}

export interface Legacy {
  status: 'alive' | 'awaiting_heir' | 'game_over'
  heirs: Heir[]
  lives: PastLife[]
}

// ---- NPCs: the Character tab's read-only list (GET /api/npcs). Matches backend NpcSummaryRead / NpcListRead. ----

/** The three groups of the tab. The backend decides who belongs where. */
export type NpcGroup = 'family' | 'acquaintances' | 'notable'

export type NpcStatus = 'active' | 'deceased' | 'retired'

export interface NpcSummary {
  id: string
  name: string
  category: 'persistent' | 'named'
  status: NpcStatus
  species: SpeciesBrief
  age: number
  stage: Stage
  power: number
  /** How they relate to the player: direct links and family roles, e.g. "child", "friend", "half_sibling". */
  relations: string[]
  /** Authored title of a named character. Persistent NPCs have none. */
  title: string | null
  /** Every group this NPC belongs to. */
  categories: NpcGroup[]
  /** 0-100 while they are an acquaintance; null otherwise. */
  closeness: number | null
  /** Whether further contact is possible: they may have turned hostile or refuse to see the player again. */
  contact: 'open' | 'hostile' | 'refused'
}

export interface NpcList {
  total: number
  limit: number
  offset: number
  items: NpcSummary[]
}
