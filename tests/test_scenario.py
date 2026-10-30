"""Behavioral pytest tests for the public Scenario API, without an ECU or registry.

The ``conftest.py`` SIL Kit fake exercises the participant/lifecycle boundary;
these tests intentionally drive simulated time via ``Scenario.step()``.
"""

import pytest

from silkit_runner import CanSignal, Scenario
from silkit_runner.scenario import SignalPredicate


class Signal:
    """Minimal writable/readable signal, recording values and simulation time."""

    def __init__(self, initial=0):
        self.value = initial
        self.writes = []

    def get(self):
        return self.value

    def set(self, value, time_ms):
        self.value = value
        self.writes.append((time_ms, value))


def tick(scenario, *times_ms):
    """Invoke simulated time steps without requiring a registry or controller."""
    for time_ms in times_ms:
        scenario.step(time_ms * 1_000_000, 1_000_000)


@pytest.fixture
def scenario():
    return Scenario(can_networks=(), end_ms=None)


def test_participant_and_time_sync_configuration(monkeypatch):
    import silkit_runner.scenario.scenario as module

    configurations = []
    original = module.SilKit.participant_configuration_from_String

    def capture(config):
        configurations.append(config)
        return original(config)

    monkeypatch.setattr(module.SilKit, "participant_configuration_from_String", capture)

    runner = Scenario(
        participant_name="test_participant",
        uri="silkit://127.0.0.1:8600",
        configuration="Logging: {}",
        can_networks=("Powertrain", "Body"),
        step_size_ns=5_000_000,
        end_ms=None,
    )

    assert configurations == ["Logging: {}"]
    assert runner.participant.name == "test_participant"
    assert runner.participant.uri == "silkit://127.0.0.1:8600"
    assert runner.time_sync.callback == runner.step
    assert runner.time_sync.step_ns == 5_000_000
    assert list(runner.can_controllers) == ["Powertrain", "Body"]
    assert runner.get_can_controller("Body") is runner.can_controller2
    assert runner.can_controller1.network == "Powertrain"
    with pytest.raises(KeyError, match="unknown"):
        runner.get_can_controller("unknown")


def test_pubsub_only_configuration(scenario):
    assert scenario.can_controllers == {}
    assert not hasattr(scenario, "can_controller1")
    scenario.lifecycle.ready_callback()


@pytest.mark.parametrize(
    "options, error",
    [
        ({"step_size_ns": 0}, "step_size_ns"),
        ({"step_size_ns": -1}, "step_size_ns"),
        ({"end_ms": -1}, "end_ms"),
        ({"can_networks": ("A", "A")}, "unique"),
        ({"async_communication_ready": True}, "completion hook"),
    ],
)
def test_invalid_constructor_options(options, error):
    with pytest.raises(ValueError, match=error):
        Scenario(**options)


def test_ready_hook_runs_after_all_can_controllers_start():
    seen = []

    def ready(runner):
        seen.append({network: ctrl.started for network, ctrl in runner.can_controllers.items()})

    runner = Scenario(can_networks=("A", "B"), on_communication_ready=ready)
    runner.lifecycle.ready_callback()

    assert seen == [{"A": True, "B": True}]
    assert runner.lifecycle.async_ready is False
    with pytest.raises(RuntimeError, match="Not configured"):
        runner.complete_communication_ready()


def test_async_ready_hook_can_be_completed_after_callback():
    callbacks = []
    runner = Scenario(
        can_networks=(),
        on_communication_ready=lambda s: callbacks.append(s),
        async_communication_ready=True,
    )
    runner.lifecycle.ready_callback()
    assert callbacks == [runner]
    assert runner.lifecycle.async_ready
    assert not runner.lifecycle.completed

    runner.complete_communication_ready()
    assert runner.lifecycle.completed


def test_run_starts_lifecycle_and_invokes_ready_callback():
    calls = []
    runner = Scenario(can_networks=("A",), on_communication_ready=lambda s: calls.append(s))

    runner.run()

    assert calls == [runner]
    assert runner.can_controller1.started


def test_at_set_executes_once_at_or_after_requested_time(scenario):
    signal = Signal()
    assert scenario.at(50).set(signal, 7) is scenario

    # 49.999 ms must not be rounded up to 50 ms.
    scenario.step(49_999_999, 1_000_000)
    assert signal.writes == []
    tick(scenario, 50, 60, 100)

    assert signal.writes == [(50, 7)]
    assert scenario._actions == []


def test_at_do_preserves_order_of_actions_scheduled_for_same_time(scenario):
    calls = []

    def record(label, *, time_ms):
        calls.append((label, time_ms))

    scenario.at(10).do(record, "first")
    scenario.at(10).do(record, "second")
    tick(scenario, 0, 10)

    assert calls == [("first", 10), ("second", 10)]
    assert not scenario._actions


