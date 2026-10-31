"""Typed DBC definitions, code generation and scenario binding.

The optional cantools dependency is required only when using DBC features.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import keyword
from pathlib import Path
import re
from typing import Any


@dataclass(frozen=True)
class DbcSignalDefinition:
    """Immutable reference to a signal within a DBC message."""

    dbc_path: str
    message_name: str
    signal_name: str
    frame_id: int


def _cantools():
    try:
        import cantools
    except ImportError as exc:
        raise ImportError(
            'DBC support requires cantools; install "SilKit_py_scenario_runner[dbc]"'
        ) from exc
    return cantools


def _identifier(name: str) -> str:
    identifier = re.sub(r"\W", "_", name)
    if not identifier or identifier[0].isdigit():
        identifier = "_" + identifier
    if keyword.iskeyword(identifier):
        identifier += "_"
    return identifier


def generate_dbc(source: str | Path, output: str | Path) -> Path:
    """Generate IDE-discoverable Python signal definitions from a DBC file.

    Raises:
        ValueError: A message or signal name cannot be represented uniquely.
    """
    source = Path(source).resolve()
    output = Path(output)
    database = _cantools().database.load_file(str(source))
    lines = [
        '"""Generated DBC signal definitions. Do not edit manually."""',
        "from silkit_runner.dbc import DbcSignalDefinition",
        "",
        "class Vehicle:",
    ]
    used_messages: set[str] = set()
    for message in database.messages:
        message_name = _identifier(message.name)
        if message_name in used_messages:
            raise ValueError(f"DBC message name collision: {message.name}")
        used_messages.add(message_name)
        lines.append(f"    class {message_name}:")
        used_signals: set[str] = set()
        for signal in message.signals:
            signal_name = _identifier(signal.name)
            if signal_name in used_signals:
                raise ValueError(f"DBC signal name collision in {message.name}: {signal.name}")
            used_signals.add(signal_name)
            lines.append(
                f"        {signal_name} = DbcSignalDefinition("
                f"{str(source)!r}, {message.name!r}, {signal.name!r}, {message.frame_id!r})"
            )
        if not message.signals:
            lines.append("        pass")
        lines.append("")
    if not database.messages:
        lines.append("    pass")
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return output


def bind_dbc_signal(scenario: Any, definition: DbcSignalDefinition, *, network: str | None = None):
    """Bind a generated signal definition to a scenario's CAN frame dispatcher."""
    from .scenario.can_signal import CanSignal

    database = _cantools().database.load_file(definition.dbc_path)
    message = database.get_message_by_name(definition.message_name)
    if message.frame_id != definition.frame_id:
        raise ValueError("DBC message frame ID differs from generated definition")
    if definition.signal_name not in {signal.name for signal in message.signals}:
        raise ValueError(f"Unknown DBC signal: {definition.signal_name}")
    if network is not None and network not in scenario.can_controllers:
        raise KeyError(f"Unknown CAN network: {network}")
    if network is None and len(scenario.can_controllers) != 1:
        raise ValueError("Specify network when zero or multiple CAN networks are configured")
    signal = CanSignal(
        name=f"{definition.message_name}.{definition.signal_name}",
        can_id=definition.frame_id,
        decode=lambda payload: message.decode(payload)[definition.signal_name],
    )
    # Use the public network-aware registry API when available.
    if network is None:
        return scenario.can_signal(signal)
    try:
        return scenario.can_signal(signal, network=network)
    except TypeError as exc:
        raise RuntimeError(
            "Network-specific DBC binding requires the network-aware CAN registry"
        ) from exc



