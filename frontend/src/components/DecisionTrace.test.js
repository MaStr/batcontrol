import { mount } from '@vue/test-utils'
import { describe, expect, it } from 'vitest'

import DecisionTrace from './DecisionTrace.vue'
import defaultFixture from '../../fixtures/default.json'

/** The trace the dashboard passes in: the status-change event, which spreads
 *  the trace and adds the mode transition. */
const trace = defaultFixture.decision.data.event ?? defaultFixture.decision.data.trace

function render(payload = trace) {
  return mount(DecisionTrace, { props: { trace: payload } })
}

describe('DecisionTrace', () => {
  it('renders the header of the real API payload', () => {
    const text = render().text()
    expect(text).toContain('timestamp')
    expect(text).toContain('control_source')
    expect(text).toContain('mode')
  })

  it('renders one step per record', () => {
    const steps = render().findAll('.steps > li')
    expect(steps.length).toBe(trace.records.length)
    expect(steps.length).toBeGreaterThan(0)
  })

  it('marks the decisive step', () => {
    const wrapper = render()
    const decisive = wrapper.findAll('.steps > li.decisive')
    expect(decisive.length).toBe(trace.records.filter((r) => r.decisive).length)
    expect(decisive[0].text()).toContain('DECISIVE')
  })

  it('translates a mode number into its label', () => {
    expect(render().text()).toMatch(/ALLOW DISCHARGE|AVOID DISCHARGE|FORCE CHARGE|LIMIT CHARGE RATE/)
  })

  it('formats prices, energies and powers with their unit', () => {
    const text = render().text()
    const inputs = trace.records.flatMap((r) => Object.keys(r.inputs || {}))
    if (inputs.some((k) => /price/.test(k))) expect(text).toMatch(/\d+\.\d{3} €/)
    if (inputs.some((k) => /energy|_wh$|demand/.test(k))) expect(text).toMatch(/\d+ Wh/)
  })

  it('collapses a long slot list behind a toggle instead of printing it', async () => {
    const withSlots = {
      ...trace,
      records: [
        {
          decision: 'grid_recharge',
          outcome: 'no_charge',
          reason: 'NO_CHEAPER_SLOT',
          decisive: true,
          inputs: {
            interval_minutes: 60,
            higher_price_slots: Array.from({ length: 42 }, (_, i) => i),
          },
        },
      ],
    }
    const wrapper = render(withSlots)
    const toggle = wrapper.find('.toggle')
    expect(toggle.text()).toContain('42 SLOTS')
    expect(wrapper.find('.slots').exists()).toBe(false)

    await toggle.trigger('click')
    const items = wrapper.findAll('.slots li')
    expect(items.length).toBe(42)
    // each line carries a from-to time
    expect(items[0].text()).toMatch(/\d{2}:\d{2}.\d{2}:\d{2}/)
  })

  it('survives a record without inputs', () => {
    const bare = { ...trace, records: [{ decision: 'mode', outcome: 'allow_discharging', reason: 'UNSPECIFIED' }] }
    expect(() => render(bare)).not.toThrow()
  })
})
