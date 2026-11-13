# Adjust the charging rate on a charging event
* Config option: `charge_rate_multiplier`
* Default: 1.1

When Batcontrol reaches the lowest point in the price curve, it determines which future hours exceed a minimum price threshold. For those hours, it retrieves the required Wh from the load profile, and those Wh are then recharged.

```
charge_rate = required_recharge_energy/remaining_time
charge_rate * charge_rate_multiplier = final_charge_rate
if final_charge_rate < 500W  then charge_rate = 500W 
```

Example:

We are currently at a price of €0.30. We plan to recharge for two hours during which the price will exceed €0.35, and we will need 600 Wh and 400 Wh respectively. The battery is empty, so we recharge a total of 1,000 Wh. Because there are conversion losses, we use the charge_rate_multiplier to increase the charging power:

```1,000 Wh * 1.1 = 1,100 W```

With a multiplier value of 1.0, you may observe that the charging power ramps up over the course of the hour because the setpoints are reached too slowly. At a value of 1.5, the target is reached more quickly and then charging slows steadily as the hour progresses.

![Example pic for different charge_rate_multiplier](../assets/charge_rate_multiplier.png)

# When grid charging starts

Batcontrol charges as late as possible: as long as a following slot is not more expensive than the
current one, charging for the expensive hours behind it is left to that later slot. On a rising
price curve this means charging happens at the lowest point.

A later slot can only take over what the inverter is able to charge in it. Batcontrol therefore
checks the following cheaper (or equally cheap) slots against `max_grid_charge_rate`:

```
capacity of one later slot = max_grid_charge_rate / max(charge_rate_multiplier, 1) * slot length
charged now                = demand of the expensive hours - capacity of the cheaper slots before them
```

A `charge_rate_multiplier` below 1 is allowed and lowers the requested charge rate as usual. For
this capacity check it counts as 1: the inverter cannot charge more than `max_grid_charge_rate`,
and assuming more would defer charging for too long.

Only the part the later slots cannot deliver is charged in the current slot. Everything else is
still deferred.

Example with 15-minute slots, `max_grid_charge_rate: 5000` and `charge_rate_multiplier: 1.1` (one
slot can charge about 1.14 kWh): the price is 0.20 €/kWh until 04:00 and 0.40 €/kWh afterwards, the
expensive block needs 4 kWh and the battery is empty.

| Time | Cheap slots still to come | They can charge | Charged in this slot |
|------|---------------------------|-----------------|----------------------|
| 02:00 | 7 | 7.95 kWh | nothing, enough time left |
| 03:00 | 3 | 3.41 kWh | 0.6 kWh missing, charging starts |
| 03:15 - 03:45 | 2, 1, 0 | 2.27 / 1.14 / 0 kWh | maximum rate |

Before 0.10.2 batcontrol waited for the last cheap slot (03:45) in this situation and then
requested the whole 4 kWh within 15 minutes. The inverter capped that at `max_grid_charge_rate`,
so only about 1.1 kWh arrived in the battery. This affected price plateaus (static tariff zones)
in particular, and any single cheap slot that is too short for the demand behind it.

The same applies to `min_grid_charge_soc`: the part of the target the following cheaper slots can
still charge is deferred to them.

The [decision trace](../development/decision-trace.md) shows this case with the reason
`GRID_RECHARGE_CHEAPER_SLOTS_INSUFFICIENT` and the inputs `deferrable_energy` and
`max_grid_charge_rate`.

!!! note
    The check only decides **how much has to be charged now**. It does not change which slots count
    as cheap or expensive, and it does not change `soften_price_difference_on_charging` or the
    charging window described below.

# Adjust Charging pricepoint
* Config options:
  * `soften_price_difference_on_charging` ; true / false  - Enable / Disable
  * `soften_price_difference_on_charging_factor` ; 5 - any number greater than 0
* Default: disabled

By default, Batcontrol checks:
```python
found_lower_price = future_price <= current_price
```
This means that any future price that is lower or equal to the current price (current_price) is considered cheaper, and Batcontrol adjusts its evaluation period accordingly. The result is, that **only** at the lowest point, batcontrol searches for possible price-targets, which needs to be covered by energy stored in the battery.

### How the Soften Mechanism Works

