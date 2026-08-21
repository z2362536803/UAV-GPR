"""Local quality gate runner (ISSUE-002).

Runs, in this fixed order:

1. non-hardware pytest
2. ruff
3. mypy
4. package import check

The first failing gate stops the run and its exit code is returned, so a
broken gate can never be hidden by later green steps.

Invoked by ``scripts\\verify.ps1`` (Windows one-click local verification) or
directly with any Python 3.12 interpreter::

    python tools\\quality\\verify.py
"""

from __future__ import annotations

import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

# The import check must prove the installed package edge is importable,
# including the layers that later issues build on.
IMPORT_CHECK = (
    "import importlib; "
    "[importlib.import_module(name) for name in ("
    "'uav_gpr', 'uav_gpr.core', 'uav_gpr.positioning', 'uav_gpr.storage'"
    ")]; "
    "print('package import ok')"
)


@dataclass(frozen=True)
class Gate:
    name: str
    command: tuple[str, ...]


def gates(interpreter: str = "python") -> tuple[Gate, ...]:
    """The ordered local gates; ``interpreter`` is the Python to run them with."""
    return (
        Gate(
            "pytest (non-hardware)",
            (interpreter, "-m", "pytest", "-m", "not hardware and not slow", "-q"),
        ),
        Gate("ruff", (interpreter, "-m", "ruff", "check", ".")),
        Gate("mypy", (interpreter, "-m", "mypy", "src")),
        Gate("package import", (interpreter, "-c", IMPORT_CHECK)),
    )


def run_gates(
    gates_to_run: Sequence[Gate],
    launch: Callable[[tuple[str, ...]], int],
    print_fn: Callable[[str], None] = print,
) -> int:
    """Run gates in order; stop at the first failure and return its exit code."""
    for gate in gates_to_run:
        print_fn(f"[quality] {gate.name} ...")
        code = launch(gate.command)
        if code != 0:
            print_fn(f"[quality] FAILED: {gate.name} (exit {code})")
            return code
        print_fn(f"[quality] ok: {gate.name}")
    print_fn("[quality] all gates passed")
    return 0


def main() -> int:
    root = Path(__file__).resolve().parents[2]
    interpreter = sys.executable

    def launch(command: tuple[str, ...]) -> int:
        return subprocess.run(list(command), cwd=str(root), check=False).returncode

    return run_gates(gates(interpreter), launch)


if __name__ == "__main__":
    raise SystemExit(main())
