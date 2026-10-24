import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import PeopleTab from './PeopleTab'
import NavSidebar from './NavSidebar'
import type { Character, NpcGroup, NpcList, NpcSummary } from '../types'

afterEach(cleanup)

function npc(over: Partial<NpcSummary> & { id: string; name: string }): NpcSummary {
  return {
    category: 'persistent', status: 'active', species: { id: 'human', name: 'Human', category: 'humanoid' }, age: 30,
    stage: { id: 'adulthood', name: 'Adult', description: '' }, power: 10, relations: [], title: null,
    categories: [], closeness: null, contact: 'open', ...over,
  }
}

const list = (items: NpcSummary[], total = items.length): NpcList => ({ total, limit: 100, offset: 0, items })

function loader(data: Partial<Record<NpcGroup, NpcList | Error>>) {
  return vi.fn((g: NpcGroup) => {
    const d = data[g] ?? list([])
    return d instanceof Error ? Promise.reject(d) : Promise.resolve(d)
  })
}

const tab = (name: RegExp | string) => screen.getByRole('tab', { name })

describe('PeopleTab', () => {
  it('shows a loading state, then the Family list first', async () => {
    const load = loader({ family: list([npc({ id: 'npc_1', name: 'Mira Ashford', relations: ['child'] })]) })
    render(<PeopleTab load={load} />)
    expect(screen.getByText('Loading…')).toBeTruthy()
    expect(await screen.findByText('Mira Ashford')).toBeTruthy()
    expect(tab(/Family/).getAttribute('aria-selected')).toBe('true')
  })

  it('loads each group once and does not loop', async () => {
    const load = loader({})
    render(<PeopleTab load={load} />)
    await screen.findByText(/No family/)
    await new Promise((r) => setTimeout(r, 30))
    expect(load).toHaveBeenCalledTimes(3)
    expect(load.mock.calls.map((c) => c[0]).sort()).toEqual(['acquaintances', 'family', 'notable'])
  })

  it('switches category and shows only that categorys NPCs', async () => {
    const load = loader({
      family: list([npc({ id: 'npc_1', name: 'Mira Ashford' })]),
      acquaintances: list([npc({ id: 'npc_2', name: 'Tobin Reed', relations: ['friend'] })]),
      notable: list([npc({ id: 'named_sage', name: 'Old Wen', category: 'named', title: 'Village Sage' })]),
    })
    render(<PeopleTab load={load} />)
    await screen.findByText('Mira Ashford')
    fireEvent.click(tab(/Acquaintances/))
    expect(screen.getByText('Tobin Reed')).toBeTruthy()
    expect(screen.queryByText('Mira Ashford')).toBeNull()
    fireEvent.click(tab(/Notable Figures/))
    expect(screen.getByText('Old Wen')).toBeTruthy()
    expect(screen.queryByText('Tobin Reed')).toBeNull()
  })

  it('shows a sensible empty state for each empty group', async () => {
    render(<PeopleTab load={loader({})} />)
    expect(await screen.findByText('No family to show yet.')).toBeTruthy()
    fireEvent.click(tab(/Acquaintances/))
    expect(screen.getByText(/haven't built a personal relationship/)).toBeTruthy()
    fireEvent.click(tab(/Notable Figures/))
    expect(screen.getByText(/haven't come across anyone notable/)).toBeTruthy()
    expect(screen.queryByRole('list')).toBeNull()
  })

  it('shows each groups count on its button', async () => {
    const load = loader({ family: list([npc({ id: 'a', name: 'A' })], 1), acquaintances: list([], 0), notable: list([npc({ id: 'b', name: 'B' })], 7) })
    render(<PeopleTab load={load} />)
    await waitFor(() => expect(within(tab(/Notable Figures/)).getByText('7')).toBeTruthy())
    expect(within(tab(/Family/)).getByText('1')).toBeTruthy()
    expect(within(tab(/Acquaintances/)).getByText('0')).toBeTruthy()
  })

  it('lists the same NPC in every group it belongs to, but only once in each', async () => {
    const both = npc({ id: 'npc_9', name: 'Edda Marsh', relations: ['child', 'friend'], categories: ['family', 'acquaintances'] })
    const load = loader({ family: list([both, both]), acquaintances: list([both, both, both]) })
    render(<PeopleTab load={load} />)
    expect(await screen.findAllByText('Edda Marsh')).toHaveLength(1)
    fireEvent.click(tab(/Acquaintances/))
    expect(screen.getAllByText('Edda Marsh')).toHaveLength(1)
  })

  it('displays the information the game has: name, age, species, status, title and relation', async () => {
    const load = loader({
      notable: list([npc({
        id: 'named_sage', name: 'Old Wen', category: 'named', title: 'Village Sage', age: 71,
        species: { id: 'elf', name: 'Elf', category: 'humanoid' }, relations: ['mentor'],
      })]),
    })
    render(<PeopleTab load={load} />)
    await screen.findByText(/No family/)
    fireEvent.click(tab(/Notable Figures/))
    const entry = screen.getByText('Old Wen').closest('li')!
    const text = entry.textContent ?? ''
    for (const part of ['Old Wen', 'Village Sage', 'Alive', 'Age 71', 'Elf', 'Mentor']) expect(text).toContain(part)
  })

  it('does not invent gender, jobs or closeness that the data does not have', async () => {
    render(<PeopleTab load={loader({ family: list([npc({ id: 'npc_1', name: 'Mira Ashford' })]) })} />)
    const entry = (await screen.findByText('Mira Ashford')).closest('li')!
    expect(entry.textContent).not.toMatch(/male|female|gender|she|he\b/i)
    expect(within(entry).queryByRole('meter')).toBeNull()
    expect(entry.querySelector('.npc-title')).toBeNull()
  })

  it('draws a closeness bar only when closeness is provided, clamped to 0-100', async () => {
    const load = loader({
      acquaintances: list([
        npc({ id: 'a', name: 'Close Friend', closeness: 72 }),
        npc({ id: 'b', name: 'Strange Value', closeness: 400 }),
        npc({ id: 'c', name: 'No Record', closeness: null }),
        npc({ id: 'd', name: 'Zero Closeness', closeness: 0 }),
      ]),
    })
    render(<PeopleTab load={load} />)
    await screen.findByText(/No family/)
    fireEvent.click(tab(/Acquaintances/))
    const bar = (name: string) => within(screen.getByText(name).closest('li')!).queryByRole('meter')
    expect(bar('Close Friend')!.getAttribute('aria-valuenow')).toBe('72')
    expect(bar('Strange Value')!.getAttribute('aria-valuenow')).toBe('100')
    expect(bar('Zero Closeness')!.getAttribute('aria-valuenow')).toBe('0')
    expect(bar('No Record')).toBeNull()
  })

  it.each([
    ['active', 'Alive'], ['deceased', 'Deceased'], ['retired', 'Away'], ['something_new', 'Unknown'],
  ])('shows status %s as %s', async (status, label) => {
    const load = loader({ family: list([npc({ id: 'x', name: 'Someone', status: status as NpcSummary['status'] })]) })
    render(<PeopleTab load={load} />)
    const entry = (await screen.findByText('Someone')).closest('li')!
    expect(within(entry).getByText(label)).toBeTruthy()
  })

  it('shows how old the dead were when they died', async () => {
    const load = loader({ family: list([npc({ id: 'x', name: 'Gone', status: 'deceased', age: 66 })]) })
    render(<PeopleTab load={load} />)
    expect(await screen.findByText('Died at 66')).toBeTruthy()
  })

  it('says when only part of a long list is shown', async () => {
    const load = loader({ family: list([npc({ id: 'a', name: 'A' })], 130) })
    render(<PeopleTab load={load} />)
    expect(await screen.findByText('Showing 1 of 130.')).toBeTruthy()
  })

  it('reports a failed group, keeps the others usable, and can try again', async () => {
    let fail = true
    const load = vi.fn((g: NpcGroup) =>
      g === 'family' && fail ? Promise.reject(new Error('Server is down')) : Promise.resolve(list([npc({ id: g, name: `Person ${g}` })])),
    )
    render(<PeopleTab load={load} />)
    expect((await screen.findByRole('alert')).textContent).toContain('Server is down')
    fireEvent.click(tab(/Acquaintances/))
    expect(screen.getByText('Person acquaintances')).toBeTruthy()
    fireEvent.click(tab(/Family/))
    fail = false
    fireEvent.click(screen.getByText('Try again'))
    expect(await screen.findByText('Person family')).toBeTruthy()
  })

  it('ignores a response that arrives after the tab was closed', async () => {
    let resolve!: (l: NpcList) => void
    const load = vi.fn(() => new Promise<NpcList>((r) => { resolve = r }))
    const { unmount } = render(<PeopleTab load={load} />)
    unmount()
    expect(() => resolve(list([npc({ id: 'a', name: 'A' })]))).not.toThrow()
  })
})

describe('navigation', () => {
  const character = { alive: true } as Character

  it('adds a People tab that opens the list and follows the other tabs rules', () => {
    const onTabChange = vi.fn()
    const { rerender } = render(<NavSidebar activeTab="character" onTabChange={onTabChange} character={character} eventPending={false} />)
    fireEvent.click(screen.getByText('People'))
    expect(onTabChange).toHaveBeenCalledWith('people')
    for (const label of ['Character', 'Life', 'Power']) expect(screen.getByText(label)).toBeTruthy()   // nothing removed
    rerender(<NavSidebar activeTab="people" onTabChange={onTabChange} character={character} eventPending />)
    expect((screen.getByText('People').closest('button') as HTMLButtonElement).disabled).toBe(true)
    expect(screen.getByText('People').closest('button')!.className).toContain('active')
  })

  it('is unavailable while the character is dead', () => {
    render(<NavSidebar activeTab="character" onTabChange={() => {}} character={{ alive: false } as Character} eventPending={false} />)
    expect((screen.getByText('People').closest('button') as HTMLButtonElement).disabled).toBe(true)
  })
})
