Currently following data providers are available:

* tibber
* awattar
* Two-Tariff Providers (e.g. Octopus)
* evcc
* energyforecast.de
* energyforecast.de (total price mode)

You can chose one and need to adjust the configuration.

## tibber
You need to get an API Key from https://developer.tibber.com/ , which looks like `Zz-XXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXXx`. After obtaining this key, use the following configuration:

```
utility:
  type: tibber 
  apikey: YOUR-PASSWORD
```


## awattar
batcontrol provides two different awattar types:

* `awattar_de` for German aWATTar
* `awattar_at` for Austrian aWATTar

Please choose the corresponding version. For aWATTar you can use this configuration:

```
utility:
  type: awattar_de
  vat: 0.19     # 19% VAT
  fees: 0.015   # Depends on you Netzendgeld
  markup: 0.03  # Depends on you aWATTar contract
```

The calculation is `( marketprice/1000*(1+markup) + fees ) * (1+vat)`

## Multi-Zone Tariff Providers (e.g. Octopus, Two-Tariff) (since 0.7.0)

If your energy provider offers distinct tariff zones (e.g. day/night rates, peak/off-peak), you can configure batcontrol to optimize battery usage accordingly. The `tariff_zones` provider supports up to **3 different price zones**.

## `tariff_zones` — Static & multi-zone tariff

The `tariff_zones` provider uses a locally configured, fixed price schedule
instead of an external API. It supports **1, 2, or 3 zones**:

| Zones configured          | Behaviour                                              |
|---------------------------|--------------------------------------------------------|
| **1 zone** (static price) | One flat price for every hour of the day              |
| **2 zones**               | Classic peak / off-peak split                          |
| **3 zones**               | Peak / shoulder / off-peak                             |

> **Since v0.8.0:** `tariff_zone_2` / `zone_2_hours` are **optional**. In
> earlier versions zone 2 was mandatory. Configurations written for the old
> 2-zone-only behaviour keep working unchanged.

This makes it suitable for:

