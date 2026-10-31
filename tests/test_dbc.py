"""Regression tests for typed DBC generation and binding."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from silkit_runner.dbc import DbcSignalDefinition, bind_dbc_signal, bind_dbc_sender, bind_dbc_vehicle, generate_dbc


class FakeSignal:
    def __init__(self, name):
        self.name = name
        self.initial = 0


class FakeMessage:
    name = "VehicleStatus"
    frame_id = 0x123
    is_extended_frame = False
    cycle_time = 20
    signals = [FakeSignal("VehicleSpeed"), FakeSignal("EngineSpeed")]

    def encode(self, values):
        return bytes([int(values["VehicleSpeed"] * 2), int(values["EngineSpeed"] / 10)])

    def decode(self, payload):
        return {"VehicleSpeed": payload[0] / 2, "EngineSpeed": payload[1] * 10}


class FakeDatabase:
    messages = [FakeMessage()]

    def get_message_by_name(self, name):
        assert name == "VehicleStatus"
        return self.messages[0]


class FakeCantools:
    database = SimpleNamespace(load_file=lambda path: FakeDatabase())


@pytest.fixture(autouse=True)
def mock_cantools(monkeypatch):
    import silkit_runner.dbc as dbc
    monkeypatch.setattr(dbc, "_cantools", lambda: FakeCantools())


def test_generate_typed_definitions(tmp_path):
    generated = generate_dbc(tmp_path / "vehicle.dbc", tmp_path / "vehicle_signals.py")
    spec = importlib.util.spec_from_file_location("vehicle_signals", generated)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    definition = module.Vehicle.VehicleStatus.VehicleSpeed
    assert isinstance(definition, DbcSignalDefinition)
    assert definition.frame_id == 0x123
    assert definition.signal_name == "VehicleSpeed"


def test_bind_decodes_physical_value(tmp_path):
    definition = DbcSignalDefinition(str(tmp_path / "vehicle.dbc"), "VehicleStatus", "VehicleSpeed", 0x123)
    class FakeScenario:
        can_controllers = {"CAN 1": object()}
        def can_signal(self, signal, **kwargs):
            self.signal = signal
            return signal
    scenario = FakeScenario()
    bound = bind_dbc_signal(scenario, definition)
    bound.update(bytes([60, 20]), 10)
    assert bound.get() == 30.0
    assert bound.greater_than(20)()


def test_rejects_ambiguous_network(tmp_path):
    definition = DbcSignalDefinition(str(tmp_path / "vehicle.dbc"), "VehicleStatus", "VehicleSpeed", 0x123)
    scenario = SimpleNamespace(can_controllers={"CAN 1": object(), "CAN 2": object()})
    with pytest.raises(ValueError, match="Specify network"):
        bind_dbc_signal(scenario, definition)


def test_rejects_stale_frame_id(tmp_path):
    definition = DbcSignalDefinition(str(tmp_path / "vehicle.dbc"), "VehicleStatus", "VehicleSpeed", 0x999)
    with pytest.raises(ValueError, match="frame ID"):
        bind_dbc_signal(SimpleNamespace(can_controllers={"CAN 1": object()}), definition)

def test_send_dbc_signal_preserves_other_values(tmp_path):
    from silkit_runner import Scenario
    scenario = Scenario(can_networks=("CAN 1",))
    definition = DbcSignalDefinition(str(tmp_path / "vehicle.dbc"), "VehicleStatus", "VehicleSpeed", 0x123)
    sender = bind_dbc_sender(scenario, definition, initial_values={"EngineSpeed": 200})
    scenario.at(5).set(sender, 30)
    scenario.step(5_000_000, 1_000_000)
    assert scenario.get_can_controller("CAN 1").sent == [
        {"can_id": 0x123, "data": bytes([60, 20]), "flags": 0}
    ]
    sender.set(40)
    assert scenario.get_can_controller("CAN 1").sent[-1]["data"] == bytes([80, 20])


def test_send_dbc_signal_rejects_unknown_initial_signal(tmp_path):
    from silkit_runner import Scenario
    scenario = Scenario(can_networks=("CAN 1",))
    definition = DbcSignalDefinition(str(tmp_path / "vehicle.dbc"), "VehicleStatus", "VehicleSpeed", 0x123)
    with pytest.raises(ValueError, match="Unknown initial"):
        bind_dbc_sender(scenario, definition, initial_values={"Missing": 5})


def test_send_dbc_signal_requires_network_when_ambiguous(tmp_path):
    from silkit_runner import Scenario
    scenario = Scenario(can_networks=("CAN 1", "CAN 2"))
    definition = DbcSignalDefinition(str(tmp_path / "vehicle.dbc"), "VehicleStatus", "VehicleSpeed", 0x123)
    with pytest.raises(ValueError, match="Specify network"):
        bind_dbc_sender(scenario, definition)

def test_message_oriented_send_without_signal_strings(tmp_path):
    from silkit_runner import Scenario
    scenario = Scenario(can_networks=("CAN 1",))
    generated = generate_dbc(tmp_path / "vehicle.dbc", tmp_path / "vehicle_signals.py")
    spec = importlib.util.spec_from_file_location("vehicle_signals", generated)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    vehicle = bind_dbc_vehicle(scenario, module.Vehicle, network="CAN 1")
    vehicle.VehicleStatus.EngineSpeed.set(800)
    vehicle.VehicleStatus.VehicleSpeed.set(35)
    assert vehicle.VehicleStatus.VehicleSpeed.get() == 35
    scenario.at(100).do(vehicle.VehicleStatus.send)
    scenario.step(100_000_000, 1_000_000)
    assert scenario.get_can_controller("CAN 1").sent == [
        {"can_id": 0x123, "data": bytes([70, 80]), "flags": 0}
    ]


def test_message_staging_does_not_transmit(tmp_path):
    from silkit_runner import Scenario
    scenario = Scenario(can_networks=("CAN 1",))
    generated = generate_dbc(tmp_path / "vehicle.dbc", tmp_path / "vehicle_signals.py")
    spec = importlib.util.spec_from_file_location("vehicle_signals", generated)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    vehicle = bind_dbc_vehicle(scenario, module.Vehicle)
    vehicle.VehicleStatus.VehicleSpeed.set(20)
    assert scenario.get_can_controller("CAN 1").sent == []
    vehicle.VehicleStatus.send()
    assert len(scenario.get_can_controller("CAN 1").sent) == 1

def test_message_periodic_send_buffers_signal_updates(tmp_path):
    from silkit_runner import Scenario
    scenario = Scenario(can_networks=("CAN 1",))
    module_path = generate_dbc(tmp_path / "vehicle.dbc", tmp_path / "generated.py")
    spec = importlib.util.spec_from_file_location("generated_vehicle", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    vehicle = scenario.dbc(module.Vehicle, network="CAN 1")
    vehicle.VehicleStatus.EngineSpeed.set(800)
    vehicle.VehicleStatus.VehicleSpeed.set(35)
    vehicle.VehicleStatus.send_every()  # DBC GenMsgCycleTime = 20 ms
    controller = scenario.get_can_controller("CAN 1")
    scenario.step(0, 1_000_000)
    assert controller.sent[-1]["data"] == bytes([70, 80])
    scenario.at(10).set(vehicle.VehicleStatus.VehicleSpeed, 40)
    scenario.step(10_000_000, 1_000_000)
    assert len(controller.sent) == 1  # Updating a signal does not transmit.
    scenario.step(20_000_000, 1_000_000)
    assert len(controller.sent) == 2
    assert controller.sent[-1]["data"] == bytes([80, 80])


def test_message_send_once_and_invalid_cycle(tmp_path):
    from silkit_runner import Scenario
    scenario = Scenario(can_networks=("CAN 1",))
    module_path = generate_dbc(tmp_path / "vehicle.dbc", tmp_path / "generated.py")
    spec = importlib.util.spec_from_file_location("generated_vehicle", module_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    vehicle = scenario.dbc(module.Vehicle)
    vehicle.VehicleStatus.VehicleSpeed.set(15)
    vehicle.VehicleStatus.send_once()
    assert scenario.get_can_controller("CAN 1").sent[-1]["data"] == bytes([30, 0])
    with pytest.raises(ValueError, match="positive period"):
        vehicle.VehicleStatus.send_every(0)
