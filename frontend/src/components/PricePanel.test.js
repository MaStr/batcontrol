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
    const wrapper = mount(PricePanel, {
      props: { p: { current: 0.3, intervalMinutes: 15, minDynamicDiff: 0.05, slots: slots() } },
    })
    const drawn = spark(wrapper)
    expect(drawn).toContain(BARS[0])
    expect(drawn).toContain(BARS[BARS.length - 1])
  })

  it('draws one bar per hour at either resolution', () => {
    for (const intervalMinutes of [15, 60]) {
      const wrapper = mount(PricePanel, {
        props: { p: { current: 0.3, intervalMinutes, minDynamicDiff: 0.05,
                      slots: slots({ intervalMinutes }) } },
      })
      expect(spark(wrapper)).toHaveLength(24)
    }
  })

  it('reports min and max from the slots, not from the hourly means', () => {
    const wrapper = mount(PricePanel, {
      props: { p: { current: 0.3, intervalMinutes: 15, minDynamicDiff: 0.05, slots: slots() } },
    })
    expect(wrapper.text()).toContain('0.050')
  })

  it('says so instead of drawing nothing when there is no forecast', () => {
    const wrapper = mount(PricePanel, {
      props: { p: { current: null, intervalMinutes: 60, minDynamicDiff: null, slots: [] } },
    })
    expect(wrapper.text()).toContain('no price forecast yet')
  })
})
