"""Contract tests for the migrated LibreVNA USB transport layer (ISSUE-019).

The transport layer is sweep-free: USB device discovery/open/claim/release,
bulk read/write, protocol frame codec (header/length/type/CRC32) with strict
length caps, timeout/cancellable I/O, idempotent close and structured error
mapping.  No real USB is ever touched: the session facade is tested with an
in-memory adapter and the PyUSB adapter is tested with fake ``usb`` modules
injected through ``importlib`` (the reference-project test pattern).

Golden byte vectors are taken verbatim from the audited reference tests
(``tests/test_librevna_protocol.py`` L45-47); the migration checklist and
provenance are recorded in ``docs/plans/2026-08-30-issue-019-librevna-transport.md``.
"""

from __future__ import annotations

import struct
import subprocess
import sys
import types
from contextlib import contextmanager
from pathlib import Path
from unittest import mock

import pytest

import uav_gpr.acquisition.librevna.transport as transport_module
from uav_gpr.acquisition.librevna.transport import (
    ACK,
    HEADER,
    MAX_PACKET_LENGTH,
    MIN_PACKET_LENGTH,
    REQUEST_DEVICE_INFO,
    SET_IDLE,
    SWEEP_SETTINGS,
    VNA_DATAPOINT,
    LibreVnaBusyError,
    LibreVnaCancelledError,
    LibreVnaDeviceNotFoundError,
    LibreVnaDisconnectedError,
    LibreVnaMissingDependencyError,
    LibreVnaNotOpenError,
    LibreVnaReleaseError,
    LibreVnaTimeoutError,
    LibreVnaTransportError,
    LibreVnaUsbTransport,
    Packet,
    PacketStream,
    PyUsbAdapter,
    crc32,
    encode_packet,
)
from uav_gpr.core import DomainError, ErrorCode

SRC_DIR = Path(__file__).resolve().parents[2] / "src"

# ---- Golden byte vectors (reference tests/test_librevna_protocol.py L45-47) ----

ACK_PACKET_HEX = "5a080007c1f48315"
REQ_DEV_INFO_HEX = "5a08000ff37c581b"
SET_IDLE_HEX = "5a0800141fb53d91"
ACK_PACKET_BYTES = bytes.fromhex(ACK_PACKET_HEX)


# ---------------------------------------------------------------------------
# Frame codec: crc32 / encode_packet / PacketStream
# ---------------------------------------------------------------------------


class TestCrc32:
    def test_known_vector(self) -> None:
        # zlib known vector: crc32(b"123456789") == 0xCBF43926
        assert crc32(b"123456789") == 0xCBF43926

    def test_empty_data(self) -> None:
        assert crc32(b"") == 0

    def test_high_bit_range(self) -> None:
        assert crc32(b"a") == crc32(b"a") & 0xFFFFFFFF


class TestEncodePacket:
    def test_ack_fixed_bytes(self) -> None:
        assert encode_packet(ACK).hex() == ACK_PACKET_HEX

    def test_request_device_info_fixed_bytes(self) -> None:
        assert encode_packet(REQUEST_DEVICE_INFO).hex() == REQ_DEV_INFO_HEX

    def test_set_idle_fixed_bytes(self) -> None:
        assert encode_packet(SET_IDLE).hex() == SET_IDLE_HEX

    def test_packet_length_field(self) -> None:
        data = encode_packet(SWEEP_SETTINGS, b"\x00" * 10)
        # length = HEADER(1) + length(2) + type(1) + payload(10) + CRC(4) = 18
        assert struct.unpack_from("<H", data, 1)[0] == 18
        assert len(data) == 18

    def test_crc_covers_body(self) -> None:
        data = encode_packet(ACK)
        body = data[:-4]
        assert struct.unpack_from("<I", data, 4)[0] == crc32(body)


