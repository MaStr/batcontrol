Currently following data providers are available:

* tibber
* awattar
* Two-Tariff Providers (e.g. Octopus)
* evcc
* energyforecast.de

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
  modest value (e.g. battery capacity ÷ low-price hours).
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

## energyforecast.de (0.5.6)
[energyforecast.de](https://www.energyforecast.de) provides a calculated forecast for upcoming prices. Dayahead prices are populated at 14:00 GMT+2, which is after the lunch-drop in prices and prevents a good energy calculation. Based on different values, energyforecast.de calculates a price expectation with a median of 3 cent of. 
batcontrol uses the 48h forecast only and it is not possible to activate the 96 hour forecast. You need to setup VAT, markup (+ % on energy price) and fees (Netzentgeld) in the configuration. We are not using the calculation provided by energyforecast.de.
If you like to use this forecast type, please create a login at [energyforecast.de](https://www.energyforecast.de) to aquire an API key.

```
utility:
  type: energyforecast
  apikey:  xxxxxxxxx
  vat: 0.19     # 19% VAT
  fees: 0.15    # Depends on you Netzendgeld
  markup: 0.00  # Depends on you aWATTar contract
```

To enable the paid 96h forecast, use type: energyforecast_96

## Dynamic network fees (§14a EnWG)

Batcontrol can add time-of-use network fees (NT/ST/HT zones according to §14a EnWG) on top of the energy prices. This only applies to providers that calculate fees locally: **awattar** and **energyforecast**. All-inclusive providers (tibber, evcc, tariff_zones) already deliver final prices and do not need this.

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