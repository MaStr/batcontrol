import { afterEach, describe, expect, it, vi } from 'vitest'
import { fetchAll, getToken, normalize, setToken, UnauthorizedError } from './api.js'

import { loadFixture } from '../../fixtures/load.js'

const defaultFixture = loadFixture('default')
const fullFixture = loadFixture('battery-full')
const lowFixture = loadFixture('battery-low')
const overFixture = loadFixture('reserved-over-capacity')
const noDataFixture = loadFixture('no-data')
const offlineFixture = loadFixture('offline')

/** Feed a fixture through normalize() the way fetchAll() would. */
function view(fixture) {
  const payload = (key) => (fixture[key]?.ok ? fixture[key].data : null)
  return normalize({
    status: payload('status'),
    battery: payload('battery'),
    current: payload('current'),
    price: payload('forecast/price'),
    priceStatus: payload('price/status'),
    decision: payload('decision'),
    version: payload('version'),
  })
}

describe('normalize', () => {
  it('maps the default scenario', () => {
    const v = view(defaultFixture)
    expect(v.online).toBe(true)
    expect(typeof v.soc).toBe('number')
    expect(v.modeLabel).toBeTruthy()
    expect(v.version).toMatch(/\d+\.\d+/)
  })

  it('keeps numbers as numbers', () => {
    const v = view(defaultFixture)
    for (const key of ['soc', 'capacity', 'stored', 'storedUsable', 'reserved']) {
      expect(typeof v[key]).toBe('number')
    }
    expect(typeof v.price.current).toBe('number')
  })

  it('takes the SoC limits from the API instead of assuming them', () => {
    // The prototype hardcoded MIN_SOC 7 / MAX_SOC 100 because the Node-RED
    // bridge did not expose them. They are per-inverter values.
    const v = view(defaultFixture)
    expect(v.minSoc).toBe(defaultFixture.battery.data.soc_limits.min_soc)
    expect(v.maxSoc).toBe(defaultFixture.battery.data.soc_limits.max_soc)
  })

  it('uses the gross capacity, not max_energy_capacity', () => {
    // Regression: max_energy_capacity is max_soc/100 * gross. Treating it as
    // the gross capacity applied max_soc twice (8.08 instead of 8.5 kWh
    // usable). The API now reports the gross capacity itself.
    const v = view(defaultFixture)
    const { max_soc: maxSoc, min_soc: minSoc } = defaultFixture.battery.data.soc_limits
    const gross = (defaultFixture.battery.data.max_energy_capacity * 100) / maxSoc
    expect(maxSoc).toBeLessThan(100)
    expect(defaultFixture.battery.data.gross_energy_capacity).toBeCloseTo(gross, 6)
    expect(v.capacity).toBeCloseTo(gross, 6)
    expect(v.usableCapacity).toBeCloseTo((gross * (maxSoc - minSoc)) / 100, 6)
    // stored_energy_capacity is soc * gross, so the two must agree
    expect(v.stored).toBeCloseTo((v.soc * v.capacity) / 100, 6)
  })

  it.each(['default', 'battery-full', 'battery-low', 'reserved-over-capacity', 'offline'])(
    'fixture %s is consistent with the inverter math',
    (name) => {
      // stored = soc * gross, usable = stored above min_soc, and the API's
      // max_energy_capacity = gross * max_soc. A fixture that breaks this
      // tests the rendering against numbers no inverter produces.
      const v = view(loadFixture(name))
      expect(v.stored).toBeCloseTo((v.soc * v.capacity) / 100, 6)
      expect(v.storedUsable).toBeCloseTo(
        Math.max(0, v.stored - (v.minSoc * v.capacity) / 100), 6)
    },
  )

  it('derives usable capacity from the real limits', () => {
    const v = view(defaultFixture)
    expect(v.usableCapacity).toBeCloseTo((v.capacity * (v.maxSoc - v.minSoc)) / 100, 6)
  })

  it('exposes the price forecast from the API, not a mock', () => {
    const v = view(defaultFixture)
    expect(v.price.slots.length).toBeGreaterThan(0)
    expect(v.price.slots[0]).toHaveProperty('start')
    expect(v.price.slots[0]).toHaveProperty('price')
    // slot 0 is the current price
    expect(v.price.slots[0].price).toBeCloseTo(v.price.current, 6)
  })

  it('reports the decision with its timestamp as epoch ms', () => {
    const v = view(defaultFixture)
    expect(v.decision).toBeTruthy()
    expect(typeof v.decisionTs).toBe('number')
    expect(v.decisionTs).toBeGreaterThan(1e12)
    expect(v.reason).toBeTruthy()
  })

  it('passes the trace through for the trace view', () => {
    const v = view(defaultFixture)
    expect(Array.isArray(v.trace.records)).toBe(true)
  })

  describe('a full battery', () => {
    it('has no free capacity and no reservation', () => {
      const v = view(fullFixture)
      expect(v.soc).toBe(100)
      expect(v.reservedCapped).toBe(0)
    })
  })

  describe('a nearly empty battery in force charge', () => {
    it('reports the charge rate and the amber mode', () => {
      const v = view(lowFixture)
      expect(v.soc).toBe(3)
      expect(v.mode).toBe('-1')
      expect(v.modeLabel).toBe('FORCE CHARGE')
      expect(v.chargeRate).toBeGreaterThan(0)
    })
  })

  describe('a reservation larger than the battery', () => {
    it('takes the cap at always_allow_discharge from the API', () => {
      const v = view(overFixture)
      expect(v.reserved).toBeGreaterThan(v.capacity)
      expect(v.reservedCapped).toBeLessThan(v.reserved)
      const ceiling = (v.capacity * (Math.min(v.maxSoc, v.allowDischargeSoc) - v.minSoc)) / 100
      expect(v.reservedCapped).toBeCloseTo(ceiling, 6)
      expect(v.reservedCapped).toBe(
        overFixture.battery.data.reserved_energy_capacity_effective)
    })
  })

  describe('the price statistics', () => {
    it('come from /price/status', () => {
      const v = view(defaultFixture)
      const status = defaultFixture['price/status'].data
      expect(v.price.stats.min).toEqual({ start: status.min.time_start, price: status.min.value })
      expect(v.price.stats.max).toEqual({ start: status.max.time_start, price: status.max.value })
      expect(v.price.stats.spread).toBe(status.spread)
    })

    it('agree with the forecast the fixture was taken from', () => {
      // The fixture's status is derived from its own forecast; if the two
      // drift apart, the dashboard renders a MIN that is not in the curve.
      const v = view(defaultFixture)
      const day = v.price.slots.slice(0, 24).map((s) => s.price)
      expect(v.price.stats.min.price).toBe(Math.min(...day))
      expect(v.price.stats.max.price).toBe(Math.max(...day))
    })

    it('are absent before the first cycle', () => {
      expect(view(noDataFixture).price.stats).toBeNull()
    })
  })

  describe('before the first control cycle', () => {
    it('renders status and battery and leaves the rest empty', () => {
      const v = view(noDataFixture)
      expect(v.modeLabel).toBeTruthy()
      expect(v.price.current).toBeNull()
      expect(v.price.slots).toEqual([])
      expect(v.decision).toBeNull()
      expect(v.trace).toBeNull()
    })

    it('does not invent a price', () => {
      // The prototype kept the last seen price and showed it as
      // "CURRENT - LAST KNOWN"; with a real forecast endpoint there is
      // nothing to guess.
      expect(view(noDataFixture).price.current).toBeNull()
    })
  })

  describe('a stalled control loop', () => {
    it('is reported as offline', () => {
      expect(view(offlineFixture).online).toBe(false)
    })
  })

  it('survives a missing mode', () => {
    const v = normalize({
      status: { mode: null, mode_label: '', online: true },
      battery: { soc_limits: {} },
    })
    expect(v.modeLabel).toMatch(/MODE|UNKNOWN/)
    expect(v.soc).toBeUndefined()
  })
})

