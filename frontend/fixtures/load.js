/**
 * Fixture loader shared by the dev server (vite.fixtures.js) and the unit
 * tests (src/lib/api.test.js).
 *
 * A scenario only stores what makes it a scenario: `"$extends": "default"`
 * plus the fields that differ, deep-merged over the base. Objects merge key by
 * key; arrays and scalars replace. A contract change then has to be made in
 * default.json only.
 */
import { readFileSync, readdirSync } from 'node:fs'
import { join } from 'node:path'
import { fileURLToPath } from 'node:url'

// import.meta.dirname: vitest's jsdom environment rewrites import.meta.url to
// an http URL, so it cannot be used to find files there.
const DIR = import.meta.dirname ?? fileURLToPath(new URL('./', import.meta.url))

const isObject = (v) => v && typeof v === 'object' && !Array.isArray(v)

function merge(base, patch) {
  const out = { ...base }
  for (const [key, value] of Object.entries(patch)) {
    out[key] = isObject(value) && isObject(base[key]) ? merge(base[key], value) : value
  }
  return out
}

function read(name) {
  try {
    return readFileSync(join(DIR, `${name}.json`), 'utf8')
  } catch {
    const available = readdirSync(DIR)
      .filter((f) => f.endsWith('.json'))
      .map((f) => f.replace(/\.json$/, ''))
    throw new Error(
      `No such fixture. Available: ${available.join(', ')}` + ' (set VITE_FIXTURE=<name>)',
    )
  }
}

/** The fully resolved fixture. Read on every call: edit, reload, done. */
export function loadFixture(name) {
  const { $extends: parent, ...patch } = JSON.parse(read(name))
  return parent ? merge(loadFixture(parent), patch) : patch
}
