# MCP Evaluation

Ten questions for checking that the [MCP server](https://mastr.github.io/batcontrol/development/mcp-spec/index.md) is actually usable by an assistant. They are the kind of thing a user asks, they are read-only, each one stands alone, and each has an answer that can be verified rather than judged.

## Why a fixture, not a live instance

"Verifiable" and "the answer does not change over time" rule out a live battery: the SoC moves, prices roll over at midnight, and the decision journal empties on restart. The evaluation therefore runs against a **frozen scenario** - a `ForecastSnapshot` and a recorded decision with fixed values - served through the real service layer and the real MCP tools. Only the data source is pinned; everything the evaluation exercises is production code.

The scenario below extends the test fixture in `tests/batcontrol/web/conftest.py`, which is already a complete stand-in for the core.

## Scenario

| Quantity        | Value                                                                                 |
| --------------- | ------------------------------------------------------------------------------------- |
| Interval        | 60 minutes                                                                            |
| Battery         | 11000 Wh capacity, SoC 87.4 %, 9614 Wh stored, 8844 Wh usable, 1500 Wh reserved       |
| SoC limits      | inverter min 7 %, max 100 %; always_allow_discharge 90 %; max_charging_from_grid 89 % |
| Mode            | 10 (allow discharge), set by `optimizer`                                              |
| Price forecast  | slot 0: 0.308, slot 1: 0.254, slot 2: 0.201, slot 3: 0.350 EUR/kWh                    |
| Production      | 0, 120, 900, 1500 Wh per slot                                                         |
| Consumption     | 450, 420, 400, 500 Wh per slot                                                        |
| Net consumption | 450, 300, -500, -1000 Wh per slot                                                     |
| Decision        | decisive reason `ALWAYS_ALLOW_DISCHARGE_LIMIT`                                        |
| Config          | min_price_difference 0.05, min_price_difference_rel 0.10, peak shaving off            |

## The questions

| #   | Question                                                                             | Tools the assistant has to reach for                         | Verifiable answer                                                                                                                                                                                                            |
| --- | ------------------------------------------------------------------------------------ | ------------------------------------------------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | "What is my battery doing right now, and why?"                                       | `get_status` + `get_decision`                                | Discharging is allowed (mode 10), because the SoC is above the always-allow-discharge limit. Must name the reason, not just the mode.                                                                                        |
| 2   | "How much usable energy is left in the battery?"                                     | `get_battery`                                                | 8844 Wh. Must not answer 9614 Wh (that is the stored total, including what sits below the inverter's min SoC).                                                                                                               |
| 3   | "When is electricity cheapest in the next few hours, and how much cheaper than now?" | `get_price_forecast`                                         | Slot 2, 0.201 EUR/kWh, 0.107 below the current 0.308. Requires reading `time_start` to name a time, not a slot index.                                                                                                        |
| 4   | "Will my solar cover the house this afternoon?"                                      | `get_net_consumption_forecast` (or production + consumption) | From slot 2 onward net consumption is negative (-500 and -1000 Wh), so yes, with surplus. A wrong sign convention inverts this answer.                                                                                       |
| 5   | "What is the peak solar power expected today?"                                       | `get_solar_forecast`                                         | 1500 Wh in the last slot, which at 60-minute resolution is 1500 W average. At 15-minute resolution the same Wh value would be 6000 W - the answer must come from `power_w`, not from `value`.                                |
| 6   | "Is batcontrol holding energy back, and how much?"                                   | `get_battery` (+ `get_config`)                               | 1500 Wh reserved. A good answer notes the reservation is capped by the always-allow-discharge limit before it is rendered.                                                                                                   |
| 7   | "How big does a price difference have to be before it charges from the grid?"        | `get_config`                                                 | 0.05 EUR/kWh absolute and 10 % relative. Must not confuse the two or report 0.10 EUR.                                                                                                                                        |
| 8   | "Has the mode been flapping in the last hour?"                                       | `list_decisions`                                             | Answerable from the journal; with a single recorded decision the answer is no. Must also say the journal is in memory and short, rather than implying a full history.                                                        |
| 9   | "Can you set the battery to charge from the grid now?"                               | none                                                         | A refusal: these tools are read-only, and the change has to be made in the configuration or over the MQTT API. Any attempt to find a write tool is a failure.                                                                |
| 10  | "Give me the whole price forecast for the next two days."                            | `get_price_forecast`, then paging                            | The horizon in the scenario is 4 slots, so `has_more` is false and the honest answer is that only four hours are available - not an invented two days. Against a longer horizon, the assistant must page with `next_offset`. |

## What each question is actually testing

- **1, 6, 7** - whether the decision trace and the thresholds are presented together. A mode without its reason is not an answer.
- **2, 5** - unit discipline. These are the two places where a plausible wrong number exists right next to the right one.
- **3** - whether epoch-millisecond timestamps get rendered as local times.
- **4** - the sign convention of net consumption.
- **8, 10** - whether the model reports the limits of the data (in-memory journal, finite horizon) instead of filling the gap.
- **9** - the read-only guarantee, from the model's side.

## Running it

The deterministic parts are already automated in `tests/batcontrol/web/test_mcp.py`: the tool surface, the paging metadata, the sign convention, the units in the descriptions, and the absence of any write tool. What a test cannot check is whether the *descriptions* are good enough for a model to pick the right tool - that needs a real client:

1. Start batcontrol with `api.enabled: true` and `api.mcp_enabled: true`.
1. Point a client at `http://127.0.0.1:8080/mcp`.
1. Ask the ten questions verbatim, in separate conversations.
1. A question that needs a hint, or that produces a wrong unit or sign, is a bug in the tool description, not in the model. Fix the docstring.
