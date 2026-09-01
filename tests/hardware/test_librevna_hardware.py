"""LibreVNA opt-in hardware tests (ISSUE-023).

These tests are marked ``hardware`` and therefore require the double opt-in
enforced by ``tests/conftest.py``: ``pytest --hardware`` AND
``UAV_GPR_HARDWARE_OPTIN=1``.  Default runs deselect them at collection;
running with only one of the two gates marks them skipped.

Honest BLOCKED semantics: when no LibreVNA device matches
VID 0x1209 / PID 0x4121 the tests ``pytest.skip`` with an explicit BLOCKED
message -- hardware acceptance is never faked (docs/issues/M04_LIBREVNA.md
ISSUE-023).  pyusb is imported lazily inside the tests so a missing
dependency can never break a default (non-opt-in) run.

The full benchmark matrix (all bands x points x IFBW x S11/dual) is
delivered by ``tools/benchmark/librevna_benchmark.py --backend hardware``;
the matrix test below pins the real-device report structure
(hardware/firmware/config/commit + p50/p95/p99, docs/PERFORMANCE.md 3/6).
"""

from __future__ import annotations

import importlib
import os
import subprocess
import time
from datetime import UTC, datetime

import numpy as np
import pytest

from uav_gpr.acquisition.librevna.backend import (
    S11_CHANNEL,
    LibreVnaUsbBackend,
    LibreVnaUsbSettings,
)
from uav_gpr.acquisition.librevna.transport import (
    PID,
    VID,
    LibreVnaUsbTransport,
    PyUsbAdapter,
)
from uav_gpr.core import (
    AcquisitionMode,
    DeviceId,
    GnssNoFixPolicy,
    MissionConfig,
    MissionId,
)

