import { afterEach, expect, test, vi } from 'vitest'

import { followSystemTheme } from './theme'

function systemIsDark(dark: boolean) {
  const listeners: Array<() => void> = []
  const query = {
    matches: dark,
    addEventListener: (_: string, fn: () => void) => listeners.push(fn),
    removeEventListener: (_: string, fn: () => void) => listeners.splice(listeners.indexOf(fn), 1),
  }
  vi.stubGlobal('matchMedia', () => query)
  return {
    change(next: boolean) {
      query.matches = next
      listeners.forEach((fn) => fn())
    },
    listeners,
  }
}

afterEach(() => {
  document.documentElement.classList.remove('dark')
  vi.unstubAllGlobals()
})

test('a dark system gets the dark class and a light one does not', () => {
  systemIsDark(true)
  followSystemTheme()
  expect(document.documentElement).toHaveClass('dark')

  systemIsDark(false)
  followSystemTheme()
  expect(document.documentElement).not.toHaveClass('dark')
})

test('the class follows the system while the app runs, and stops when told to', () => {
  const system = systemIsDark(false)
  const stop = followSystemTheme()

  system.change(true)
  expect(document.documentElement).toHaveClass('dark')
  system.change(false)
  expect(document.documentElement).not.toHaveClass('dark')

  stop()
  expect(system.listeners).toHaveLength(0)
})
