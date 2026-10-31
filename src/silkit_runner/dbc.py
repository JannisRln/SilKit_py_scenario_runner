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
