"""LibreVNA USB transport layer (ISSUE-019), migrated from the audited
rebar-inspector reference (migration checklist and source hashes:
``docs/plans/2026-08-30-issue-019-librevna-transport.md`` section 4).

Sweep-free USB transport: device discovery/open/claim/release, bulk
read/write, protocol frame codec (header/length/type/CRC32) with strict
length caps, timeout and cancellable I/O, idempotent close and structured
error mapping.  No VNADatapoint/sweep/backend logic lives here
(ISSUE-020/021).

Behaviors audited from the reference:

- ``VID 0x1209 / PID 0x4121``, bulk ``EP_OUT 0x01 / EP_IN 0x81``;
- ``open`` is idempotent; ``set_configuration`` failures warn and continue
  (Windows); kernel-driver detach failures are ignored; claim failure
  disposes resources before raising ``LibreVnaBusyError``;
- ``read``: USB timeout -> ``LibreVnaTimeoutError``, other USB errors ->
  ``LibreVnaDisconnectedError``; ``write``: any failure ->
  ``LibreVnaDisconnectedError``;
- ``close`` is idempotent, clears state first, and still disposes resources
  when release fails (then raises ``LibreVnaReleaseError``);
- pyusb/libusb are lazily loaded only inside ``open``; a missing dependency
  raises ``LibreVnaMissingDependencyError`` with an install hint;
- frame codec: ``HEADER 0x5A``, length field covers the whole packet,
  ``crc32`` is IEEE 802.3 (zlib-compatible); ``PacketStream.feed`` discards
  noise before HEADER, realigns on out-of-range lengths (< 8 or > 4096),
  buffers incomplete packets, verifies CRC for every packet except
  ``VNA_DATAPOINT`` (type 27) which skips CRC validation -- existing
  reference protocol behavior that must not be "fixed" without device
  evidence.

Structured errors follow the repository pattern established by
``AcquisitionBackend`` (``acquisition/backend.py``): the core ``ErrorCode``
enum is read-only, so transport faults reuse ``ErrorCode.INVALID_ARGUMENT``
with a machine-stable ``reason`` discriminator and typed subclasses.
"""

from __future__ import annotations

import binascii
import ctypes
import importlib
import struct
import sys
import warnings
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from uav_gpr.core.errors import DomainError, ErrorCode, JsonValue

# ---- Device constants (reference protocol) ----

VID = 0x1209
PID = 0x4121
EP_OUT = 0x01
EP_IN = 0x81
HEADER = 0x5A

# ---- Packet types (frame layer only; payload semantics live in ISSUE-020+) ----

SWEEP_SETTINGS = 2
DEVICE_INFO = 5
ACK = 7
NACK = 10
REQUEST_DEVICE_INFO = 15
SET_IDLE = 20
DEVICE_STATUS = 25
VNA_DATAPOINT = 27

#: Strict frame length bounds (reference behavior).
MIN_PACKET_LENGTH = 8
MAX_PACKET_LENGTH = 4096

_HARDWARE_INSTALL_HINT = 'pip install -e ".[hardware]"'


# ---------------------------------------------------------------------------
# Structured errors
# ---------------------------------------------------------------------------


class LibreVnaTransportError(DomainError):
    """Structured LibreVNA USB transport failure.

    Business logic branches on the typed subclass and
    ``context["reason"]`` (the core ``ErrorCode`` enum is read-only;
    transport faults reuse ``INVALID_ARGUMENT`` with a machine-stable
    reason discriminator -- same pattern as ``BackendError``).
    """

    _reason: str = "librevna_transport_error"

    def __init__(self, message: str, **context: JsonValue) -> None:
        super().__init__(
            ErrorCode.INVALID_ARGUMENT,
            message,
            {"reason": self._reason, **context},
        )

    @property
    def reason(self) -> str:
        return self._reason


class LibreVnaMissingDependencyError(LibreVnaTransportError):
    """pyusb/libusb runtime dependency is missing (lazy load failed)."""

    _reason = "missing_dependency"


class LibreVnaDeviceNotFoundError(LibreVnaTransportError):
    """No device matched the configured VID/PID."""

    _reason = "device_not_found"


class LibreVnaBusyError(LibreVnaTransportError):
    """Device access or interface claim failed (likely busy/claimed)."""

    _reason = "busy"


