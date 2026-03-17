# Decision Explainability

## Overview

The logic engine produces human-readable explanations alongside its control output.
These are surfaced via the MCP `get_decision_explanation` tool and stored in
`CalculationOutput.explanation`.

## Data Flow

```
DefaultLogic.calculate()
    → self._explain("Current price: 0.1500, Battery: 5000 Wh stored, ...")
    → __is_discharge_allowed()
        → self._explain("Battery above always-allow-discharge limit ...")
        OR
        → self._explain("Usable energy (4500 Wh) <= reserved energy (5000 Wh) ...")
    → self._explain("Decision: Allow discharge")
    → stored in CalculationOutput.explanation: List[str]

core.py stores last_logic_instance
    → api_get_decision_explanation() reads explanation from it

MCP get_decision_explanation tool
    → returns {mode, mode_name, explanation_steps[], override_active}
```

## Explanation Points

The following decision points produce explanation messages:

| Location | Explanation |
|----------|-------------|
| `calculate_inverter_mode` entry | Current price, battery state summary |
| `is_discharge_always_allowed` | Above/below always-allow-discharge limit |
| Discharge surplus check | Usable energy vs reserved energy comparison |
| Reserved slots | Number of higher-price slots reserved for |
| Grid charging possible | SOC vs charging limit check |
| Required recharge energy | How much grid energy needed |
| Final decision | "Allow discharge" / "Force charge at X W" / "Avoid discharge" |

## Lifecycle

- Explanation list is **reset every evaluation cycle** (new `CalculationOutput()`)
- Between cycles, `api_get_decision_explanation()` returns the **last completed** evaluation
- No accumulation across cycles, no staleness

## Files Modified

| File | Change |
|------|--------|
| `logic/logic_interface.py` | Added `explanation: List[str]` to `CalculationOutput` |
| `logic/default.py` | Added `_explain()` method, annotation calls throughout logic |
| `core.py` | Added `api_get_decision_explanation()` getter |

## Example Output

```json
{
  "mode": 10,
  "mode_name": "Allow Discharge",
  "explanation_steps": [
    "Current price: 0.1500, Battery: 5000 Wh stored, 4500 Wh usable, 5000 Wh free capacity",
    "Usable energy (4500 Wh) > reserved energy (2000 Wh) — surplus available for discharge",
    "Decision: Allow discharge"
  ],
  "override_active": false
}
```
