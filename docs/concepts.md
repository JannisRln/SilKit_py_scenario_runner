# Core concepts

## Simulation time

A scenario schedules actions at simulated times, not wall-clock times. Use `scenario.at(time_ms)` to configure an action and `scenario.run()` to execute a scenario lifecycle.

## Signals

`SilKitSignal` communicates over SIL Kit Pub/Sub topics. Its default payload is a signed 32-bit little-endian integer. Configure matching encoder/decoder functions for other types, and ensure the peer uses the same topic and wire format.

## Assertions

Use `expect()` for a point-in-time check and `expect_eventually()` for a condition that may become true later. Invariants check conditions across scenario execution. Assertions against externally observed signals are more informative than simply checking locally written values.

## Lifecycle and networks

Use `can_networks=()` for Pub/Sub-only scenarios. Configure matching CAN network names when CAN controllers are needed. Coordinated tests need a registry, controller, and participating peers; the runner does not start these processes.

## Project boundaries

The runner supplies basic DID-oriented UDS helpers but not a complete ISO-TP or CAN FD transport stack. ECU-specific startup or RPC handshakes belong in the consuming test project.
