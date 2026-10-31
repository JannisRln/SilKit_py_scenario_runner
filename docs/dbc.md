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

**Compatibility:** On the current main branch, the CAN registry does not yet distinguish networks. Binding a specific network requires the network-aware registry introduced in PR #2. Until that PR is merged, use this feature only with single-network scenarios. DBC generation itself works independently.

This initial version supports receive-side signal decoding; it does not yet generate CAN transmit APIs, implement ISO-TP, or replace the SIL Kit lifecycle.
