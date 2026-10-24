import { describe, expect, it } from 'vitest'
import { GROUPS, ageText, closenessPercent, relationWords, statusInfo, uniqueById } from './npcFormat'

describe('statusInfo', () => {
  it('maps each lifecycle status to wording', () => {
    expect(statusInfo('active').label).toBe('Alive')
    expect(statusInfo('deceased').label).toBe('Deceased')
    expect(statusInfo('retired').label).toBe('Away')
  })
  it('shows anything unrecognised as Unknown instead of guessing', () => {
    expect(statusInfo('missing_in_action')).toMatchObject({ label: 'Unknown', tone: 'unknown' })
    expect(statusInfo('').label).toBe('Unknown')
  })
})

describe('closenessPercent', () => {
  it('returns null when there is nothing to draw', () => {
    expect(closenessPercent(null)).toBeNull()
    expect(closenessPercent(undefined)).toBeNull()
    expect(closenessPercent(Number.NaN)).toBeNull()
    expect(closenessPercent(Infinity)).toBeNull()
  })
  it('keeps real values, clamped to 0-100, and 0 is a real value', () => {
    expect(closenessPercent(0)).toBe(0)
    expect(closenessPercent(42)).toBe(42)
    expect(closenessPercent(180)).toBe(100)
    expect(closenessPercent(-5)).toBe(0)
  })
})

describe('ageText', () => {
  it('says "Died at" for the dead and "Age" for everyone else', () => {
    expect(ageText({ age: 40, status: 'active' })).toBe('Age 40')
    expect(ageText({ age: 40, status: 'retired' })).toBe('Age 40')
    expect(ageText({ age: 71, status: 'deceased' })).toBe('Died at 71')
  })
})

describe('relationWords', () => {
  it('reads link names as words, without repeats', () => {
    expect(relationWords(['child', 'friend', 'child'])).toEqual(['Child', 'Friend'])
    expect(relationWords(['half_sibling'])).toEqual(['Half-sibling'])
    expect(relationWords([])).toEqual([])
  })
})

describe('uniqueById', () => {
  it('keeps the first of each id, in order', () => {
    const out = uniqueById([{ id: 'a', n: 1 }, { id: 'b', n: 2 }, { id: 'a', n: 3 }])
    expect(out).toEqual([{ id: 'a', n: 1 }, { id: 'b', n: 2 }])
  })
})

describe('GROUPS', () => {
  it('are the three groups, each with its own empty state', () => {
    expect(GROUPS.map((g) => g.id)).toEqual(['family', 'acquaintances', 'notable'])
    expect(new Set(GROUPS.map((g) => g.empty)).size).toBe(3)
  })
})