class TestPacketStream:
    def test_single_packet(self) -> None:
        stream = PacketStream()
        assert stream.feed(ACK_PACKET_BYTES) == [Packet(ACK, b"")]

    def test_noise_byte_prefix(self) -> None:
        stream = PacketStream()
        packets = stream.feed(b"\xde\xad\xbe\xef" + ACK_PACKET_BYTES)
        assert packets == [Packet(ACK, b"")]

    def test_multiple_packets_one_read(self) -> None:
        stream = PacketStream()
        packets = stream.feed(ACK_PACKET_BYTES + bytes.fromhex(SET_IDLE_HEX))
        assert packets == [Packet(ACK, b""), Packet(SET_IDLE, b"")]

    def test_split_across_reads(self) -> None:
        stream = PacketStream()
        assert stream.feed(ACK_PACKET_BYTES[:5]) == []
        assert stream.feed(ACK_PACKET_BYTES[5:]) == [Packet(ACK, b"")]

    def test_buffer_persists_across_reads(self) -> None:
        stream = PacketStream()
        assert stream.feed(b"\x00\x01\x02") == []
        assert stream.feed(ACK_PACKET_BYTES) == [Packet(ACK, b"")]

    def test_invalid_length_drops_byte(self) -> None:
        # length=0 (< 8) fake header: drop that byte and realign to the next packet
        stream = PacketStream()
        packets = stream.feed(b"\x5a\x00\x00" + ACK_PACKET_BYTES)
        assert packets == [Packet(ACK, b"")]

    def test_length_lower_bound_resync(self) -> None:
        # length=7 (< MIN_PACKET_LENGTH=8, little-endian field): the header
        # byte is dropped and the stream realigns to the next packet
        stream = PacketStream()
        packets = stream.feed(b"\x5a\x07\x00" + ACK_PACKET_BYTES)
        assert packets == [Packet(ACK, b"")]

    def test_length_upper_bound_resync(self) -> None:
        # length=0x1001 (4097 > MAX_PACKET_LENGTH=4096): drop header byte, realign
        stream = PacketStream()
        packets = stream.feed(b"\x5a\x01\x10" + ACK_PACKET_BYTES)
        assert packets == [Packet(ACK, b"")]

    def test_length_upper_bound_accepts_max(self) -> None:
        # length == 4096 is the inclusive upper bound: a full-size packet parses
        payload = b"\x00" * (MAX_PACKET_LENGTH - 8)
        stream = PacketStream()
        packets = stream.feed(encode_packet(SWEEP_SETTINGS, payload))
        assert packets == [Packet(SWEEP_SETTINGS, payload)]

    def test_crc_error_drops_non_datapoint_packet(self) -> None:
        bad = bytearray(ACK_PACKET_BYTES)
        bad[-1] ^= 0xFF
        stream = PacketStream()
        packets = stream.feed(bytes(bad) + ACK_PACKET_BYTES)
        assert packets == [Packet(ACK, b"")]

    def test_vna_datapoint_crc_is_skipped(self) -> None:
        # Reference behavior: VNA_DATAPOINT (type 27) skips CRC validation.
        # This is existing protocol behavior and must not be "fixed".
        payload = b"\x00\x11\x22\x33\x44\x55\x66\x77"
        bad = bytearray(encode_packet(VNA_DATAPOINT, payload))
        bad[-1] ^= 0xFF
        stream = PacketStream()
        packets = stream.feed(bytes(bad))
        assert packets == [Packet(VNA_DATAPOINT, payload)]

    def test_reset_clears_buffer(self) -> None:
        stream = PacketStream()
        assert stream.feed(ACK_PACKET_BYTES[:5]) == []
        stream.reset()
        assert stream.feed(ACK_PACKET_BYTES) == [Packet(ACK, b"")]


# ---------------------------------------------------------------------------
# Structured errors
# ---------------------------------------------------------------------------


