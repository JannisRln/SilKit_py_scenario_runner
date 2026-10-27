from dataclasses import dataclass
from typing import Callable, Optional, Any, Sequence
from abc import ABC, abstractmethod
import SilKit_py as SilKit
import asyncio

from .scenario_signal import SignalPredicate
from .can_signal import CanSignal, CanSignalRegistry


class Action(ABC):
    @abstractmethod
    def execute(self, sim_time_ms: int) -> None:
        ...

@dataclass
class OneShotAction(Action):
    time_ms: int
    func: Callable
    args: tuple
    executed: bool = False

    def execute(self, sim_time_ms: int) -> None:
        if not self.executed and sim_time_ms >= self.time_ms:
            self.func(*self.args, time_ms=sim_time_ms )
            self.executed = True

@dataclass
class PeriodicAction(Action):
    start_ms: int
    period_ms: int
    func: Callable
    args: tuple
    end_ms: Optional[int] = None

    def execute(self, sim_time_ms: int) -> None:
        if sim_time_ms < self.start_ms:
            return
        if self.end_ms is not None and sim_time_ms > self.end_ms:
            return
        if (sim_time_ms - self.start_ms) % self.period_ms == 0:
            self.func(*self.args, time_ms=sim_time_ms)

@dataclass
class PulseAction(Action):
    start_ms: int
    duration_ms: int
    set_func: Callable
    value: Any
    reset_value: Any
    started: bool = False
    finished: bool = False

    def execute(self, sim_time_ms: int) -> None:
        if not self.started and sim_time_ms >= self.start_ms:
            self.set_func(self.value, time_ms=sim_time_ms )
            self.started = True

        if self.started and not self.finished and sim_time_ms >= self.start_ms + self.duration_ms:
            self.set_func(self.reset_value, time_ms=sim_time_ms )
            self.finished = True

@dataclass
class RampAction(Action):
    start_ms: int
    period_ms: int
    set_func: Callable
    start_value: float
    end_value: float
    step: float

    _current: float|None = None

    def execute(self, sim_time_ms: int) -> None:
        if sim_time_ms < self.start_ms:
            return

        if (sim_time_ms - self.start_ms) % self.period_ms != 0:
            return

        if self._current is None:
            self._current = self.start_value
        else:
            self._current += self.step

        if (self.step > 0 and self._current > self.end_value) or \
           (self.step < 0 and self._current < self.end_value):
            self._current = self.end_value

        self.set_func(self._current, time_ms=sim_time_ms)

@dataclass
class ModulationAction(Action):
    start_ms: int
    period_ms: int
    set_func: Callable
    func: Callable[[int], float]
    end_ms: Optional[int] = None

    def execute(self, sim_time_ms: int) -> None:
        if sim_time_ms < self.start_ms:
            return
        if self.end_ms is not None and sim_time_ms > self.end_ms:
            return
        if (sim_time_ms - self.start_ms) % self.period_ms != 0:
            return

        value = self.func(sim_time_ms)
        self.set_func(value, time_ms=sim_time_ms)

@dataclass
class ClosedLoopAction(Action):
    start_ms: int
    period_ms: int
    read_func: Callable[[], float]
    set_func: Callable[[float, int], None]
    control_func: Callable[[float, int], float]
    end_ms: Optional[int] = None

    def execute(self, sim_time_ms: int) -> None:
        if sim_time_ms < self.start_ms:
            return
        if self.end_ms is not None and sim_time_ms > self.end_ms:
            return
        if (sim_time_ms - self.start_ms) % self.period_ms != 0:
            return

        measurement = self.read_func()
        output = self.control_func(measurement, sim_time_ms)
        self.set_func(output, sim_time_ms)

@dataclass
class GuardedAction(Action):
    inner: Action
    condition: Callable[[], bool]

    def execute(self, sim_time_ms: int) -> None:
        if self.condition():
            self.inner.execute(sim_time_ms)

@dataclass
class ExpectAction(Action):
    predicate: SignalPredicate
    message: str = "Expectation failed"

    def execute(self, sim_time_ms: int) -> None:
        if not self.predicate():
            raise AssertionError(
                f"[t={sim_time_ms}ms] {self.message}: {self.predicate.description}"
            )

