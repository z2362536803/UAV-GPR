"""Tests for the local quality gates and shared test infrastructure (ISSUE-002)."""

from __future__ import annotations

import importlib.util
import os
import random
import subprocess
import sys
from collections.abc import Sequence
from datetime import timedelta
from pathlib import Path

from conftest import VirtualClock

VERIFY_PATH = (
    Path(__file__).resolve().parents[2] / "tools" / "quality" / "verify.py"
)

HARDWARE_OPTIN_ENV = "UAV_GPR_HARDWARE_OPTIN"


def _load_verify() -> object:
    spec = importlib.util.spec_from_file_location("quality_verify", VERIFY_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _run(
    args: list[str],
    cwd: Path | None = None,
    extra_env: dict[str, str] | None = None,
    remove_env: Sequence[str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Run a subprocess with a controlled environment.

    ``remove_env`` deletes variables from the child environment so a test can
    prove a property regardless of what the *parent* process happens to have
    set (e.g. ``UAV_GPR_HARDWARE_OPTIN=1`` exported in the developer shell).
    """
    env = os.environ.copy()
    for name in remove_env or ():
        env.pop(name, None)
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        args,
        cwd=cwd,
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        env=env,
    )


def test_gate_names_and_order() -> None:
    verify = _load_verify()
    names = [gate.name for gate in verify.gates("python")]
    assert names == [
        "pytest (non-hardware)",
        "ruff",
        "mypy",
        "package import",
    ]


def test_run_gates_runs_in_order_and_stops_on_failure() -> None:
    verify = _load_verify()
    calls: list[str] = []

    def launch(command: tuple[str, ...]) -> int:
        calls.append(command[0])
        return 0 if command[0] != "bad" else 7

    gates = (
        verify.Gate("good", ("good",)),
        verify.Gate("bad", ("bad",)),
        verify.Gate("never", ("never",)),
    )
    assert verify.run_gates(gates, launch, print_fn=lambda _: None) == 7
    assert calls == ["good", "bad"]


def test_run_gates_all_pass_returns_zero() -> None:
    verify = _load_verify()
    calls: list[tuple[str, ...]] = []

    def launch(command: tuple[str, ...]) -> int:
        calls.append(command)
        return 0

    gates = (
        verify.Gate("a", ("x",)),
        verify.Gate("b", ("y",)),
    )
    assert verify.run_gates(gates, launch, print_fn=lambda _: None) == 0
    assert calls == [("x",), ("y",)]


def test_package_import_check_succeeds() -> None:
    verify = _load_verify()
    result = _run([sys.executable, "-c", verify.IMPORT_CHECK], cwd=Path.cwd())
    assert result.returncode == 0, result.stderr
    assert "package import ok" in result.stdout


def test_default_run_skips_hardware_marked_tests() -> None:
    result = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/hardware",
            "-q",
            "-p",
            "no:cacheprovider",
        ]
    )
    assert result.returncode == 0, result.stdout + result.stderr
    # ISSUE-023 added 3 LibreVNA hardware tests: the hardware directory now
    # holds 4 hardware-marked modules (sentinel + 3), all skipped without the
    # double opt-in.
    assert "4 skipped" in result.stdout
    assert "HARDWARE_SENTINEL_RAN" not in result.stdout


def test_hardware_double_optin_runs_marked_test() -> None:
    result = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/hardware",
            "-q",
            "-p",
            "no:cacheprovider",
            "--hardware",
        ],
        # Regression guard: strip the opt-in variable explicitly so this test
        # proves "flag alone is not enough" even when the *parent* process
        # (developer shell, CI wrapper) already exported the variable.
        remove_env=[HARDWARE_OPTIN_ENV],
    )
    # The env opt-in is missing, so even with --hardware the test must skip.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 skipped" in result.stdout
    assert "HARDWARE_SENTINEL_RAN" not in result.stdout


def test_parent_optin_env_does_not_execute_hardware_dir() -> None:
    """Parent env has the opt-in set, outer run passes no --hardware flag.

    Non-hardware tests start nested pytest subprocesses; none of them may
    execute ``tests/hardware`` just because the variable is inherited.  A
    small non-hardware module is selected so the probe stays fast and cannot
    recurse into the quality-gate tests' own subprocesses.
    """
    result = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/unit/test_no_external_access.py",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        extra_env={HARDWARE_OPTIN_ENV: "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "3 passed" in result.stdout
    assert "HARDWARE_SENTINEL_RAN" not in result.stdout


def test_hardware_env_alone_is_not_authorization() -> None:
    result = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/hardware",
            "-q",
            "-p",
            "no:cacheprovider",
        ],
        extra_env={HARDWARE_OPTIN_ENV: "1"},
    )
    # Env alone (no --hardware) must still skip: env is not an authorization.
    assert result.returncode == 0, result.stdout + result.stderr
    assert "4 skipped" in result.stdout
    assert "HARDWARE_SENTINEL_RAN" not in result.stdout


def test_hardware_runs_only_with_both_authorizations() -> None:
    result = _run(
        [
            sys.executable,
            "-m",
            "pytest",
            "tests/hardware",
            "-q",
            "-s",
            "-p",
            "no:cacheprovider",
            "--hardware",
        ],
        extra_env={HARDWARE_OPTIN_ENV: "1"},
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "1 passed" in result.stdout
    assert "HARDWARE_SENTINEL_RAN" in result.stdout


def test_random_seed_is_deterministic() -> None:
    # conftest seeds the stdlib random module with --seed (default 0).
    expected = random.Random(0).random()
    assert random.random() == expected


def test_virtual_clock_fixture_tracks_utc_and_monotonic(
    virtual_clock: VirtualClock,
) -> None:
    first_utc = virtual_clock.utc_now()
    first_mono = virtual_clock.monotonic_ns()
    assert first_utc.tzinfo is not None
    virtual_clock.advance(delta=timedelta(seconds=1, microseconds=250), ns=500)
    assert virtual_clock.utc_now() - first_utc == timedelta(seconds=1, microseconds=250)
    assert virtual_clock.monotonic_ns() - first_mono == 500
    assert virtual_clock.monotonic_ns() > first_mono


def test_scratch_dir_fixture_is_isolated(scratch_dir: Path) -> None:
    assert scratch_dir.is_dir()
    assert list(scratch_dir.iterdir()) == []