class LibreVnaTimeoutError(LibreVnaTransportError):
    """Bulk read timed out."""

    _reason = "timeout"


class LibreVnaDisconnectedError(LibreVnaTransportError):
    """Bulk read/write failed (device likely disconnected)."""

    _reason = "disconnected"


class LibreVnaReleaseError(LibreVnaTransportError):
    """Interface release failed (resources were still disposed)."""

    _reason = "release_failed"


class LibreVnaCancelledError(LibreVnaTransportError):
    """I/O was cancelled through :meth:`LibreVnaUsbTransport.cancel`."""

    _reason = "cancelled"


class LibreVnaNotOpenError(LibreVnaTransportError):
    """Read/write attempted while the device is not open."""

    _reason = "not_open"


# ---------------------------------------------------------------------------
# Frame codec (strict length caps; sweep payload semantics are NOT parsed)
# ---------------------------------------------------------------------------


def crc32(data: bytes) -> int:
    """CRC32 (IEEE 802.3, zlib-compatible), low 32 bits."""
    return binascii.crc32(data) & 0xFFFFFFFF


@dataclass(frozen=True)
class Packet:
    """A decoded protocol packet (frame layer: type + raw payload)."""

    packet_type: int
    payload: bytes


def encode_packet(packet_type: int, payload: bytes = b"") -> bytes:
    """Encode one packet: HEADER + length(2) + type(1) + payload + CRC32(4).

    ``length`` covers the whole packet (1 + 2 + 1 + len(payload) + 4).
    """
    length = 1 + 2 + 1 + len(payload) + 4
    body = struct.pack("<BHB", HEADER, length, packet_type) + payload
    return body + struct.pack("<I", crc32(body))


class PacketStream:
    """Sticky de-framing buffer across reads (reference ``PacketStream``).

    - discards noise bytes before ``HEADER``;
    - out-of-range length (< 8 or > 4096) drops the current byte and
      realigns -- a malicious length can never allocate an unbounded buffer;
    - incomplete packets stay buffered for later data;
    - non-``VNA_DATAPOINT`` packets are CRC-verified; ``VNA_DATAPOINT``
      (type 27) skips CRC validation (existing reference protocol behavior).
    """

    def __init__(self) -> None:
        self.buffer = bytearray()

    def reset(self) -> None:
        """Clear the internal buffer (session boundary)."""
        self.buffer.clear()

    def feed(self, data: bytes) -> list[Packet]:
        """Append raw bytes and return every complete packet decoded."""
        self.buffer.extend(data)
        packets: list[Packet] = []
        while True:
            while self.buffer and self.buffer[0] != HEADER:
                del self.buffer[0]
            if len(self.buffer) < 4:
                return packets
            length = struct.unpack_from("<H", self.buffer, 1)[0]
            if length < MIN_PACKET_LENGTH or length > MAX_PACKET_LENGTH:
                del self.buffer[0]
                continue
            if len(self.buffer) < length:
                return packets
            raw = bytes(self.buffer[:length])
            del self.buffer[:length]
            packet_type = raw[3]
            expected_crc = struct.unpack_from("<I", raw, length - 4)[0]
            if packet_type != VNA_DATAPOINT and expected_crc != crc32(raw[:-4]):
                continue
            packets.append(Packet(packet_type, raw[4:-4]))


# ---------------------------------------------------------------------------
# USB adapter boundary (dependency injection for hardware-free tests)
# ---------------------------------------------------------------------------


class UsbAdapter(Protocol):
    """Minimal raw-USB adapter the transport session depends on.

    Implementations raise :class:`LibreVnaTransportError` subclasses on
    failure and must release resources on every failure stage; ``close`` is
    idempotent.
    """

    @property
    def is_open(self) -> bool:
        """Whether the device is currently open."""

    def open(self) -> None:
        """Open the device and claim the interface (idempotent)."""

    def read(self, max_length: int, timeout_ms: int) -> bytes:
        """Read raw bytes from the IN endpoint (timeout in ms)."""

    def write(self, data: bytes) -> None:
        """Write raw bytes to the OUT endpoint."""

    def close(self) -> None:
        """Release the interface and resources (idempotent)."""