@dataclass
class ExpectAtAction(Action):
    time_ms: int
    predicate: SignalPredicate
    message: str = "Expectation failed"
    executed: bool = False

    def execute(self, sim_time_ms: int) -> None:
        if self.executed:
            return

        if sim_time_ms >= self.time_ms:
            self.executed = True
            if not self.predicate():
                raise AssertionError(
                    f"[t={sim_time_ms}ms] {self.message}: {self.predicate.description}"
                )
@dataclass
class InvariantAction(Action):
    predicate: Callable[[], bool]
    message: str
    severity: str = "fail"  # "fail" | "warn"
    start_ms: int = 0
    end_ms: Optional[int] = None

    def execute(self, sim_time_ms: int) -> None:
        if sim_time_ms < self.start_ms:
            return
        if self.end_ms is not None and sim_time_ms > self.end_ms:
            return

        if not self.predicate():
            msg = f"[t={sim_time_ms}ms] Invariant violated: {self.message}"
            if self.severity == "fail":
                raise AssertionError(msg)
            else:
                print("WARNING:", msg)

@dataclass
class EventuallyAction(Action):
    predicate_factory: Callable[[], SignalPredicate] | SignalPredicate
    timeout_ms: int
    message: str = "Expectation not met in time"

    _start_ms: Optional[int] = None
    _satisfied: bool = False
    _predicate: Optional[SignalPredicate] = None

    def execute(self, sim_time_ms: int) -> None:
        if self._satisfied:
            return
        
        if self._predicate is None:
            self._predicate = (
                self.predicate_factory
                if isinstance(self.predicate_factory, SignalPredicate)
                else self.predicate_factory()
            )

        if self._start_ms is None:
            self._start_ms = sim_time_ms

        if self._predicate():
            self._satisfied = True
            return

        if sim_time_ms - self._start_ms >= self.timeout_ms:
            raise AssertionError(
                f"[t={sim_time_ms}ms] {self.message}: {self._predicate.description}"
            )