class TestTransportErrors:
    def test_all_errors_are_domain_errors_with_stable_reasons(self) -> None:
        cases: list[tuple[LibreVnaTransportError, str]] = [
            (LibreVnaMissingDependencyError("dependency missing"), "missing_dependency"),
            (LibreVnaDeviceNotFoundError("device not found"), "device_not_found"),
            (LibreVnaBusyError("device busy"), "busy"),
            (LibreVnaTimeoutError("read timed out"), "timeout"),
            (LibreVnaDisconnectedError("device disconnected"), "disconnected"),
            (LibreVnaReleaseError("release failed"), "release_failed"),
            (LibreVnaCancelledError("cancelled"), "cancelled"),
            (LibreVnaNotOpenError("not open"), "not_open"),
        ]
        for error, reason in cases:
            assert isinstance(error, DomainError)
            assert error.code == ErrorCode.INVALID_ARGUMENT
            assert error.reason == reason
            assert error.context["reason"] == reason
            assert error.to_dict()["code"] == "invalid_argument"


# ---------------------------------------------------------------------------
# Transport session with an injected in-memory adapter (no USB anywhere)
# ---------------------------------------------------------------------------


class _FakeAdapter:
    """In-memory adapter implementing the ``UsbAdapter`` protocol."""

    def __init__(
        self,
        *,
        read_data: bytes = b"payload",
        read_error: Exception | None = None,
        write_error: Exception | None = None,
    ) -> None:
        self._read_data = read_data
        self._read_error = read_error
        self._write_error = write_error
        self.opened = False
        self.closed = False
        self.open_calls = 0
        self.close_calls = 0
        self.read_calls: list[tuple[int, int]] = []
        self.written: list[bytes] = []

    @property
    def is_open(self) -> bool:
        return self.opened

    def open(self) -> None:
        self.open_calls += 1
        self.opened = True

    def read(self, max_length: int, timeout_ms: int) -> bytes:
        self.read_calls.append((max_length, timeout_ms))
        if self._read_error is not None:
            raise self._read_error
        return self._read_data

    def write(self, data: bytes) -> None:
        if self._write_error is not None:
            raise self._write_error
        self.written.append(bytes(data))

    def close(self) -> None:
        self.close_calls += 1
        self.opened = False
        self.closed = True


class TestTransportSession:
    def test_open_read_write_close_roundtrip(self) -> None:
        adapter = _FakeAdapter()
        transport = LibreVnaUsbTransport(adapter)
        assert not transport.is_open
        transport.open()
        assert transport.is_open
        assert adapter.open_calls == 1
        assert transport.read(64, 50) == b"payload"
        assert adapter.read_calls == [(64, 50)]
        transport.write(b"\x5a\x00")
        assert adapter.written == [b"\x5a\x00"]
        transport.close()
        assert not transport.is_open
        assert adapter.closed
        assert adapter.close_calls == 1

    def test_open_is_idempotent(self) -> None:
        adapter = _FakeAdapter()
        transport = LibreVnaUsbTransport(adapter)
        transport.open()
        transport.open()
        assert adapter.open_calls == 1

    def test_close_is_idempotent(self) -> None:
        adapter = _FakeAdapter()
        transport = LibreVnaUsbTransport(adapter)
        transport.close()  # not open: no-op
        transport.open()
        transport.close()
        transport.close()
        assert not transport.is_open
        assert adapter.close_calls == 1

    def test_read_before_open_rejected(self) -> None:
        transport = LibreVnaUsbTransport(_FakeAdapter())
        with pytest.raises(LibreVnaNotOpenError):
            transport.read(64, 50)

    def test_write_before_open_rejected(self) -> None:
        transport = LibreVnaUsbTransport(_FakeAdapter())
        with pytest.raises(LibreVnaNotOpenError):
            transport.write(b"\x5a\x00")

    def test_adapter_timeout_error_propagates(self) -> None:
        adapter = _FakeAdapter(read_error=LibreVnaTimeoutError("read timed out"))
        transport = LibreVnaUsbTransport(adapter)
        transport.open()
        with pytest.raises(LibreVnaTimeoutError):
            transport.read(64, 50)

    def test_adapter_disconnect_error_propagates(self) -> None:
        adapter = _FakeAdapter(write_error=LibreVnaDisconnectedError("lost device"))
        transport = LibreVnaUsbTransport(adapter)
        transport.open()
        with pytest.raises(LibreVnaDisconnectedError):
            transport.write(b"\x5a\x00")


