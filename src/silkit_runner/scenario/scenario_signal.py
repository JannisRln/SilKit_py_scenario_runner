from dataclasses import dataclass, field
from typing import Any, Optional, Callable
import SilKit_py as SilKit


def encode_int32(value: Any) -> bytes:
    """Encode a signed 32-bit signal in little-endian order."""
    return int(value).to_bytes(4, "little", signed=True)


def decode_int32(data: bytes) -> int:
    """Decode a signed little-endian integer payload."""
    return int.from_bytes(data, "little", signed=True)

@dataclass(frozen=True)
class SignalPredicate:
    check: Callable[[], bool]
    description: str = ""

    def __call__(self) -> bool:
        return self.check()

    def __and__(self, other: "SignalPredicate") -> "SignalPredicate":
        return SignalPredicate(
            lambda: self() and other(),
            f"({self.description} AND {other.description})",
        )

    def __or__(self, other: "SignalPredicate") -> "SignalPredicate":
        return SignalPredicate(
            lambda: self() or other(),
            f"({self.description} OR {other.description})",
        )

    def __invert__(self) -> "SignalPredicate":
        return SignalPredicate(
            lambda: not self(),
            f"(NOT {self.description})",
        )


@dataclass
class SilKitSignal:
    name: str
    participant: SilKit.IParticipant
    topic: str
    media_type: str = "application/vnd.vector.silkit.data; protocolVersion=1"
    initial: Any = 0
    encoder: Callable[[Any], bytes] = encode_int32
    decoder: Callable[[bytes], Any] = decode_int32
    publisher: Optional[SilKit.IDataPublisher] = None
    subscriber: Optional[SilKit.IDataSubscriber] = None

    _value: Any = field(init=False)
    _last_sent_value: Any = field(init=False)
    _last_received_value: Any = field(init=False, default=None)
    _received: bool = field(init=False, default=False)

    def __post_init__(self):
        self._value = self.initial
        self._last_sent_value = self.initial

        spec = SilKit.PubSubSpec(self.topic, self.media_type)

        self.publisher = self.participant.create_data_publisher(
            f"Pub_{self.topic}",
            spec,
        )

        self.subscriber = self.participant.create_data_subscriber(
            f"Sub_{self.topic}",
            spec,
            self._on_message,
        )

    def _on_message(
        self,
        subscriber: SilKit.IDataSubscriber,
        event: SilKit.DataMessageEvent,
    ):
        self._last_received_value = self.decoder(bytes(event.data))
        self._received = True
        self._value = self._last_received_value

    @property
    def last_sent_value(self) -> Any:
        return self._last_sent_value

    @property
    def last_received_value(self) -> Any:
        return self._last_received_value

    @property
    def received(self) -> bool:
        return self._received

    def set(self, value: Any, time_ms: int):
        self._last_sent_value = value
        self.publish()

    def publish(self):
        payload = self.encoder(self._last_sent_value)
        if self.publisher is None:
            raise RuntimeError("SIL Kit publisher has not been created")
        self.publisher.publish(payload)

    def get(self):
        return self._value

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
            lambda: self.get() > value,
            f"{self.name} > {value}",
        )

    def greater_equal(self, value: float) -> SignalPredicate:
        return SignalPredicate(
            lambda: self.get() >= value,
            f"{self.name} >= {value}",
        )

    def less_than(self, value: float) -> SignalPredicate:
        return SignalPredicate(
            lambda: self.get() < value,
            f"{self.name} < {value}",
        )

    def between(self, low: float, high: float, inclusive: bool = True) -> SignalPredicate:
        if inclusive:
            return SignalPredicate(
                lambda: low <= self.get() <= high,
                f"{low} <= {self.name} <= {high}",
            )
        return SignalPredicate(
            lambda: low < self.get() < high,
            f"{low} < {self.name} < {high}",
        )

    def has_value(self) -> SignalPredicate:
        return SignalPredicate(
            lambda: self.get() is not None,
            f"{self.name} has value",
        )

class LoggedSilKitSignal(SilKitSignal):
    def __init__(self, name, participant, topic, mdf_context, **kwargs):
        super().__init__(name, participant, topic, **kwargs)
        self._ctx = mdf_context
        self._ctx["signals"].setdefault(name, [])

    def set(self, value, time_ms: int):
        super().set(value, time_ms)
        self._ctx["signals"][self.name].append((time_ms, value))

class SignalGroup:
    def __init__(self, *signals):
        self._signals = signals

    def all(self, predicate_factory):
        return SignalPredicate(
            lambda: all(predicate_factory(s)() for s in self._signals),
            "ALL signals satisfy condition",
        )

    def any(self, predicate_factory):
        return SignalPredicate(
            lambda: any(predicate_factory(s)() for s in self._signals),
            "ANY signal satisfies condition",
        )

    def none(self, predicate_factory):
        return SignalPredicate(
            lambda: not any(predicate_factory(s)() for s in self._signals),
            "NO signal satisfies condition",
        )


