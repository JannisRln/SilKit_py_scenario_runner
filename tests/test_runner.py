"""Standalone regression tests, runnable without pytest or the SIL Kit DLL."""

from pathlib import Path
import importlib
import inspect
import sys
import unittest

from fake_silkit import register

register()
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from silkit_runner import (  # noqa: E402
    CanSignal,
    LoggedCanSignal,
    LoggedSilKitSignal,
    Scenario,
    SilKitSignal,
    UdsDidSignal,
    decode_uds_response,
    uds_rdbi,
    uds_wdbi,
)
from silkit_runner.scenario import (  # noqa: E402
    ClosedLoopAction,
    EventuallyAction,
    ExpectAtAction,
    ModulationAction,
    OneShotAction,
    PeriodicAction,
    PulseAction,
    RampAction,
    SignalPredicate,
)


class SampleSignal:
    def __init__(self, initial=0):
        self.value = initial
        self.calls = []

    def get(self):
        return self.value

    def set(self, value, time_ms=None):
        self.value = value
        self.calls.append((time_ms, value))


class ActionTests(unittest.TestCase):
    def test_oneshot(self):
        sig = SampleSignal()
        action = OneShotAction(100, sig.set, (42,))
        for t in [0, 99, 100, 120]:
            action.execute(t)
        self.assertEqual(sig.calls, [(100, 42)])

    def test_periodic(self):
        sig = SampleSignal()
        action = PeriodicAction(100, 50, sig.set, (1,), 200)
        for t in [0, 99, 100, 150, 200, 250]:
            action.execute(t)
        self.assertEqual(sig.calls, [(100, 1), (150, 1), (200, 1)])

    def test_pulse(self):
        sig = SampleSignal()
        action = PulseAction(100, 50, sig.set, 7, 0)
        for t in [0, 100, 140, 150, 180]:
            action.execute(t)
        self.assertEqual(sig.calls, [(100, 7), (150, 0)])

    def test_ramp_modulation_closed_loop(self):
        sig = SampleSignal()
        ramp = RampAction(100, 50, sig.set, 0, 5, 2)
        for t in [100, 150, 200, 250, 300]:
            ramp.execute(t)
        self.assertEqual(sig.calls, [(100, 0), (150, 2), (200, 4), (250, 5), (300, 5)])
        mod = ModulationAction(0, 50, sig.set, lambda t: t // 10, end_ms=100)
        for t in [0, 50, 100, 150]:
            mod.execute(t)
        self.assertEqual(sig.calls[-3:], [(0, 0), (50, 5), (100, 10)])
        loop = ClosedLoopAction(0, 50, sig.get, sig.set, lambda x, t: x + 1)
        loop.execute(150)
        self.assertEqual(sig.get(), 11)

    def test_eventually_factory_and_predicate(self):
        for argument in (lambda: SignalPredicate(lambda: True, "ok"), SignalPredicate(lambda: True, "ok")):
            action = EventuallyAction(argument, 50)
            action.execute(0)
            self.assertTrue(action._satisfied)

    def test_eventually_timeout(self):
        action = EventuallyAction(lambda: SignalPredicate(lambda: False, "bad"), 50)
        action.execute(0)
        with self.assertRaisesRegex(AssertionError, "bad"):
            action.execute(50)


class ScenarioTests(unittest.TestCase):
    def test_no_ecu_handshake_and_dynamic_can(self):
        scenario = Scenario(can_networks=("bus_A", "bus_B"))
        self.assertEqual(set(scenario.can_controllers), {"bus_A", "bus_B"})
        self.assertEqual(scenario.get_can_controller("bus_A"), scenario.can_controller1)
        self.assertFalse(hasattr(scenario, "_ecu_dataset"))
        self.assertEqual(scenario.participant.subscribers, {})
        self.assertFalse(hasattr(scenario.participant, "create_rpc_client"))
        scenario.lifecycle.ready_callback()
        self.assertTrue(all(ctrl.started for ctrl in scenario.can_controllers.values()))
        self.assertFalse(scenario.lifecycle.async_ready)

    def test_pubsub_only_without_can_controllers(self):
        scenario = Scenario(can_networks=())
        self.assertEqual(scenario.can_controllers, {})
        self.assertFalse(hasattr(scenario, "can_controller1"))
        scenario.lifecycle.ready_callback()

    def test_ready_hook_sync_and_async(self):
        observed = []
        sync = Scenario(on_communication_ready=lambda scenario: observed.append(scenario.participant.name))
        sync.lifecycle.ready_callback()
        self.assertEqual(observed, ["scenario_runner"])
        self.assertTrue(sync.can_controller1.started)
        with self.assertRaises(RuntimeError):
            sync.complete_communication_ready()

        async_scenario = Scenario(
            async_communication_ready=True,
            on_communication_ready=lambda scenario: scenario.complete_communication_ready(),
        )
        async_scenario.lifecycle.ready_callback()
        self.assertTrue(async_scenario.lifecycle.async_ready)
        self.assertTrue(async_scenario.lifecycle.completed)

    def test_invalid_options(self):
        for kwargs in ({"can_networks": ("a", "a")},
                       {"step_size_ns": 0}, {"end_ms": -1},
                       {"async_communication_ready": True}):
            with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                Scenario(**kwargs)
        with self.assertRaises(ValueError):
            Scenario().every(0)

    def test_step_actions_without_skipping_following_items(self):
        scenario = Scenario(end_ms=200)
        sig = SampleSignal()
        scenario.at(100).set(sig, 1)
        scenario.at(100).set(sig, 2)
        scenario.at(100).expect(SignalPredicate(lambda: sig.get() == 2))
        scenario.step(100_000_000, 1_000_000)
        self.assertEqual(sig.calls, [(100, 1), (100, 2)])
        self.assertEqual(scenario._actions, [])
        self.assertIsNone(scenario._failure)

    def test_expect_failure_delayed_until_run(self):
        scenario = Scenario(end_ms=None)
        scenario.expect(SignalPredicate(lambda: False, "value wrong"))
        scenario.step(0, 1_000_000)
        self.assertEqual(scenario.lifecycle.stopped, "Assertion failed")
        with self.assertRaisesRegex(AssertionError, "value wrong"):
            scenario.run()

    def test_can_dispatch(self):
        scenario = Scenario()
        speed = scenario.can_signal(CanSignal("speed", 0x100, lambda data: data[0]))
        scenario.can_controller1.receive(0x100, b"\x14")
        self.assertEqual(speed.get(), 20)
        self.assertTrue(speed.greater_than(10)())

    def test_pubsub_payload_and_log(self):
        scenario = Scenario()
        signal = SilKitSignal("example", scenario.participant, "topic", initial=-4)
        signal.publish()
        signal.set(4, 100)
        self.assertEqual(scenario.participant.publishers["Pub_topic"].sent,
                         [(-4).to_bytes(4, "little", signed=True), (4).to_bytes(4, "little", signed=True)])
        callback = scenario.participant.subscribers["Sub_topic"]
        callback(None, type("Event", (), {"data": (11).to_bytes(4, "little", signed=True)})())
        self.assertEqual(signal.get(), 11)
        custom = SilKitSignal("raw", scenario.participant, "raw", initial=b"x", encoder=bytes, decoder=bytes)
        custom.publish()
        self.assertEqual(scenario.participant.publishers["Pub_raw"].sent, [b"x"])

        log_context = {"signals": {}}
        logged = LoggedSilKitSignal("logged", scenario.participant, "logging", log_context)
        logged.set(12, 10)
        self.assertEqual(log_context["signals"]["logged"], [(10, 12)])
        logged_can = LoggedCanSignal("raw_can", 123, lambda b: b[0])
        logged_can.update(b"\x01", 10)
        self.assertEqual(logged_can.log, [(10, 1)])

    def test_package_imports_no_ecu_or_third_party(self):
        root = Path(__file__).resolve().parents[1] / "src" / "silkit_runner"
        for source in root.rglob("*.py"):
            data = source.read_text(encoding="utf-8")
            for forbidden in ("import pytest", "from pytest", "import asammdf", "from asammdf",
                              "ecu_dataset", "topic_extractor", "from numpy", "import pandas"):
                self.assertNotIn(forbidden, data, f"{source}: {forbidden}")


class UdsTests(unittest.TestCase):
    def test_decode(self):
        r = decode_uds_response(bytes([0x62, 0x01, 0x38, 0xF4, 0x01]))
        self.assertEqual((r.service, r.did, r.payload), (0x22, 0x138, b"\xf4\x01"))
        negative = decode_uds_response(bytes([0x7F, 0x22, 0x31]))
        self.assertTrue(negative.negative)
        self.assertEqual(negative.nrc, 0x31)
        for bad in (b"", b"\x7f", b"\x7f\x22"):
            with self.assertRaises(ValueError):
                decode_uds_response(bad)
        self.assertEqual(uds_rdbi(0x1234), b"\x22\x12\x34")
        self.assertEqual(uds_wdbi(0x1234, b"AB"), b"\x2e\x12\x34AB")

    def test_did_request_response(self):
        scenario = Scenario()
        signal = scenario.can_signal(UdsDidSignal(name="rpm", did=0x138))
        signal.request(scenario.can_controller1)
        msg = scenario.can_controller1.sent[-1]
        self.assertEqual(msg["data"], uds_rdbi(0x138))
        self.assertEqual(msg["can_id"], 0x18DAF110)
        scenario.can_controller1.receive(0x18DA10F1, bytes([0x62, 0x01, 0x38, 0xF4, 0x01]))
        self.assertEqual(signal.get(), 500)


if __name__ == "__main__":
    unittest.main()


class ImprovementTests(unittest.TestCase):
    def test_tx_rx_are_distinct(self):
        scenario = Scenario(can_networks=())
        signal = SilKitSignal("sample", scenario.participant, "sample", initial=0)
        signal.set(7, 0)
        self.assertEqual(signal.last_sent_value, 7)
        self.assertEqual(signal.get(), 0)
        self.assertFalse(signal.received)
        callback = scenario.participant.subscribers["Sub_sample"]
        callback(None, type("Event", (), {"data": (9).to_bytes(4, "little", signed=True)})())
        self.assertEqual(signal.last_received_value, 9)
        self.assertEqual(signal.get(), 9)
        self.assertTrue(signal.received)

    def test_uds_non_did_and_decoder(self):
        response = decode_uds_response(bytes([0x50, 0x03, 0x12]))
        self.assertIsNone(response.did)
        self.assertEqual(response.payload, bytes([0x03, 0x12]))
        signal = UdsDidSignal(name="rpm", did=0x1234, decoder=lambda data: int.from_bytes(data, "big"))
        signal.update(bytes([0x7f, 0x22, 0x31]), 1)
        self.assertEqual(signal.last_nrc, 0x31)
        signal.update(bytes([0x62, 0x12, 0x34, 0x01, 0x02]), 2)
        self.assertEqual(signal.get(), 258)
        self.assertIsNone(signal.last_nrc)

    def test_timeout_validation_and_context_manager(self):
        with Scenario(can_networks=()) as scenario:
            with self.assertRaises(ValueError):
                scenario.run(timeout_s=0)
        self.assertEqual(scenario.lifecycle.stopped, "Scenario closed")
