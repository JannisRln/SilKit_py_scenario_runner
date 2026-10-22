"""Test-only SIL Kit stand-in (stdlib only, not distributed in the wheel)."""

from dataclasses import dataclass
from types import ModuleType, SimpleNamespace
import sys


class FakeCanController:
    def __init__(self, name, network):
        self.name = name
        self.network = network
        self.frame_handler = None
        self.started = False
        self.sent = []

    def add_frame_handler(self, handler, mask):
        self.frame_handler = handler

    def start(self):
        self.started = True

    def send_frame(self, **kwargs):
        self.sent.append(kwargs)

    def receive(self, can_id, data):
        self.frame_handler(self, SimpleNamespace(frame=SimpleNamespace(can_id=can_id, data=data)))


class FakePublisher:
    def __init__(self):
        self.sent = []

    def publish(self, data):
        self.sent.append(data)


class FakeParticipant:
    def __init__(self, name, uri):
        self.name = name
        self.uri = uri
        self.can_controllers = []
        self.publishers = {}
        self.subscribers = {}
        self.lifecycle = FakeLifecycle()

    def create_can_controller(self, name, network):
        can = FakeCanController(name, network)
        self.can_controllers.append(can)
        return can

    def create_lifecycle_service(self, cfg):
        return self.lifecycle

    def create_data_publisher(self, name, spec):
        publisher = FakePublisher()
        self.publishers[name] = publisher
        return publisher

    def create_data_subscriber(self, name, spec, callback):
        self.subscribers[name] = callback
        return SimpleNamespace()


class FakeLifecycle:
    def __init__(self):
        self.ready_callback = None
        self.async_ready = False
        self.completed = False
        self.stopped = None
        self.time_sync = FakeTimeSync()

    def create_time_sync_service(self):
        return self.time_sync

    def set_communication_ready_handler(self, callback):
        self.ready_callback = callback

    def set_communication_ready_handler_async(self, callback):
        self.ready_callback = callback
        self.async_ready = True

    def complete_communication_ready_handler_async(self):
        self.completed = True

    def stop(self, message):
        self.stopped = message

    async def start_lifecycle(self):
        if self.ready_callback:
            self.ready_callback()
        return None


class FakeTimeSync:
    def set_simulation_step_handler(self, callback, step_ns):
        self.callback = callback
        self.step_ns = step_ns


def register():
    fake = ModuleType("SilKit_py")
    fake.IParticipant = FakeParticipant
    fake.IDataPublisher = FakePublisher
    fake.IDataSubscriber = object
    fake.DataMessageEvent = object
    fake.ICanController = FakeCanController
    fake.IRpcClient = object
    fake.RpcCallResultEvent = object
    fake.CanFrameFlag = SimpleNamespace(Ide=1)
    fake.OperationMode = SimpleNamespace(Coordinated="coordinated")
    fake.LifecycleConfiguration = lambda value: value
    fake.participant_configuration_from_String = lambda yaml: yaml
    fake.create_participant = lambda cfg, name, uri: FakeParticipant(name, uri)
    fake.PubSubSpec = lambda topic, media_type: (topic, media_type)
    sys.modules["SilKit_py"] = fake
    return fake
