"""Regression tests for typed DBC generation and binding."""
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from silkit_runner.dbc import DbcSignalDefinition, bind_dbc_signal, bind_dbc_sender, generate_dbc


class FakeSignal:
    def __init__(self, name):
        self.name = name
        self.initial = 0


class FakeMessage:
    name = "VehicleStatus"
    frame_id = 0x123
    is_extended_frame = False
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
