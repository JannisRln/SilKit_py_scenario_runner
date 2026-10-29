# Fluent API reference

This guide documents the methods implemented in `src/silkit_runner/scenario/scenario.py` and the signal predicates. All scheduling values below use **simulation milliseconds**, not wall-clock time. The simulation step size is configured separately in nanoseconds.

## Scheduling entry points

| Method | Arguments | Behavior |
| --- | --- | --- |
| `scenario.at(time_ms)` | scheduled time | Returns an at-time builder. |
| `scenario.every(period_ms, start_ms=0, end_ms=None)` | period, inclusive start and optional end | Returns a periodic builder. `period_ms` must be positive. |
| `scenario.when(condition)` | zero-argument callable returning bool | Wraps the **most recently registered action** in a guard. Returns `Scenario`. Raises `RuntimeError` without a preceding action. |
| `scenario.expect(predicate, message=None)` | `SignalPredicate` | Checks the predicate on **each simulation step**, until the scenario ends or a check fails. |
| `scenario.expect_eventually(predicate_factory, *, within_ms, message=None)` | factory returning `SignalPredicate` | Checks from the first simulation step; fails when the timeout expires. |
| `scenario.invariant(predicate, message, *, severity="fail", start_ms=0, end_ms=None)` | bool callable, message, severity and time interval | Checks on every step in the inclusive time interval; `"fail"` raises, other severity values print warnings. |
| `scenario.run()` | none | Starts and waits for the coordinated lifecycle, then raises a recorded assertion failure. |

**Chaining rule:** `at(...)` and `every(...)` return builder objects; their action methods return the **Scenario**, not the same builder. Therefore write `scenario.at(0).set(...).at(100).expect(...)`, or use separate statements; do **not** assume `scenario.at(0).set(...).pulse(...)` works.

## One-shot actions: `scenario.at(time_ms)`

| Method | Arguments | Behavior |
| --- | --- | --- |
| `.do(func, *args)` | callable and positional arguments | Calls `func(*args, time_ms=actual_step_ms)` once, at the first simulation step at or after the target time. |
| `.set(signal, value)` | signal, value | Convenience wrapper around `.do(signal.set, value)`. |
| `.pulse(signal, value, duration_ms, reset_value=0)` | signal, pulse value, duration, reset value | Calls `signal.set(value, time_ms=...)` at/after the start, then `signal.set(reset_value, time_ms=...)` at/after the end. |
| `.expect(predicate, message=None)` | `SignalPredicate` | Checks exactly once, at the first step at or after the target time. |
| `.expect_eventually(predicate_factory, *, within_ms, message=None)` | predicate factory | Starts checking at/after the scheduled time, failing if it has not become true within the deadline. |

```python
scenario.at(0).set(requested, 100)
scenario.at(20).pulse(requested, 200, duration_ms=10, reset_value=100)
scenario.at(100).expect(observed.is_equal(100), "Incorrect response")
scenario.at(100).expect_eventually(
    lambda: observed.greater_equal(100),
    within_ms=50,
    message="Response not received",
)
```

A scheduled action is executed at the next eligible simulation step; the simulation step size determines timing resolution. The callback passed to `.do()` must accept a `time_ms` keyword argument.

## Periodic actions: `scenario.every(period_ms, start_ms=0, end_ms=None)`

| Method | Arguments | Behavior |
| --- | --- | --- |
| `.do(func, *args)` | callable and positional arguments | Calls `func(*args, time_ms=actual_step_ms)` whenever `(time_ms - start_ms) % period_ms == 0` within the interval. |
| `.set(signal, value)` | signal and value | Publishes the same value on every eligible step. |
| `.ramp(signal, start, end, step)` | signal and numeric ramp parameters | Sets the start value on the first eligible step, then adds `step` and clamps overshoot to `end`. |
| `.modulate(signal, func)` | signal, `func(sim_time_ms)` | Evaluates the function on each eligible step and writes the result. |
| `.control(measured_signal, output_signal, controller)` | measured signal, output signal, controller | Reads `measured_signal.get()`, calls `controller(measurement, time_ms)`, and writes the result with `output_signal.set(result, time_ms)`. |

