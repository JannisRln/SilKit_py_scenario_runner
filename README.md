# SIL Kit Scenario Runner

A standalone, pip-installable Python scenario runner built around the `SilKit_py` bindings.
It **does not import or bundle any ECU implementation**, C adapter, test harness,
Vector binaries, MDF4 writer, machine-type data, or C-topic extraction.

## Requirements

- Python 3.10+ for the runner; match your Python interpreter to your
  `SilKit_py` wheel. The wheel used in the original Windows project was
  `silkit_py-1.0.3-cp312-cp312-win_amd64.whl`, requiring Windows x64 and
  CPython 3.12.
- The `SilKit_py` binding and the underlying SIL Kit native runtime
  required by that binding.
- An external SIL Kit registry and a suitable system controller when using
  coordinated lifecycle mode. **Neither executable is bundled or started**
  by this package.

## Installation

Install the matching `SilKit_py` wheel first (it may not be available from
PyPI; the original project referenced a machine-local `.whl`):

```powershell
py -3.12 -m pip install C:\path\to\silkit_py-1.0.3-cp312-cp312-win_amd64.whl
```

From the root of this extracted project:

```powershell
py -3.12 -m pip install .
```

For an offline installation with the binding already installed:

```powershell
py -3.12 -m pip install --no-deps .
```

The **only runtime dependency declared** in `pyproject.toml` is
`SilKit_py>=1.0.3`. Dependencies required internally by that binding are
managed by the binding's own distribution. `setuptools` is a build tool,
not a runner runtime dependency.

## Basic usage with pytest

The example is a **pytest integration test**, not a standalone script. It uses
`@pytest.fixture` for scenario creation and validates data received from a
*separate* SIL Kit participant. This avoids testing only the value the runner
has just assigned itself.

```python
import pytest
from silkit_runner import Scenario, SilKitSignal


@pytest.fixture
def scenario():
    return Scenario(participant_name="scenario_example", can_networks=(), end_ms=300)


@pytest.mark.integration
def test_speed_echo(scenario):
    requested = SilKitSignal("requested", scenario.participant, "input.speed", initial=0)
    observed = SilKitSignal("observed", scenario.participant, "output.speed", initial=None)
    scenario.at(0).set(requested, 100)
    scenario.at(200).expect(observed.is_equal(100))
    scenario.run()
```

The runnable version is at `examples/test_basic_scenario.py`. It also
checks `SILKIT_RUN_INTEGRATION` before connecting, so a default test run
cannot accidentally wait for an absent registry. `pytest` is an **optional
`test` extra**, not a runtime dependency of the runner.

The runner creates one CAN controller by default; `can_networks=()` runs a
Pub/Sub-only participant. Configuring four networks
requires specifying them explicitly:

```python
scenario = Scenario(can_networks=("CAN 1", "CAN 2", "CAN 3", "CAN 4"))
controller = scenario.get_can_controller("CAN 1")
# The first controller can also be accessed as scenario.can_controller1.
```

`Scenario.run()` blocks until the SIL Kit lifecycle ends. The registry and
system controller must already exist and expect the appropriate participant
names. A `Scenario` alone does not start the ECU, registry, or system
controller. Actions run at simulated time, not wall-clock time.

### Change the signal payload format

`SilKitSignal` defaults to signed 32-bit little-endian integers for backwards
compatibility with the original runner. Replace the codec for other payloads:

```python
import struct
from silkit_runner import SilKitSignal

signal = SilKitSignal(
    name="temperature",
    participant=scenario.participant,
    topic="input.temperature",
    initial=20.0,
    encoder=lambda value: struct.pack("<f", value),
    decoder=lambda data: struct.unpack("<f", data)[0],
)
```

### Schedule CAN/UDS events

```python
from silkit_runner import UdsDidSignal

measured = scenario.can_signal(UdsDidSignal(name="measured", did=0x0138))
scenario.at(100).do(measured.request, scenario.get_can_controller("CAN 1"))
scenario.at(100).expect_eventually(
    lambda: measured.is_equal(500), within_ms=100
)
```

The UDS helper handles simple CAN request/response payloads. It does not
implement ISO-TP or CAN FD framing.

## ECU-specific startup belongs to the consumer

The old runner sent a hardcoded RPC (`v_SetMemPerRpc`) with a
`DefaultEcuDataset` during communication-ready. This was tied to the ECU C
adapter. The package **does not do this**; implement that logic in your ECU
repository, if that ECU requires it.

The runner exposes `on_communication_ready`, called after starting the
configured CAN controllers. For simple initialization use a synchronous
callback:

```python
def ready(scenario):
    input_signal.publish()

scenario = Scenario(on_communication_ready=ready)
```

For a handshake that must finish after an asynchronous RPC response, use the
asynchronous communication-ready mode. Create the RPC client in the **ECU
integration code before** calling `scenario.run()`:

```python
import SilKit_py as SilKit
from silkit_runner import Scenario

# `ecu_payload` is supplied by the ECU-specific application, not this package.
scenario = Scenario(
    on_communication_ready=lambda s: rpc_client.call(ecu_payload),
    async_communication_ready=True,
)

rpc_client = scenario.participant.create_rpc_client(
    "EcuInitClient",
    SilKit.RpcSpec("v_SetMemPerRpc", SilKit.media_type_rpc()),
    lambda client, result: scenario.complete_communication_ready(),
)
scenario.run()
```

Be sure the remote participant implements the matching RPC and the
system controller coordinates both participants. Only call
`complete_communication_ready()` once your own handshake is finished.

## Removed from the standalone package

| Original component | Where it belongs now |
| --- | --- |
| `ecu_dataset.py` (`DefaultEcuDataset`, machine types) | ECU project |
| `topic_extractor.py` (reads a fixed C adapter path) | ECU project |
| C adapter, generated headers and `build/app_sil.exe` | ECU project |
| root `conftest.py`, `Test/helper/fixture.py`, `pytest.ini` | ECU test project |
| `scenario/mdf4.py` (needs `asammdf`) | Optional separate integration outside the runner |
| hardcoded `v_SetMemPerRpc` handshake | ECU project, via the ready hook |
| fixed four-CAN configuration | Explicit `can_networks` argument |

`LoggedSilKitSignal` and `LoggedCanSignal` continue to collect samples **in
memory**, using the Python standard library only. Persisting them as MDF4 is
left to a separate consumer-specific adapter; this keeps `asammdf`, NumPy,
Pandas, Qt, pytest, etc. out of the runner installation.

## Tests

Install pytest in your development environment (after installing the
platform-specific `SilKit_py` binding, or with `--no-deps` when the binding
is not available on your machine):

```sh
python -m pip install pytest
python -m pytest -q
```

You may instead install the optional extra with `python -m pip install
".[test]"` on a platform where pip can resolve `SilKit_py`.

The default pytest configuration collects **only `tests/`**. These unit
tests use a test-only fake of `SilKit_py`; no native SIL Kit DLL, ECU or registry
is required. The fake is installed by `tests/conftest.py` during pytest
collection; it is **not** included in the wheel.

Scenario-specific regression tests are in **`tests/test_scenario.py`** and
cover constructor validation, CAN controller creation, sync/async ready hooks,
`run()`, simulated-time scheduling, action chaining and conditional execution,
`expect()`/`expect_eventually()`/`invariant()` failures, timeouts, and CAN frame
dispatch. Run just these tests with:

```sh
python -m pytest -q tests/test_scenario.py
```

The older `tests/test_runner.py` tests remain compatible with
`python -m unittest discover -s tests -v`. The complete suite (including
`test_scenario.py`) is pytest-based.

### Live SIL Kit integration example (pytest)

The `examples/test_basic_scenario.py` test demonstrates an externally
observable assertion. Start the SIL Kit registry, coordinated system
controller (including participant `scenario_example`) and a **peer echo
participant** that consumes topic `input.speed` and publishes the same
signed 32-bit little-endian value on `output.speed`. The runner does not
start those processes and does not supply the peer. Adapt the topics and
assertion to your own system under test.

From PowerShell on a machine with the real `SilKit_py` binding installed:

```powershell
$env:SILKIT_RUN_INTEGRATION = "1"
$env:SILKIT_REGISTRY_URI = "silkit://localhost:8500"
py -3.12 -m pytest -v examples/test_basic_scenario.py
```

Without `SILKIT_RUN_INTEGRATION=1`, this example is **skipped** rather than
opening a SIL Kit connection or hanging while waiting for another participant.
Running the example is not part of the fake-backed unit test suite.

## Packaging

Build a wheel on a machine with the Python packaging build tools installed:

```sh
python -m pip wheel --no-deps . -w dist
```

The resulting `silkit_scenario_runner-0.1.0-py3-none-any.whl` contains Python
only; the platform-specific `SilKit_py` wheel must be installed separately.

## Migrating an existing ECU test project

- Replace `python_runner.scenario.scenario` with
  `silkit_runner.scenario.scenario` (or use the top-level `silkit_runner`
  exports), and `python_runner.uds` with `silkit_runner.uds`.
- Keep your original pytest fixtures, ECU executable startup/stop logic,
  topic extraction and machine-type data **in the ECU repository**.
- Construct `Scenario` with the CAN network names actually required by the
  ECU and supply the ECU's RPC initialization via the communication-ready
  hook. Do not set `scenario._ecu_dataset`; that private field was removed.
- If you need MDF4 output, keep an `asammdf` adapter in the ECU test project
  and feed it the in-memory signal logs; it is not a runner dependency.

Before uploading this project to public PyPI, choose and add the appropriate
license/ownership metadata. This extraction does not assume that the original
project was licensed for redistribution.