_MISSION = MissionId("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_DEVICE = DeviceId("cccccccc-cccc-4ccc-8ccc-cccccccccccc")


def _find_device() -> tuple[object | None, str | None]:
    """Find a LibreVNA by VID/PID; returns (device, None) or (None, reason)."""
    try:
        usb_core = importlib.import_module("usb.core")
    except ModuleNotFoundError:
        return None, "pyusb is not installed"
    try:
        dev = usb_core.find(idVendor=VID, idProduct=PID)
    except Exception as exc:
        return None, f"USB enumeration failed: {exc}"
    if dev is None:
        return None, f"no LibreVNA device found (VID 0x{VID:04x}/PID 0x{PID:04x})"
    return dev, None


def _skip_blocked(reason: str) -> None:
    pytest.skip(
        "BLOCKED: " + reason + " - hardware acceptance requires a designated "
        "real LibreVNA device; nothing is faked"
    )


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except Exception:
        return "unknown"
    return result.stdout.strip() or "unknown"


def _make_config(
    *,
    start_hz: float = 100_000_000.0,
    stop_hz: float = 200_000_000.0,
    points: int = 101,
    ifbw_hz: float = 100_000.0,
) -> MissionConfig:
    """Benchmark config; the matrix cells pass their own start/stop so every
    cell really measures its declared band (P2-2)."""
    return MissionConfig(
        frequency_start_hz=start_hz,
        frequency_stop_hz=stop_hz,
        frequency_points=points,
        if_bw_hz=ifbw_hz,
        power_dbm=-10.0,
        channels=[S11_CHANNEL],
        acquisition_mode=AcquisitionMode.CONTINUOUS,
        planned_trace_count=None,
        target_interval_s=0.05,
        gnss_max_age_s=2.0,
        gnss_no_fix_policy=GnssNoFixPolicy.RECORD_WITHOUT_POSITION,
        calibration_profile_id=None,
        apply_calibration=False,
        background_reference_id=None,
        apply_background=False,
        created_utc=datetime(2026, 1, 1, tzinfo=UTC),
        note="librevna hardware test",
        software_version="0.1.0.dev0",
    )


@pytest.mark.hardware
def test_hardware_device_present_and_identity() -> None:
    dev, reason = _find_device()
    if dev is None:
        _skip_blocked(reason)
    expected = os.environ.get("UAV_GPR_DEVICE_ID")
    if expected:
        serial = getattr(dev, "serial_number", None)
        assert serial == expected, (
            f"UAV_GPR_DEVICE_ID={expected} does not match the found device "
            f"serial {serial!r}"
        )


@pytest.mark.hardware
def test_hardware_open_configure_acquire_s11() -> None:
    dev, reason = _find_device()
    if dev is None:
        _skip_blocked(reason)
    backend = LibreVnaUsbBackend(
        LibreVnaUsbTransport(PyUsbAdapter()),
        mission_id=_MISSION,
        device_id=_DEVICE,
        settings=LibreVnaUsbSettings(sweep_timeout_s=60.0),
    )
    try:
        caps = backend.open()
        assert caps.device_id == _DEVICE
        info = backend.device_info
        assert info is not None
        assert info.protocol > 0
        config = _make_config()
        applied = backend.configure(config)
        assert applied.config.frequency_points == config.frequency_points
        sweep = backend.acquire(timeout_s=60.0)
        assert sweep.data.shape == (1, config.frequency_points)
        assert sweep.metadata.connection_generation == 1
    finally:
        backend.close()


@pytest.mark.hardware
def test_hardware_benchmark_matrix_report_structure() -> None:
    """Real-device mini matrix: 2 cells x S11, >= 5 sweeps each.

    Pins the report structure required by the ISSUE-023 acceptance:
    hardware/firmware/configuration/commit plus p50/p95/p99
    (docs/PERFORMANCE.md sections 3/6; M04_LIBREVNA.md ISSUE-023).
    """
    dev, reason = _find_device()
    if dev is None:
        _skip_blocked(reason)
    cells = [
        (100_000_000, 200_000_000, 101, 100_000.0),
        (100_000_000, 500_000_000, 201, 50_000.0),
    ]
    report: dict[str, object] = {
        "commit": _git_commit(),
        "hardware": {
            "vid": VID,
            "pid": PID,
            "firmware": None,
            "protocol": None,
        },
        "config": None,
        "cells": [],
    }
    for start_hz, stop_hz, points, ifbw_hz in cells:
        config = _make_config(
            start_hz=float(start_hz),
            stop_hz=float(stop_hz),
            points=points,
            ifbw_hz=ifbw_hz,
        )
        backend = LibreVnaUsbBackend(
            LibreVnaUsbTransport(PyUsbAdapter()),
            mission_id=_MISSION,
            device_id=_DEVICE,
            settings=LibreVnaUsbSettings(sweep_timeout_s=60.0),
        )
        try:
            backend.open()
            info = backend.device_info
            assert info is not None
            hardware = report["hardware"]
            assert isinstance(hardware, dict)
            hardware["firmware"] = info.firmware
            hardware["protocol"] = info.protocol
            applied = backend.configure(config)
            # P2-2: the cell must really measure its declared band -- the
            # reported start/stop must equal the applied device config.
            assert applied.config.frequency_start_hz == float(start_hz)
            assert applied.config.frequency_stop_hz == float(stop_hz)
            assert applied.config.frequency_points == points
            assert applied.config.if_bw_hz == float(ifbw_hz)
            durations: list[float] = []
            for _ in range(5):
                started = time.perf_counter()
                sweep = backend.acquire(timeout_s=60.0)
                assert sweep.data.shape == (1, points)
                durations.append(time.perf_counter() - started)
        finally:
            backend.close()
        arr = np.asarray(durations, dtype=np.float64)
        cells_out = report["cells"]
        assert isinstance(cells_out, list)
        cells_out.append(
            {
                "start_hz": start_hz,
                "stop_hz": stop_hz,
                "points": points,
                "ifbw_hz": ifbw_hz,
                "sweep_duration_s_p50": float(np.percentile(arr, 50)),
                "sweep_duration_s_p95": float(np.percentile(arr, 95)),
                "sweep_duration_s_p99": float(np.percentile(arr, 99)),
            }
        )
    hardware = report["hardware"]
    assert isinstance(hardware, dict)
    assert hardware["firmware"]
    assert len(report["cells"]) == 2
    for cell in report["cells"]:
        assert isinstance(cell, dict)
        assert cell["sweep_duration_s_p50"] > 0.0
        assert cell["sweep_duration_s_p99"] >= cell["sweep_duration_s_p50"]