When you enable `soften_price_difference_on_charging` (by setting it to True), Batcontrol modifies the current price by a fraction ( `soften_price_difference_on_charging_factor` ) of `min_price_difference`:

```python
modified_price = current_price - min_price_difference / self.soften_price_difference_on_charging_factor
found_lower_price = future_price <= modified_price
```
**Key idea:** The code requires the future price to be lower than a slightly reduced threshold (modified_price) before treating it as “cheaper.” In other words, batcontrol will only stop evaluating (the current price window) if the future price is truly lower by some margin—rather than just marginally lower.

### Example

Let's assume:

* `current_price = 0.30 €/kWh`
* `min_price_difference = 0.02 €/kWh`
* `soften_price_difference_on_charging = True`
* `soften_price_difference_on_charging_factor = 2.0`

Then
```
modified_price = current_price - min_price_difference / self.soften_price_difference_on_charging_factor
              = 0.30 - (0.02 / 2.0)
              = 0.30 - 0.01
              = 0.29
```
Now, a future price will only be considered “cheaper” if it's **less than or equal** to `€0.29/kWh`. If the next hour’s price is `€0.295/kWh` (which is indeed lower than `€0.30/kWh`), Batcontrol **will not** count it as cheap enough to interrupt the current evaluation period—because `€0.295` is still higher than `€0.29.

Without `soften_price_difference_on_charging`, Batcontrol would see €0.295 as cheaper than €0.30, so it does not evaluate for chasing a marginally lower price. By introducing this “softening” factor, we allow batcontrol to decide earlier how much energy needs to be charged. This helps in scenarios where the **battery can not be charged to maximum within one hour**. It ensures Batcontrol waits for a more significant price drop before adjusting its strategy, too.

The **downside** is, that not each cent of saving is achieved.

### Charging window (spread grid charging)

* Config option: `spread_grid_charge_over_charge_window` ; true / false
* Default: enabled
* Only effective when `soften_price_difference_on_charging` is enabled

With soften enabled, batcontrol already decides during a cheap block how much energy needs to be
charged. Without a charging window, the charge rate is calculated for the remaining time of the
**current** slot only. With 15-minute slots this requests very high charge rates (often capped at
`max_grid_charge_rate`) and the battery is full long before the cheap block ends.

With the charging window enabled, all directly following slots with a price **lower than or equal
to** the current price are treated as one charging opportunity. The window ends at the first more
expensive slot (even if it is only marginally more expensive) or at the end of the recharge
evaluation window.

```
charge_rate = recharge_energy / (remaining time of current slot + slots in window * slot length)
```

Example: at 12:15 the price stays at 0.086 €/kWh until 15:00, 6.86 kWh need to be recharged:

* without charging window: 6.86 kWh / 0.25 h × 1.1 ≈ 30 kW (capped at `max_grid_charge_rate`)
* with charging window: 6.86 kWh / 2.75 h × 1.1 ≈ 2.7 kW

The charge rate is recalculated on every run. As the window gets shorter, the rate rises, so the
target energy is still reached before the expensive period starts. This leaves room for PV
production during the cheap block. Set `spread_grid_charge_over_charge_window: false` to restore
the previous behaviour (charge as fast as possible within the current slot).

# Granularity in price calculations:
* Config option: `round_price_digits`
* Default: 4

## round_price_digits

**Config option**: `round_price_digits`  
**Default**: `4`

This option defines how many decimal places the algorithm uses when comparing electricity prices. By default, Batcontrol rounds prices to 4 decimal places (e.g., `0.30345` becomes `0.3035`). Since Batcontrol relies on precise comparisons (e.g., "greater than" or "less than") to determine when to charge or discharge the battery, the number of decimal places can significantly affect how it perceives small price differences.

### Why Does It Matter?

- **Higher Precision (more decimal places)**  
  If you increase `round_price_digits`, Batcontrol will consider smaller price differences. This can lead to more frequent adjustments if tiny price variations cause the algorithm to switch between charging and not charging.

- **Lower Precision (fewer decimal places)**  
  If you decrease `round_price_digits`, Batcontrol ignores subtle fluctuations below that rounding threshold. As a result, the algorithm behaves more conservatively and won't react to very small price movements.

### Example

