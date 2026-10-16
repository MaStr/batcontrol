/**
 * The only place that knows what the batcontrol API looks like.
 *
 * Components read the view model this module returns, never a raw response.
 * That was true of the prototype against the Node-RED bridge and it stays
 * true here: the payloads are typed now, so there is no string parsing left,
 * but the mapping still happens in exactly one file.
 */

/**
 * Base URL of the API, derived from the document rather than configured.
 *
 * The same bundle is served at /, under a Home Assistant ingress token path
 * (/api/hassio_ingress/<token>/) and under a reverse-proxy subpath. Deriving
 * the base from document.baseURI - which the relative <base>-less index.html
 * resolves to the directory the page was served from - makes all three work
 * without a rebuild. In dev, Vite proxies /api to a real batcontrol.
 */
export const API_BASE = new URL('api/v1/', document.baseURI).href

export const releaseUrl = (version) =>
  `https://github.com/MaStr/batcontrol/releases/tag/${version}`

/** Raised when the API answers 401, so the caller can ask for a token. */
export class UnauthorizedError extends Error {
  constructor(message = 'Unauthorized') {
    super(message)
    this.name = 'UnauthorizedError'
  }
}

/** Raised while batcontrol has not completed its first control cycle. */
export class NoDataError extends Error {
  constructor(detail) {
    super(detail || 'No data yet')
    this.name = 'NoDataError'
  }
}

const TOKEN_KEY = 'batcontrol.token'

/**
 * The bearer token, if one was entered. Kept in sessionStorage: it should
 * survive a reload but not outlive the browser session, and it must never be
 * written anywhere the dashboard could leak it.
 */
export function getToken() {
  try {
    return sessionStorage.getItem(TOKEN_KEY) || ''
  } catch {
    // Private mode or blocked storage - work without persistence.
    return ''
  }
}

export function setToken(token) {
  try {
    if (token) sessionStorage.setItem(TOKEN_KEY, token)
    else sessionStorage.removeItem(TOKEN_KEY)
  } catch {
    /* ignore - the token then only lives for this page view */
  }
}

async function get(path) {
  const token = getToken()
  const res = await fetch(API_BASE + path, {
    cache: 'no-store',
    headers: token ? { Authorization: `Bearer ${token}` } : {},
  })
  if (res.status === 401) throw new UnauthorizedError()
  if (res.status === 503 || res.status === 404) {
    const body = await res.json().catch(() => ({}))
    throw new NoDataError(body.detail)
  }
  if (!res.ok) throw new Error(`HTTP ${res.status}`)
  const json = await res.json()
  if (!json.ok) throw new Error('batcontrol reported ok=false')
  return json.data
}

/** Same shape as a successful get(), but a missing resource becomes null. */
async function getOptional(path) {
  try {
    return await get(path)
  } catch (e) {
    if (e instanceof NoDataError) return null
    throw e
  }
}

export const MODES = {
  '-1': 'FORCE CHARGE',
  '0': 'AVOID DISCHARGE',
  '8': 'LIMIT CHARGE RATE',
  '10': 'ALLOW DISCHARGE',
}

/** Price forecast slots as [{ start, price }] for the UI. */
function priceSlots(forecast) {
  if (!forecast) return []
  return forecast.slots.map((slot) => ({ start: slot.time_start, price: slot.value }))
}

/**
 * Build the view model from the API responses.
 *
 * Exported separately from fetchAll so it can be unit tested against the
 * fixtures in frontend/fixtures/ - the edge cases that matter here (SoC at
 * 0 % and 100 %, a reservation larger than the battery, no cycle yet) are
 * hard to produce on a live system.
 */
