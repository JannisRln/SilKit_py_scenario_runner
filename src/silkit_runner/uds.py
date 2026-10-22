import SilKit_py as SilKit
from dataclasses import dataclass
from .scenario.can_signal import CanSignal

@dataclass(frozen=True)
class DecodedUdsResponse:
    service: int
    response_sid: int
    did: int | None
    payload: bytes
    negative: bool
    nrc: int | None


def decode_uds_response(data: bytes) -> DecodedUdsResponse:
    if not data:
        raise ValueError("Empty UDS frame")

    sid = data[0]

    # Negative Response
    if sid == 0x7F:
        if len(data) < 3:
            raise ValueError("Incomplete negative UDS response")
        return DecodedUdsResponse(
            service=data[1],
            response_sid=0x7F,
            did=None,
            payload=b"",
            negative=True,
            nrc=data[2],
        )

    # Positive response
    service = sid - 0x40 if sid >= 0x40 else sid

    did = None
    payload = b""

    if len(data) >= 3:
        did = (data[1] << 8) | data[2]
        payload = data[3:]

    return DecodedUdsResponse(
        service=service,
        response_sid=sid,
        did=did,
        payload=payload,
        negative=False,
        nrc=None,
    )


def send_uds_request(
    can_controller: SilKit.ICanController,
    can_id: int,
    payload: bytes,
    *,
    time_ms: int = 0,
) -> None:
    """Send a single-frame UDS payload on a 29-bit CAN identifier.

    ``time_ms`` is accepted for scheduling compatibility; SIL Kit determines
    the transmit time. ISO-TP segmentation and CAN FD are not implemented.
    """
    can_controller.send_frame(
        can_id = can_id,
        data = payload,
        flags = SilKit.CanFrameFlag.Ide
        )


def uds_rdbi(did: int) -> bytes:
    return bytes([
        0x22,
        (did >> 8) & 0xFF,
        did & 0xFF,
    ])

def uds_wdbi(did: int, data: bytes) -> bytes:
    return bytes([
        0x2E,
        (did >> 8) & 0xFF,
        did & 0xFF,
    ]) + data


class UdsDidSignal(CanSignal):
    def __init__(
        self,
        *, # * is used to force keyword-only arguments.
        name: str,
        did: int,
        req_can_id: int = 0x18DAF110,
        resp_can_id: int = 0x18DA10F1,
    ):
        super().__init__(
            name=name,
            can_id=resp_can_id,
            decode=lambda _: None,  # decoding handled in update()
        )
        self.did = did
        self.req_can_id = req_can_id

    # -------- response handling (sticky) --------

    def update(self, data: bytes, time_ms: int) -> None:
        r = decode_uds_response(data)

        if r.negative:
            return

        if r.did != self.did:
            return

        self._value = int.from_bytes(r.payload, byteorder="little")
        self._last_time_ms = time_ms

    # -------- request API --------

    def request(
        self,
        can_controller: SilKit.ICanController,
        *,
        time_ms: int = 0,
    ) -> None:
        send_uds_request(
            can_controller,
            self.req_can_id,
            uds_rdbi(self.did),
            time_ms=time_ms,
        )
