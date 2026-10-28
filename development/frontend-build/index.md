# Frontend Build

The dashboard is a Vue 3 application in `frontend/`. It is built by npm into `src/batcontrol/web/static/`, from where setuptools picks it up as package data - so the assets travel **inside the wheel**.

That last point is the one to remember: the Home Assistant stable add-on installs nothing but the released wheel. A wheel built without the npm step produces an add-on without a dashboard, and nothing fails loudly on the way there.

## Building it

```
cd frontend
npm ci
npm run build          # -> ../src/batcontrol/web/static/
```

`npm run build` also runs `scripts/check-offline.mjs`, which fails if the built output would load a font, stylesheet or script from the network. batcontrol runs in homes without internet egress and behind Home Assistant ingress, where an external asset does not degrade - it never arrives.

A source checkout without Node is a legitimate state: batcontrol then serves a short hint page at `/` instead of the dashboard, and the REST API is unaffected.

## Developing

Two modes, depending on whether you need live data:

```
# Against a running batcontrol (api.enabled: true). The dummy inverter in
# config/batcontrol_config_dummy.yaml is enough - no hardware needed.
npm run dev

# Against frozen fixtures, no backend at all
npm run dev:fixtures
VITE_FIXTURE=battery-full npm run dev:fixtures
```

Fixtures exist for the states a live battery will not produce on request: exactly 100 %, 3 % in force charge, a reservation larger than the battery, the 503 before the first control cycle, a stalled control loop, a wrong token. They were captured from a real instance, so their shape is the API's. See `frontend/fixtures/README.md`.

`npm run dev` proxies `/api` to `VITE_BACKEND_URL` (default `http://127.0.0.1:8080`), so the browser sees one origin and no CORS or private-network prompt.

## Dependency pinning

`package.json` holds **exact versions**, not ranges, and `.npmrc` sets `save-exact=true` so a later `npm install <pkg>` cannot reintroduce one. `package-lock.json` pins every transitive package by its `sha512` integrity hash, and `npm ci` - which is what the Dockerfile and both CI workflows run - verifies each hash and fails on a mismatch rather than resolving anything.

Updating a dependency is therefore deliberate: `npm install <pkg>@<version>`, commit both `package.json` and `package-lock.json`, and check the build output still passes `check-offline.mjs`.

## Tests

```
npm test               # vitest
```

Covered: `normalize()` against every fixture, the SOC grid cell states against the table in the [dashboard specification](https://mastr.github.io/batcontrol/development/dashboard-spec/index.md), and the decision trace component against a captured payload.

## How it reaches the user

```
frontend/                      npm run build
  └─> src/batcontrol/web/static/
        └─> wheel (package data)
              ├─> Docker image         (Dockerfile, node stage)
              ├─> HA stable add-on     (installs the released wheel)
              └─> HA development add-on (builds the wheel from main)
```

The Docker build uses a dedicated `node:24-alpine` stage with `--platform=$BUILDPLATFORM`, so a multi-arch build runs npm once, natively, instead of once per architecture under emulation. The output is static files and architecture independent.

## Pitfalls

- **`base: './'` is required.** The same bundle is served at `/`, under a Home Assistant ingress token path and under a reverse-proxy subpath. Absolute asset paths break two of the three. The API base URL is derived from `document.baseURI` for the same reason.
- **An asset only ships if something imports it.** Vite does not copy unreferenced files under `src/`. Anything that has to ship verbatim - the font licenses, for example - belongs in `frontend/public/`.
- **Timestamps are epoch milliseconds** in the API and epoch seconds in MQTT.
- **`value` is Wh per slot, `power_w` is W.** At 15-minute resolution they differ by a factor of four.
