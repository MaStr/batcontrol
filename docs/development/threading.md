# Threading Model and the Control Lock

batcontrol is a single process with several threads. Commands from MQTT and
evcc arrive while the control cycle is running, and all of them end up at the
same inverter. This page describes which thread does what and how they are
kept from overwriting each other.

## Threads

| Thread | Runs | Touches the inverter |
| --- | --- | --- |
| Main thread | The control cycle (`Batcontrol.run()`), every 3 minutes; `shutdown()` | yes |
| `batcontrol-commands` (a `SerialExecutor`) | Every MQTT `/set` callback: `api_set_*`, the grid charge lock topic, the inverters' own set topics (Fronius `em_mode`, ...) | yes |
| evcc network thread (paho) | evcc callbacks: `set_discharge_blocked`, the discharge/grid charge limit setters | yes |
| MQTT network thread (paho) | Receives messages; queues `/set` callbacks on `batcontrol-commands`; delivers MQTT inverter state | no |
| Scheduler thread | Periodic provider refreshes (tariff, solar, consumption) | no |
| Web server threads | REST API, dashboard, MCP - read only | no |

## The control lock

`Batcontrol` owns one re-entrant lock, `_control_lock`. It serializes:

- the **decide-and-apply part of a control cycle**: inverter readings, the
  API override and external discharge block checks, the calculation, the
  inverter mode command and publishing the result;
- every **external entry point** that changes control state or talks to the
  inverter. These methods carry the `@_serialized` decorator in `core.py`
  (all `api_set_*`, `set_discharge_blocked`, the limit setters, the mode
  commands and `refresh_static_values`);
- every MQTT `/set` callback, because the `batcontrol-commands` worker takes
  the lock around each call. This covers the inverters' own set topics, which
  do not go through `core.py`;
- the inverter restore in `shutdown()`. Commands arriving afterwards are
  ignored.

What this guarantees: an external command that arrives while a cycle is
running waits and is applied **after** the cycle. Before the lock existed, an
evcc discharge block could arrive between the cycle's block check and its mode
command; the cycle then switched to "allow discharging" after evcc had
switched to "avoid discharging", and the battery discharged despite the block
until the next cycle. Two threads also could not talk to the inverter at the
same time any more (Fronius HTTP session with digest authentication, Modbus
TCP socket).

### What runs without the lock

Fetching the forecasts (`__fetch_forecasts`) happens **before** the cycle takes
the lock. Providers may wait for HTTP (or for their rate limit), and external
commands must not wait for that. The fetch reads no control state. Building the
forecast arrays (which applies the runtime `production_offset`) happens under
the lock.

### How long the lock is held

Under the lock the cycle reads the inverter (SOC, capacities, per-inverter API
values), runs the calculation (milliseconds) and sends one mode command.
Publishing to MQTT only queues the message in paho and does not wait.

- Normally: well below a second for Modbus and the MQTT inverter, about a
  second for the Fronius HTTP API.
- Worst case, inverter failing: the Fronius backend retries a request up to
  3 times with a 30 s timeout and 60 s pause, and the login with 10 s pauses,
  so a cycle can hold the lock for minutes. A command that waits meanwhile
  would talk to the same failing inverter anyway. The resilient wrapper ends
  the cycle on the first failing call, which bounds it to roughly one such
  retry sequence.
- MQTT inverter at startup: `get_SOC()` waits up to 180 s for the first SOC
  message, in the first cycle, under the lock. That message arrives on the
  MQTT network thread, which is why `/set` callbacks never wait for the lock
  on that thread (see below). This happens on every startup with an MQTT
  inverter whose SOC is late, not only with a failing inverter.

## Why MQTT commands run on their own thread

paho runs every message callback on its single network thread. If an
`api_set_*` callback waited there for the control lock, the whole client would
stall: no keepalive and no further messages - including the MQTT inverter's SOC
messages. A cycle that holds the lock and waits for such a SOC message would
wait for a thread that waits for it. `MqttApi.set_command_executor()` therefore
hands the callbacks to the `SerialExecutor`: the network thread only queues
them, and the worker runs them one by one, in arrival order, under the lock.

evcc callbacks run directly on the evcc client's network thread and block it
while a cycle holds the lock. That is safe because the cycle never waits for
the evcc client - it only reads the evcc state attributes. The lock can be held
long in two situations: a failing inverter (see above) and the first cycle
with an MQTT inverter, while `get_SOC()` waits up to 180 s for the first SOC
message. evcc is started in `Batcontrol.__init__`, before the first cycle, so
its retained messages arrive right then and the evcc network thread waits for
the lock until the SOC arrives. The evcc client uses paho's default keepalive
of 60 s; a network thread blocked for more than about 90 s sends no ping, and
the broker may drop the evcc connection. paho reconnects, resubscribes, and
evcc's retained topics restore the state; the evcc block reaches the inverter
only after the wait.

## Rules for new code

- A new external entry point that changes control state or calls the inverter
  gets `@_serialized`. A new MQTT set topic needs nothing extra: it runs on the
  command worker under the lock.
- Never hold the lock while waiting for another thread that may need it. In
  particular, do not stop the MQTT or evcc client under the lock: their
  `shutdown()` joins the network thread, which may be waiting for the lock in
  an evcc callback.
- Raw MQTT subscriptions (`MqttApi.register_raw_subscription`, used by the
  MQTT inverter for its state topics) run on paho's network thread. They must
  never take the control lock and must only update cached state.
- Decision journal listeners run synchronously while the lock is held and must
  not block.
- Slow I/O that reads no control state (forecast providers) belongs before the
  lock in `_run_once`, not inside it.
- The web layer never takes the lock. It reads the frozen per-cycle objects
  (`ForecastSnapshot`, `runtime_state.py`), see the dashboard specification.