class TestTransportCancel:
    def test_cancel_makes_read_raise_cancelled(self) -> None:
        adapter = _FakeAdapter()
        transport = LibreVnaUsbTransport(adapter)
        transport.open()
        transport.cancel()
        with pytest.raises(LibreVnaCancelledError):
            transport.read(64, 50)
        assert adapter.read_calls == []  # adapter never touched

    def test_cancel_makes_write_raise_cancelled(self) -> None:
        adapter = _FakeAdapter()
        transport = LibreVnaUsbTransport(adapter)
        transport.open()
        transport.cancel()
        with pytest.raises(LibreVnaCancelledError):
            transport.write(b"\x5a\x00")
        assert adapter.written == []

    def test_cancel_before_open_is_safe(self) -> None:
        transport = LibreVnaUsbTransport(_FakeAdapter())
        transport.cancel()  # no-op, no handle
        with pytest.raises(LibreVnaNotOpenError):
            transport.read(64, 50)

    def test_cancel_after_close_is_safe(self) -> None:
        adapter = _FakeAdapter()
        transport = LibreVnaUsbTransport(adapter)
        transport.open()
        transport.cancel()
        transport.close()
        assert adapter.closed
        transport.cancel()  # safe no-op after close
        with pytest.raises(LibreVnaNotOpenError):
            transport.read(64, 50)

    def test_open_clears_cancel(self) -> None:
        adapter = _FakeAdapter()
        transport = LibreVnaUsbTransport(adapter)
        transport.open()
        transport.cancel()
        transport.open()  # idempotent open also starts a fresh session
        assert transport.read(64, 50) == b"payload"

    def test_close_after_cancel_releases_resources(self) -> None:
        adapter = _FakeAdapter()
        transport = LibreVnaUsbTransport(adapter)
        transport.open()
        transport.cancel()
        transport.close()
        assert adapter.closed
        assert not transport.is_open


# ---------------------------------------------------------------------------
# PyUsbAdapter mapped through fake usb modules (reference test pattern)
# ---------------------------------------------------------------------------


class _FakeUsbError(Exception):
    pass


class _FakeUsbTimeoutError(_FakeUsbError):
    pass


class _FakeInterface:
    bInterfaceNumber = 0


class _FakeConfig:
    def __getitem__(self, key: object) -> _FakeInterface:
        if key != (0, 0):
            raise KeyError(key)
        return _FakeInterface()


class _FakeDevice:
    """Fake PyUSB device with programmable read/write behavior."""

    def __init__(
        self,
        *,
        read_data: bytes = b"payload",
        read_error: Exception | None = None,
        write_error: Exception | None = None,
    ) -> None:
        self._read_data = read_data
        self._read_error = read_error
        self._write_error = write_error
        self.written: list[bytes] = []
        self.set_configuration_called = False

    def set_configuration(self) -> None:
        self.set_configuration_called = True

    def get_active_configuration(self) -> _FakeConfig:
        return _FakeConfig()

    def is_kernel_driver_active(self, number: int) -> bool:
        return False

    def read(self, ep: object, max_length: int, timeout: object = None) -> bytes:
        if self._read_error is not None:
            raise self._read_error
        return self._read_data

    def write(self, ep: object, data: bytes, timeout: object = None) -> None:
        if self._write_error is not None:
            raise self._write_error
        self.written.append(bytes(data))


class _FakeUsbUtil:
    def __init__(
        self,
        *,
        claim_error: Exception | None = None,
        release_error: Exception | None = None,
    ) -> None:
        self._claim_error = claim_error
        self._release_error = release_error
        self.claimed: list[int] = []
        self.released: list[int] = []
        self.disposed = False

    def claim_interface(self, dev: object, interface: int) -> None:
        if self._claim_error is not None:
            raise self._claim_error
        self.claimed.append(interface)

    def release_interface(self, dev: object, interface: int) -> None:
        if self._release_error is not None:
            raise self._release_error
        self.released.append(interface)

    def dispose_resources(self, dev: object) -> None:
        self.disposed = True