class PyUsbAdapter:
    """PyUSB/libusb implementation of :class:`UsbAdapter`.

    Lazy-loads ``usb.core``/``usb.util`` only inside :meth:`open`; importing
    this module never loads pyusb (default tests never enumerate USB).
    """

    def __init__(
        self,
        *,
        vid: int = VID,
        pid: int = PID,
        ep_out: int = EP_OUT,
        ep_in: int = EP_IN,
    ) -> None:
        if not isinstance(vid, int) or not isinstance(pid, int):
            raise TypeError("vid/pid must be int")
        if not isinstance(ep_out, int) or not isinstance(ep_in, int):
            raise TypeError("ep_out/ep_in must be int")
        self._vid = vid
        self._pid = pid
        self._ep_out = ep_out
        self._ep_in = ep_in
        # Lazily loaded pyusb handles are dynamic by design (Any).
        self._dev: Any = None
        self._interface_number: int | None = None
        self._usb_core: Any = None
        self._usb_util: Any = None

    # ---- query ----

    @property
    def is_open(self) -> bool:
        return self._dev is not None

    # ---- lifecycle ----

    def open(self) -> None:
        """Open the device, set configuration and claim the interface."""
        if self._dev is not None:
            return
        usb_core, usb_util = self._load_usb_modules()
        backend = self._build_libusb1_backend(usb_core)
        try:
            dev = usb_core.find(
                idVendor=self._vid, idProduct=self._pid, backend=backend
            )
        except usb_core.USBError as exc:
            raise LibreVnaBusyError(
                f"LibreVNA access failed (VID={self._vid:#06x} "
                f"PID={self._pid:#06x}), device may be busy: {exc}"
            ) from exc
        if dev is None:
            raise LibreVnaDeviceNotFoundError(
                f"LibreVNA device not found (VID={self._vid:#06x} "
                f"PID={self._pid:#06x})"
            )

        try:
            dev.set_configuration()
        except usb_core.USBError as exc:
            # Already-active WinUSB configuration can fail; warn and continue.
            warnings.warn(
                f"LibreVNA set_configuration warning: {exc}", stacklevel=2
            )

        cfg = dev.get_active_configuration()
        interface = cfg[(0, 0)]
        try:
            if dev.is_kernel_driver_active(interface.bInterfaceNumber):
                dev.detach_kernel_driver(interface.bInterfaceNumber)
        except (NotImplementedError, usb_core.USBError):
            pass
        try:
            usb_util.claim_interface(dev, interface.bInterfaceNumber)
        except usb_core.USBError as exc:
            try:
                usb_util.dispose_resources(dev)
            except Exception:
                pass
            raise LibreVnaBusyError(
                "LibreVNA interface claim failed (device busy, e.g. "
                f"LibreVNA-GUI open): {exc}"
            ) from exc

        self._dev = dev
        self._interface_number = interface.bInterfaceNumber
        self._usb_core = usb_core
        self._usb_util = usb_util

    def close(self) -> None:
        """Release the interface and resources (idempotent).

        State is cleared first; a release failure still disposes resources
        and is reported as ``LibreVnaReleaseError`` (never swallowed).
        """
        if self._dev is None:
            return
        dev = self._dev
        interface_number = self._interface_number
        usb_util = self._usb_util
        self._dev = None
        self._interface_number = None

        release_error: Exception | None = None
        if interface_number is not None and usb_util is not None:
            try:
                usb_util.release_interface(dev, interface_number)
            except Exception as exc:  # wrapped as release error, never swallowed
                release_error = exc
        if usb_util is not None:
            try:
                usb_util.dispose_resources(dev)
            except Exception:  # best-effort resource cleanup
                pass
        if release_error is not None:
            raise LibreVnaReleaseError(
                f"LibreVNA device release failed: {release_error}"
            ) from release_error

    # ---- I/O ----

    def read(self, max_length: int, timeout_ms: int) -> bytes:
        if self._dev is None or self._usb_core is None:
            raise LibreVnaNotOpenError("USB device is not open, cannot read")
        try:
            data = self._dev.read(self._ep_in, max_length, timeout=timeout_ms)
        except self._usb_core.USBTimeoutError:
            raise LibreVnaTimeoutError("USB read timed out") from None
        except self._usb_core.USBError as exc:
            raise LibreVnaDisconnectedError(
                f"USB read failed (device may be disconnected): {exc}"
            ) from exc
        return bytes(data)

    def write(self, data: bytes) -> None:
        if self._dev is None:
            raise LibreVnaNotOpenError("USB device is not open, cannot write")
        try:
            self._dev.write(self._ep_out, data, timeout=1000)
        except Exception as exc:  # uniformly wrapped as disconnected
            raise LibreVnaDisconnectedError(
                f"USB write failed (device may be disconnected): {exc}"
            ) from exc

    # ---- internals ----

    def _load_usb_modules(self) -> tuple[Any, Any]:
        """Lazily import pyusb; a missing dependency is a friendly error."""
        try:
            usb_core = importlib.import_module("usb.core")
            usb_util = importlib.import_module("usb.util")
            return usb_core, usb_util
        except ModuleNotFoundError as exc:
            name = (exc.name or "").split(".")[0]
            raise LibreVnaMissingDependencyError(
                f"LibreVNA USB runtime dependency missing ({name or 'pyusb'}); "
                f"install with: {_HARDWARE_INSTALL_HINT}"
            ) from exc

    def _build_libusb1_backend(self, usb_core: Any) -> Any:
        """Prefer the libusb-1.0 DLL shipped with libusb_package on Windows."""
        backend: Any = None
        dll: Path | None = None
        try:
            libusb_package = importlib.import_module("libusb_package")
            package_file = libusb_package.__file__
            if package_file is not None:
                dll_dir = Path(package_file).resolve().parent
                candidate = dll_dir / "libusb-1.0.dll"
                if candidate.is_file():
                    dll = candidate
        except (ModuleNotFoundError, OSError):
            dll = None
        windll = getattr(ctypes, "WinDLL", None)
        if dll is not None and windll is not None and sys.platform.startswith("win"):
            try:
                windll(str(dll))
            except OSError:
                dll = None
        if dll is not None:
            try:
                usb_backend = importlib.import_module("usb.backend.libusb1")
                backend = usb_backend.get_backend(
                    find_library=lambda _: str(dll)
                )
            except Exception:  # fall back to the default backend
                backend = None
        return backend