- **4 decimal places (default)**  
  - A price of `0.30345 €/kWh` is rounded to `0.3035 €/kWh`.
  - A price of `0.30344 €/kWh` is rounded to `0.3034 €/kWh`.
  - The difference (`0.3035 - 0.3034`) is `0.0001 €/kWh`. 

- **2 decimal places**  
  - Both `0.30345 €/kWh` and `0.30344 €/kWh` become `0.30 €/kWh`.
  - The difference disappears, so Batcontrol sees them as the same price.

By adjusting `round_price_digits`, you can fine-tune how sensitive Batcontrol is to minor variations in the price curve. If your tariff data is very precise and you want every tiny fluctuation to matter, use more decimal places. If you prefer a more stable, less reactive charging strategy, you can reduce the decimal precision.

# Adjust Solar Production Forecast
* Config option: `production_offset_percent`
* Default: 1.0

## production_offset_percent (since 0.6.1)

**Config option**: `production_offset_percent`
**Default**: `1.0`

This option allows you to adjust the solar production forecast by a percentage multiplier. This is particularly useful for scenarios where your actual solar production differs systematically from the forecast—such as during winter months when solar panels may be partially covered with snow, or when dust/dirt affects panel efficiency.

### How It Works

The `production_offset_percent` parameter multiplies the entire solar production forecast. For example:

- `1.0` = 100% of the forecast (no adjustment, default behavior)
- `0.8` = 80% of the forecast (20% reduction, useful for winter/snow conditions)
- `1.2` = 120% of the forecast (20% increase)
- `0.5` = 50% of the forecast (50% reduction)

The adjusted forecast is then used throughout Batcontrol's decision-making logic, affecting:
- Charging recommendations based on solar production
- Battery discharge decisions
- Grid charging evaluations

### Use Cases

**Winter Mode (Snow Coverage)**
```yaml
battery_control_expert:
  production_offset_percent: 0.7  # Reduce forecast to 70% during winter
```
If panels are covered with snow, actual production may be 20-30% lower than the forecast. Using `0.7` helps Batcontrol make more conservative charging decisions and avoid over-discharging the battery when the predicted solar energy doesn't materialize.

**Panel Degradation or Dirt**
```yaml
battery_control_expert:
  production_offset_percent: 0.95  # 5% reduction for degraded panels
```
Over time, solar panels degrade slightly. If your panels are particularly dirty or aged, you can apply a small reduction factor.

**High-Efficiency Summer**
```yaml
battery_control_expert:
  production_offset_percent: 1.05  # 5% increase during peak summer
```
In optimal conditions, your system might consistently outperform forecasts. A slight increase can help maximize battery charging from available solar energy.

### Example Impact

Assume:
- Forecasted solar production: 5,000 W
- `production_offset_percent: 0.8` (winter mode)

Then:
- **Adjusted forecast**: 5,000 W × 0.8 = 4,000 W
- Batcontrol will base its decisions on 4,000 W instead of 5,000 W

This conservative approach prevents Batcontrol from over-planning battery discharge based on overly optimistic solar forecasts.

### Logging

When `production_offset_percent` differs from `1.0`, Batcontrol logs the adjustment:

```
Production forecast adjusted by 80% (factor: 0.8)
```

This helps you verify that your offset setting is being applied correctly.

# Valid values

The expert values are checked when batcontrol starts. An invalid value stops the start with a
message that names the key and the value, for example:

```
ValueError: battery_control_expert.soften_price_difference_on_charging_factor must be a positive number, got 0
```

| Parameter | Valid values |
|-----------|--------------|
| `charge_rate_multiplier` | number greater than 0 |
| `soften_price_difference_on_charging` | `true` / `false` |
| `soften_price_difference_on_charging_factor` | number greater than 0 (it is used as a divisor) |
| `spread_grid_charge_over_charge_window` | `true` / `false` |
| `round_price_digits` | whole number |
| `production_offset_percent` | number, 0 or greater |
| `preserve_min_grid_charge_soc` | `true` / `false` |

Booleans may also be written as `yes` / `no`, `on` / `off` or `1` / `0`, and numbers as text
(`"5"`), which is what a form field of the Home Assistant add-on delivers. The text `"false"` is
read as `false`.
