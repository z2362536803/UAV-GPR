"""Shared pytest fixtures and environment policy (ISSUE-002).

Policies enforced here:

- Deterministic random seed (``random`` and ``numpy``), configurable with
  ``--seed`` (default 0).
- ``TZ=UTC`` and ``QT_QPA_PLATFORM=offscreen`` environment defaults so tests
  never depend on the host timezone or open a Qt window.
- Hardware tests require a double opt-in: the ``--hardware`` CLI flag AND the
  ``UAV_GPR_HARDWARE_OPTIN=1`` environment variable.  Default runs skip them.
- ``scratch_dir`` and ``virtual_clock`` are the standard isolated-fixture
  building blocks used by unit tests.
"""

from __future__ import annotations

import os
import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import pytest

# Environment policy: must be set before any Qt application is instantiated.
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
os.environ.setdefault("TZ", "UTC")

HARDWARE_OPTIN_ENV = "UAV_GPR_HARDWARE_OPTIN"


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--seed",
        action="store",
        type=int,
        default=0,
        help="random seed for deterministic tests (default 0)",
    )
    parser.addoption(
        "--hardware",
        action="store_true",
        default=False,
        help=f"opt-in to run hardware tests; also requires {HARDWARE_OPTIN_ENV}=1",
    )


@pytest.fixture(autouse=True)
def _deterministic_random(request: pytest.FixtureRequest) -> Iterator[None]:
    """Seed ``random`` and ``numpy`` before every test for reproducibility."""
    seed = request.config.getoption("--seed")
    random.seed(seed)
    np.random.seed(seed)
    yield


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    """Skip hardware tests unless both opt-in gates are enabled."""
    run_hardware = bool(config.getoption("--hardware")) and os.environ.get(
        HARDWARE_OPTIN_ENV
    ) == "1"
    for item in items:
        if "hardware" in item.keywords and not run_hardware:
            item.add_marker(
                pytest.mark.skip(
                    reason=(
                        "hardware double opt-in required: run with --hardware "
                        f"and set {HARDWARE_OPTIN_ENV}=1"
                    )
                )
            )


@pytest.fixture()
def scratch_dir(tmp_path: Path) -> Path:
    """A fresh, isolated directory owned by the test (base temp fixture)."""
    path = tmp_path / "scratch"
    path.mkdir()
    return path


@dataclass
class VirtualClock:
    """Test-only controllable clock (UTC + monotonic ns, no hardware)."""

    _utc: datetime = field(
        default_factory=lambda: datetime(2026, 1, 1, tzinfo=UTC)
    )
    _monotonic_ns: int = 0

    def utc_now(self) -> datetime:
        return self._utc

    def monotonic_ns(self) -> int:
        return self._monotonic_ns

    def advance(self, delta: timedelta | None = None, ns: int | None = None) -> None:
        if delta is not None:
            self._utc = self._utc + delta
        if ns is not None:
            self._monotonic_ns += ns


@pytest.fixture()
def virtual_clock() -> VirtualClock:
    """Virtual clock base fixture: deterministic UTC and monotonic time."""
    return VirtualClock()