export function normalize({ status, battery, current, price, decision, version }) {
  // Left null when the inverter has not been read yet: the grid draws an
  // unknown limit differently from a limit of zero.
  const minSoc = battery.soc_limits?.min_soc ?? null
  const maxSoc = battery.soc_limits?.max_soc ?? null
  // max_energy_capacity is already reduced by max_soc (the inverter's
  // get_max_capacity()), but every formula below works on percent of the
  // gross capacity - scale it back up, or max_soc is applied twice.
  const maxCapacity = battery.max_energy_capacity ?? 0
  const capacity = maxSoc > 0 ? (maxCapacity * 100) / maxSoc : maxCapacity
  const minSocOr0 = minSoc ?? 0
  const maxSocOr100 = maxSoc ?? 100
  const allowDischargeSoc = battery.always_allow_discharge_limit_percent
  const reserved = battery.reserved_energy_capacity ?? 0

  // Nothing is held back above always_allow_discharge, so that is the upper
  // bound for a reservation; the API reports the raw demand, which can exceed
  // the battery.
  const reserveTopSoc = Math.min(maxSocOr100, allowDischargeSoc ?? maxSocOr100)
  const reservedCapped = Math.min(
    reserved,
    (capacity * Math.max(0, reserveTopSoc - minSocOr0)) / 100,
  )

  const mode = String(status.mode ?? '')

  return {
    online: status.online,
    version: version?.version ?? current?.version ?? null,
    lastEvaluation: status.last_evaluation,
    nextEvaluation: status.next_evaluation,
    controlSource: status.control_source,
    chargeRate: status.charge_rate,
    intervalMinutes: version?.interval_minutes ?? current?.price?.interval_minutes ?? 60,

    decision: decision?.status_text || decision?.explanation || null,
    decisionTs: decision?.decided_at ?? null,
    reason: decision?.trace?.decided_by?.reason ?? null,
    // The event carries the mode transition (kind, previous_mode, value),
    // the trace carries the records of the latest evaluation. The trace wins
    // on every key they share, because the event can be up to 15 minutes
    // older - rendering its records next to the current mode would show a
    // "why" that no longer explains what is on screen.
    trace: decision ? { ...(decision.event ?? {}), ...decision.trace } : null,

    mode,
    modeLabel: status.mode_label || MODES[mode] || `MODE ${mode}`,
    modeTs: status.mode_changed_at,

    soc: battery.soc,
    minSoc,
    maxSoc,
    allowDischargeSoc,
    capacity,
    stored: battery.stored_energy_capacity,
    storedUsable: battery.stored_usable_energy_capacity,
    usableCapacity: (capacity * (maxSocOr100 - minSocOr0)) / 100,
    reserved,
    reservedCapped,

    price: {
      current: current?.price?.current ?? null,
      intervalMinutes: current?.price?.interval_minutes ?? 60,
      minDynamicDiff: current?.price?.min_dynamic_price_difference ?? null,
      slots: priceSlots(price),
    },
  }
}

/**
 * Fetch everything the dashboard needs.
 *
 * One request per resource, in parallel: the endpoints are cheap reads of
 * values the control loop has already cached, and separate resources keep the
 * dashboard from re-fetching a 24 h forecast to learn the current mode.
 *
 * status and battery are required - without them there is nothing to draw.
 * current, price and decision are optional, because they are unavailable in
 * legitimate states: before the first control cycle, or with an empty
 * decision journal after a restart.
 */
export async function fetchAll() {
  // fetch() cannot put anything outside Latin-1 into a header and throws a
  // TypeError, which would read as "unreachable" and never show the token
  // prompt again - the dashboard would be locked out for the session.
  if (/[^\x00-\xff]/.test(getToken())) {
    setToken('')
    throw new UnauthorizedError(
      'The token contains characters a browser cannot send in a header.')
  }
  const [status, battery, version, current, price, decision] = await Promise.all([
    get('status'),
    get('battery'),
    getOptional('version'),
    getOptional('current'),
    getOptional('forecast/price'),
    getOptional('decision'),
  ])
  return normalize({ status, battery, current, price, decision, version })
}
