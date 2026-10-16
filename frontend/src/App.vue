<script setup>
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { fetchAll, releaseUrl, setToken, UnauthorizedError } from './lib/api.js'
import { clock } from './lib/format.js'
import DecisionPanel from './components/DecisionPanel.vue'
import SocGrid from './components/SocGrid.vue'
import PricePanel from './components/PricePanel.vue'

// Fallback only. The real cadence comes from next_evaluation, so the
// countdown matches the control loop instead of drifting against it.
const FALLBACK_POLL_MS = 60_000
// Never poll faster than this, whatever the backend reports.
const MIN_POLL_MS = 5_000

const status = ref(null)
const error = ref(null)
const needsToken = ref(false)
const tokenError = ref(null)
const tokenInput = ref('')
const fetchedAt = ref(0)
const nextPollAt = ref(0)
const now = ref(Date.now())
let pollTimer, clockTimer

/** When to poll next: just after the control loop's next evaluation. */
function schedule() {
  const next = status.value?.nextEvaluation
  const delay = next ? next + 1000 - Date.now() : FALLBACK_POLL_MS
  const wait = Math.max(MIN_POLL_MS, Math.min(delay, 10 * 60_000))
  nextPollAt.value = Date.now() + wait
  pollTimer = setTimeout(load, wait)
}

async function load() {
  clearTimeout(pollTimer)
  try {
    status.value = await fetchAll()
    error.value = null
    needsToken.value = false
  } catch (e) {
    if (e instanceof UnauthorizedError) {
      // Ask once, then keep the token for this browser session.
      needsToken.value = true
      tokenError.value = e.message === 'Unauthorized' ? null : e.message
      error.value = null
    } else {
      // Keep the last known data on screen, dimmed, rather than blanking it.
      error.value = e.message
    }
  } finally {
    fetchedAt.value = Date.now()
    schedule()
  }
}

function submitToken() {
  setToken(tokenInput.value.trim())
  tokenInput.value = ''
  load()
}

onMounted(() => {
  load()
  clockTimer = setInterval(() => (now.value = Date.now()), 1000)
})
onUnmounted(() => {
  clearTimeout(pollTimer)
  clearInterval(clockTimer)
})

const nextIn = () => Math.max(0, Math.ceil((nextPollAt.value - now.value) / 1000))
// Stale: either the request failed or the control loop stopped evaluating.
const stale = computed(() => !!error.value || status.value?.online === false)
</script>

<template>
  <header class="bar">
    <span class="brand">BATCONTROL<a
      v-if="status?.version"
      class="dim version"
      :href="releaseUrl(status.version)"
      target="_blank"
      rel="noopener"
    >v{{ status.version }}</a></span>
    <span v-if="status" :class="status.online && !error ? 'ok' : 'bad'">●
      {{ status.online && !error ? 'ONLINE' : 'OFFLINE' }}</span>
    <span v-if="status" class="dim hide-sm">eval {{ clock(status.lastEvaluation) }}
      · {{ status.controlSource }} · {{ status.chargeRate }} W</span>
    <span class="dim right">↻ {{ nextIn() }}s</span>
  </header>

  <form v-if="needsToken" class="token" @submit.prevent="submitToken">
    <label for="token">This batcontrol requires an API token (api.auth_token):</label>
    <span v-if="tokenError" class="err">!! {{ tokenError }}</span>
    <input id="token" v-model="tokenInput" type="password" autocomplete="off"
           placeholder="bearer token" />
    <button class="toggle" type="submit">&gt;UNLOCK&lt;</button>
  </form>

  <p v-else-if="error" class="err">
    !! batcontrol unreachable: {{ error }}
    <span v-if="status" class="dim">— showing the last known data</span>
  </p>
  <p v-else-if="status && !status.online" class="err">
    !! the control loop has missed several evaluations — data may be stale
  </p>

  <main v-if="status" class="layout" :class="{ stale }">
    <DecisionPanel class="a-decision" :s="status" :now="now" />
    <SocGrid class="a-soc" :s="status" />
    <PricePanel class="a-price" :p="status.price" />
  </main>
  <p v-else-if="!error && !needsToken" class="loading">LOADING<span class="blink">_</span></p>
</template>

<style scoped>
.bar {
  display: flex;
  flex-wrap: wrap;
  align-items: baseline;
  gap: 4px 16px;
  padding: 10px 16px;
  border-bottom: 1px solid var(--fg-faint);
  font-size: 13px;
}
.brand { font-weight: 600; letter-spacing: 0.2em; }
.version { font-weight: 400; letter-spacing: 0; margin-left: 10px; color: var(--fg-dim); text-decoration: none; }
.version:hover { color: var(--fg); text-decoration: underline; }
.ok { color: var(--fg); }
.bad { color: var(--red); text-shadow: 0 0 6px rgba(255, 85, 68, 0.5); }
.right { margin-left: auto; }

.err { color: var(--red); margin: 12px 16px; }
.loading { margin: 24px 16px; }
.blink { animation: blink 1s steps(1) infinite; }

.token {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 8px 12px;
  margin: 16px;
  padding: 12px 14px;
  border: 1px solid var(--fg-dim);
  font-size: 13px;
}
.token input {
  font: inherit;
  color: var(--fg);
  background: var(--bg);
  border: 1px solid var(--fg-faint);
  padding: 4px 6px;
  min-width: 18ch;
  flex: 1 1 18ch;
}
.token input:focus-visible { outline: 1px solid var(--fg); }

/* Mobile: decision → soc → price */
.layout {
  display: grid;
  gap: 26px;
  padding: 24px 16px;
  grid-template-areas: 'decision' 'soc' 'price';
  max-width: 1100px;
  margin: 0 auto;
}
/* Last known data stays readable but visibly not current. */
.layout.stale { opacity: 0.55; }
/* grid items must be allowed to shrink below their longest word */
.layout > * { min-width: 0; }
.a-decision { grid-area: decision; }
.a-soc { grid-area: soc; }
.a-price { grid-area: price; }

/* Desktop: soc left, decision + price stacked right */
@media (min-width: 820px) {
  .layout {
    grid-template-columns: minmax(380px, 460px) 1fr;
    grid-template-areas: 'soc decision' 'soc price';
    grid-template-rows: auto 1fr;
    align-items: start;
    padding-top: 32px;
  }
}

@media (max-width: 480px) {
  .hide-sm { display: none; }
}
</style>
