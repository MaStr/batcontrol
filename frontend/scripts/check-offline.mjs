#!/usr/bin/env node
/**
 * Fails when the built dashboard would load anything from the network.
 *
 * batcontrol runs in homes without internet egress and behind Home Assistant
 * ingress. An external font, stylesheet or script does not degrade there - it
 * simply never arrives. This check is the guard rail for that: it inspects
 * the actual build output, not the sources, so a dependency that pulls in a
 * CDN URL is caught too.
 *
 * It only looks at references the browser would *fetch*. Link targets a user
 * clicks, XML namespace identifiers and URLs inside error messages are fine
 * and are listed as allowed prefixes below.
 */
import { readdirSync, readFileSync, statSync } from 'node:fs'
import { join, extname } from 'node:path'
import { fileURLToPath, URL } from 'node:url'

const ROOT = fileURLToPath(new URL('../../src/batcontrol/web/static', import.meta.url))

// URLs that are never fetched by the browser.
const ALLOWED = [
  'https://github.com/MaStr/batcontrol', // release link in the header, user clicks it
  'https://vuejs.org/error-reference/', // text of a Vue runtime error message
  'http://www.w3.org/', // XML/SVG/MathML namespace identifiers
]

// Patterns that mean "the browser will fetch this".
const FETCHING = [
  { re: /url\(\s*['"]?(https?:)?\/\/[^)'"]+/gi, what: 'CSS url() to a remote host' },
  { re: /@import\s+(url\()?['"](https?:)?\/\//gi, what: 'CSS @import from a remote host' },
  { re: /<link[^>]+href=["'](https?:)?\/\/[^"']+/gi, what: '<link> to a remote host' },
  { re: /<script[^>]+src=["'](https?:)?\/\/[^"']+/gi, what: '<script> from a remote host' },
  { re: /fonts\.(googleapis|gstatic)\.com/gi, what: 'Google Fonts' },
  { re: /\b(cdn\.jsdelivr\.net|unpkg\.com|cdnjs\.cloudflare\.com)/gi, what: 'a CDN' },
]

const TEXT = new Set(['.html', '.css', '.js', '.mjs', '.json', '.svg', '.map'])

/**
 * Strip comments before scanning. A comment cannot fetch anything, and the
 * sources deliberately *name* fonts.googleapis.com to explain why nothing is
 * loaded from it - scanning comments would flag that explanation.
 * Only HTML and CSS block comments are removed; "//" is left alone because
 * it appears inside URLs in minified JavaScript.
 */
function withoutComments(content) {
  return content
    .replace(/<!--[\s\S]*?-->/g, '')
    .replace(/\/\*[\s\S]*?\*\//g, '')
}

function* files(dir) {
  for (const entry of readdirSync(dir)) {
    const path = join(dir, entry)
    if (statSync(path).isDirectory()) yield* files(path)
    else yield path
  }
}

let failures = 0
try {
  statSync(ROOT)
} catch {
  console.error(`No build output at ${ROOT} - run "npm run build" first.`)
  process.exit(1)
}

for (const path of files(ROOT)) {
  if (!TEXT.has(extname(path))) continue
  const content = withoutComments(readFileSync(path, 'utf8'))
  for (const { re, what } of FETCHING) {
    for (const match of content.matchAll(re)) {
      const hit = match[0]
      if (ALLOWED.some((prefix) => hit.includes(prefix.replace(/^https?:\/\//, '')))) continue
      console.error(`${path}: ${what}: ${hit.slice(0, 120)}`)
      failures += 1
    }
  }
}

if (failures) {
  console.error(
    `\n${failures} external asset reference(s) in the build output.` +
      '\nBundle the asset instead - see docs/development/frontend-build.md.',
  )
  process.exit(1)
}
console.log('check-offline: no external asset references in the build output')