class Scenario:
    """A generic SIL Kit participant with a simulated-time scenario scheduler.

    ECU-specific RPC initialization, data sets, process management and topic
    discovery are intentionally outside this package. A consumer can register
    a communication-ready hook to implement their own startup protocol.

    Args:
        participant_name: Participant name used by the SIL Kit registry.
        uri: SIL Kit registry URI.
        end_ms: Simulation time at which this participant stops; ``None`` to
            let another participant / the system controller decide.
        can_networks: CAN network names to attach to. Defaults to one network;
            an empty sequence creates a Pub/Sub-only participant.
        step_size_ns: Simulation step size, in nanoseconds.
        on_communication_ready: Optional hook called with this Scenario after
            all configured CAN controllers have been started.
        async_communication_ready: When True, the hook MUST call
            ``complete_communication_ready()`` after its asynchronous work
            finishes, e.g. in an RPC result handler. Not needed for typical
            participants; False uses the normal synchronous lifecycle handler.
        configuration: SIL Kit participant configuration YAML string.
    """

    def __init__(
        self,
        participant_name: str = "scenario_runner",
        uri: str = "silkit://localhost:8500",
        end_ms: int | None = 500,
        *,
        can_networks: Sequence[str] = ("CAN 1",),
        step_size_ns: int = 1_000_000,
        on_communication_ready: Callable[["Scenario"], None] | None = None,
        async_communication_ready: bool = False,
        configuration: str = "",
    ) -> None:
        if step_size_ns <= 0:
            raise ValueError("step_size_ns must be positive")
        if end_ms is not None and end_ms < 0:
            raise ValueError("end_ms must be nonnegative or None")
        if len(set(can_networks)) != len(can_networks):
            raise ValueError("can_networks must contain unique names")
        if async_communication_ready and on_communication_ready is None:
            raise ValueError("async_communication_ready requires a completion hook")

        self._end_ms = end_ms
        self._now_ms = 0
        self._actions: list[Action] = []
        self._last_action: Action | None = None
        self._failure: AssertionError | None = None
        self._on_ready = on_communication_ready
        self._async_ready = async_communication_ready
        self._can_registry = CanSignalRegistry(unhandled_policy="ignore")

        cfg = SilKit.participant_configuration_from_String(configuration)
        self.participant = SilKit.create_participant(cfg, participant_name, uri)

        def _on_can_frame(ctrl: Any, event: Any) -> None:
            self._can_registry.dispatch(
                event.frame.can_id, bytes(event.frame.data), self._now_ms
            )

        self.can_controllers: dict[str, Any] = {}
        for idx, network in enumerate(can_networks, start=1):
            controller = self.participant.create_can_controller(
                f"Scenario_CAN_Controller{idx}", network
            )
            controller.add_frame_handler(_on_can_frame, 2)
            self.can_controllers[network] = controller
            # Preserve the old attribute for simple single-network users.
            setattr(self, f"can_controller{idx}", controller)

        lc_cfg = SilKit.LifecycleConfiguration(SilKit.OperationMode.Coordinated)
        self.lifecycle = self.participant.create_lifecycle_service(lc_cfg)
        self.time_sync = self.lifecycle.create_time_sync_service()
        self.time_sync.set_simulation_step_handler(self.step, step_size_ns)

        if async_communication_ready:
            self.lifecycle.set_communication_ready_handler_async(self._communication_ready)
        else:
            self.lifecycle.set_communication_ready_handler(self._communication_ready)

    def _communication_ready(self) -> None:
        for controller in self.can_controllers.values():
            controller.start()
        if self._on_ready is not None:
            self._on_ready(self)

    def complete_communication_ready(self) -> None:
        """Complete an asynchronous ready hook after its external work finishes."""
        if not self._async_ready:
            raise RuntimeError("Not configured for asynchronous communication-ready")
        self.lifecycle.complete_communication_ready_handler_async()

    def get_can_controller(self, network: str) -> Any:
        """Get the SIL Kit CAN controller attached to the given network."""
        return self.can_controllers[network]

    def _add_action(self, action: Action):
        self._actions.append(action)
        self._last_action = action

    def at(self, time_ms: int):
        return _ScenarioAt(self, time_ms)

    def every(self, period_ms: int, start_ms=0, end_ms=None):
        if period_ms <= 0:
            raise ValueError("period_ms must be positive")
        return _ScenarioEvery(self, period_ms, start_ms, end_ms)

    def when(self, condition: Callable[[], bool]):
        if self._last_action is None:
            raise RuntimeError("when() called without a preceding action")

        guarded = GuardedAction(
            condition=condition,
            inner=self._last_action,
        )

        # replace last action
        self._actions[-1] = guarded
        self._last_action = guarded

        return self
    
    def expect(self, predicate: SignalPredicate, message: str | None = None):
        action = ExpectAction(
            predicate=predicate,
            message=message or "Expectation failed",
        )
        self._add_action(action)
        return self
    
    def invariant(
        self,
        predicate: Callable[[], bool],
        message: str,
        *,
        severity: str = "fail",
        start_ms: int = 0,
        end_ms: int | None = None,
    ):
        action = InvariantAction(
            predicate=predicate,
            message=message,
            severity=severity,
            start_ms=start_ms,
            end_ms=end_ms,
        )
        self._add_action(action)
        return self
    
    def expect_eventually(
        self,
        predicate_factory: Callable[[], SignalPredicate],
        *,
        within_ms: int,
        message: str | None = None,
    ):
        action = EventuallyAction(
            predicate_factory=predicate_factory,
            timeout_ms=within_ms,
            message=message or "Expectation not met in time",
        )
        self._add_action(action)
        return self

    def step(self, now_ns: int, duration_ns: int) -> None:
        self._now_ms = now_ns // 1_000_000

        remaining = []
        for action in self._actions:
            try:
                action.execute(self._now_ms)
            except AssertionError as error:
                self._failure = error
                self.lifecycle.stop("Assertion failed")
                return
            except Exception as error:
                # SIL Kit swallows exceptions raised in the step handler, which would leave run() hanging.
                self._failure = AssertionError(f"{type(error).__name__} in scenario step at t={self._now_ms}ms: {error}")
                self._failure.__cause__ = error
                self.lifecycle.stop("Scenario error")
                return

            finished = (
                (isinstance(action, OneShotAction) and action.executed)
                or (isinstance(action, ExpectAtAction) and action.executed)
                or (isinstance(action, EventuallyAction) and action._satisfied)
                or (
                    isinstance(action, GuardedAction)
                    and (
                        (isinstance(action.inner, OneShotAction) and action.inner.executed)
                        or (isinstance(action.inner, EventuallyAction) and action.inner._satisfied)
                        or (isinstance(action.inner, ExpectAtAction) and action.inner.executed)
                    )
                )
            )
            if not finished:
                remaining.append(action)
        self._actions = remaining

        # if all(
        #     isinstance(a, (OneShotAction, EventuallyAction)) and getattr(a, "executed", True)
        #     for a in self._actions
        # ):
        #     self.lifecycle.stop("Scenario complete")
        #     return

        if self._end_ms is not None and self._now_ms >= self._end_ms:
            self.lifecycle.stop("Simulation done")

    def run(self):
        asyncio.run(self.lifecycle.start_lifecycle()) # type: ignore
        if self._failure:
            raise self._failure

    def can_signal(self, signal: CanSignal) -> CanSignal:
        return self._can_registry.register(signal)

