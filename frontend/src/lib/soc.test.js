import { describe, expect, it } from 'vitest'
import { socCells, socRows } from './soc.js'

const base = {
  soc: 50,
  minSoc: 10,
  maxSoc: 95,
  allowDischargeSoc: 90,
  capacity: 10000,
  reservedCapped: 0,
}

const cell = (state, p) => socCells(state)[p]

describe('socCells', () => {
  it('has one cell per percent, 0..99', () => {
    const cells = socCells(base)
    expect(cells).toHaveLength(100)
    expect(cells[0].p).toBe(0)
    expect(cells[99].p).toBe(99)
  })

  // The spec is explicit: 87.0 % -> cell 87 blinks, 87.1 % -> cell 88.
  it.each([
    [87.0, 87],
    [87.1, 88],
    [87.9, 88],
    [0.5, 1],
  ])('at %s %% the blinking cell is %i', (soc, expected) => {
    const cells = socCells({ ...base, soc })
    expect(cells.filter((c) => c.partial).map((c) => c.p)).toEqual([expected])
  })

  it('lights every cell and blinks none at 100 %', () => {
    const cells = socCells({ ...base, soc: 100, maxSoc: 100 })
    expect(cells.every((c) => c.on)).toBe(true)
    expect(cells.some((c) => c.partial)).toBe(false)
  })

  it('lights every cell from 99.1 % upwards, by design', () => {
    const cells = socCells({ ...base, soc: 99.1, maxSoc: 100 })
    expect(cells.every((c) => c.on)).toBe(true)
  })

  it('lights nothing at 0 %', () => {
    const cells = socCells({ ...base, soc: 0 })
    expect(cells.some((c) => c.on)).toBe(false)
    expect(cells.filter((c) => c.partial).map((c) => c.p)).toEqual([0])
  })

  it('blinks nothing when the SoC is unknown', () => {
    const cells = socCells({ ...base, soc: null })
    expect(cells.some((c) => c.partial)).toBe(false)
    expect(cells.some((c) => c.on)).toBe(false)
  })

  describe('locked', () => {
    it('locks everything up to and including MIN_SOC', () => {
      expect(cell(base, 10).locked).toBe(true)
      expect(cell(base, 11).locked).toBe(false)
    })

    it('locks above MAX_SOC only when MAX_SOC is below 100', () => {
      expect(cell(base, 96).locked).toBe(true)
      expect(cell({ ...base, maxSoc: 100 }, 96).locked).toBe(false)
    })

    it('can be locked and lit at the same time', () => {
      const c = cell({ ...base, soc: 50 }, 5)
      expect([c.on, c.locked]).toEqual([true, true])
    })
  })

  describe('reserved', () => {
    // 2000 Wh of 10000 Wh is 20 %, starting at MIN_SOC 10 -> up to 30 %.
    const reserved = { ...base, reservedCapped: 2000 }

    it('hatches every cell the reservation reaches into', () => {
      expect(cell(reserved, 11).hatched).toBe(true)
      expect(cell(reserved, 30).hatched).toBe(true)
      expect(cell(reserved, 31).hatched).toBe(false)
    })

    it('never hatches at or below MIN_SOC', () => {
      expect(cell(reserved, 10).hatched).toBe(false)
      expect(cell(reserved, 0).hatched).toBe(false)
    })

    it('hatches nothing without a reservation', () => {
      expect(socCells(base).some((c) => c.hatched)).toBe(false)
    })

    it('hatches the blinking cell too when the reservation reaches it', () => {
      const c = cell({ ...reserved, soc: 25 }, 25)
      expect([c.partial, c.hatched]).toEqual([true, true])
    })
  })

  describe('always_discharge', () => {
    it('marks everything above the limit', () => {
      expect(cell(base, 90).discharge).toBe(false)
      expect(cell(base, 91).discharge).toBe(true)
    })

    it('marks nothing when the limit is unknown', () => {
      expect(socCells({ ...base, allowDischargeSoc: null })
        .some((c) => c.discharge)).toBe(false)
    })
  })
})

describe('socRows', () => {
  it('puts cell 0 bottom left and cell 99 top right', () => {
    const rows = socRows(base)
    expect(rows).toHaveLength(10)
    expect(rows[0].cells[0].p).toBe(90) // top row starts at 90
    expect(rows[0].cells[9].p).toBe(99) // 99 is top right
    expect(rows[9].cells[0].p).toBe(0) // 0 is bottom left
    expect(rows[9].start).toBe(0)
  })

  it('labels each row with its decade', () => {
    expect(socRows(base).map((r) => r.start)).toEqual([90, 80, 70, 60, 50, 40, 30, 20, 10, 0])
  })
})

describe('unknown limits', () => {
  // Before the inverter has been read the API reports soc_limits as null.
  const unknown = { ...base, minSoc: null, maxSoc: null }

  it('locks nothing when no limit is known', () => {
    // An unknown limit is not a limit of zero: cell 0 used to render as
    // "locked (<= MIN)" on a backend that reports no min_soc.
    expect(socCells(unknown).some((c) => c.locked)).toBe(false)
  })

  it('still lights and blinks the right cells', () => {
    const cells = socCells({ ...unknown, soc: 50 })
    expect(cells.filter((c) => c.partial).map((c) => c.p)).toEqual([50])
    expect(cells[49].on).toBe(true)
  })

  it('treats a known minSoc of 0 as a real limit', () => {
    expect(cell({ ...base, minSoc: 0 }, 0).locked).toBe(true)
  })

  it('hatches from the floor when the limit is unknown', () => {
    const cells = socCells({ ...unknown, reservedCapped: 1000 })  // 10 % of 10000
    expect(cells[1].hatched).toBe(true)
    expect(cells[10].hatched).toBe(true)
    expect(cells[11].hatched).toBe(false)
  })
})
