# Multiple Inverters / Batteries

Since 0.9.1 the `inverter:` section accepts a **list** of inverters. batcontrol then treats all of them as one large battery: it sums their capacities, derives a common state of charge, and splits every charge command over the individual devices.

The single-inverter form keeps working unchanged — nothing to do if you run one battery.

Not available in the Home Assistant add-on

This feature requires a YAML configuration file, so it is only available for the Docker, Docker Compose and local Python installations. The [Home Assistant add-on](https://github.com/MaStr/batcontrol_ha_addon) is configured through the add-on UI, whose `options`/`schema` form cannot express a list of inverters with per-entry type-specific keys. The add-on therefore stays on a single inverter, and the parameters on this page are intentionally **not** mirrored into the add-on configuration.

If you run two batteries under Home Assistant, use the Docker or Compose installation instead — see [Installation](https://mastr.github.io/batcontrol/getting-started/installation/index.md).

## Configuration

```
inverter:
  - type: fronius_gen24
    address: 192.168.0.10
    user: customer
    password: YOUR-PASSWORD
    max_grid_charge_rate: 5000   # Watt, limit of THIS inverter

  - type: fronius-modbus
    address: 192.168.0.11
    capacity: 5000               # Wh
    max_grid_charge_rate: 3000   # Watt, limit of THIS inverter
```

Every entry is a complete inverter configuration, exactly as described in [Inverter Configuration](https://mastr.github.io/batcontrol/configuration/inverter-configuration/index.md). The types may be mixed freely — a Fronius GEN24 next to a Modbus inverter next to an MQTT bridge is fine.

The order matters: it determines each inverter's number `n`, which is used for the MQTT subtree and the Home Assistant entity names (see [MQTT topics](#mqtt-topics)). Inserting an inverter in the middle of the list renumbers the ones behind it.

## How values are aggregated

| Value                                              | Aggregation               |
| -------------------------------------------------- | ------------------------- |
| Installed capacity, usable capacity, free capacity | sum                       |
| Stored energy, stored usable energy                | sum                       |
| State of charge                                    | capacity-weighted average |
| `min_soc`, `max_soc`                               | capacity-weighted average |
| `max_grid_charge_rate`                             | sum                       |
| `max_pv_charge_rate`                               | sum, but see below        |
| `min_pv_charge_rate`                               | sum                       |

A battery with twice the capacity therefore carries twice the weight in the group SoC. The logic that decides *when* to charge works on these aggregated numbers and is unaware of how many physical batteries there are.

### max_pv_charge_rate is special

For a single inverter `0` means "no limit". That carries over to the group: if **any** inverter has `max_pv_charge_rate: 0`, the whole group counts as unlimited, because batcontrol cannot bound the total PV charge power any more. Only if every inverter declares a positive limit is the group limit their sum.

## How charge rates are distributed

When the logic asks for a charge rate, the group splits it over the inverters **proportional to their free capacity**, so an empty battery gets more power than a nearly full one and both reach their target at roughly the same time. Each share is capped by that inverter's own `max_grid_charge_rate`; power that does not fit is redistributed over the remaining inverters.

Example — 6000 W requested:

|            | Capacity | SoC  | Free capacity | Own limit | Share                  |
| ---------- | -------- | ---- | ------------- | --------- | ---------------------- |
| Inverter 0 | 10000 Wh | 20 % | 7500 Wh       | 5000 W    | **5000 W** (capped)    |
| Inverter 1 | 5000 Wh  | 80 % | 750 Wh        | 3000 W    | **1000 W** (remainder) |

Inverter 0 would get 5454 W by its share of the free capacity, is capped at its 5000 W limit, and the leftover 1000 W moves to inverter 1.

If the requested rate exceeds the sum of all limits, each inverter simply runs at its own maximum.

### The minimum charge rate is never split away

Charging a battery with a few hundred watts is inefficient, which is why the logic layer already raises any group charge rate to `MIN_CHARGE_RATE` (500 W). Splitting 500 W into 250 W + 250 W would throw that guarantee away, so the distribution works the other way round:

1. Walk the inverters in order of free capacity and take as many as the requested rate can pay a full minimum for.
1. Give each of them its minimum.
1. Spread whatever is left over those same inverters, proportional to free capacity.

The inverters the rate does not reach get 0 W. The requested total is always kept exactly — batcontrol never buys more grid power than the optimizer asked for.

For two inverters with 7500 Wh and 250 Wh free capacity and a 500 W minimum each:

| Requested | Inverter 0 | Inverter 1 | Comment                                        |
| --------- | ---------- | ---------- | ---------------------------------------------- |
| 300 W     | 300 W      | 0 W        | below one minimum, goes to the emptier battery |
| 500 W     | 500 W      | 0 W        | pays exactly one minimum                       |
| 800 W     | 800 W      | 0 W        | not enough for a second minimum                |
| 1000 W    | 500 W      | 500 W      | pays two minima, no remainder                  |
| 1200 W    | 694 W      | 506 W      | two minima plus 200 W split 7500 : 250         |
| 2000 W    | 1468 W     | 532 W      | two minima plus 1000 W split 7500 : 250        |
| 6000 W    | 5000 W     | 1000 W     | inverter 0 capped at its 5000 W limit          |

As a result the two batteries drift apart in SoC while the requested rate is small, and converge again once it is large enough to feed both.

### min_charge_rate

The minimum defaults to 500 W per inverter and can be raised per inverter if a device needs more to charge efficiently. It only takes effect with several inverters — with a single inverter batcontrol already raises every charge rate to 500 W, so there is nothing left to distribute:

```
inverter:
  - type: fronius_gen24
    address: 192.168.0.10
    user: customer
    password: YOUR-PASSWORD
    max_grid_charge_rate: 5000
    min_charge_rate: 1000      # this inverter wants at least 1000 W
  - type: fronius-modbus
    address: 192.168.0.11
    capacity: 5000
    max_grid_charge_rate: 3000
```

An inverter whose `max_grid_charge_rate` is below its own `min_charge_rate` can never charge efficiently and is skipped unless it is the only candidate.

For PV charge limiting the equivalent floor is `min_pv_charge_rate`, which defaults to `0` (no minimum) and therefore keeps the plain proportional split unless you set it.

### A full battery is set to avoid-discharge

An inverter that receives **no** share during grid charging is not left in its previous mode — it is set to *avoid discharge*. Without this, a full battery would happily discharge into the battery that is being charged from the grid, which would cycle both batteries for nothing and draw grid power that never gets stored.

### PV charge limiting

`MODE_LIMIT_BATTERY_CHARGE_RATE` (mode 8) is distributed the same way, with each share capped by the inverter's own `max_pv_charge_rate` and floored at its `min_pv_charge_rate`. A requested limit of `0` (block charging entirely) is passed to every inverter without distribution.

## MQTT topics

Each inverter keeps its own subtree under batcontrol's base topic, numbered by its position in the config list:

```
batcontrol/inverters/0/SOC
batcontrol/inverters/0/stored_energy
batcontrol/inverters/1/SOC
batcontrol/inverters/1/stored_energy
...
```

The top-level topics (`batcontrol/SOC`, `batcontrol/max_energy_capacity`, `batcontrol/stored_energy_capacity`, ...) report the **aggregated** values of the whole group, so existing dashboards keep working.

Home Assistant auto-discovery publishes one entity set per inverter, named `Inverter <n> ...` with unique IDs `batcontrol_inverter_<n>_*`.

### MQTT inverters need distinct topics

If you configure several inverters of `type: mqtt`, each needs its own `base_topic` — otherwise both would read the same status and send their commands to the same receiver. batcontrol refuses to start on a duplicate:

```
inverter:
  - type: mqtt
    base_topic: house/battery_a
    capacity: 10000
    max_grid_charge_rate: 5000
  - type: mqtt
    base_topic: house/battery_b
    capacity: 5000
    max_grid_charge_rate: 3000
```

Leaving `base_topic` at its default (`default`) for all of them is also fine: the topic is then derived from the inverter number and is unique by construction (`batcontrol/inverters/0/`, `batcontrol/inverters/1/`, ...).

## Outage handling

The [resilient wrapper](https://mastr.github.io/batcontrol/configuration/inverter-configuration/#resilient-wrapper-options-since-070) is applied **per inverter**, so `enable_resilient_wrapper` and `outage_tolerance_minutes` are configured per entry. If one inverter becomes unreachable, that inverter's wrapper reports the outage and batcontrol skips the control cycle — it does not keep controlling the remaining batteries on a partial picture, because the aggregated SoC would be wrong.

`refresh_api_values` (MQTT publishing) and `shutdown` are best-effort: a failing inverter is logged and the remaining ones are still served.

## Limitations

- **Commands are not transactional.** If an inverter fails midway through a group command, the inverters before it have already been switched. The next control cycle reconciles the state.
- **No per-inverter strategy.** All inverters follow the same mode; batcontrol does not, for example, charge one battery from the grid while discharging another.
- The `battery_control` limits (`max_charging_from_grid_limit`, `always_allow_discharge_limit`, `min_grid_charge_soc`) apply to the **group** SoC, not to individual batteries.
- **Not configurable from the Home Assistant add-on UI**, which is limited to a single inverter — see the note at the top of this page.
