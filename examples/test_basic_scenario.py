"""Pytest integration example for a generic SIL Kit Pub/Sub echo participant.

Prerequisites:
- a running SIL Kit registry and coordinated system controller
- a second SIL Kit participant that subscribes to ``input.speed`` and
  publishes the same signed little-endian int32 value to ``output.speed``
- set ``SILKIT_RUN_INTEGRATION=1`` to explicitly enable this test

The echo participant is intentionally not included in the runner package.
Replace the two topics with signals from your own system under test.
"""

import os

import pytest


@pytest.fixture
def scenario():
    """Create a participant only when the external test system is running."""
    if os.environ.get("SILKIT_RUN_INTEGRATION") != "1":
        pytest.skip("Set SILKIT_RUN_INTEGRATION=1 for live SIL Kit integration tests")

    pytest.importorskip("SilKit_py")
    from silkit_runner import Scenario

    return Scenario(
        participant_name="scenario_example",
        uri=os.environ.get("SILKIT_REGISTRY_URI", "silkit://localhost:8500"),
        can_networks=(),  # Pub/Sub only, no ECU-specific CAN configuration
        end_ms=300,
    )


@pytest.mark.integration
def test_speed_echo(scenario):
    """Publish a speed request and expect a response from a second participant."""
    from silkit_runner import SilKitSignal

    requested_speed = SilKitSignal(
        "requested_speed", scenario.participant, "input.speed", initial=0
    )
    observed_speed = SilKitSignal(
        "observed_speed", scenario.participant, "output.speed", initial=None
    )

    scenario.at(0).set(requested_speed, 100)
    scenario.at(200).expect(
        observed_speed.is_equal(100),
        message="The peer did not publish speed=100 on output.speed",
    )
    scenario.run()
