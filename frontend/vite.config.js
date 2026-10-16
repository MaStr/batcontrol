import { fileURLToPath, URL } from 'node:url'
import { defineConfig, loadEnv } from 'vite'
import vue from '@vitejs/plugin-vue'

import { fixtureApi } from './vite.fixtures.js'

/**
 * Copy the two Swagger UI files batcontrol serves at /docs into the build.
 *
 * FastAPI points /docs at cdn.jsdelivr.net by default, which means the API
 * documentation is blank on a Home Assistant box without internet egress -
 * the same reason the dashboard's fonts are bundled. Only the bundle and the
 * stylesheet are copied; the rest of swagger-ui-dist (11 MB) is not needed.
 *
 * Swagger UI is Apache-2.0 and ships a NOTICE, so both travel with it into
 * licenses/ - redistributing the files without them would not be allowed.
 */
function swaggerAssets() {
  const assets = ['swagger-ui-bundle.js', 'swagger-ui.css']
  const licenses = {
    LICENSE: 'LICENSE-Apache-2.0-SwaggerUI.txt',
    NOTICE: 'NOTICE-SwaggerUI.txt',
    'swagger-ui-bundle.js.LICENSE.txt': 'LICENSE-SwaggerUI-bundled-dependencies.txt',
  }
  const from = (file) =>
    fileURLToPath(new URL(`node_modules/swagger-ui-dist/${file}`, import.meta.url))

  return {
    name: 'batcontrol-swagger-assets',
    apply: 'build',
    async writeBundle(options) {
      const { copyFile, mkdir } = await import('node:fs/promises')
      const out = `file://${options.dir}/`
      const swagger = new URL('swagger/', out)
      const licenseDir = new URL('licenses/', out)
      await mkdir(swagger, { recursive: true })
      await mkdir(licenseDir, { recursive: true })
      for (const file of assets) {
        await copyFile(from(file), fileURLToPath(new URL(file, swagger)))
      }
      for (const [source, name] of Object.entries(licenses)) {
        await copyFile(from(source), fileURLToPath(new URL(name, licenseDir)))
      }
    },
  }
}

// The dashboard is served by batcontrol itself, from inside the Python
// package, and it has to work under three different base paths:
//   /                               Docker, direct port access
//   /api/hassio_ingress/<token>/    Home Assistant ingress
//   /dashboard/ (or similar)        a reverse proxy subpath
// Hence base: './' - every asset is referenced relatively, so the bundle
// does not care where it is mounted. The API base URL is derived from
// document.baseURI at runtime (see src/lib/api.js).
export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), '')
  // With VITE_FIXTURE set, /api/v1/* is answered from fixtures/<name>.json
  // instead of being proxied - see vite.fixtures.js for why.
  const fixture = env.VITE_FIXTURE
  return {
    base: './',
    plugins: [vue(), swaggerAssets(), ...(fixture ? [fixtureApi(fixture)] : [])],
    build: {
      // Straight into the package, so `uv build --wheel` picks the assets up
      // as package data. The stable Home Assistant add-on installs nothing
      // but that wheel, so assets missing here means a dashboard-less add-on.
      outDir: fileURLToPath(new URL('../src/batcontrol/web/static', import.meta.url)),
      emptyOutDir: true,
      // No sourcemaps: they would triple the size of the wheel for a UI that
      // is debugged against the dev server anyway.
      sourcemap: false,
      assetsInlineLimit: 0,
    },
    server: {
      // Same-origin proxy to a running batcontrol, so the browser sees no
      // CORS or private-network prompt during development. Skipped when
      // fixtures answer instead.
      proxy: fixture
        ? undefined
        : {
            '/api': {
              target: env.VITE_BACKEND_URL || 'http://127.0.0.1:8080',
              changeOrigin: true,
            },
          },
    },
    test: {
      environment: 'jsdom',
      include: ['src/**/*.test.js'],
    },
  }
})
