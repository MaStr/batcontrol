## Understanding `min_price_difference` and the Relative Approach

In [Issue #84](https://github.com/MaStr/batcontrol/issues/84), the idea arose to make the parameter `min_price_difference` more flexible. Originally, it was a fixed absolute value, like `€0.03`, meaning Batcontrol would only consider future prices “high enough” if they exceeded the current price by at least `€0.03`. However, when prices vary a lot (e.g., from `€0.20` to `€1.00`), a fixed amount may not always make sense.

This feature is introduced in version 0.4.0, but defaults to `0%` (disabled).

### How It Works

1. **Absolute threshold** (`min_price_difference`)  
   - Example: `€0.05`  
   - Batcontrol checks if the future price is at least `€0.05` higher than the current price.

2. **Relative threshold** (`min_price_difference_rel`)  
   - Example: `0.10` (i.e., 10% of the current price)  
   - Batcontrol multiplies the current price by `0.10`.  
   - If `current_price = €0.50`, the difference is `€0.05`.  
   - If `current_price = €1.00`, the difference becomes `€0.10`.

Batcontrol then takes **whichever value is larger**—the absolute amount or the relative amount. If your price is low, you might be governed mostly by the fixed value. If your price is high, the relative difference might exceed the absolute difference, so Batcontrol won’t trigger too soon.

### Example Scenarios

1. **Prices around `€0.30`:**
   - Absolute threshold: `€0.05`  
   - Relative threshold (`10%`): `€0.30 * 0.10 = €0.03`  
   - In this case, the absolute threshold (`€0.05`) is larger, so Batcontrol waits for at least a `€0.05` jump.

2. **Prices around `€1.00`:**
   - Absolute threshold: `€0.05`  
   - Relative threshold (`10%`): `€1.00 * 0.10 = €0.10`  
   - Now the relative threshold (`€0.10`) is bigger than `€0.05`, so Batcontrol won’t start charging unless the future price is at least `€0.10` higher.

### Why This Matters

- **Fixed Absolute Value Only**: Easy to configure, but may be too small when prices get very high, or too large when prices are very low.  
- **Relative Plus Absolute**: Scales automatically with the current price, ensuring Batcontrol’s logic remains balanced across a wide range of price levels.

This change helps prevent overreacting at high prices (where a small amount like `€0.03` is negligible) and avoids unnecessary waiting at low prices (where a large absolute amount might never occur).

## Discharge reserve and grid recharge use different comparisons

The two decisions that depend on future prices do not look at the same slots. Both start from the current price and use the dynamic minimum price difference (`max(min_price_difference, min_price_difference_rel * current price)`).

**Discharge reserve** (should the battery keep its energy instead of discharging?):

- The look-ahead window ends at the first slot whose price is lower than the current price by at least the dynamic minimum price difference (`price <= current price - min difference`).
- Within that window **every** slot with a price above the current price counts, with no minimum difference. The net consumption of these slots (reduced by PV production in earlier slots) is reserved.
- A cheaper slot ends the window because the battery can be recharged there. That only holds for what the inverter can charge in it. If more expensive slots follow behind the cheaper ones, the part of their net consumption that the cheaper slots cannot recharge at `max_grid_charge_rate` is reserved as well:

    ```
    capacity of one cheaper slot = max_grid_charge_rate / max(charge_rate_multiplier, 1) * slot length
    reserved in addition         = demand of the more expensive slots behind - capacity of the cheaper slots before them
    ```

    This looks at the rest of the forecast, in time order: every cheaper slot adds capacity, every slot above the current price adds demand, and the largest gap between the two is reserved. Energy kept now only helps as long as the battery has room for it on top of what the cheaper slots charge, so the reserve is never larger than that room (battery filled to `max_charging_from_grid_limit`, minus what the cheaper slots charge). Once the cheaper slots can fill the battery, nothing more is reserved.

    A single cheap 15-minute slot recharges about 1.1 kWh at 5000 W, so this mostly matters at 15-minute resolution. It applies to 60-minute slots in the same way: one cheap hour (about 4.5 kWh) in front of a long, more expensive stretch reserves energy too.
- Discharge is allowed only while the usable stored energy exceeds this reserve.

**Grid recharge** (should the battery be charged from the grid?):

- The look-ahead window ends at the first slot whose price is lower than or equal to the current price. With `soften_price_difference_on_charging` enabled, the slot must be lower by `min_price_difference / soften_price_difference_on_charging_factor` instead.
- Within that window only slots **more expensive than the current price plus the dynamic minimum price difference** count. Their net consumption (reduced by PV production in earlier slots), minus the usable stored energy, is the energy to charge.

**Consequence:** energy can be held back (no discharge) without any grid charging happening. A slot that is only slightly more expensive than the current one reserves energy for the discharge decision but is too close in price to justify buying energy from the grid.

### Example: a short price dip before an expensive block

15-minute slots, `max_grid_charge_rate: 5000`, `charge_rate_multiplier: 1.1`. The current price is 0.30 €/kWh, the next slot costs 0.20 €/kWh, the eight slots after it cost 0.40 €/kWh and need 4 kWh in total.

| | Reserve | Result with 2 kWh usable energy |
|---|---|---|
| Before | 0 kWh - the window ends at the cheap slot | Discharge allowed now; the cheap slot recharges 1.1 kWh, the rest of the expensive block is bought at 0.40 €/kWh |
| Now | 4 kWh - 1.1 kWh = 2.9 kWh | Discharge is held back; the grid recharge rule then decides whether charging at 0.30 €/kWh pays off |

The decision trace shows this in the inputs `reserve_behind_cheaper_slot`, `cheaper_slots_charge_capacity` and `higher_price_slots_behind` of the discharge decision.

### Known limitations

- The charge capacity of a cheaper slot is an estimate. It is not reduced by consumption that is only expensive seen from the price of that cheaper slot.
- The grid recharge rule uses the same capacity estimate and the same limit: for demand behind cheaper slots it charges at most the room the battery has left after those slots, and nothing if they can fill it.
- With `preserve_min_grid_charge_soc`, only a reserve inside the window activates the SoC target, as before; the reserve behind a cheaper slot is added without it.
- If the inverter does not report a `max_grid_charge_rate`, a cheaper slot is assumed to recharge any amount, as in earlier versions.