def test_every_set_respects_start_period_and_inclusive_end(scenario):
    signal = Signal()
    assert scenario.every(10, start_ms=5, end_ms=25).set(signal, 3) is scenario
    tick(scenario, 0, 5, 10, 15, 20, 25, 26, 35)
    assert signal.writes == [(5, 3), (15, 3), (25, 3)]


@pytest.mark.parametrize("period", [0, -5])
def test_every_rejects_nonpositive_period(scenario, period):
    with pytest.raises(ValueError, match="period_ms"):
        scenario.every(period)


def test_pulse_sets_and_resets_only_once(scenario):
    signal = Signal()
    assert scenario.at(10).pulse(signal, 9, duration_ms=20, reset_value=-1) is scenario
    tick(scenario, 0, 10, 20, 29, 30, 40)
    assert signal.writes == [(10, 9), (30, -1)]


@pytest.mark.parametrize(
    "start, end, increment, expected",
    [
        (0, 5, 2, [0, 2, 4, 5, 5]),
        (5, -1, -2, [5, 3, 1, -1, -1]),
    ],
)
def test_ramp_progression_and_clamping(scenario, start, end, increment, expected):
    signal = Signal()
    assert scenario.every(10).ramp(signal, start, end, increment) is scenario
    tick(scenario, 0, 10, 20, 30, 40)
    assert signal.writes == list(zip([0, 10, 20, 30, 40], expected))


def test_modulation_uses_simulation_time_and_time_window(scenario):
    signal = Signal()
    assert scenario.every(10, start_ms=5, end_ms=25).modulate(signal, lambda t: t * 2) is scenario
    tick(scenario, 0, 5, 10, 15, 25, 35)
    assert signal.writes == [(5, 10), (15, 30), (25, 50)]


def test_closed_loop_uses_current_measurement_and_time(scenario):
    measured = Signal(initial=4)
    commanded = Signal()
    assert scenario.every(10, start_ms=10, end_ms=30).control(
        measured, commanded, lambda measurement, now: measurement * 2 + now
    ) is scenario
    tick(scenario, 0, 10)
    measured.value = 5
    tick(scenario, 20, 30, 40)
    assert commanded.writes == [(10, 18), (20, 30), (30, 40)]


def test_when_guards_last_action_until_condition_is_true(scenario):
    active = False
    signal = Signal()
    scenario.at(10).set(signal, 12)
    assert scenario.when(lambda: active) is scenario
    tick(scenario, 0, 10)
    assert signal.writes == []
    active = True
    tick(scenario, 11, 12)
    assert signal.writes == [(11, 12)]
    assert scenario._actions == []


def test_when_requires_previous_action(scenario):
    with pytest.raises(RuntimeError, match="preceding action"):
        scenario.when(lambda: True)


def test_expect_checks_each_step_and_stops_on_failure(scenario):
    signal = Signal(initial=1)
    scenario.expect(SignalPredicate(lambda: signal.get() == 1, "signal == 1"), "bad value")
    tick(scenario, 0, 10)
    assert scenario.lifecycle.stopped is None
    signal.value = 2
    tick(scenario, 20)
    assert scenario.lifecycle.stopped == "Assertion failed"
    with pytest.raises(AssertionError, match=r"\[t=20ms\] bad value: signal == 1"):
        scenario.run()


def test_at_expect_evaluates_once_when_due(scenario):
    checks = []
    scenario.at(20).expect(
        SignalPredicate(lambda: checks.append(True) or True, "passed")
    )
    tick(scenario, 0, 10, 20, 30)
    assert checks == [True]
    assert scenario._actions == []


def test_at_expect_reports_description_and_time(scenario):
    scenario.at(20).expect(SignalPredicate(lambda: False, "rpm == 1000"), "rpm mismatch")
    tick(scenario, 0, 20)
    assert scenario.lifecycle.stopped == "Assertion failed"
    with pytest.raises(AssertionError, match=r"\[t=20ms\] rpm mismatch: rpm == 1000"):
        scenario.run()


def test_invariant_is_enforced_only_inside_its_window(scenario):
    signal = Signal(initial=0)
    scenario.invariant(lambda: signal.get() > 0, "speed required", start_ms=10, end_ms=20)
    tick(scenario, 0)
    assert scenario.lifecycle.stopped is None
    signal.value = 1
    tick(scenario, 10, 20)
    signal.value = 0
    tick(scenario, 30)
    assert scenario.lifecycle.stopped is None


def test_invariant_failures_are_rethrown_by_run(scenario):
    scenario.invariant(lambda: False, "unsafe")
    tick(scenario, 0)
    assert scenario.lifecycle.stopped == "Assertion failed"
    with pytest.raises(AssertionError, match="Invariant violated: unsafe"):
        scenario.run()


def test_invariant_warn_only_reports_without_stopping(scenario, capsys):
    scenario.invariant(lambda: False, "warn message", severity="warn", start_ms=5, end_ms=10)
    tick(scenario, 0, 5, 10, 15)
    output = capsys.readouterr().out
    assert "[t=5ms] Invariant violated: warn message" in output
    assert "[t=10ms] Invariant violated: warn message" in output
    assert "[t=0ms]" not in output
    assert "[t=15ms]" not in output
    assert scenario.lifecycle.stopped is None


