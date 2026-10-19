/**
 * Dev-server plugin that answers /api/v1/* from a fixture file instead of
 * proxying to a running batcontrol.
 *
 * Why this exists: the states worth looking at hardest are the ones a live
 * system will not produce on request - a battery at exactly 100 %, a battery
 * at 3 %, a reservation larger than the battery, the 503 before the first
 * control cycle, a stalled control loop, a wrong token. Waiting for those on
 * real hardware is not a workflow.
 *
 * The fixtures are captured from a real instance, so their shape is the
 * API's, not an invention. Timestamps are rebased onto the current time at
 * request time, so relative displays ("5 min ago", the poll countdown) stay
 * meaningful however old the file is.
 *
 *   npm run dev            -> proxy to a real batcontrol
 *   npm run dev:fixtures   -> serve fixtures/default.json
 *   VITE_FIXTURE=no-data npm run dev:fixtures
 */
import { loadFixture } from './fixtures/load.js'

const PREFIX = '/api/v1/'

/** Fields that carry an epoch-ms timestamp and get rebased. */
const TIME_FIELDS = [
  'timestamp',
  'last_evaluation',
  'next_evaluation',
  'mode_changed_at',
  'decided_at',
  'current_ts',
  'run_time',
  'time_start',
  'time_end',
  'window_start',
  'window_end',
]

/**
 * Shift every timestamp in the payload by the same offset.
 *
 * Forecast slots get an offset rounded down to a whole slot, so slot
 * boundaries stay on the interval the way the real API produces them -
 * otherwise a fixture renders "MIN 24h @ 13:04" for hourly prices and sends
 * you looking for a bug that is not there.
 */
function rebase(value, offset) {
  if (Array.isArray(value)) return value.map((v) => rebase(v, offset))
  if (value && typeof value === 'object') {
    const out = {}
    for (const [key, inner] of Object.entries(value)) {
      out[key] =
        TIME_FIELDS.includes(key) && typeof inner === 'number' && inner > 0
          ? inner + offset
          : rebase(inner, offset)
    }
    return out
  }
  return value
}

export function fixtureApi(name = 'default') {
  return {
    name: 'batcontrol-fixture-api',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        if (!req.url?.startsWith(PREFIX)) return next()
        const path = req.url.slice(PREFIX.length).split('?')[0]

        let fixture
        try {
          fixture = loadFixture(name)
        } catch (e) {
          res.statusCode = 500
          res.setHeader('content-type', 'application/json')
          res.end(JSON.stringify({ ok: false, error: 'fixture', detail: e.message }))
          return
        }

        const body = fixture[path]
        if (!body) {
          res.statusCode = 404
          res.setHeader('content-type', 'application/json')
          res.end(
            JSON.stringify({
              ok: false,
              error: 'not_found',
              detail: `Fixture "${name}" has no entry for ${path}.`,
            }),
          )
          return
        }

        // Anchor: the fixture's own status timestamp becomes "now".
        const anchor = fixture.status?.timestamp ?? body.timestamp ?? Date.now()
        let offset = Date.now() - anchor
        const intervalMinutes = body.data?.interval_minutes ?? body.data?.price?.interval_minutes
        if (intervalMinutes) {
          const slot = intervalMinutes * 60_000
          offset = Math.floor(offset / slot) * slot
        }
        const shifted = rebase(body, offset)

        // _status carries the HTTP code for the error fixtures (503/404/401).
        const status = shifted._status ?? 200
        delete shifted._status
        res.statusCode = status
        res.setHeader('content-type', 'application/json')
        res.setHeader('cache-control', 'no-store')
        if (status === 401) res.setHeader('www-authenticate', 'Bearer')
        res.end(JSON.stringify(shifted))
      })

      server.config.logger.info(
        `  \x1b[32m➜\x1b[0m  fixtures: serving ${PREFIX}* from fixtures/${name}.json`,
      )
    },
  }
}