class DbcSignalSender:
    """Transmit one DBC signal while retaining other message signal values.

    Values are physical units as understood by cantools. All non-target
    signals must be initialized via `initial_values` or their DBC defaults.
    """

    def __init__(
        self,
        scenario: Any,
        definition: DbcSignalDefinition,
        *,
        network: str | None = None,
        initial_values: dict[str, Any] | None = None,
    ) -> None:
        import SilKit_py as SilKit

        if network is None:
            if len(scenario.can_controllers) != 1:
                raise ValueError("Specify network when zero or multiple CAN networks are configured")
            network = next(iter(scenario.can_controllers))
        self.controller = scenario.get_can_controller(network)
        database = _cantools().database.load_file(definition.dbc_path)
        self.message = database.get_message_by_name(definition.message_name)
        if self.message.frame_id != definition.frame_id:
            raise ValueError("DBC message frame ID differs from generated definition")
        names = {signal.name for signal in self.message.signals}
        if definition.signal_name not in names:
            raise ValueError(f"Unknown DBC signal: {definition.signal_name}")
        self.definition = definition
        self._flags = SilKit.CanFrameFlag.Ide if self.message.is_extended_frame else 0
        self._values = {
            signal.name: signal.initial if signal.initial is not None else 0
            for signal in self.message.signals
        }
        if initial_values:
            unknown = set(initial_values) - names
            if unknown:
                raise ValueError(f"Unknown initial DBC signals: {sorted(unknown)}")
            self._values.update(initial_values)

    def set(self, value: Any, *, time_ms: int = 0) -> None:
        """Encode and send the complete CAN frame with the updated signal.

        `time_ms` is accepted for compatibility with scenario scheduling;
        SIL Kit determines the actual transmission timestamp.
        """
        next_values = {**self._values, self.definition.signal_name: value}
        data = self.message.encode(next_values)
        self.controller.send_frame(
            can_id=self.message.frame_id,
            data=data,
            flags=self._flags,
        )
        self._values = next_values


def bind_dbc_sender(
    scenario: Any,
    definition: DbcSignalDefinition,
    *,
    network: str | None = None,
    initial_values: dict[str, Any] | None = None,
) -> DbcSignalSender:
    """Bind a generated signal definition for scheduled CAN transmission."""
    return DbcSignalSender(
        scenario, definition, network=network, initial_values=initial_values
    )



class DbcMessage:
    """Mutable transmit-side DBC message with attribute-based signal access."""

    def __init__(self, scenario: Any, definitions: type, *, network: str | None = None):
        definitions_by_name = {
            name: value for name, value in vars(definitions).items()
            if isinstance(value, DbcSignalDefinition)
        }
        if not definitions_by_name:
            raise ValueError("No DBC signals in message definition")
        first = next(iter(definitions_by_name.values()))
        self._sender = DbcSignalSender(scenario, first, network=network)
        self._definitions = definitions_by_name
        self._signals = {
            name: DbcMutableSignal(self, definition)
            for name, definition in definitions_by_name.items()
        }

    def __getattr__(self, name: str):
        try:
            return self._signals[name]
        except KeyError:
            raise AttributeError(name) from None

    def send(self, *, time_ms: int = 0) -> None:
        """Encode and transmit the entire current DBC message."""
        data = self._sender.message.encode(self._sender._values)
        self._sender.controller.send_frame(
            can_id=self._sender.message.frame_id,
            data=data,
            flags=self._sender._flags,
        )


class DbcMutableSignal:
    """Mutable value of one DBC signal in a message awaiting transmission."""

    def __init__(self, message: DbcMessage, definition: DbcSignalDefinition):
        self._message = message
        self.definition = definition

    def set(self, value: Any) -> None:
        """Stage a physical signal value without transmitting."""
        self._message._sender._values[self.definition.signal_name] = value

    def get(self) -> Any:
        """Return the currently staged physical signal value."""
        return self._message._sender._values[self.definition.signal_name]


class DbcVehicle:
    """Bind generated message definitions as mutable transmit-side messages."""

    def __init__(self, scenario: Any, vehicle: type, *, network: str | None = None):
        self._messages = {
            name: DbcMessage(scenario, definition, network=network)
            for name, definition in vars(vehicle).items()
            if isinstance(definition, type)
            and any(isinstance(value, DbcSignalDefinition) for value in vars(definition).values())
        }

    def __getattr__(self, name: str):
        try:
            return self._messages[name]
        except KeyError:
            raise AttributeError(name) from None


