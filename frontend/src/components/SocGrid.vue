<script setup>
import { computed } from 'vue'
import { kwh, pct } from '../lib/format.js'
import { socRows } from '../lib/soc.js'

const props = defineProps({ s: { type: Object, required: true } })

// The cell states live in lib/soc.js: five combinable states with two
// off-by-one traps, unit tested against the table in the dashboard spec.
const rows = computed(() => socRows(props.s))
</script>

<template>
  <section class="panel soc">
    <span class="panel-title">SOC</span>

    <div class="frame">
      <div v-for="row in rows" :key="row.start" class="row">
        <span class="axis dim">{{ String(row.start).padStart(2, '0') }}</span>
        <div
          v-for="c in row.cells"
          :key="c.p"
          class="cell"
          :class="{ on: c.on, partial: c.partial, locked: c.locked, hatched: c.hatched, discharge: c.discharge }"
          :title="`${c.p} %`"
        />
      </div>
    </div>

    <ul class="legend dim">
      <li><span class="cell on" /> charged</li>
      <li><span class="cell" /> free</li>
      <li><span class="cell on locked" /> locked (≤ MIN / &gt; MAX)</li>
      <li><span class="cell on hatched" /><span class="cell hatched" /> reserved</li>
      <li><span class="cell on discharge" /> always_discharge</li>
      <li><span class="cell on partial" /> current</li>
    </ul>

    <dl class="kv summary">
      <dt>RESERVED</dt>
      <dd>
        {{ kwh(s.reserved) }}
        <span v-if="s.reserved > s.reservedCapped" class="dim">(→ max. {{ kwh(s.reservedCapped) }} up to {{ pct(s.allowDischargeSoc, 0) }})</span>
      </dd>
      <dt>USABLE</dt>
      <dd>{{ kwh(s.storedUsable) }} <span class="dim">/ {{ kwh(s.usableCapacity) }}</span></dd>
      <dt>STORED</dt>
      <dd>{{ kwh(s.stored) }} <span class="dim">/ {{ kwh(s.capacity) }}</span></dd>
      <dt>LIMITS</dt>
      <dd>MIN {{ pct(s.minSoc, 0) }} · MAX {{ pct(s.maxSoc, 0) }}</dd>
      <dt>ALWAYS ALLOW DISCHARGE</dt>
      <dd class="violet">above {{ pct(s.allowDischargeSoc, 0) }}</dd>
    </dl>
  </section>
</template>

<style scoped>
/* Windows-9x defrag look: sunken frame, bevelled blocks */
.frame {
  display: flex;
  flex-direction: column;
  gap: 3px;
  padding: 6px;
  border: 2px solid;
  border-color: var(--cell-off-lo) var(--cell-off-hi) var(--cell-off-hi) var(--cell-off-lo);
  background: #010402;
  text-shadow: none;
}

.row {
  display: grid;
  grid-template-columns: 1.6em repeat(10, 1fr);
  gap: 3px;
  align-items: center;
}

.axis { font-size: 11px; text-align: right; padding-right: 2px; }

.cell {
  display: inline-block;
  aspect-ratio: 1;
  position: relative;
  background: var(--cell-off);
  border: 2px solid;
  border-color: var(--cell-off-hi) var(--cell-off-lo) var(--cell-off-lo) var(--cell-off-hi);
}

/* Above always_allow_discharge: same states, violet palette */
.cell.discharge {
  --cell-on: #c77dff;
  --cell-on-hi: #ecd4ff;
  --cell-on-lo: #7b2cbf;
  --cell-off: #1d0b2b;
  --cell-off-hi: #3b1b57;
  --cell-off-lo: #090310;
  --fg-glow: rgba(199, 125, 255, 0.55);
  --fg-dim: #9b5ed1;
}

.cell.on {
  background: var(--cell-on);
  border-color: var(--cell-on-hi) var(--cell-on-lo) var(--cell-on-lo) var(--cell-on-hi);
  box-shadow: 0 0 6px var(--fg-glow);
}

.cell.on.hatched {
  background: repeating-linear-gradient(45deg, var(--cell-on) 0 3px, var(--cell-on-lo) 3px 6px);
}

/* reserved but not (yet) charged: dark stripes */
.cell.hatched {
  background: repeating-linear-gradient(
    45deg,
    color-mix(in srgb, var(--cell-on-lo) 55%, var(--cell-off)) 0 2px,
    var(--cell-off) 2px 6px
  );
}

.cell.partial {
  background: var(--cell-on);
  border-color: var(--cell-on-hi) var(--cell-on-lo) var(--cell-on-lo) var(--cell-on-hi);
  animation: blink 1.2s steps(1) infinite;
}

.cell.partial.hatched {
  background: repeating-linear-gradient(45deg, var(--cell-on) 0 3px, var(--cell-on-lo) 3px 6px);
}

.cell.locked::after {
  content: '';
  position: absolute;
  inset: 0;
  margin: auto;
  width: 28%;
  height: 28%;
  border-radius: 50%;
  background: var(--fg-dim);
}
.cell.on.locked::after { background: var(--cell-off-lo); }

.violet { color: #c77dff; text-shadow: 0 0 6px rgba(199, 125, 255, 0.55); }

.legend {
  list-style: none;
  margin: 12px 0 0;
  padding: 0;
  display: flex;
  flex-wrap: wrap;
  gap: 4px 14px;
  font-size: 12px;
}
.legend li { display: flex; align-items: center; gap: 6px; }
.legend .cell { width: 14px; border-width: 1px; }

.summary {
  margin: 14px 0 0;
  padding-top: 10px;
  border-top: 1px dashed var(--fg-faint);
}
</style>
