from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .scenario_signal import SignalPredicate


@dataclass
class CanSignal:
    """
    Represents a decoded value derived from a CAN frame.
    Fully compatible with SignalPredicate, SignalGroup, expect(), when().
    """
    name: str
    can_id: int
    decode: Callable[[bytes], Any]

    _value: Any = None
    _last_time_ms: Optional[int] = None

    # ---------- update / access ----------

    def update(self, data: bytes, time_ms: int) -> None:
        self._value = self.decode(data)
        self._last_time_ms = time_ms

    def get(self):
        return self._value

    def has_value(self) -> bool:
        return self._value is not None

    # ---------- predicate helpers (mirror SilKitSignal) ----------

    def is_equal(self, value: Any) -> SignalPredicate:
        return SignalPredicate(
            lambda: self.get() == value,
            f"{self.name} == {value}",
        )

    def is_not_equal(self, value: Any) -> SignalPredicate:
        return SignalPredicate(
            lambda: self.get() != value,
            f"{self.name} != {value}",
        )

    def greater_than(self, value: float) -> SignalPredicate:
        return SignalPredicate(
            lambda: self.get() is not None and self.get() > value,
            f"{self.name} > {value}",
        )

    def greater_equal(self, value: float) -> SignalPredicate:
        return SignalPredicate(
            lambda: self.get() is not None and self.get() >= value,
            f"{self.name} >= {value}",
        )

    def less_than(self, value: float) -> SignalPredicate:
        return SignalPredicate(
            lambda: self.get() is not None and self.get() < value,
            f"{self.name} < {value}",
        )

    def between(self, low: float, high: float, inclusive: bool = True) -> SignalPredicate:
        if inclusive:
            return SignalPredicate(
                lambda: self.get() is not None and low <= self.get() <= high,
                f"{low} <= {self.name} <= {high}",
            )
        return SignalPredicate(
            lambda: self.get() is not None and low < self.get() < high,
            f"{low} < {self.name} < {high}",
        )

#TODO: Should be changed to a feature in CanSignal and SilKitSignal instead of a extra class, to turned on if a flag
@dataclass
class LoggedCanSignal(CanSignal):
    log: list[tuple[int, Any]] = field(default_factory=list)

    def update(self, data: bytes, time_ms: int) -> None:
        super().update(data, time_ms)
        self.log.append((time_ms, self._value))


class CanSignalRegistry:
    def __init__(self, *, unhandled_policy: str = "warn"):
        self._by_can_id: dict[int, list[CanSignal]] = {}
        self._unhandled_policy = unhandled_policy
        self._unhandled: list[tuple[int, bytes, int]] = []

    def register(self, signal: CanSignal) -> CanSignal:
        self._by_can_id.setdefault(signal.can_id, []).append(signal)
        return signal

    def dispatch(self, can_id: int, data: bytes, time_ms: int) -> None:
        signals = self._by_can_id.get(can_id)

        if not signals:
            self._handle_unhandled(can_id, data, time_ms)
            return

        for sig in signals:
            sig.update(data, time_ms)

    def _handle_unhandled(self, can_id: int, data: bytes, time_ms: int):
        if self._unhandled_policy == "ignore":
            return

        if self._unhandled_policy == "log":
            print(f"[CAN] Unhandled frame 0x{can_id:X} @ {time_ms}ms - payload {data}")

        elif self._unhandled_policy == "warn":
            print(f"WARNING: Unhandled CAN frame 0x{can_id:X} @ {time_ms}ms - payload {data}")

        elif self._unhandled_policy == "fail":
            raise AssertionError(
                f"Unhandled CAN frame 0x{can_id:X} @ {time_ms}ms - payload {data}"
            )

        elif self._unhandled_policy == "collect":
            self._unhandled.append((can_id, data, time_ms))
