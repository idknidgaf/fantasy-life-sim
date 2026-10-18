import type { Choice, Life, Summary } from './types'

const BASE: string = import.meta.env.VITE_API_URL ?? 'http://localhost:8000'

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response
  try {
    res = await fetch(BASE + path, {
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

export const api = {
  getSkills: () => request<Record<string, string>>('/skills'),
  listLives: () => request<Life[]>('/lives'),
  getLife: (id: number) => request<Life>(`/lives/${id}`),
  getChoices: (id: number) => request<Choice[]>(`/lives/${id}/choices`),
  getSummary: (id: number) => request<Summary>(`/lives/${id}/summary`),
  createLife: (name: string, seed?: number) =>
    request<Life>('/lives', {
      method: 'POST',
      body: JSON.stringify({ name, seed: seed ?? null }),
    }),
  advance: (id: number, choice: string | null) =>
    request<Life>(`/lives/${id}/advance`, {
      method: 'POST',
      body: JSON.stringify({ choice }),
    }),
  deleteLife: (id: number) => request<void>(`/lives/${id}`, { method: 'DELETE' }),
}
