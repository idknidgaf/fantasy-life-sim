import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, within } from '@testing-library/react'
import LifeTab from './LifeTab'
import SocialActions from './SocialActions'
import { newRequestId } from '../api'
import type { Character, Option } from '../types'

afterEach(cleanup)

const character = (over: Partial<Character> = {}): Character =>
  ({
    alive: true, stage: { id: 'adulthood', name: 'Adult', description: 'Grown.' }, action_budget: 3, actions_remaining: 2,
    actions_taken_labels: [], social_budget: 3, social_remaining: 2, ...over,
  }) as Character

const actions: Option[] = [{ id: 'rest', label: 'Take the time off', available: true }]

const tab = (over: Partial<Character> = {}, extra: Partial<Parameters<typeof LifeTab>[0]> = {}) =>
  render(<LifeTab character={character(over)} actions={actions} busy={false} onAction={() => {}} onSocial={() => {}} {...extra} />)

describe('Social Actions display', () => {
  it('shows the remaining budget as "Social Actions: n/3"', () => {
    tab({ social_remaining: 2 })
    expect(screen.getByText(/Social Actions:/).parentElement!.textContent).toBe('Social Actions: 2/3')
  })

  it('shows a full budget and an empty one', () => {
    tab({ social_remaining: 3 })
    expect(screen.getByText(/Social Actions:/).parentElement!.textContent).toBe('Social Actions: 3/3')
    cleanup()
    tab({ social_remaining: 0 })
    expect(screen.getByText(/Social Actions:/).parentElement!.textContent).toBe('Social Actions: 0/3')
  })

  it('offers a Socialise entry that starts one when pressed', () => {
    const onSocial = vi.fn()
    tab({}, { onSocial })
    fireEvent.click(screen.getByRole('button', { name: 'Socialise' }))
    expect(onSocial).toHaveBeenCalledTimes(1)
  })

  it('disables Socialise when none remain, and says why', () => {
    const onSocial = vi.fn()
    tab({ social_remaining: 0 }, { onSocial })
    const button = screen.getByRole('button', { name: 'Socialise' }) as HTMLButtonElement
    expect(button.disabled).toBe(true)
    fireEvent.click(button)
    expect(onSocial).not.toHaveBeenCalled()
    expect(screen.getByText(/No Social Actions left this year/)).toBeTruthy()
  })

  it('disables Socialise while another request is in flight', () => {
    tab({}, { busy: true })
    expect((screen.getByRole('button', { name: 'Socialise' }) as HTMLButtonElement).disabled).toBe(true)
  })

  it('is not shown for a character who has died', () => {
    tab({ alive: false, social_remaining: 0, social_budget: 0 })
    expect(screen.queryByRole('button', { name: 'Socialise' })).toBeNull()
  })
})

describe('independence from the normal Life actions', () => {
  it('keeps Socialise usable when every Life action is spent', () => {
    tab({ actions_remaining: 0, social_remaining: 3 })
    expect(screen.getByText(/You have used all 3 actions/)).toBeTruthy()
    expect((screen.getByRole('button', { name: 'Socialise' }) as HTMLButtonElement).disabled).toBe(false)
  })

  it('keeps the Life actions usable when every Social Action is spent', () => {
    const onAction = vi.fn()
    tab({ actions_remaining: 2, social_remaining: 0 }, { onAction })
    const rest = screen.getByRole('button', { name: 'Take the time off' }) as HTMLButtonElement
    expect(rest.disabled).toBe(false)
    fireEvent.click(rest)
    expect(onAction).toHaveBeenCalledWith('rest')
  })

  it('shows the two budgets separately and leaves the Life budget display as it was', () => {
    const { container } = tab({ actions_remaining: 2, action_budget: 3, social_remaining: 1 })
    const life = container.querySelector('.life-head .budget-badge')!
    expect(life.textContent).toBe('2 / 3 actions remaining')
    expect(screen.getByText(/Social Actions:/).parentElement!.textContent).toBe('Social Actions: 1/3')
  })

  it('does not change the existing "socialize" Life action: it is still a Life action under Lifestyle', () => {
    const onAction = vi.fn()
    tab({}, { actions: [{ id: 'socialize', label: 'Socialize', available: true }], onAction })
    const lifestyle = screen.getByText('Lifestyle').closest('.life-category') as HTMLElement
    fireEvent.click(within(lifestyle).getByRole('button', { name: 'Socialize' }))
    expect(onAction).toHaveBeenCalledWith('socialize')
  })
})

describe('SocialActions on its own', () => {
  it('renders from the numbers it is given, not its own copy', () => {
    const { rerender } = render(<SocialActions remaining={3} budget={3} busy={false} onStart={() => {}} />)
    expect(screen.getByText('3')).toBeTruthy()
    rerender(<SocialActions remaining={1} budget={3} busy={false} onStart={() => {}} />)
    expect(screen.getByText('1')).toBeTruthy()
  })
})

describe('request ids', () => {
  it('are fresh for every click and match what the server accepts', () => {
    const ids = new Set(Array.from({ length: 50 }, newRequestId))
    expect(ids.size).toBe(50)
    for (const id of ids) expect(id).toMatch(/^[A-Za-z0-9_.:-]{1,64}$/)
  })
})