def bind_dbc_vehicle(scenario: Any, vehicle: type, *, network: str | None = None) -> DbcVehicle:
    """Bind generated DBC messages for string-free signal staging and sending."""
    return DbcVehicle(scenario, vehicle, network=network)



class DbcBoundSignal:
    """A writable signal value within a periodically transmitted CAN message."""

    def __init__(self, message: "DbcBoundMessage", name: str):
        self._message = message
        self.name = name

    def set(self, value: Any, *, time_ms: int = 0) -> None:
        """Update the signal value without transmitting a CAN frame."""
        self._message.set_signal(self.name, value)


class DbcBoundMessage:
    """A CAN message with buffered signal values and explicit send scheduling."""

    def __init__(self, scenario: Any, definition: DbcSignalDefinition, network: str):
        import SilKit_py as SilKit

        self._scenario = scenario
        self._controller = scenario.get_can_controller(network)
        database = _cantools().database.load_file(definition.dbc_path)
        self._message = database.get_message_by_name(definition.message_name)
        self._flags = SilKit.CanFrameFlag.Ide if self._message.is_extended_frame else 0
        self._values = {
            signal.name: signal.initial if signal.initial is not None else 0
            for signal in self._message.signals
        }
        for signal in self._message.signals:
            setattr(self, _identifier(signal.name), DbcBoundSignal(self, signal.name))

    def set_signal(self, name: str, value: Any) -> None:
        """Buffer a physical signal value for the next transmission."""
        if name not in self._values:
            raise ValueError(f"Unknown DBC signal: {name}")
        self._values[name] = value

    def send_once(self, *, time_ms: int = 0) -> None:
        """Encode all buffered signals and send exactly one CAN frame."""
        data = self._message.encode(self._values)
        self._controller.send_frame(
            can_id=self._message.frame_id, data=data, flags=self._flags
        )

    def send_every(
        self, period_ms: int | None = None, *, start_ms: int = 0,
        end_ms: int | None = None
    ) -> None:
        """Schedule periodic transmission; optionally use DBC GenMsgCycleTime."""
        if period_ms is None:
            period_ms = self._message.cycle_time
        if period_ms is None or period_ms <= 0:
            raise ValueError("A positive period or DBC GenMsgCycleTime is required")
        self._scenario.every(period_ms, start_ms=start_ms, end_ms=end_ms).do(self.send_once)


def bind_dbc(scenario: Any, definitions: type, *, network: str | None = None) -> Any:
    """Build a message-oriented DBC namespace from generated definitions."""
    from types import SimpleNamespace

    if network is None:
        if len(scenario.can_controllers) != 1:
            raise ValueError("Specify network when zero or multiple CAN networks are configured")
        network = next(iter(scenario.can_controllers))
    if network not in scenario.can_controllers:
        raise ValueError(f"Unknown CAN network: {network}")
    messages = {}
    for name, message_type in vars(definitions).items():
        if name.startswith("_") or not isinstance(message_type, type):
            continue
        definitions_in_message = [
            value for value in vars(message_type).values()
            if isinstance(value, DbcSignalDefinition)
        ]
        if definitions_in_message:
            messages[name] = DbcBoundMessage(scenario, definitions_in_message[0], network)
    return SimpleNamespace(**messages)


def main(argv: list[str] | None = None) -> None:
    """Command-line entry point for generating typed DBC definitions."""
    parser = argparse.ArgumentParser(description="Generate typed Python DBC signal definitions")
    parser.add_argument("command", choices=["generate"])
    parser.add_argument("source", help="DBC input file")
    parser.add_argument("--output", required=True, help="Generated Python module path")
    args = parser.parse_args(argv)
    generate_dbc(args.source, args.output)


if __name__ == "__main__":
    main()