- Users **without a dynamic tariff** who still want peak-shaving (see issue
  [#318](https://github.com/MaStr/batcontrol/issues/318)).
- Users with a fixed day / night tariff.
- Users with a fixed three-period (HT/NT/shoulder) tariff.

### Static price mode (single zone)

> Available since **v0.8.0**.

Set only `tariff_zone_1`. `zone_1_hours` is optional and defaults to all 24
hours.

```yaml
utility:
  type: tariff_zones
  tariff_zone_1: 0.30   # EUR/kWh incl. VAT/fees, applied to every hour
```

### Two zones (peak / off-peak)

```yaml
utility:
  type: tariff_zones
  tariff_zone_1: 0.2733   # peak price
  zone_1_hours: 7-22
  tariff_zone_2: 0.1734   # off-peak price
  zone_2_hours: 0-6,23
```

### Three zones (peak / shoulder / off-peak)

```yaml
utility:
  type: tariff_zones
  tariff_zone_1: 0.30
  zone_1_hours: 9-16
  tariff_zone_2: 0.15
  zone_2_hours: 0-6,23
  tariff_zone_3: 0.22
  zone_3_hours: 7-8,17-22
```

### Configuration reference

| Key              | Required                         | Description                                     |
|------------------|----------------------------------|-------------------------------------------------|
| `type`           | yes                              | Must be `tariff_zones`                          |
| `tariff_zone_1`  | **yes**                          | Price for zone 1 in EUR/kWh (incl. VAT & fees)  |
| `zone_1_hours`   | optional in single-zone mode; required otherwise | Hours assigned to zone 1 |
| `tariff_zone_2`  | optional since v0.8.0 (paired with `zone_2_hours`) | Price for zone 2          |
| `zone_2_hours`   | optional since v0.8.0 (paired with `tariff_zone_2`) | Hours assigned to zone 2 |
| `tariff_zone_3`  | optional (paired with `zone_3_hours`) | Price for zone 3                           |
| `zone_3_hours`   | optional (paired with `tariff_zone_3`) | Hours assigned to zone 3                  |

#### Hour syntax

`zone_*_hours` accepts flexible formats (may be mixed):

- Single integer: `5`
- Comma-separated list: `0,1,2,3`
- Inclusive range: `0-5`  →  `[0, 1, 2, 3, 4, 5]`
- Mixed: `0-5,6,7`
- YAML list with any of the above: `[7, 8, '17-22']`

#### Validation rules

- `tariff_zone_1` is always required. All prices must be positive.
- If `tariff_zone_2` is set, `zone_2_hours` **must** also be set (and vice
  versa). Same rule applies to zone 3.
- **Every hour 0–23 must be assigned to exactly one zone** — no gaps, no
  overlaps. If you configure `zone_1_hours` explicitly in single-zone mode,
  it must cover the full day (it is **not** auto-extended); omit it to get
  the default `0-23`.
- `zone_1_hours` may only be omitted when zones 2 and 3 are also omitted
  (single-zone / static mode, v0.8.0+).

### Charging behaviour tips

The charge rate is not evenly distributed across low-price hours by default.

- For **more even charging** across low-price hours, enable
  `soften_price_difference_on_charging` and set `max_grid_charge_rate` to a
  modest value (e.g. battery capacity / low-price hours).
- For a **late charging start** (optimise efficiency, keep the battery at
  high SOC for less time), disable `soften_price_difference_on_charging`.

In pure single-zone (static) mode, prices never differ between hours, so
price-based scheduling has no effect — use it together with peak-shaving
(see [#271](https://github.com/MaStr/batcontrol/issues/271)) or fixed
charging-window settings.

## evcc
If you are running evcc, it can be used to fetch the price information from this endpoint.
The configuration for this is

```
utility:
  type: evcc
  url: http://evcc.local:7070/api/tariff/grid
```
You may need to adjust hostname + port for your setup. If evcc is running under HomeAssistant, you should use either `http://homeassistant:7070/api/tariff/grid` or `http://<homeassistant-ip>:7070/api/tariff/grid`

## energyforecast.de

[energyforecast.de](https://www.energyforecast.de) provides a multi-day price forecast using
API v2. The API delivers quarter-hourly data for a plan-dependent horizon (typically several
days ahead). Day-ahead prices are populated at around 14:00 CET, which provides good coverage
for next-day planning.

batcontrol fetches the raw market price (`price_ct_kwh`) from the API and calculates the
final price locally by applying your configured VAT, fees, and markup — the same approach used
for aWATTar. This gives you full control over the price components.

To use energyforecast.de, create a login at [energyforecast.de](https://www.energyforecast.de)
to obtain an API key.

```yaml
utility:
  type: energyforecast
  apikey: xxxxxxxxx
  vat: 0.19     # 19% VAT
  fees: 0.15    # Your network fees (Netzentgelt), EUR/kWh excl. VAT
  markup: 0.00  # Optional markup, e.g. supplier margin
  # market_zone: DE  # optional: DE/LU (default, both map to DE-LU market zone), AT, FR, NL, BE, PL, DK1, DK2
```

The price calculation is: `( price_ct_kwh/100 * (1+markup) + fees ) * (1+vat)`

> **Note:** `energyforecast_96` is deprecated. API v2 automatically delivers a
> multi-day forecast based on your plan -- use `energyforecast` instead.

## energyforecast.de -- total price mode

If you prefer to let the energyforecast.de API calculate the full price -- including
dynamic network fees, VAT, and markup -- use `energyforecast_total_price`. In this mode
batcontrol reads the API's `total_ct_kwh` field directly without any local calculation.

This is useful if:

- You want the API to handle dynamic network fees automatically (including para. 14a EnWG),
  without configuring them separately in batcontrol.
- You trust the API's price model and want a simpler local configuration.

```yaml
utility:
  type: energyforecast_total_price
  apikey: xxxxxxxxx
  # market_zone: DE  # optional: DE/LU (default, both map to DE-LU market zone), AT, FR, NL, BE, PL, DK1, DK2
```

**Important:** Do not set `vat`, `fees`, or `markup` -- they are ignored for this provider
type. Do not enable `dynamic_network_fees` either; the API already includes network fees in
`total_ct_kwh`.

## Dynamic network fees (para. 14a EnWG)

batcontrol can add time-of-use network fees (NT/ST/HT zones according to para. 14a EnWG)
on top of the energy prices. This only applies to providers that calculate fees locally:
**awattar** and **energyforecast**. All-inclusive providers (tibber, evcc, tariff_zones,
and **energyforecast_total_price**) already deliver final prices and do not need this.

The fee data (NET prices, excluding VAT) is fetched from [dyn-net.batcontrol.software](https://dyn-net.batcontrol.software/) and added to the raw energy price before VAT is applied. Data is cached for 12 hours, as tariffs change at most quarterly.

Configure it as a top-level block (next to `utility`):

```yaml
dynamic_network_fees:
  enabled: true         # Set to true to activate dynamic network fees
  country: de           # Country code: de, at, ch
  operator: syna        # Network operator ID (e.g. syna, westnetz, ggv, ewr-netz)
  # url: https://dyn-net.batcontrol.software/api/  # Optional: override for self-hosted instance
```

| Key        | Required | Description                                                        |
|------------|----------|--------------------------------------------------------------------|
| `enabled`  | yes      | Master switch, defaults to `false`                                 |
| `country`  | yes      | Country code (`de`, `at`, `ch`)                                    |
| `operator` | yes      | Your network operator ID — see [dyn-net.batcontrol.software](https://dyn-net.batcontrol.software/) for available IDs |
| `url`      | no       | Override the API endpoint, e.g. for a self-hosted instance         |
