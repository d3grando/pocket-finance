import { describe, expect, it } from 'vitest'
import { REMOTE_DEBUGGING_SWITCHES, stripRemoteDebugging, type CommandLinePort } from '../../src/main/cli-hardening'

function cmdline(switches: string[]) {
  const set = new Set(switches)
  const port: CommandLinePort = {
    hasSwitch: (n) => set.has(n),
    removeSwitch: (n) => {
      set.delete(n)
    },
  }
  return { set, port }
}

describe('stripRemoteDebugging', () => {
  it('packaged: removes every remote-debugging switch, keeps the rest', () => {
    const { set, port } = cmdline(['remote-debugging-port', 'remote-debugging-pipe', 'remote-allow-origins', 'hidden', 'no-sandbox'])
    const removed = stripRemoteDebugging(port, { packaged: true, inspectorActive: false })
    expect(removed.sort()).toEqual(['remote-allow-origins', 'remote-debugging-pipe', 'remote-debugging-port'])
    expect([...set].sort()).toEqual(['hidden', 'no-sandbox'])
  })

  it('covers the address switch too', () => {
    expect(REMOTE_DEBUGGING_SWITCHES).toContain('remote-debugging-address')
  })

  it('dev (unpacked) runs keep DevTools switches for debugging', () => {
    const { set, port } = cmdline(['remote-debugging-port'])
    expect(stripRemoteDebugging(port, { packaged: false, inspectorActive: false })).toEqual([])
    expect(set.has('remote-debugging-port')).toBe(true)
  })

  it('packaged with an active Node inspector (smoke copy with the inspect fuse flipped back) is left alone', () => {
    const { set, port } = cmdline(['remote-debugging-port'])
    expect(stripRemoteDebugging(port, { packaged: true, inspectorActive: true })).toEqual([])
    expect(set.has('remote-debugging-port')).toBe(true)
  })
})