def test_expect_eventually_succeeds_before_deadline_and_is_removed(scenario):
    signal = Signal(initial=0)
    scenario.expect_eventually(lambda: SignalPredicate(lambda: signal.get() == 4, "signal == 4"), within_ms=30)
    tick(scenario, 0, 10, 20)
    signal.value = 4
    tick(scenario, 30, 40)
    assert scenario.lifecycle.stopped is None
    assert scenario._actions == []


def test_expect_eventually_fails_at_deadline(scenario):
    scenario.expect_eventually(lambda: SignalPredicate(lambda: False, "not ready"), within_ms=20)
    tick(scenario, 0, 10)
    assert scenario.lifecycle.stopped is None
    tick(scenario, 20)
    assert scenario.lifecycle.stopped == "Assertion failed"
    with pytest.raises(AssertionError, match=r"\[t=20ms\] Expectation not met in time: not ready"):
        scenario.run()


def test_at_expect_eventually_starts_timeout_at_scheduled_time(scenario):
    scenario.at(50).expect_eventually(
        lambda: SignalPredicate(lambda: False, "waiting for signal"),
        within_ms=20,
        message="peer timed out",
    )
    tick(scenario, 0, 49, 50, 69)
    assert scenario.lifecycle.stopped is None
    tick(scenario, 70)
    assert scenario.lifecycle.stopped == "Assertion failed"
    with pytest.raises(AssertionError, match=r"\[t=70ms\] peer timed out: waiting for signal"):
        scenario.run()


def test_at_expect_eventually_is_removed_once_satisfied(scenario):
    ready = False
    scenario.at(10).expect_eventually(
        lambda: SignalPredicate(lambda: ready, "ready"), within_ms=20
    )
    tick(scenario, 0, 10)
    ready = True
    tick(scenario, 20, 30, 40)
    assert scenario.lifecycle.stopped is None
    assert scenario._actions == []


def test_scenario_stops_at_inclusive_end_time():
    runner = Scenario(can_networks=(), end_ms=25)
    tick(runner, 0, 20)
    assert runner.lifecycle.stopped is None
    tick(runner, 25)
    assert runner.lifecycle.stopped == "Simulation done"


def test_end_time_none_does_not_automatically_stop(scenario):
    tick(scenario, 0, 100, 1_000)
    assert scenario.lifecycle.stopped is None


def test_can_signal_dispatch_decodes_multiple_signals_and_tracks_time():
    runner = Scenario(can_networks=("bus",), end_ms=None)
    speed = runner.can_signal(CanSignal("speed", 0x123, lambda raw: raw[0]))
    doubled = runner.can_signal(CanSignal("double_speed", 0x123, lambda raw: raw[0] * 2))
    tick(runner, 20)
    runner.can_controller1.receive(0x123, b"\x1e")
    runner.can_controller1.receive(0x777, b"ignored")

    assert speed.get() == 30
    assert doubled.get() == 60
    assert speed._last_time_ms == 20
    assert doubled._last_time_ms == 20
    assert runner.lifecycle.stopped is None


def test_assertion_failure_prevents_following_actions(scenario):
    signal = Signal()
    scenario.at(10).expect(SignalPredicate(lambda: False, "test failure"))
    scenario.at(10).set(signal, 9)
    tick(scenario, 10)
    assert signal.writes == []
    assert scenario.lifecycle.stopped == "Assertion failed"


def test_unexpected_exception_in_step_stops_and_is_rethrown_by_run(scenario):
    def broken():
        raise ValueError("boom")

    scenario.at(10).do(lambda time_ms: broken())
    tick(scenario, 10)
    assert scenario.lifecycle.stopped == "Scenario error"
    with pytest.raises(AssertionError, match=r"ValueError in scenario step at t=10ms: boom"):
        scenario.run()


def test_periodic_action_coalesces_irregular_steps(scenario):
    signal = Signal()
    scenario.every(10).set(signal, 1)
    tick(scenario, 0, 7, 14, 21, 28)
    assert signal.writes == [(0, 1), (14, 1), (21, 1)]


def test_can_registry_isolates_identical_ids_by_network():
    runner = Scenario(can_networks=("A", "B"), end_ms=None)
    a = runner.can_signal(CanSignal("a", 0x123, lambda data: data[0]), network="A")
    b = runner.can_signal(CanSignal("b", 0x123, lambda data: data[0]), network="B")
    runner.get_can_controller("A").receive(0x123, bytes([1]))
    assert a.get() == 1
    assert b.get() is None
    runner.get_can_controller("B").receive(0x123, bytes([2]))
    assert b.get() == 2


def test_multibus_registration_requires_network():
    runner = Scenario(can_networks=("A", "B"))
    with pytest.raises(ValueError, match="Specify network"):
        runner.can_signal(CanSignal("ambiguous", 0x123, lambda data: data[0]))
