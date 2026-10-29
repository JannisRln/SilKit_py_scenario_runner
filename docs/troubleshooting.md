# Troubleshooting

## Test is skipped

The live example deliberately skips unless `SILKIT_RUN_INTEGRATION=1` is set. Use `python -m pytest -q` for unit tests that do not require the registry or a peer.

## Scenario waits indefinitely

Check that the registry and system controller are running, that the participant names match lifecycle configuration, and that the peer participant is connected. Confirm that `SILKIT_REGISTRY_URI` points to the correct registry.

## Assertion never succeeds

Check Pub/Sub topic names, publication timing, wire encoding, and whether the external participant actually sends the expected response.

## Import or installation error

The runner declares Python 3.12–3.14 and depends on SilKit_py native bindings. Confirm a compatible SilKit_py distribution is available for your interpreter and operating system.
