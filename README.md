# SIL Kit Python Scenario Runner

An **ECU-independent Python framework** for scheduling SIL Kit simulations, exchanging signals, and validating participant behavior with `pytest`.

Built on [SilKit_py](https://github.com/JannisRln/SilKit_py), the Python bindings for [Vector SIL Kit](https://github.com/vectorgrp/sil-kit). This project provides a higher-level testing layer; it is not a replacement for the underlying SIL Kit API.

> **Project status:** Early release (v0.1.1). The runner API and its integration-test setup may evolve.

## Features

- **Simulation-time scheduling:** one-shot actions, periodic actions, pulses, ramps, and modulation.
- **Assertions:** `expect()`, `expect_eventually()`, and invariants evaluated against simulation state.
- **Signals:** SIL Kit Pub/Sub signals with configurable encoders and decoders.
- **CAN support:** configure only the CAN networks your test needs and dispatch CAN events.
- **Basic UDS helpers:** DID-oriented request/response helpers over CAN (not a complete ISO-TP or CAN FD stack).
- **Logging:** collect signal samples in memory without requiring MDF4 libraries.
- **pytest integration:** use ordinary fixtures and tests to describe scenarios.
- **ECU independence:** no hardcoded ECU datasets, RPC names, C adapters, or machine-specific paths.

## Documentation

See the [documentation index](docs/index.md) for setup and troubleshooting, and the [Fluent API reference](docs/fluent-api.md) for all scheduling builders, actions, assertions, and predicates.

## Requirements

- Python **3.12–3.14** as declared by the runner package metadata. Use a Python version and operating system for which a compatible **SilKit_py** distribution is available on PyPI.
- `SilKit_py` (installed automatically as a package dependency).
- For live integration tests: a running SIL Kit registry, an appropriate system controller for coordinated lifecycle execution, and the participant or system under test.

The runner does **not** launch the registry, system controller, or ECU itself. SIL Kit tools can be obtained through the SIL Kit installation or the executables included with compatible `SilKit_py` distributions.

## Installation

**After this runner's first PyPI release**, install it with:

```bash
python -m pip install SilKit_py_scenario_runner
```

`pip` will resolve `SilKit_py` from PyPI automatically; you no longer need to download or install a local binding wheel.

Until the runner has been published, install it directly from GitHub:

```bash
python -m pip install "git+https://github.com/JannisRln/SilKit_py_scenario_runner.git"
```

For test development, install the optional `pytest` dependencies:

```bash
python -m pip install "SilKit_py_scenario_runner[test]"
```

> **Package naming:** The commands above use the intended PyPI project name `SilKit_py_scenario_runner`. The Python import name remains `silkit_runner`.

## Quick start: a pytest scenario

A scenario describes what to publish, when to publish it in **simulation time**, and what result to expect from another SIL Kit participant.

```python
import os

import pytest
from silkit_runner import Scenario, SilKitSignal


@pytest.fixture
def scenario():
    if os.getenv("SILKIT_RUN_INTEGRATION") != "1":
        pytest.skip("Live SIL Kit environment is not enabled")

    return Scenario(
        participant_name="scenario_example",
        uri=os.getenv("SILKIT_REGISTRY_URI", "silkit://localhost:8500"),
        can_networks=(),  # Pub/Sub only
        end_ms=300,
    )


@pytest.mark.integration
def test_speed_echo(scenario):
    requested = SilKitSignal(
        "requested_speed", scenario.participant, "input.speed", initial=0
    )
    observed = SilKitSignal(
        "observed_speed", scenario.participant, "output.speed", initial=None
    )

    scenario.at(0).set(requested, 100)
    scenario.at(200).expect(
        observed.is_equal(100),
        message="The peer did not publish speed=100",
    )

    scenario.run()
```

This test expects a **separate echo participant** that subscribes to `input.speed` and publishes the received signed 32-bit little-endian value to `output.speed`. It does not validate a signal by merely reading back a value that the scenario wrote itself.

The repository contains a runnable version at [`examples/test_basic_scenario.py`](examples/test_basic_scenario.py).

### Run the integration example

Start the SIL Kit registry, system controller, and the matching peer participant first. Include `scenario_example` in the coordinated lifecycle configuration.

**PowerShell (Windows):**

```powershell
$env:SILKIT_RUN_INTEGRATION = "1"
$env:SILKIT_REGISTRY_URI = "silkit://localhost:8500"
python -m pytest -v examples/test_basic_scenario.py
```

**Bash (Linux):**

```bash
SILKIT_RUN_INTEGRATION=1 \
SILKIT_REGISTRY_URI=silkit://localhost:8500 \
python -m pytest -v examples/test_basic_scenario.py
```

Without `SILKIT_RUN_INTEGRATION=1`, the example is skipped so it does not hang while waiting for an unavailable SIL Kit environment. `Scenario.run()` runs the configured lifecycle and blocks until it finishes.

## Typed DBC signals (optional)

Generate Python definitions with IDE autocomplete instead of using signal-name strings:

```bash
python -m pip install "SilKit_py_scenario_runner[dbc]"
python -m silkit_runner.dbc generate vehicle.dbc --output vehicle_signals.py
```

```python
from vehicle_signals import Vehicle
from silkit_runner.dbc import bind_dbc_signal

speed = bind_dbc_signal(scenario, Vehicle.VehicleStatus.VehicleSpeed, network="CAN 1")
scenario.at(500).expect(speed.greater_than(30))
```

For message-oriented sending without signal-name strings, use `bind_dbc_vehicle(scenario, Vehicle, network="CAN 1")`, stage values with `vehicle.VehicleStatus.EngineSpeed.set(800)` and `vehicle.VehicleStatus.VehicleSpeed.set(35)`, then schedule `scenario.at(100).do(vehicle.VehicleStatus.send)`. Setting values does not transmit until `send()` is called.

The generated classes describe DBC messages and signals. `bind_dbc_signal(...)` creates a receive-side `CanSignal` with physical-value decoding; `bind_dbc_sender(...)` creates a transmitter usable with `scenario.at(...).set(...)` and `scenario.every(...).set(...)`. A sender encodes the complete DBC message, retaining other signal values between transmissions. See [typed DBC documentation](docs/dbc.md) for limitations and network compatibility.

## Common configuration

### CAN networks

Choose your CAN networks explicitly if you do not want the default single controller:

```python
from silkit_runner import Scenario

scenario = Scenario(
    participant_name="can_test",
    can_networks=("CAN 1", "CAN 2"),
    end_ms=1_000,
)
controller = scenario.get_can_controller("CAN 1")
```

Use `can_networks=()` for Pub/Sub-only tests. Network names must match those used by your SIL Kit participants.

### Custom signal encoding

`SilKitSignal` defaults to signed little-endian 32-bit integer payloads. Use custom encoding and decoding for other data types:

```python
import struct
from silkit_runner import SilKitSignal

signal = SilKitSignal(
    name="temperature",
    participant=scenario.participant,
    topic="input.temperature",
    initial=20.0,
    encoder=lambda value: struct.pack("<f", value),
    decoder=lambda payload: struct.unpack("<f", payload)[0],
)
```

### UDS over CAN

The package includes `UdsDidSignal` and UDS request/response helpers for basic DID scenarios. The helpers do **not** implement ISO-TP segmentation or full CAN FD transport; use a dedicated transport implementation when those features are required.

### ECU-specific initialization

ECU-specific initialization belongs in your consuming test project. Use `Scenario(on_communication_ready=...)` for simple ready-time actions, or `async_communication_ready=True` together with `complete_communication_ready()` when an RPC handshake must finish before simulation starts. The package does not define or send a fixed ECU startup RPC.

## Development and tests

Clone the repository and install it in editable mode:

```bash
git clone https://github.com/JannisRln/SilKit_py_scenario_runner.git
cd SilKit_py_scenario_runner
python -m pip install -e ".[test]"
```

Run the unit test suite:

```bash
python -m pytest -q
```

Run the dedicated scenario regression tests:

```bash
python -m pytest tests/test_scenario.py -v
```

The unit tests use a test-only fake for `SilKit_py` and do not require a running registry, controller, ECU, or native SIL Kit libraries. Live integration tests are kept separate under `examples/`.

## Packaging and releases

The runner is **pure Python**. Its wheel is platform-independent; the native platform dependencies are managed by `SilKit_py`.

```bash
python -m pip install --upgrade build twine
python -m build
python -m twine check dist/*
```

GitHub Actions uses separate workflows: `ci.yml` for continuous tests and `wheels.yml` for distribution builds and release publishing. PyPI upload takes place only after a GitHub release is published and the publishing workflow succeeds.

## Project boundaries

This repository intentionally excludes ECU-specific datasets, generated C headers, ECU binaries, hardcoded startup RPCs, C-source topic extractors, and MDF4 persistence dependencies such as `asammdf`. Implement those integrations in the ECU test project, using the runner's public interfaces.

## Related projects

- [SilKit_py](https://github.com/JannisRln/SilKit_py) — Python bindings and native SIL Kit integration.
- [Vector SIL Kit](https://github.com/vectorgrp/sil-kit) — The underlying simulation communication framework.

## License

Distributed under the [MIT License](LICENSE). See the repository's `LICENSE` file for details.
