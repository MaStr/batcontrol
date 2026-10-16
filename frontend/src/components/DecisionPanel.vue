<script setup>
import { ref } from 'vue'
import { clock, ago, pct } from '../lib/format.js'
import DecisionTrace from './DecisionTrace.vue'

defineProps({
  s: { type: Object, required: true },
  now: { type: Number, required: true },
})

const showTrace = ref(false)
</script>

<template>
  <section class="panel decision">
    <span class="panel-title">DECISION</span>

    <p v-if="s.decisionTs" class="since dim">since {{ clock(s.decisionTs) }} · {{ ago(s.decisionTs, now) }}</p>
    <p class="text">&gt; {{ s.decision || 'waiting for the first decision' }}<span class="cursor">█</span></p>
    <div class="meta">
      <span v-if="s.reason" class="reason dim">[{{ s.reason }}]</span>
      <button
        v-if="s.trace?.records?.length"
        class="toggle trace-btn"
        :class="{ active: showTrace }"
        :aria-expanded="showTrace"
        @click="showTrace = !showTrace"
      >&gt;TRACE&lt;</button>
    </div>
    <DecisionTrace v-if="showTrace" :trace="s.trace" />

    <div class="state">
      <div>
        <div class="label dim">MODE</div>
        <div class="big mode" :class="'m' + s.mode.replace('-', 'n')">{{ s.modeLabel }}</div>
        <div v-if="s.modeTs" class="dim small">since {{ clock(s.modeTs) }} · {{ ago(s.modeTs, now) }}</div>
      </div>
      <div class="soc">
        <div class="label dim">SOC</div>
        <div class="big socval">{{ pct(s.soc) }}</div>
      </div>
    </div>
  </section>
</template>

<style scoped>
p { margin: 0; }
.since { font-size: 12px; margin-bottom: 6px; }
.text { overflow-wrap: anywhere; }
.meta {
  display: flex;
  justify-content: space-between;
  align-items: baseline;
  gap: 12px;
  margin-top: 4px;
}
.reason { font-size: 12px; overflow-wrap: anywhere; min-width: 0; }
.trace-btn { margin-left: auto; }
.cursor { animation: blink 1s steps(1) infinite; margin-left: 2px; }

.state {
  display: flex;
  justify-content: space-between;
  align-items: flex-end;
  gap: 16px;
  margin-top: 14px;
  padding-top: 10px;
  border-top: 1px dashed var(--fg-faint);
}
.label { font-size: 11px; letter-spacing: 0.15em; }
.small { font-size: 11px; }
.mode { font-size: clamp(24px, 6.5vw, 34px); white-space: nowrap; }
.mode.mn1 { color: var(--amber); text-shadow: 0 0 8px rgba(255, 204, 51, 0.5); }
.soc { text-align: right; }
.socval { font-size: clamp(34px, 9vw, 48px); white-space: nowrap; }
</style>
