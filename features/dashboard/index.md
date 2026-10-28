# Dashboard

batcontrol ships a small web dashboard that shows what it is doing and why, without needing Home Assistant, MQTT or Grafana.

It is a **reader**. There are no controls on it - nothing to click that changes the battery. Overrides stay on the [MQTT API](https://mastr.github.io/batcontrol/integrations/mqtt-api/index.md).

## Opening it

Enable the API and the dashboard is served at the root of the same port:

```
api:
  enabled: true
  host: 127.0.0.1   # 0.0.0.0 to reach it from another device
  port: 8080
  dashboard_enabled: true
```

- **Docker**: publish the port (`-p 8080:8080`), bind to `0.0.0.0`, open `http://<host>:8080/`.
- **Home Assistant add-on**: it appears in the sidebar through ingress; no port to publish.
- **Source install**: the assets are built by npm and ship inside the wheel. A checkout without a build serves a hint page instead - see [frontend build](https://mastr.github.io/batcontrol/development/frontend-build/index.md).

## Reading it

### Header

Version (linking to its release notes), online state, the time of the last evaluation, which source set the current mode, the forced charge rate, and a countdown to the next poll. The countdown follows batcontrol's own evaluation cycle rather than a fixed interval.

`OFFLINE` means the control loop has missed several evaluations, or the browser cannot reach batcontrol. The last known values stay on screen, dimmed, instead of disappearing.

### DECISION

The decision batcontrol last took, in the same words as the Home Assistant Decision sensor, with the reason code below it and how long it has been in effect.

`>TRACE<` expands the full decision trace: every rule that was evaluated, which one was decisive, and the inputs behind each. Long slot lists stay collapsed behind a `>N SLOTS<` button. This is the view that answers "why is it not discharging right now" - see [Understanding the Decision Sensor](https://mastr.github.io/batcontrol/features/decision-sensor/index.md) for how to read the reasons.

Below it, the current mode and the state of charge.

### SOC grid

A hundred cells, one per percent, in the style of the old Windows defragmenter. Cell 0 is bottom left, cell 99 top right.

| Cell         | Meaning                                                                       |
| ------------ | ----------------------------------------------------------------------------- |
| bright green | charged - the SoC is above this percent                                       |
| blinking     | the cell the SoC currently sits in                                            |
| dark         | free                                                                          |
| with a dot   | locked: at or below the inverter's MIN_SOC, or above its MAX_SOC              |
| hatched      | reserved for upcoming expensive hours; bright stripes if charged, dark if not |
| purple       | above `always_allow_discharge_limit`, where discharging is always permitted   |

At 100 % every cell is lit and none blinks. The reserved energy is a demand forecast and can exceed the battery, so the hatching is capped at the always-allow-discharge limit before it is drawn - above that nothing is ever held back.

Under the grid: the reserved energy (with the cap, where it applies), usable versus total energy, and the configured limits.

### PRICE

The current price, a 24-hour sparkline of hourly means, the cheapest and most expensive hour with their times, the spread, and `GRID_CHARGE Δ` - the price difference a future slot has to beat before batcontrol charges from the grid.

## Notes

- The whole UI is English, 24-hour times, decimal point.
- It is dark-only by design: it is a terminal, not a theme.
- Animations respect `prefers-reduced-motion`.
- It works at phone width; on a desktop the grid moves to the left.
- Fonts are bundled, so it looks the same without internet access.
- With `api.auth_token` set, the dashboard asks for the token once and keeps it for the browser session. Set one whenever the dashboard sits behind a reverse proxy - see [REST API security](https://mastr.github.io/batcontrol/integrations/rest-api/#security).
