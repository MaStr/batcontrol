## Understanding `min_price_difference` and the Relative Approach

In [Issue #84](https://github.com/muexxl/batcontrol/issues/84), the idea arose to make the parameter `min_price_difference` more flexible. Originally, it was a fixed absolute value, like `€0.03`, meaning Batcontrol would only consider future prices “high enough” if they exceeded the current price by at least `€0.03`. However, when prices vary a lot (e.g., from `€0.20` to `€1.00`), a fixed amount may not always make sense.

This feature is introduced in version 0.4.0, but defaults to `0%` (disabled).

### How It Works

1. **Absolute threshold** (`min_price_difference`)  
   - Example: `€0.05`  
   - Batcontrol checks if the future price is at least `€0.05` higher than the current price.

2. **Relative threshold** (`min_price_difference_relative`)  
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
