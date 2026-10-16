<script setup>
import { computed, reactive } from 'vue'
import { MODES } from '../lib/api.js'
import { clock, eur } from '../lib/format.js'

const props = defineProps({ trace: { type: Object, required: true } })

const isSlotList = (v) => Array.isArray(v) && v.length > 0 && v.every(Number.isInteger)

// Assumption: slot 0 is the interval containing the decision timestamp.
function slotTime(i, intervalMin) {
  const ms = intervalMin * 60 * 1000
  const base = Math.floor(new Date(props.trace.timestamp).getTime() / ms) * ms
  const from = new Date(base + i * ms)
  const to = new Date(base + (i + 1) * ms)
  const day = from.toLocaleDateString('en-GB', { weekday: 'short' })
  return `${day} ${clock(from)}–${clock(to)}`
}

function fmt(key, v, inputs) {
  if (v == null) return '—'
  if (Array.isArray(v)) return v.length ? v.join(', ') : '[]'
  if (typeof v === 'object') return JSON.stringify(v)
  if (typeof v === 'boolean') return v ? 'yes' : 'no'
  if (typeof v !== 'number') return String(v)
  if (/mode$/.test(key) && MODES[String(v)]) return `${v} (${MODES[String(v)]})`
  if (/_slot$|window_end$/.test(key) && inputs?.interval_minutes)
    return `${v} (${slotTime(v, inputs.interval_minutes)})`
  if (/price/.test(key)) return eur(v)
  if (/energy|_wh$|demand/.test(key)) return `${Math.round(v)} Wh`
  if (key === 'charge_rate' || key === 'value') return `${Math.round(v)} W`
  if (key === 'remaining_time') return `${v} h`
  if (key === 'interval_minutes') return `${v} min`
  return Number.isInteger(v) ? String(v) : String(+v.toFixed(3))
}

const header = computed(() => {
  const t = props.trace
  const rows = [
    ['timestamp', t.timestamp?.replace('T', ' ').replace(/\.\d+/, '')],
    ['kind', t.kind],
    ['control_source', t.control_source],
    ['mode', `${fmt('mode', t.previous_mode)} → ${fmt('mode', t.mode)}`],
  ]
  if (t.value != null || t.previous_value != null)
    rows.push(['value', `${fmt('value', t.previous_value)} → ${fmt('value', t.value)}`])
  return rows
})

const steps = computed(() =>
  (props.trace.records || []).map((r, i) => {
    const inputs = r.inputs || {}
    const interval = inputs.interval_minutes || 15
    return {
      n: i + 1,
      decision: r.decision,
      outcome: r.outcome,
      reason: r.reason,
      why: r.why,
      decisive: r.decisive,
      inputs: Object.entries(inputs).map(([k, v]) =>
        isSlotList(v)
          ? { k, id: `${i}:${k}`, slots: v.map((s) => ({ s, time: slotTime(s, interval) })) }
          : { k, id: `${i}:${k}`, text: fmt(k, v, inputs) },
      ),
    }
  }),
)

// which slot lists are expanded
const open = reactive({})
</script>

<template>
  <div class="trace">
    <dl class="rows">
      <template v-for="[k, v] in header" :key="k">
        <dt>{{ k }}</dt>
        <dd>{{ v }}</dd>
      </template>
    </dl>

    <ol class="steps">
      <li v-for="st in steps" :key="st.n" :class="{ decisive: st.decisive }">
        <div class="head">
          <span class="dim">#{{ st.n }}</span>
          {{ st.decision }} → <b>{{ st.outcome }}</b>&#32;<span class="dim">[{{ st.reason }}]</span>
          <span v-if="st.decisive" class="tag">◄ DECISIVE</span>
        </div>
        <p v-if="st.why" class="why dim">{{ st.why }}</p>
        <dl class="rows">
          <template v-for="inp in st.inputs" :key="inp.id">
            <dt>{{ inp.k }}</dt>
            <dd v-if="!inp.slots">{{ inp.text }}</dd>
            <dd v-else>
              <button class="toggle" :class="{ active: open[inp.id] }" :aria-expanded="!!open[inp.id]" @click="open[inp.id] = !open[inp.id]">
                &gt;{{ inp.slots.length }} SLOT{{ inp.slots.length === 1 ? '' : 'S' }}&lt;
              </button>
              <ol v-if="open[inp.id]" class="slots">
                <li v-for="sl in inp.slots" :key="sl.s">
                  <span class="dim idx">{{ sl.s }}</span>{{ sl.time }}
                </li>
              </ol>
            </dd>
          </template>
        </dl>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.trace {
  margin-top: 12px;
  padding: 10px 12px;
  border: 1px dashed var(--fg-faint);
  font-size: 12px;
  max-height: 60vh;
  overflow-y: auto;
}
.rows {
  display: grid;
  grid-template-columns: minmax(0, max-content) 1fr;
  gap: 1px 12px;
  margin: 0;
}
.rows dt { color: var(--fg-dim); text-shadow: none; overflow-wrap: anywhere; }
.rows dd { margin: 0; overflow-wrap: anywhere; }

.steps { list-style: none; margin: 10px 0 0; padding: 0; }
.steps > li {
  padding: 8px 0 8px 10px;
  border-left: 2px solid var(--fg-faint);
  margin-top: 8px;
}
.steps > li.decisive { border-left-color: var(--fg); }
.head { margin-bottom: 2px; overflow-wrap: anywhere; }
.tag { color: var(--amber); text-shadow: 0 0 6px rgba(255, 204, 51, 0.5); margin-left: 4px; }
.why { margin: 0 0 6px; }
.steps .rows { padding-left: 4px; }

.slots {
  list-style: none;
  margin: 4px 0 6px;
  padding: 0;
}
.slots .idx { display: inline-block; width: 4ch; text-align: right; margin-right: 1ch; }

/* phones: key above value instead of two cramped columns */
@media (max-width: 480px) {
  .rows { grid-template-columns: 1fr; }
  .rows dd { padding-left: 2ch; margin-bottom: 3px; }
}
</style>