describe('the decision trace', () => {
  const base = defaultFixture.decision.data

  it('renders the records of the latest evaluation, not of the older event', () => {
    // The event is the last status *change* and can be up to 15 minutes old.
    const stale = {
      ...base,
      event: { ...base.event, records: [{ decision: 'stale', outcome: 'old', reason: 'OLD' }],
               timestamp: '2020-01-01T00:00:00' },
    }
    const v = normalize({
      status: defaultFixture.status.data,
      battery: defaultFixture.battery.data,
      decision: stale,
    })
    expect(v.trace.records).toEqual(base.trace.records)
    expect(v.trace.records.some((r) => r.reason === 'OLD')).toBe(false)
    expect(v.trace.timestamp).toBe(base.trace.timestamp)
  })

  it('still carries the mode transition, which only the event knows', () => {
    const v = normalize({
      status: defaultFixture.status.data,
      battery: defaultFixture.battery.data,
      decision: base,
    })
    expect(v.trace.kind).toBe(base.event.kind)
    expect(v.trace).toHaveProperty('previous_mode')
  })
})

describe('the stored token', () => {
  afterEach(() => {
    vi.unstubAllGlobals()
    setToken('')
  })

  it('is dropped, not sent, when a header cannot carry it', async () => {
    // Regression: fetch() threw a TypeError for a non-Latin-1 token, which
    // was shown as "unreachable" and never brought the token prompt back.
    const fetch = vi.fn()
    vi.stubGlobal('fetch', fetch)
    setToken('geheim\u20ac')
    await expect(fetchAll()).rejects.toBeInstanceOf(UnauthorizedError)
    expect(fetch).not.toHaveBeenCalled()
    expect(getToken()).toBe('')
  })
})
