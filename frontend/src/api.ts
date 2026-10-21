import type {
  Campaign, Character, HistoryEntry, Option, PowerBreakdown, Species, StoryEvent, StoryResolution,
} from './types'

const BASE: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(`${BASE}/api${path}`, {
      headers: { 'Content-Type': 'application/json' },
      ...init,
    })
  } catch {
    throw new Error(
      "Can't reach the game server. Is the backend running? (fastapi dev app/main.py)",
    )
  }
  if (!res.ok) {
    let detail = `Request failed (${res.status})`
    try {
      const body = await res.json()
      if (typeof body.detail === 'string') detail = body.detail
    } catch {
      /* keep default message */
    }
    throw new Error(detail)
  }
  return res.status === 204 ? (undefined as T) : ((await res.json()) as T)
}

const post = <T>(path: string, body?: unknown) =>
  request<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

const enc = encodeURIComponent

export const api = {
  getSkills: () => request<Record<string, string>>('/skills'),
  getSpecies: () => request<Species[]>('/species'),

  listCampaigns: () => request<Campaign[]>('/campaigns'),
  createCampaign: (characterName: string, species: string, seed?: number) =>
    post<Campaign>('/campaign', { character_name: characterName, species, seed: seed ?? null }),
  activateCampaign: (id: number) => post<Campaign>(`/campaigns/${id}/activate`),
  deleteCampaign: (id: number) => request<void>(`/campaigns/${id}`, { method: 'DELETE' }),

  getCharacter: () => request<Character>('/character'),
  getActions: () => request<Option[]>('/actions'),
  performAction: (actionId: string) => post<Character>(`/actions/${enc(actionId)}`),
  advanceTurn: () => post<Character>('/turn/advance'),
  resolveEvent: (eventId: string, choiceId: string) =>
    post<Character>(`/events/${enc(eventId)}/choices/${enc(choiceId)}`),
  acknowledgeBattle: (battleId: number) => post<Character>(`/battles/${battleId}/acknowledge`),
  getHistory: () => request<HistoryEntry[]>('/history'),
  getPower: () => request<PowerBreakdown>('/power'),

  // Data-driven story engine
  getPendingStoryEvents: () => request<StoryEvent[]>('/story/events/pending'),
  resolveStoryEvent: (instanceId: number, choiceId: string) =>
    post<StoryResolution>(`/story/instances/${instanceId}/choices/${enc(choiceId)}`),
}