# ---------------------------------------------------------------------------
# Transport session facade (cancellable, idempotent, testable without hardware)
# ---------------------------------------------------------------------------


class LibreVnaUsbTransport:
    """Cancellable USB transport session over an injected :class:`UsbAdapter`.

    Session semantics (deterministic, no threads):

    - ``open`` is idempotent and starts a fresh session (clears cancel);
    - ``read``/``write`` raise ``LibreVnaNotOpenError`` when not open and
      ``LibreVnaCancelledError`` when a cancellation was requested;
    - ``cancel`` requests cancellation: the next ``read``/``write`` raises
      ``LibreVnaCancelledError`` immediately; it is safe before open and
      after close and never leaves a handle behind (in-flight blocking read
      preemption is device-disconnect handling, ISSUE-023);
    - ``close`` is idempotent, is not affected by cancel, and releases the
      adapter (no leaked handles).
    """

    def __init__(self, adapter: UsbAdapter) -> None:
        self._adapter = adapter
        self._cancelled = False

    @property
    def is_open(self) -> bool:
        return self._adapter.is_open

    def open(self) -> None:
        """Open the adapter and start a fresh session (idempotent)."""
        if not self._adapter.is_open:
            self._adapter.open()
        self._cancelled = False

    def read(self, max_length: int, timeout_ms: int) -> bytes:
        if not self._adapter.is_open:
            raise LibreVnaNotOpenError("USB device is not open, cannot read")
        if self._cancelled:
            raise LibreVnaCancelledError("USB transport cancelled")
        return self._adapter.read(max_length, timeout_ms)

    def write(self, data: bytes) -> None:
        if not self._adapter.is_open:
            raise LibreVnaNotOpenError("USB device is not open, cannot write")
        if self._cancelled:
            raise LibreVnaCancelledError("USB transport cancelled")
        self._adapter.write(data)

    def cancel(self) -> None:
        """Request cancellation of further I/O (safe in any state)."""
        self._cancelled = True

    def close(self) -> None:
        """Release the adapter (idempotent; not blocked by cancel)."""
        if self._adapter.is_open:
            self._adapter.close()