```python
scenario.every(10, start_ms=0, end_ms=100).set(requested, 42)
scenario.every(10, start_ms=0).ramp(requested, start=0, end=100, step=10)
scenario.every(20, start_ms=0, end_ms=200).modulate(
    requested, lambda time_ms: time_ms // 20
)
scenario.every(10, start_ms=0, end_ms=100).control(
    measured_signal=observed,
    output_signal=requested,
    controller=lambda measurement, time_ms: 100 - measurement,
)
```

**Implementation details:** `end_ms` is applied by `.do()`, `.set()`, `.modulate()` and `.control()`. Currently `.ramp()` does **not** honor the builder's `end_ms`; after reaching its target, it continues writing the final value on subsequent eligible steps. Periodic execution requires exact alignment with the simulation steps. For example, with 10 ms simulation steps, a 7 ms period will not run on every requested 7 ms boundary.

## Guards and assertions

```python
scenario.at(50).set(requested, 10).when(lambda: observed.get() is not None)
scenario.invariant(
    lambda: observed.get() is None or observed.get() >= 0,
    "Observed speed must be nonnegative",
    start_ms=0,
    end_ms=200,
)
scenario.expect_eventually(
    lambda: observed.is_equal(10),
    within_ms=100,
)
```

`when()` only guards the immediately preceding registered action. If the condition is false, the wrapped action is not executed on that step; one-shot actions may execute later when the condition becomes true. It does not guard all actions in a chain.

`scenario.expect()` is a recurring check, whereas `scenario.at(t).expect()` is a one-time check. An eventual expectation considers its first eligible simulation step to be the timeout start. A failed assertion stops the lifecycle and is re-raised by `run()`.

## Signal predicate API

Both `SilKitSignal` and `CanSignal` provide comparison methods returning a `SignalPredicate`:

| Method | Meaning |
| --- | --- |
| `is_equal(value)` | Current value equals `value` |
| `is_not_equal(value)` | Current value differs from `value` |
| `greater_than(value)` | Current value is greater than `value` |
| `greater_equal(value)` | Current value is greater than or equal to `value` |
| `less_than(value)` | Current value is less than `value` |
| `between(low, high, inclusive=True)` | Current value lies between bounds, inclusive by default |

`SilKitSignal` additionally provides `has_value()`, which returns a predicate. **`CanSignal.has_value()` instead returns a boolean immediately**, not a predicate; wrap it in `lambda: can_signal.has_value()` for an invariant or guard. CAN comparison predicates guard against missing (`None`) values for numerical comparisons; the corresponding `SilKitSignal` comparisons do not.

### Composing predicates

`SignalPredicate` supports `&` (AND), `|` (OR), and `~` (NOT):

```python
valid = observed.between(0, 120) & ~observed.is_equal(50)
scenario.at(100).expect(valid)
```

`SignalGroup(*signals)` provides:

- `group.all(predicate_factory)`: every signal matches
- `group.any(predicate_factory)`: at least one signal matches
- `group.none(predicate_factory)`: no signal matches

Each factory receives one signal and must return a `SignalPredicate`.

```python
from silkit_runner import SignalGroup

group = SignalGroup(requested, observed)
scenario.at(100).expect(group.all(lambda signal: signal.has_value()))
```

Use `SignalGroup` with signals offering the required predicate method; note the different return type of `CanSignal.has_value()`.

## CAN-related scenario helpers

`scenario.can_signal(signal: CanSignal)` registers a CAN signal with the scenario's frame dispatcher and returns the same signal. `scenario.get_can_controller(network)` retrieves a configured controller (or raises `KeyError` if it does not exist). A `CanSignal` is decoded from frames with the matching CAN ID and can be used in predicate-based expectations.

For Pub/Sub-only scenarios, initialize with `can_networks=()`. For CAN examples, supply matching network names and register the CAN signals before running.

## Timing and limitations

- All fluent scheduling arguments are simulation-time **milliseconds**; `Scenario(step_size_ns=...)` takes **nanoseconds**.
- At-time actions execute on the first eligible simulation step, not necessarily exactly at the requested millisecond.
- The scheduler processes registered actions in insertion order.
- `Scenario` uses a coordinated SIL Kit lifecycle; registry, system controller and peers are external dependencies.
- The current implementation stores a single `_last_action` for `when()`; subsequent unrelated scheduling may change which action it guards.
- The current `run()` starts the lifecycle using `asyncio.run()`, so it is intended to be called from synchronous test code, not inside an already running event loop.