class _FakeImportlib:
    """Replaces ``importlib`` inside the transport module with fake usb modules."""

    def __init__(self, core: object, util: object) -> None:
        self._core = core
        self._util = util
        self.imports: list[str] = []

    def import_module(self, name: str) -> object:
        self.imports.append(name)
        if name == "usb.core":
            return self._core
        if name == "usb.util":
            return self._util
        raise ModuleNotFoundError(f"No module named '{name}'", name=name)


def _make_core(find_fn: object) -> types.SimpleNamespace:
    return types.SimpleNamespace(
        USBError=_FakeUsbError,
        USBTimeoutError=_FakeUsbTimeoutError,
        find=find_fn,
    )


@contextmanager
def _patched_transport(
    core: types.SimpleNamespace, util: _FakeUsbUtil
) -> types.Generator[_FakeImportlib, None, None]:
    fake_importlib = _FakeImportlib(core, util)
    with mock.patch.object(transport_module, "importlib", fake_importlib):
        yield fake_importlib


class TestPyUsbAdapter:
    def test_constructor_does_not_open(self) -> None:
        adapter = PyUsbAdapter()
        assert not adapter.is_open

    def test_missing_dependency_friendly_error(self) -> None:
        def import_module(name: str) -> object:
            raise ModuleNotFoundError(f"No module named '{name}'", name=name)

        fake = types.SimpleNamespace(import_module=import_module)
        with mock.patch.object(transport_module, "importlib", fake):
            adapter = PyUsbAdapter()
            with pytest.raises(LibreVnaMissingDependencyError) as ctx:
                adapter.open()
        message = str(ctx.value)
        assert "pip install" in message
        assert "hardware" in message
        assert not adapter.is_open

    def test_device_not_found(self) -> None:
        core = _make_core(lambda idVendor, idProduct, backend=None: None)
        with _patched_transport(core, _FakeUsbUtil()):
            adapter = PyUsbAdapter()
            with pytest.raises(LibreVnaDeviceNotFoundError) as ctx:
                adapter.open()
        assert "1209" in str(ctx.value)
        assert not adapter.is_open

    def test_find_error_is_busy(self) -> None:
        def find_fn(idVendor: object, idProduct: object, backend: object = None) -> object:
            raise _FakeUsbError("device busy")

        core = _make_core(find_fn)
        with _patched_transport(core, _FakeUsbUtil()):
            adapter = PyUsbAdapter()
            with pytest.raises(LibreVnaBusyError):
                adapter.open()

    def test_claim_error_is_busy_and_disposes(self) -> None:
        dev = _FakeDevice()
        core = _make_core(lambda idVendor, idProduct, backend=None: dev)
        util = _FakeUsbUtil(claim_error=_FakeUsbError("claimed elsewhere"))
        with _patched_transport(core, util):
            adapter = PyUsbAdapter()
            with pytest.raises(LibreVnaBusyError):
                adapter.open()
        assert not adapter.is_open
        # claim failure must still attempt to dispose device resources
        assert util.disposed

    def test_open_read_write_close(self) -> None:
        dev = _FakeDevice()
        util = _FakeUsbUtil()
        core = _make_core(lambda idVendor, idProduct, backend=None: dev)
        with _patched_transport(core, util):
            adapter = PyUsbAdapter()
            adapter.open()
            assert adapter.is_open
            assert dev.set_configuration_called
            assert util.claimed == [0]
            assert adapter.read(512, 50) == b"payload"
            adapter.write(b"\x5a\x00")
            assert dev.written == [b"\x5a\x00"]
            adapter.close()
            assert util.released == [0]
            assert util.disposed
            assert not adapter.is_open

    def test_open_is_idempotent(self) -> None:
        dev = _FakeDevice()
        util = _FakeUsbUtil()
        core = _make_core(lambda idVendor, idProduct, backend=None: dev)
        with _patched_transport(core, util):
            adapter = PyUsbAdapter()
            adapter.open()
            adapter.open()
            assert util.claimed == [0]

    def test_close_is_idempotent(self) -> None:
        dev = _FakeDevice()
        util = _FakeUsbUtil()
        core = _make_core(lambda idVendor, idProduct, backend=None: dev)
        with _patched_transport(core, util):
            adapter = PyUsbAdapter()
            adapter.close()  # not open: no-op
            adapter.open()
            adapter.close()
            adapter.close()
            assert not adapter.is_open

    def test_read_timeout_maps(self) -> None:
        dev = _FakeDevice(read_error=_FakeUsbTimeoutError("timeout"))
        util = _FakeUsbUtil()
        core = _make_core(lambda idVendor, idProduct, backend=None: dev)
        with _patched_transport(core, util):
            adapter = PyUsbAdapter()
            adapter.open()
            with pytest.raises(LibreVnaTimeoutError):
                adapter.read(512, 50)

    def test_read_disconnect_maps(self) -> None:
        dev = _FakeDevice(read_error=_FakeUsbError("pipe closed"))
        util = _FakeUsbUtil()
        core = _make_core(lambda idVendor, idProduct, backend=None: dev)
        with _patched_transport(core, util):
            adapter = PyUsbAdapter()
            adapter.open()
            with pytest.raises(LibreVnaDisconnectedError):
                adapter.read(512, 50)

    def test_write_disconnect_maps(self) -> None:
        dev = _FakeDevice(write_error=_FakeUsbError("pipe closed"))
        util = _FakeUsbUtil()
        core = _make_core(lambda idVendor, idProduct, backend=None: dev)
        with _patched_transport(core, util):
            adapter = PyUsbAdapter()
            adapter.open()
            with pytest.raises(LibreVnaDisconnectedError):
                adapter.write(b"\x5a\x00")

    def test_read_before_open_rejected(self) -> None:
        adapter = PyUsbAdapter()
        with pytest.raises(LibreVnaNotOpenError):
            adapter.read(512, 50)

    def test_write_before_open_rejected(self) -> None:
        adapter = PyUsbAdapter()
        with pytest.raises(LibreVnaNotOpenError):
            adapter.write(b"\x5a\x00")

    def test_release_error_reported_and_state_cleared(self) -> None:
        dev = _FakeDevice()
        util = _FakeUsbUtil(release_error=_FakeUsbError("release failed"))
        core = _make_core(lambda idVendor, idProduct, backend=None: dev)
        with _patched_transport(core, util):
            adapter = PyUsbAdapter()
            adapter.open()
            with pytest.raises(LibreVnaReleaseError):
                adapter.close()
            # state already cleared and resources still disposed
            assert util.disposed
            assert not adapter.is_open
            adapter.close()  # second close: idempotent no-op


# ---------------------------------------------------------------------------
# Lazy loading: importing the transport must not load pyusb
# ---------------------------------------------------------------------------


class TestLazyLoading:
    def test_import_does_not_load_usb(self) -> None:
        code = (
            "import sys; sys.path.insert(0, r'"
            + str(SRC_DIR)
            + "'); import uav_gpr.acquisition.librevna.transport;"
            "bad = [m for m in sys.modules if m.split('.')[0]"
            " in ('usb', 'libusb_package')];"
            "print(bad); sys.exit(1 if bad else 0)"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            env=None,
        )
        assert result.returncode == 0, (
            f"importing the transport must not load usb/libusb_package: "
            f"{result.stdout.strip()}"
        )

    def test_constants_match_reference(self) -> None:
        from uav_gpr.acquisition.librevna.transport import EP_IN, EP_OUT, PID, VID

        assert VID == 0x1209
        assert PID == 0x4121
        assert EP_OUT == 0x01
        assert EP_IN == 0x81
        assert HEADER == 0x5A
        assert MIN_PACKET_LENGTH == 8
        assert MAX_PACKET_LENGTH == 4096
