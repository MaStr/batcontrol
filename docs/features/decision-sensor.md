# Understanding the Decision Sensor

If MQTT is enabled, batcontrol publishes a **Decision** sensor to Home
Assistant. It answers, in plain language, *why* batcontrol is currently
charging, discharging, limiting PV charging, or holding the battery.

This page is for anyone running batcontrol who wants to read that sensor.
For the underlying data model (reason codes, the decision journal, how to
add a step), see [Decision Trace and Journal](../development/decision-trace.md).

## Where to find it

The sensor's state is the text shown below; its attributes (click into the
entity in Home Assistant) carry the full detail behind that one line,
including every step that was checked during that control cycle.

## How to read the state

The state always has the same shape:

```
<Mode> [<value> W] - <why>
```

- **Mode** is what the inverter is doing: `Discharge Allowed`, `Avoid
  Discharge`, `Limit Battery Charge`, or `Charge from Grid`.
- **value** is the charge rate (force charge) or the PV charge limit
  (limit mode), in watts. The other two modes have no value.
- **why** is one sentence explaining the mode, built from the numbers
  batcontrol actually used to decide.

The sensor only updates when something meaningful changes: a different
mode, or the charge rate / PV limit changing by 25&nbsp;% or more. Small,
continuous adjustments between evaluations do not create a new state.

## Worked examples

These are real sensor states, not simplified paraphrases -- each one is
exactly what `status_text()` produces for the numbers shown.

**Battery is allowed to discharge, there is enough energy**
> Discharge Allowed - usable energy (3500 Wh) exceeds the 1200 Wh reserved
> for upcoming expensive hours

batcontrol is holding back 1200 Wh for a price spike it has already seen
coming; the rest (3500 Wh usable) is free to use now.

**Battery is charged from the grid**
> Charge from Grid 2133 W - usable energy (900 Wh) is below the 2500 Wh
> reserved for upcoming expensive hours, so 1600 Wh is charged from the grid

The 900 Wh left is not enough to cover the reserve for the next expensive
hours, so the missing 1600 Wh is bought now while it is cheap, at a rate
that fills the battery before the current price slot ends.

**Peak shaving slows down PV charging**
> Limit Battery Charge 1200 W - PV charging is capped at 1200 W
> (time,price) so the battery does not fill up before 16:00

See [Peak Shaving](peak-shaving.md): without the cap, the battery would
fill up early and stop absorbing solar, well before the configured target
hour (16:00 here).

**Solar feed-in limit (`Solarspitzengesetz`) absorption**
> Limit Battery Charge 900 W - the battery charges at 900 W to absorb
> solar surplus that would otherwise be clipped at the 6000 W feed-in limit

Production is about to exceed the configured 6000 W feed-in limit; rather
than curtailing (= wasting) the surplus, the battery absorbs it.

**An external system blocks discharge**
> Avoid Discharge - an external system (such as evcc) requested that the
> battery not discharge

Today this is always evcc, holding the battery back while, for example, a
car is charging.

**A Home Assistant / API request could not be applied as asked**
> Discharge Allowed - requested via the API or Home Assistant

The attributes' `inputs` show a `requested_mode` that differs from the
`mode` actually applied: the requested mode (e.g. "Limit Battery Charge")
had no limit configured, so batcontrol fell back to a safe mode instead.

**Forecasts could not be refreshed**
> Discharge Allowed - forecast data could not be refreshed for 185
> seconds, falling back to a safe mode

batcontrol could not reach a forecast provider; after a short grace period
it falls back to allowing discharge rather than guessing.

**Battery is full enough to always allow discharge**
> Discharge Allowed - stored energy (8200 Wh) is above the
> always-allow-discharge level (80% of capacity)

Above the configured `always_allow_discharge_limit`, batcontrol always
allows discharging, regardless of price.

## Reading the attributes

The attributes are the full trace of that control cycle: every rule that
was checked, not only the one that decided the outcome. A rule that did
not apply (`"outcome": "skipped"` or `"not_needed"`) is still listed --
that is how you can tell, for example, that peak shaving was evaluated and
found nothing to do, rather than not running at all. The step that
produced the sensor's state is named at the top level as `decided_by`,
and its plain-language sentence is repeated there as `why`.
