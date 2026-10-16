# Third-Party Licenses

batcontrol itself is MIT licensed (see [`LICENSE`](LICENSE)). The dashboard ships third-party
assets *inside* the distributed artifacts - the wheel, the Docker image and the Home Assistant
add-on - so their licenses travel with them.

This file covers everything that ends up in a build output. Runtime Python dependencies are
installed by pip from PyPI and are not redistributed by this project; their licenses are
declared in their own packages.

## Fonts

Both fonts are bundled rather than loaded from a CDN: batcontrol runs in homes without internet
egress and behind Home Assistant ingress, where an external font request simply fails. They are
imported from `frontend/src/style.css` and emitted into `src/batcontrol/web/static/assets/`.

| Font | Version | License | Copyright |
|---|---|---|---|
| IBM Plex Mono (via `@fontsource/ibm-plex-mono`) | 5.3.0 | SIL Open Font License 1.1 | Copyright 2017 IBM Corp. |
| VT323 (via `@fontsource/vt323`) | 5.3.0 | SIL Open Font License 1.1 | Copyright 2011, The VT323 Project Authors |

The OFL permits bundling and redistribution, including inside a commercial or differently
licensed product, provided the license text and copyright notice travel with the font files.
The verbatim license texts therefore live in `frontend/public/licenses/`, which the build copies
unchanged into the dashboard's static assets. They end up in the wheel, the Docker image and the
add-on, and are reachable from a running dashboard at `licenses/`:

- `licenses/LICENSE-OFL-IBMPlexMono.txt`
- `licenses/LICENSE-OFL-VT323.txt`

`public/` is the mechanism that matters here: Vite only emits assets something imports, so a
license file placed under `src/` would silently never reach the build output - which is how this
was wrong once already.

Only the `latin` subsets in the weights actually used (400 and 600 for IBM Plex Mono, 400 for
VT323) are imported.

## JavaScript

| Package | Version | License | Where |
|---|---|---|---|
| Vue | 3.5.44 | MIT | bundled into the dashboard |
| Swagger UI (`swagger-ui-dist`) | 5.33.3 | Apache-2.0 | copied verbatim, served at `/docs` |

Vue is the only runtime dependency compiled into the dashboard bundle. The rest of the build
toolchain (Vite, `@vitejs/plugin-vue`, Vitest, ESLint) runs at build time and ships nothing.

Swagger UI is a special case: FastAPI points `/docs` at a CDN by default, which leaves the API
documentation blank on a Home Assistant box without internet egress. Two files
(`swagger-ui-bundle.js`, `swagger-ui.css`) are therefore copied into the package and served
from it. Apache-2.0 requires the license text and the NOTICE to be redistributed with them, and
the bundle carries its own third-party notices, so all three ship alongside:

- `licenses/LICENSE-Apache-2.0-SwaggerUI.txt`
- `licenses/NOTICE-SwaggerUI.txt`
- `licenses/LICENSE-SwaggerUI-bundled-dependencies.txt`

## Keeping this current

A new bundled asset means a new row here. The check is deliberately manual, because "does this
end up in the artifact" is a judgement a tool gets wrong in both directions: `npm ls` lists the
build toolchain too, and a font imported from CSS is invisible to a dependency scan.

When adding a dependency that lands in the bundle:

1. Add it to the matching table with its version and license.
2. If the license requires the text to be redistributed (OFL, Apache-2.0 with NOTICE, ...),
   commit the verbatim text under `frontend/public/licenses/` and verify it appears in
   `src/batcontrol/web/static/licenses/` after a build. Under `src/` it would not be emitted.
3. Prefer a bundled asset over a CDN reference - `npm run build` fails on an external asset
   reference (`frontend/scripts/check-offline.mjs`).
