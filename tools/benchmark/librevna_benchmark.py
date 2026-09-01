"""LibreVNA reproducible benchmark tool (ISSUE-023).

Measures the acquisition-side benchmark matrix agreed in
docs/PERFORMANCE.md sections 1/3/6: sweep duration percentiles
(p50/p95/p99/mean/max), the pre-write model overhead (the ISSUE-009
canonical raw hash -- the representative step before any storage write),
the error rate, the CPU ratio and the target configuration, together with
the environment (git commit, Python, platform, numpy).

Modes:

- ``--backend simulated`` (default): deterministic, hardware-free.  The
  same ``seed`` + config produces the same sweep data every run; durations
  are wall-clock but the procedure is fully reproducible.  ``--inject-timeouts``
  deterministically injects ``SimulationFaults.timeout_at`` so the error
  rate metric is exercised without hardware.
- ``--backend hardware``: real LibreVNA path with the double opt-in
  (``--hardware`` CLI flag AND ``UAV_GPR_HARDWARE_OPTIN=1``).  When the
  device is absent (or pyusb is missing) the tool prints an honest
  ``status: "blocked"`` report and exits 3 -- real-device numbers are never
  fabricated.  On this machine (no designated LibreVNA, see
  docs/reports/ISSUE_023_BASELINE_CONFIRMATION.md section 3.5) the hardware
  matrix stays BLOCKED.

Reference numbers from the audited rebar-inspector
``LibreVNA采集速度测试`` are comparison material only and are never
written into this report as results (docs/ACQUISITION.md section 3).
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np

from uav_gpr.acquisition.backend import (
    Capabilities,
    SimulatedBackend,
    SimulationFaults,
)
from uav_gpr.acquisition.librevna.backend import (
    S11_CHANNEL,
    S22_CHANNEL,
    LibreVnaUsbBackend,
    LibreVnaUsbSettings,
)
from uav_gpr.acquisition.librevna.transport import (
    PID,
    VID,
    LibreVnaDeviceNotFoundError,
    LibreVnaMissingDependencyError,
    LibreVnaUsbTransport,
    PyUsbAdapter,
)
from uav_gpr.core import (
    AcquisitionMode,
    DeviceId,
    GnssNoFixPolicy,
    MissionConfig,
    MissionId,
    RawHashSpec,
)

TOOL_NAME = "librevna_benchmark"
TOOL_VERSION = "1.0.0"
HARDWARE_OPTIN_ENV = "UAV_GPR_HARDWARE_OPTIN"

_REPO_ROOT = Path(__file__).resolve().parents[2]
_MISSION = MissionId("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa")
_DEVICE = DeviceId("bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb")
_FIXED_UTC = datetime(2026, 1, 1, tzinfo=UTC)


def _percentiles(values: list[float]) -> dict[str, float]:
    """p50/p95/p99/mean/max of a duration list (all-zero when empty)."""
    if not values:
        return {"p50": 0.0, "p95": 0.0, "p99": 0.0, "mean": 0.0, "max": 0.0}
    arr = np.asarray(values, dtype=np.float64)
    return {
        "p50": float(np.percentile(arr, 50)),
        "p95": float(np.percentile(arr, 95)),
        "p99": float(np.percentile(arr, 99)),
        "mean": float(np.mean(arr)),
        "max": float(np.max(arr)),
    }


def build_config(args: argparse.Namespace) -> MissionConfig:
    """The frozen benchmark config; same args -> same config digest."""
    channels = [S11_CHANNEL, S22_CHANNEL] if args.dual else [S11_CHANNEL]
    return MissionConfig(
        frequency_start_hz=float(args.start_hz),
        frequency_stop_hz=float(args.stop_hz),
        frequency_points=args.points,
        if_bw_hz=float(args.ifbw_hz),
        power_dbm=args.power_dbm,
        channels=channels,
        acquisition_mode=AcquisitionMode.CONTINUOUS,
        planned_trace_count=None,
        target_interval_s=0.05,
        gnss_max_age_s=2.0,
        gnss_no_fix_policy=GnssNoFixPolicy.RECORD_WITHOUT_POSITION,
        calibration_profile_id=None,
        apply_calibration=False,
        background_reference_id=None,
        apply_background=False,
        created_utc=_FIXED_UTC,
        note="librevna_benchmark tool",
        software_version="0.1.0.dev0",
    )


def measure(
    backend: Any,
    config: MissionConfig,
    *,
    sweeps: int,
) -> dict[str, Any]:
    """Acquire ``sweeps`` completed sweeps and collect the metrics.

    ``backend`` is any ``AcquisitionBackend`` (SimulatedBackend or
    LibreVnaUsbBackend); failed acquire attempts are counted as errors and
    never fabricate a sweep.  The loop is safety-bounded so a dead device
    cannot hang the tool.
    """
    durations: list[float] = []
    overheads: list[float] = []
    errors = 0
    completed = 0
    wall_start = time.perf_counter()
    cpu_start = time.process_time()
    while completed < sweeps:
        if errors > sweeps + 100:
            raise RuntimeError("benchmark aborted: too many consecutive errors")
        started = time.perf_counter()
        try:
            sweep = backend.acquire()
        except Exception:
            errors += 1
            continue
        durations.append(time.perf_counter() - started)
        spec = RawHashSpec(
            mission_id=sweep.metadata.mission_id,
            trace_index=sweep.metadata.trace_index,
            trace_uid=sweep.metadata.trace_uid,
            channels=sweep.channels,
            frequencies_hz=sweep.frequencies_hz,
            data=sweep.data,
        )
        hash_started = time.perf_counter()
        spec.compute()
        overheads.append(time.perf_counter() - hash_started)
        completed += 1
    wall_s = time.perf_counter() - wall_start
    cpu_s = time.process_time() - cpu_start
    total_attempts = completed + errors
    return {
        "completed_sweeps": completed,
        "failed_attempts": errors,
        "error_rate": (errors / total_attempts) if total_attempts else 0.0,
        "sweep_rate_hz": (completed / wall_s) if wall_s > 0.0 else 0.0,
        "total_wall_s": wall_s,
        "cpu_ratio": (cpu_s / wall_s) if wall_s > 0.0 else 0.0,
        "sweep_duration_s": _percentiles(durations),
        "model_overhead_s": _percentiles(overheads),
    }


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=str(_REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=5.0,
            check=False,
        )
    except Exception:
        return "unknown"
    commit = result.stdout.strip()
    return commit or "unknown"


def _environment() -> dict[str, str]:
    return {
        "commit": _git_commit(),
        "python": platform.python_version(),
        "platform": platform.platform(),
        "numpy": np.__version__,
    }


def run_simulated(args: argparse.Namespace) -> dict[str, Any]:
    config = build_config(args)
    faults = SimulationFaults(timeout_at=tuple(range(args.inject_timeouts)))
    backend = SimulatedBackend(
        mission_id=_MISSION,
        device_id=_DEVICE,
        channels=config.channels,
        seed=args.seed,
        clock=None,
        faults=faults,
        gnss_enabled=False,
    )
    capabilities = backend.open()
    backend.configure(config)
    try:
        results = measure(backend, config, sweeps=args.sweeps)
    finally:
        backend.close()
    return {
        "backend": "simulated",
        "hardware": {
            "vid": None,
            "pid": None,
            "device_present": False,
            "firmware": None,
            "capabilities": _capabilities_dict(capabilities),
        },
        "config": _config_dict(config, args),
        "results": results,
    }


def _capabilities_dict(capabilities: Capabilities) -> dict[str, Any]:
    return {
        "device_id": capabilities.device_id.to_json(),
        "channels": [str(c.channel_id) for c in capabilities.channels],
        "supports_dual_channel": capabilities.supports_dual_channel,
    }


def _config_dict(config: MissionConfig, args: argparse.Namespace) -> dict[str, Any]:
    return {
        "frequency_start_hz": config.frequency_start_hz,
        "frequency_stop_hz": config.frequency_stop_hz,
        "frequency_points": config.frequency_points,
        "if_bw_hz": config.if_bw_hz,
        "power_dbm": config.power_dbm,
        "channels": [str(c.channel_id) for c in config.channels],
        "target_interval_s": config.target_interval_s,
        "sweeps": args.sweeps,
        "seed": args.seed,
        "inject_timeouts": args.inject_timeouts,
        "config_sha256": config.config_sha256,
    }


def run_hardware(args: argparse.Namespace) -> dict[str, Any]:
    """Real-device matrix; BLOCKED (exit 3) when the device is unavailable."""
    if os.environ.get(HARDWARE_OPTIN_ENV) != "1":
        print(
            "hardware benchmark requires the double opt-in: pass --hardware "
            f"AND set {HARDWARE_OPTIN_ENV}=1",
            file=sys.stderr,
        )
        raise SystemExit(2)
    config = build_config(args)
    backend = LibreVnaUsbBackend(
        LibreVnaUsbTransport(PyUsbAdapter()),
        mission_id=_MISSION,
        device_id=_DEVICE,
        settings=LibreVnaUsbSettings(sweep_timeout_s=60.0),
    )
    try:
        capabilities = backend.open()
    except LibreVnaDeviceNotFoundError as exc:
        return _blocked_report(f"no LibreVNA device found: {exc}")
    except LibreVnaMissingDependencyError as exc:
        return _blocked_report(f"USB runtime dependency missing: {exc}")
    info = backend.device_info
    try:
        backend.configure(config)
        results = measure(backend, config, sweeps=args.sweeps)
    finally:
        backend.close()
    return {
        "backend": "librevna-usb",
        "hardware": {
            "vid": f"0x{VID:04x}",
            "pid": f"0x{PID:04x}",
            "device_present": True,
            "firmware": info.firmware if info is not None else None,
            "protocol": info.protocol if info is not None else None,
            "capabilities": _capabilities_dict(capabilities),
        },
        "config": _config_dict(config, args),
        "results": results,
    }


def _blocked_report(reason: str) -> dict[str, Any]:
    """Honest BLOCKED report: hardware acceptance must never be fabricated."""
    return {
        "backend": "librevna-usb",
        "hardware": {
            "vid": f"0x{VID:04x}",
            "pid": f"0x{PID:04x}",
            "device_present": False,
            "firmware": None,
            "capabilities": None,
        },
        "config": None,
        "results": None,
        "status": "blocked",
        "blocked_reason": reason,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog=TOOL_NAME,
        description=(
            "LibreVNA reproducible benchmark (ISSUE-023): sweep durations "
            "p50/p95/p99, pre-write model overhead, error rate, CPU and "
            "target config."
        ),
    )
    parser.add_argument(
        "--backend",
        choices=("simulated", "hardware"),
        default="simulated",
        help="simulated (default, deterministic) or hardware (double opt-in)",
    )
    parser.add_argument("--start-hz", type=int, default=100_000_000)
    parser.add_argument("--stop-hz", type=int, default=200_000_000)
    parser.add_argument("--points", type=int, default=101)
    parser.add_argument("--ifbw-hz", type=int, default=100_000)
    parser.add_argument("--power-dbm", type=float, default=-10.0)
    parser.add_argument("--dual", action="store_true", help="S11/S22 dual channel")
    parser.add_argument("--sweeps", type=int, default=20)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument(
        "--inject-timeouts",
        type=int,
        default=0,
        help="deterministic simulated timeouts before the measured sweeps",
    )
    parser.add_argument(
        "--smoke",
        action="store_true",
        help="CI smoke preset: 3 sweeps, small config, order-of-magnitude only",
    )
    parser.add_argument(
        "--hardware",
        action="store_true",
        help="opt-in to the real-device path; also requires "
        f"{HARDWARE_OPTIN_ENV}=1",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="write the JSON report to this file (default: stdout only)",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.smoke:
        args.sweeps = 3
        args.inject_timeouts = 0
    if args.backend == "hardware" or args.hardware:
        report = run_hardware(args)
    else:
        report = run_simulated(args)
    report = {
        "tool": TOOL_NAME,
        "tool_version": TOOL_VERSION,
        "environment": _environment(),
        **report,
    }
    if report.get("status") != "blocked":
        report["status"] = "ok"
    text = json.dumps(report, indent=2)
    print(text)
    if args.output is not None:
        args.output.write_text(text + "\n", encoding="utf-8")
    if report.get("status") == "blocked":
        print(
            "BLOCKED: hardware acceptance requires a designated real LibreVNA "
            "device; no numbers were fabricated.",
            file=sys.stderr,
        )
        return 3
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
