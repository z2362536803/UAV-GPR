"""Hardware sentinel test.

This test is marked ``hardware`` and must NEVER execute during a default
(non-opt-in) run.  If it ever does, the double opt-in gate in
``tests/conftest.py`` is broken and a real-device test could have run
unintentionally.
"""

from __future__ import annotations

import pytest


@pytest.mark.hardware
def test_hardware_sentinel_never_runs_without_opt_in() -> None:
    # Never reached in default runs; used by tests/unit/test_quality_gates.py
    # to verify the default collection skips hardware-marked tests.
    print("HARDWARE_SENTINEL_RAN")
