<script setup>
import { computed } from 'vue'
import { eur, clock } from '../lib/format.js'

const props = defineProps({ p: { type: Object, required: true } })

const BARS = '▁▂▃▄▅▆▇█'

const HOURS = 24

const stats = computed(() => {
  // Only the next 24 h: MIN/MAX/SPREAD are labelled "24h" and the sparkline
  // has 24 characters. The forecast horizon itself can be longer.
  const perHour = Math.max(1, Math.round(60 / (props.p.intervalMinutes || 60)))
  const slots = (props.p.slots || []).slice(0, HOURS * perHour)
  if (!slots.length) return null

  let min = slots[0]
  let max = slots[0]
  for (const s of slots) {
    if (s.price < min.price) min = s
    if (s.price > max.price) max = s
  }

  // Hourly means, so the sparkline reads the same at 15- and 60-minute
  // resolution instead of being four times as wide in one of them.
  const hours = []
  for (let i = 0; i < slots.length; i += perHour) {
    const chunk = slots.slice(i, i + perHour)
    hours.push(chunk.reduce((a, s) => a + s.price, 0) / chunk.length)
  }

  // Normalise against the hourly means, not against the per-slot MIN/MAX
  // printed below: at 15-minute resolution the means sit strictly inside
  // [min, max], so scaling them by the slot extremes would never reach the
  // lowest or highest glyph and would visually understate the spread.
  const lowestHour = Math.min(...hours)
  const highestHour = Math.max(...hours)
  const range = highestHour - lowestHour || 1
  const spark = hours
    .map((v) => BARS[Math.round(((v - lowestHour) / range) * (BARS.length - 1))])
    .join('')
  return { min, max, spread: max.price - min.price, spark }
})
</script>

<template>
  <section class="panel price">
    <span class="panel-title">PRICE</span>
    <div class="now">
      <div class="label dim">CURRENT</div>
      <div class="big cur">{{ eur(p.current) }}<span class="unit dim">/kWh</span></div>
    </div>

    <p v-if="!stats" class="dim waiting">no price forecast yet</p>

    <template v-if="stats">
      <div class="chart" aria-hidden="true">
        <div class="spark">{{ stats.spark }}</div>
        <div class="axis dim"><span>now</span><span>+12h</span><span>+24h</span></div>
      </div>

      <dl class="kv">
        <dt>MIN 24h</dt>
        <dd>{{ eur(stats.min.price) }} <span class="dim">@ {{ clock(stats.min.start) }}</span></dd>
        <dt>MAX 24h</dt>
        <dd>{{ eur(stats.max.price) }} <span class="dim">@ {{ clock(stats.max.start) }}</span></dd>
        <dt>SPREAD 24h</dt>
        <dd>{{ eur(stats.spread) }}</dd>
        <dt>GRID_CHARGE Δ</dt>
        <dd>{{ eur(p.minDynamicDiff) }}</dd>
      </dl>
    </template>
  </section>
</template>

<style scoped>
.waiting { margin: 10px 0 0; font-size: 12px; }
.label { font-size: 11px; letter-spacing: 0.15em; }
.cur { font-size: 44px; }
.unit { font-size: 20px; margin-left: 4px; }

.chart {
  display: inline-block;
  max-width: 100%;
  margin: 10px 0 12px;
}
.spark {
  font-size: 22px;
  line-height: 1;
  letter-spacing: 0;
  white-space: nowrap;
  overflow: hidden;
}
.axis {
  display: flex;
  justify-content: space-between;
  font-size: 11px;
  margin-top: 2px;
}
.kv {
  margin: 0;
  padding-top: 10px;
  border-top: 1px dashed var(--fg-faint);
}
</style>