class _ScenarioAt:
    def __init__(self, scenario: Scenario, time_ms: int):
        self._scenario = scenario
        self._time_ms = time_ms

    def do(self, func: Callable, *args):
        action = OneShotAction(self._time_ms, func, args)
        self._scenario._add_action(action)
        return self._scenario

    def set(self, signal, value):
        return self.do(signal.set, value)
    
    def pulse(self, signal, value, duration_ms, reset_value=0):
        action = PulseAction(
            start_ms=self._time_ms,
            duration_ms=duration_ms,
            set_func=signal.set,
            value=value,
            reset_value=reset_value,
        )
        self._scenario._add_action(action)
        return self._scenario
    
    def expect(self, predicate: SignalPredicate, message: str | None = None):
        action = ExpectAtAction(
            time_ms=self._time_ms,
            predicate=predicate,
            message=message or "Expectation failed",
        )
        self._scenario._add_action(action)
        return self._scenario
    
    def expect_eventually(
        self,
        predicate_factory: Callable[[], SignalPredicate],
        *,
        within_ms: int,
        message: str | None = None,
    ):
        action = EventuallyAction(
            predicate_factory=predicate_factory,
            timeout_ms=within_ms,
            message=message or "Expectation not met in time",
        )

        # Delay start until time_ms
        guarded = GuardedAction(
            condition=lambda: self._scenario._now_ms >= self._time_ms,
            inner=action,
        )

        self._scenario._add_action(guarded)
        return self._scenario

class _ScenarioEvery:
    def __init__(self, scenario: Scenario, period_ms: int, start_ms: int, end_ms: Optional[int]):
        self._scenario = scenario
        self._period = period_ms
        self._start = start_ms
        self._end = end_ms

    def do(self, func: Callable, *args):
        action = PeriodicAction(
            start_ms=self._start,
            period_ms=self._period,
            func=func,
            args=args,
            end_ms=self._end,
        )
        self._scenario._add_action(action)
        return self._scenario

    def set(self, signal, value):
        return self.do(signal.set, value)
    
    def ramp(self, signal, start, end, step):
        action = RampAction(
            start_ms=self._start,
            period_ms=self._period,
            set_func=signal.set,
            start_value=start,
            end_value=end,
            step=step,
        )
        self._scenario._add_action(action)
        return self._scenario

    def modulate(self, signal, func):
        action = ModulationAction(
                start_ms=self._start,
                period_ms=self._period,
                set_func=signal.set,
                func=func,
                end_ms=self._end,
            )
        self._scenario._add_action(action)
        return self._scenario

    def control(self, measured_signal, output_signal, controller):
        action = ClosedLoopAction(
                start_ms=self._start,
                period_ms=self._period,
                read_func=measured_signal.get,
                set_func=output_signal.set,
                control_func=controller,
                end_ms=self._end,
            )
        self._scenario._add_action(action)
        return self._scenario
