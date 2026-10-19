import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import PricePanel from './PricePanel.vue'

const BARS = '▁▂▃▄▅▆▇█'

/** A day of 15-minute slots with a single deep dip inside one hour. */
function slots({ intervalMinutes = 15, hours = 24 } = {}) {
  const perHour = 60 / intervalMinutes
  const start = Date.UTC(2026, 0, 1)
  return Array.from({ length: hours * perHour }, (_, i) => ({
    start: start + i * intervalMinutes * 60_000,
    // one quarter-hour far below everything else
    price: i === 5 ? 0.05 : 0.30 + (i % 8) * 0.001,
  }))
}

/** MIN/MAX/SPREAD the way api.js maps them from /price/status. */
function stats(list) {
  let min = list[0]
  let max = list[0]
  for (const s of list) {
    if (s.price < min.price) min = s
    if (s.price > max.price) max = s
  }
  return { min, max, spread: max.price - min.price }
}

function props(options = {}, extra = {}) {
  const list = slots(options)
  return {
    p: { current: 0.3, intervalMinutes: options.intervalMinutes ?? 15, minDynamicDiff: 0.05,
         slots: list, stats: stats(list), ...extra },
  }
}

function spark(wrapper) {
  return wrapper.find('.spark').text()
}

describe('PricePanel sparkline', () => {
  it('uses the full glyph range', () => {
    // Regression: the bars were scaled against the per-slot MIN/MAX while
    // the bars themselves are hourly means. At 15-minute resolution the
    // means sit strictly inside those extremes, so the lowest and highest
    // glyphs were never reached and the curve understated the spread
    // printed right beside it.
    const wrapper = mount(PricePanel, { props: props() })
    const drawn = spark(wrapper)
    expect(drawn).toContain(BARS[0])
    expect(drawn).toContain(BARS[BARS.length - 1])
  })

  it('draws one bar per hour at either resolution', () => {
    for (const intervalMinutes of [15, 60]) {
      const wrapper = mount(PricePanel, { props: props({ intervalMinutes }) })
      expect(spark(wrapper)).toHaveLength(24)
    }
  })

  it('reports min and max per slot, not from the hourly means', () => {
    const wrapper = mount(PricePanel, { props: props() })
    expect(wrapper.text()).toContain('0.050')
  })

  it('prints the statistics the API computed', () => {
    const wrapper = mount(PricePanel, {
      props: props({}, { stats: {
        min: { start: Date.UTC(2026, 0, 1), price: 0.123 },
        max: { start: Date.UTC(2026, 0, 1), price: 0.456 },
        spread: 0.333,
      } }),
    })
    expect(wrapper.text()).toContain('0.123')
    expect(wrapper.text()).toContain('0.456')
    expect(wrapper.text()).toContain('0.333')
  })

  it('says so instead of drawing nothing when there is no forecast', () => {
    const wrapper = mount(PricePanel, {
      props: { p: { current: null, intervalMinutes: 60, minDynamicDiff: null, slots: [],
                    stats: null } },
    })
    expect(wrapper.text()).toContain('no price forecast yet')
  })
})
