export interface HistoryEntry {
  age: number
  choice: string | null
  events: string[]
}

export interface Life {
  id: number
  name: string
  seed: number
  age: number
  alive: boolean
  stats: Record<string, number>
  skills: Record<string, number>
  history: HistoryEntry[]
  cause_of_death: string | null
}

export interface Choice {
  id: string
  label: string
}

export interface Summary {
  name: string
  age: number
  alive: boolean
  cause_of_death: string | null
  stats: Record<string, number>
  top_skills: { skill: string; level: number }[]
  years_recorded: number
  defining_skill: string | null
}
