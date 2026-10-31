# Typed DBC support

DBC support is optional and uses `cantools` for parsing and decoding. Install it with:

```bash
python -m pip install "SilKit_py_scenario_runner[dbc]"
```

Generate an IDE-discoverable Python module from your DBC file:

```bash
python -m silkit_runner.dbc generate vehicle.dbc --output vehicle_signals.py
```

The generated module contains immutable definitions, not mutable signal values:

```python
from vehicle_signals import Vehicle
from silkit_runner.dbc import bind_dbc_signal

speed = bind_dbc_signal(
    scenario,
    Vehicle.VehicleStatus.VehicleSpeed,
    network="CAN 1",
)
scenario.at(500).expect(speed.between(30, 80))
```

Signal attributes are derived from DBC message and signal names. Invalid identifier characters become underscores, leading digits gain an underscore, and Python keywords gain a trailing underscore. Names that collide after normalization cause generation to fail.

The generated definitions embed the absolute DBC file path. Regenerate after moving or changing the DBC file. At runtime, binding checks the frame identifier and signal name against the DBC file and uses cantools to decode physical values, including scaling and byte order. For a scenario with exactly one CAN network, `network` may be omitted. For multiple networks, specify it explicitly.

## Message-oriented CAN transmission (recommended)

For a complete message, stage several signals and transmit only when requested:

```python
from silkit_runner.dbc import bind_dbc_vehicle
from vehicle_signals import Vehicle

vehicle = bind_dbc_vehicle(scenario, Vehicle, network="CAN 1")
vehicle.VehicleStatus.EngineSpeed.set(800)
vehicle.VehicleStatus.VehicleSpeed.set(35)
scenario.at(100).do(vehicle.VehicleStatus.send)
```

Setting values does **not** send a CAN frame. Calling `send()` encodes the entire message once. This prevents accidental multiple transmissions when updating several signals. The generated `Vehicle` definitions provide IDE completion; the runtime-bound message facade resolves its attributes dynamically.

## Transmitting individual CAN signals

Use `bind_dbc_sender` to create an object compatible with `scenario.at(...).set(...)` and `scenario.every(...).set(...)`:

```python
from silkit_runner.dbc import bind_dbc_sender
from vehicle_signals import Vehicle

speed_tx = bind_dbc_sender(
    scenario,
    Vehicle.VehicleStatus.VehicleSpeed,
    network="CAN 1",
    initial_values={"EngineSpeed": 800},
)
scenario.at(100).set(speed_tx, 35)
scenario.every(20, end_ms=200).set(speed_tx, 40)
```

Each `set()` call encodes the **complete DBC message** and sends it through the selected SIL Kit CAN controller. Other signal values in the message are retained between sends. Initial values default to the DBC signal's `initial` value or zero; provide `initial_values` to initialize companion signals explicitly. Encoding and range validation are performed by cantools. A standard CAN frame uses flags `0`, while an extended frame uses `CanFrameFlag.Ide`. The sender does not implement cyclic transmission independently; schedule it using the scenario API. `time_ms` is for scheduler compatibility, not an explicit CAN timestamp.

**Compatibility:** PR #2 is merged into `main` and provides network-aware receive-side CAN registration. PR #3 should be updated with the latest `main` before merging.

This version supports receive-side decoding and transmit-side complete-message encoding. It does not implement ISO-TP or replace the SIL Kit lifecycle.
