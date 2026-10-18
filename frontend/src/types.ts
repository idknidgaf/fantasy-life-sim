export interface Option {
  id: string
  label: string
}

export interface PendingEvent {
  id: string
  title: string
  text: string
  choices: Option[]
}

export interface Summary {
  name: string
  age: number
  alive: boolean
  cause_of_death: string | null
  stats: Record<string, number>
  top_skills: { skill: string; level: number }[]
  events_faced: number
  defining_skill: string | null
}

export interface Character {
  id: number
  campaign_id: number
  name: string
  age: number
  alive: boolean
  stats: Record<string, number>
  skills: Record<string, number>
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
